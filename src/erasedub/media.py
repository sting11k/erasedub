"""Everything EraseDub asks ffmpeg and ffprobe to do.

Commands are argument lists (never a shell), file names are passed as absolute paths so a name that starts
with ``-`` is never read as an option, and every ffmpeg run reports progress through the run context and
stops as soon as the user cancels. A failing command becomes an :class:`~erasedub.errors.EraseDubError` that
says what EraseDub was doing and quotes the last lines ffmpeg printed.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from erasedub import hardware
from erasedub.context import RunContext
from erasedub.errors import CancelledError, EraseDubError, ProviderUnavailableError
from erasedub.links import doc
from erasedub.models import VideoInfo

#: Lines of ffmpeg's error output quoted in an error message.
_ERROR_LINES = 8
#: Seconds allowed for ffprobe to read a file's header.
_PROBE_TIMEOUT = 60


@dataclass(frozen=True)
class Tools:
    """The ffmpeg and ffprobe executables to run, and what the ffmpeg build can do."""

    ffmpeg: str
    ffprobe: str
    has_libass: bool = True
    has_libx264: bool = True


def find_tools() -> Tools:
    """Resolve ffmpeg and ffprobe like ``erasedub doctor`` does; raise when either is missing."""
    info = hardware.detect_ffmpeg()
    fix = f"install ffmpeg or set {hardware.FFMPEG_ENV} / {hardware.FFPROBE_ENV}"
    fix += f" ({doc('troubleshooting.md')})"
    if info.path is None:
        raise ProviderUnavailableError(f"ffmpeg not found - {fix}")
    if info.ffprobe_path is None:
        raise ProviderUnavailableError(f"ffprobe not found next to {info.path} - {fix}")
    return Tools(info.path, info.ffprobe_path, has_libass=info.has_libass, has_libx264=info.has_libx264)


@dataclass(frozen=True)
class MediaInfo:
    """What the engine needs to know about an input video."""

    video: VideoInfo
    #: ffprobe's name of the video codec, e.g. ``"h264"``.
    video_codec: str
    #: Pixel format of the video stream, e.g. ``"yuv420p"``; empty when ffprobe does not say.
    pix_fmt: str = ""


def _rate(text: object) -> float:
    """Frames per second of an ffprobe rate such as ``"30000/1001"``; 0 when unknown."""
    if not isinstance(text, str) or "/" not in text:
        return 0.0
    num, _, den = text.partition("/")
    try:
        value = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return value if value > 0 else 0.0


def _float(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def _rotation(stream: dict[str, Any]) -> int:
    """Rotation of a stream in degrees (phones store portrait video as rotated landscape)."""
    for item in stream.get("side_data_list") or []:
        if isinstance(item, dict) and "rotation" in item:
            try:
                return int(float(item["rotation"]))
            except (TypeError, ValueError):
                return 0
    tag = (stream.get("tags") or {}).get("rotate")
    try:
        return int(float(tag)) if tag is not None else 0
    except (TypeError, ValueError):
        return 0


def parse_probe(path: Path, data: dict[str, Any]) -> MediaInfo:
    """Turn ffprobe's JSON (``-show_format -show_streams``) into :class:`MediaInfo`."""
    streams = [s for s in data.get("streams") or [] if isinstance(s, dict)]
    videos = [
        s
        for s in streams
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]
    if not videos:
        raise EraseDubError(f"{path.name}: no video stream found (is it an audio file or an image?)")
    stream = videos[0]
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise EraseDubError(f"{path.name}: ffprobe reports no picture size for the video stream")
    if abs(_rotation(stream)) % 180 == 90:  # ffmpeg rotates on decode, so the picture is shown upright
        width, height = height, width
    fps = _rate(stream.get("avg_frame_rate")) or _rate(stream.get("r_frame_rate"))
    if fps <= 0:
        raise EraseDubError(f"{path.name}: ffprobe reports no frame rate for the video stream")
    duration = _float((data.get("format") or {}).get("duration")) or _float(stream.get("duration"))
    if duration <= 0:
        raise EraseDubError(
            f"{path.name}: ffprobe reports no duration; the file may be damaged or incomplete"
        )
    info = VideoInfo(
        path=path,
        width=width,
        height=height,
        duration=duration,
        fps=fps,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )
    return MediaInfo(info, str(stream.get("codec_name") or ""), str(stream.get("pix_fmt") or ""))


