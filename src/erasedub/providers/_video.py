"""Frame I/O and region planning shared by the built-in erasers.

The erasers stream the video through ffmpeg: frames are decoded to raw RGB, the ones that show text are
inpainted around the text only, and every frame is encoded again at the source frame rate, with the source
audio stream copied unchanged. Nothing is written into the input video.

Planning is pure Python so it can be tested without NumPy or PyTorch: text regions are grouped into
*jobs*. A job is one rectangle of the frame (the text boxes that overlap, plus some surrounding context the
model needs) over a run of consecutive frames. The erasers crop that rectangle, fill the masked pixels and
paste only the masked pixels back.
"""

from __future__ import annotations

import importlib
import json
import math
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import IO, Any

from erasedub.context import RunContext
from erasedub.errors import EraseDubError, ProviderUnavailableError
from erasedub.hardware import FFMPEG_ENV, FFPROBE_ENV, detect_ffmpeg, find_ffprobe
from erasedub.links import doc
from erasedub.models import TextRegion


def import_module(name: str) -> Any:
    """Import a heavy optional dependency lazily and hand it back untyped (it is not installed for mypy)."""
    return importlib.import_module(name)


# --- geometry and planning (pure Python) -----------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Rect:
    """Half-open pixel rectangle ``[x0, x1) x [y0, y1)``."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    def grow(self, by: int, width: int, height: int) -> Rect:
        """Grow by ``by`` pixels on every side, clamped to a ``width`` x ``height`` frame."""
        return Rect(
            max(0, self.x0 - by), max(0, self.y0 - by), min(width, self.x1 + by), min(height, self.y1 + by)
        )

    def overlaps(self, other: Rect) -> bool:
        return self.x0 < other.x1 and other.x0 < self.x1 and self.y0 < other.y1 and other.y0 < self.y1

    def union(self, other: Rect) -> Rect:
        return Rect(
            min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1), max(self.y1, other.y1)
        )

    def shift(self, dx: int, dy: int) -> Rect:
        return Rect(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)


@dataclass(frozen=True)
class Job:
    """Inpaint ``rect`` over frames ``[start, end)``.

    ``masks[k]`` lists the rectangles to fill in frame ``start + k``, in coordinates relative to ``rect``.
    A frame inside the job may have no mask (between two nearby regions); it still gives the model context.
    """

    rect: Rect
    start: int
    end: int
    masks: tuple[tuple[Rect, ...], ...]

    @property
    def length(self) -> int:
        return self.end - self.start

    def truncated(self, end: int) -> Job:
        return Job(self.rect, self.start, end, self.masks[: max(0, end - self.start)])


def _box_rect(region: TextRegion, dilate: int, width: int, height: int) -> Rect | None:
    box = region.box
    rect = Rect(box.x, box.y, min(width, box.x + box.width), min(height, box.y + box.height))
    if rect.width <= 0 or rect.height <= 0:
        return None
    return rect.grow(dilate, width, height)


def _frame_range(region: TextRegion, fps: float, frame_count: int | None) -> range:
    """Frames whose presentation time ``i / fps`` lies in ``[start, end)``."""
    first = max(0, math.ceil(region.start * fps - 1e-6))
    last = math.ceil(region.end * fps - 1e-6)
    if frame_count is not None:
        last = min(last, frame_count)
    return range(first, last)


def _clusters(rects: Sequence[Rect], context: int, width: int, height: int) -> list[Rect]:
    """Merge rectangles whose context areas overlap, until no two areas overlap."""
    areas = sorted(rect.grow(context, width, height) for rect in rects)
    merged = True
    while merged:
        merged = False
        out: list[Rect] = []
        for area in areas:
            for i, other in enumerate(out):
                if area.overlaps(other):
                    out[i] = other.union(area)
                    merged = True
                    break
            else:
                out.append(area)
        areas = sorted(out)
    return areas


def plan_jobs(
    regions: Sequence[TextRegion],
    *,
    width: int,
    height: int,
    fps: float,
    frame_count: int | None,
    dilate: int,
    context: int,
    max_frames: int,
    gap: int = 0,
) -> list[Job]:
    """Group ``regions`` into :class:`Job` s, sorted by the frame they end on.

    ``dilate`` grows every text box (the mask); ``context`` is how much picture around the mask each crop
    keeps. Frames of one area closer than ``gap`` frames apart go into the same job, and no job is longer
    than ``max_frames``. Boxes are clamped to the frame; regions entirely outside it are ignored.
    """
    if max_frames < 1:
        raise ValueError("max_frames must be at least 1")
    per_frame: dict[int, list[Rect]] = {}
    for region in regions:
        rect = _box_rect(region, dilate, width, height)
        if rect is None:
            continue
        for index in _frame_range(region, fps, frame_count):
            per_frame.setdefault(index, []).append(rect)
    if not per_frame:
        return []

    areas = _clusters(sorted({r for rects in per_frame.values() for r in rects}), context, width, height)
    jobs: list[Job] = []
    for area in areas:
        frames = sorted(i for i, rects in per_frame.items() if any(area.overlaps(r) for r in rects))
        runs: list[list[int]] = []
        for index in frames:
            if runs and index - runs[-1][-1] <= gap + 1 and index - runs[-1][0] < max_frames:
                runs[-1].append(index)
            else:
                runs.append([index])
        for run in runs:
            start, end = run[0], run[-1] + 1
            masks = tuple(
                tuple(sorted(r.shift(-area.x0, -area.y0) for r in per_frame.get(i, ()) if area.overlaps(r)))
                for i in range(start, end)
            )
            jobs.append(Job(area, start, end, masks))
    jobs.sort(key=lambda job: (job.end, job.start, job.rect))
    return jobs


# --- ffmpeg ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VideoStream:
    """What the erasers need to know about the first video stream (dimensions after rotation)."""

    width: int
    height: int
    rate: Fraction
    frame_count: int | None
    has_audio: bool

    @property
    def fps(self) -> float:
        return float(self.rate)


def _tool(find: Callable[[], str | None], name: str, env_var: str) -> str:
    path = find()
    if path is None:
        raise ProviderUnavailableError(
            f"{name} not found - install ffmpeg or set {env_var} ({doc('troubleshooting.md')})"
        )
    return path


def _rate(value: object) -> Fraction | None:
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def _rotation(stream: dict[str, Any]) -> int:
    for side in stream.get("side_data_list") or ():
        if "rotation" in side:
            return int(float(side["rotation"])) % 360
    tag = (stream.get("tags") or {}).get("rotate")
    return int(float(tag)) % 360 if tag else 0


def parse_probe(data: dict[str, Any]) -> VideoStream:
    """Read :class:`VideoStream` from ``ffprobe -show_streams -of json`` output."""
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise EraseDubError("the input has no video stream")
    rate = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
    if rate is None:
        raise EraseDubError("cannot read the frame rate of the input video")
    width, height = int(video["width"]), int(video["height"])
    if _rotation(video) in (90, 270):
        width, height = height, width
    count = str(video.get("nb_frames") or "")
    return VideoStream(
        width=width,
        height=height,
        rate=rate,
        frame_count=int(count) if count.isdigit() and int(count) > 0 else None,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def probe(video: Path) -> VideoStream:
    ffprobe = _tool(find_ffprobe, "ffprobe", FFPROBE_ENV)
    cmd = [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(video)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603 — fixed argv
    if result.returncode != 0:
        raise EraseDubError(f"ffprobe could not read {video}: {result.stderr.strip()[-500:]}")
    return parse_probe(json.loads(result.stdout or "{}"))


def passthrough_option(version: str | None) -> str:
    """``-fps_mode`` (FFmpeg 5.1+) or, for older builds, ``-vsync``, which takes the same ``passthrough``.

    Versions that do not start with a number (git builds such as ``N-11xxxx-g...``) are taken as recent.
    """
    match = re.match(r"n?(\d+)(?:\.(\d+))?", version or "")
    if match is None:
        return "-fps_mode"
    major, minor = int(match.group(1)), int(match.group(2) or 0)
    return "-fps_mode" if (major, minor) >= (5, 1) else "-vsync"


def decode_command(ffmpeg: str, video: Path, version: str | None = None) -> list[str]:
    # passthrough: every decoded frame comes out exactly once (no frames duplicated or dropped).
    return [ffmpeg, "-nostdin", "-v", "error", "-i", str(video), "-map", "0:v:0",
            passthrough_option(version), "passthrough",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]  # fmt: skip


def encode_command(
    ffmpeg: str, video: Path, output: Path, stream: VideoStream, *, crf: int = 18, preset: str = "medium"
) -> list[str]:
    """Encode raw RGB frames from stdin at the source rate and copy the source's audio streams.

    x264 needs an even width and height for yuv420p, so an odd last row or column is cropped, never scaled:
    the same crop the engine applies when it writes the final video.
    """
    even = ["-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2"] if stream.width % 2 or stream.height % 2 else []
    return [
        ffmpeg, "-nostdin", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{stream.width}x{stream.height}",
        "-framerate", f"{stream.rate.numerator}/{stream.rate.denominator}", "-i", "-",
        "-i", str(video),
        "-map", "0:v:0", "-map", "1:a?", *even,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart",
        str(output),
    ]  # fmt: skip


def _read_exact(pipe: IO[bytes], size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = pipe.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _finish(proc: subprocess.Popen[bytes], what: str) -> None:
    stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise EraseDubError(f"ffmpeg failed while {what}: {stderr.strip()[-500:]}")


def transform_video(
    video: Path,
    output: Path,
    jobs: Sequence[Job],
    stream: VideoStream,
    inpaint: Callable[[Job, list[Any]], None],
    *,
    ctx: RunContext,
    message: str,
) -> Path:
    """Decode ``video``, call ``inpaint(job, frames)`` once per job, encode every frame into ``output``.

    ``frames`` are the job's full frames as writable ``H x W x 3`` uint8 RGB arrays; ``inpaint`` edits them
    in place. Frames are held in memory only from the start of the earliest unfinished job, so memory is
    bounded by the longest job.
    """
    np = import_module("numpy")
    tools = detect_ffmpeg()
    ffmpeg = _tool(lambda: tools.path, "ffmpeg", FFMPEG_ENV)
    frame_size = stream.width * stream.height * 3
    pending = sorted(jobs, key=lambda job: (job.end, job.start))
    buffer: dict[int, Any] = {}
    next_out = 0
    total = stream.frame_count

    decoder = subprocess.Popen(  # noqa: S603 — fixed argv
        decode_command(ffmpeg, video, tools.version), stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    encoder = subprocess.Popen(  # noqa: S603 — fixed argv
        encode_command(ffmpeg, video, output, stream), stdin=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert decoder.stdout is not None and encoder.stdin is not None  # noqa: S101 — set by PIPE

    def emit(upto: int) -> None:
        nonlocal next_out
        while next_out < upto:
            encoder.stdin.write(buffer.pop(next_out).tobytes())  # type: ignore[union-attr]
            next_out += 1

    def run(job: Job) -> None:
        ctx.raise_if_cancelled()
        inpaint(job, [buffer[i] for i in range(job.start, job.end)])

    try:
        index = 0
        while True:
            raw = _read_exact(decoder.stdout, frame_size)
            if len(raw) < frame_size:
                break
            buffer[index] = np.frombuffer(raw, dtype=np.uint8).reshape(stream.height, stream.width, 3).copy()
            while pending and pending[0].end - 1 <= index:
                run(pending.pop(0))
            emit(min([index + 1, *(job.start for job in pending)]))
            index += 1
            if total:
                ctx.progress(index / total, message)
            if index % 32 == 0:
                ctx.raise_if_cancelled()
        for job in pending:  # regions reaching past the last frame
            if job.start < index:
                run(job.truncated(index))
        emit(index)
        encoder.stdin.close()
        _finish(decoder, f"reading {video}")
        _finish(encoder, f"writing {output}")
    except BaseException as exc:
        for proc in (decoder, encoder):
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        output.unlink(missing_ok=True)
        if isinstance(exc, BrokenPipeError):
            stderr = encoder.stderr.read().decode(errors="replace") if encoder.stderr else ""
            raise EraseDubError(f"ffmpeg failed while writing {output}: {stderr.strip()[-500:]}") from exc
        raise
    if index == 0:
        raise EraseDubError(f"no frames could be decoded from {video}")
    return output


def job_masks(job: Job) -> list[Any]:
    """The job's masks as ``h x w`` uint8 arrays (1 = fill), one per frame."""
    np = import_module("numpy")
    out = []
    for rects in job.masks:
        mask = np.zeros((job.rect.height, job.rect.width), dtype=np.uint8)
        for r in rects:
            mask[r.y0 : r.y1, r.x0 : r.x1] = 1
        out.append(mask)
    return out


def paste(frames: list[Any], job: Job, crops: Sequence[Any], masks: Sequence[Any]) -> None:
    """Copy the masked pixels of each inpainted crop back into its full frame."""
    r = job.rect
    for frame, crop, mask in zip(frames, crops, masks, strict=True):
        region = frame[r.y0 : r.y1, r.x0 : r.x1]
        keep = mask.astype(bool)
        region[keep] = crop[keep]