def probe(tools: Tools, path: Path) -> MediaInfo:
    """Read ``path``'s streams with ffprobe."""
    cmd = [
        tools.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
        str(path.resolve()),
    ]  # fmt: skip
    try:
        done = subprocess.run(  # noqa: S603 — argument list, no shell; the executable comes from find_tools
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EraseDubError(f"{path.name}: cannot run ffprobe ({exc})") from exc
    if done.returncode != 0:
        reason = done.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise EraseDubError(f"{path.name}: ffprobe cannot read this file: {reason[0]}")
    try:
        data = json.loads(done.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise EraseDubError(f"{path.name}: ffprobe printed something that is not JSON") from exc
    return parse_probe(path, data if isinstance(data, dict) else {})


def _progress_seconds(key: str, value: str) -> float | None:
    """Seconds of output written, from one ``-progress`` line; None for other lines."""
    if key not in ("out_time_us", "out_time_ms"):  # both are microseconds (a long-standing ffmpeg quirk)
        return None
    try:
        return int(value) / 1_000_000
    except ValueError:  # "N/A" before the first frame
        return None


def _tail(stream: Any) -> str:
    stream.seek(0)
    text = stream.read().decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-_ERROR_LINES:]) or "no error message"


def run_ffmpeg(
    tools: Tools,
    args: Sequence[str],
    *,
    ctx: RunContext,
    doing: str,
    duration: float | None = None,
    cwd: Path | None = None,
) -> None:
    """Run ffmpeg with ``args`` (inputs, filters, outputs) and wait for it.

    ``doing`` completes "ffmpeg failed while ..." in errors and is the progress message. With ``duration``
    (seconds of output expected), progress is reported as the share written so far. Cancelling stops ffmpeg.
    ``cwd`` is where relative paths in ``args`` point (the subtitle filter gets its file this way, so no
    path has to be escaped inside a filter graph).
    """
    cmd = [
        tools.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-nostats",
        "-progress", "pipe:1", *args,
    ]  # fmt: skip
    ctx.raise_if_cancelled()
    ctx.progress(0.0, doing)
    with tempfile.TemporaryFile() as errors:
        try:
            proc = subprocess.Popen(  # noqa: S603 — argument list, no shell; the executable comes from find_tools
                cmd,
                stdout=subprocess.PIPE,
                stderr=errors,
                stdin=subprocess.DEVNULL,
                cwd=cwd,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise EraseDubError(f"cannot start ffmpeg ({tools.ffmpeg}): {exc}") from exc
        try:
            for line in proc.stdout or ():
                if ctx.cancelled():
                    raise CancelledError("cancelled by the user")
                key, _, value = line.strip().partition("=")
                seconds = _progress_seconds(key, value)
                if seconds is not None and duration:
                    ctx.progress(seconds / duration, doing)
            code = proc.wait()
        finally:
            if proc.poll() is None:  # cancelled, Ctrl+C or an error while reading: never leave ffmpeg running
                proc.kill()
                proc.wait()
        if ctx.cancelled():
            raise CancelledError("cancelled by the user")
        if code != 0:
            raise EraseDubError(f"ffmpeg failed while {doing} (exit code {code}): {_tail(errors)}")
    ctx.progress(1.0, doing)


def extract_audio(tools: Tools, video: Path, output: Path, *, ctx: RunContext, duration: float) -> Path:
    """Write the first audio track of ``video`` as 16 kHz mono WAV, the input speech recognisers expect."""
    output.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        tools,
        [
            "-i", str(video.resolve()), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", str(output.resolve()),
        ],
        ctx=ctx,
        doing="extracting the audio",
        duration=duration,
    )  # fmt: skip
    return output
