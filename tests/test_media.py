"""ffprobe parsing and the ffmpeg runner, with ffmpeg replaced by a fake process (no media is decoded)."""

import io
import subprocess
from pathlib import Path
from typing import Any, ClassVar

import pytest

from erasedub import hardware, media
from erasedub.context import RunContext
from erasedub.errors import CancelledError, EraseDubError, ProviderUnavailableError
from erasedub.hardware import FfmpegInfo

TOOLS = media.Tools("/x/ffmpeg", "/x/ffprobe")


def stream(**extra: Any) -> dict[str, Any]:
    base = {
        "codec_type": "video",
        "codec_name": "h264",
        "pix_fmt": "yuv420p",
        "width": 1920,
        "height": 1080,
        "avg_frame_rate": "30000/1001",
    }
    return {**base, **extra}


def test_parse_probe_reads_the_video_stream() -> None:
    data = {
        "streams": [
            stream(codec_name="mjpeg", disposition={"attached_pic": 1}, width=600, height=600),
            stream(),
            {"codec_type": "audio"},
        ],
        "format": {"duration": "12.5"},
    }
    info = media.parse_probe(Path("clip.mp4"), data)
    assert (info.video.width, info.video.height) == (1920, 1080)
    assert info.video.fps == pytest.approx(29.97, abs=0.01)
    assert info.video.duration == 12.5
    assert info.video.has_audio
    assert (info.video_codec, info.pix_fmt) == ("h264", "yuv420p")


@pytest.mark.parametrize(
    "rotated",
    [
        stream(side_data_list=[{"side_data_type": "Display Matrix", "rotation": -90}]),
        stream(tags={"rotate": "90"}),
    ],
)
def test_parse_probe_swaps_the_size_of_rotated_video(rotated: dict[str, Any]) -> None:
    info = media.parse_probe(Path("phone.mp4"), {"streams": [rotated], "format": {"duration": "3"}})
    assert (info.video.width, info.video.height) == (1080, 1920)
    assert not info.video.has_audio


def test_parse_probe_falls_back_to_the_stream_duration_and_r_frame_rate() -> None:
    data = {"streams": [stream(avg_frame_rate="0/0", r_frame_rate="25/1", duration="4.0")], "format": {}}
    info = media.parse_probe(Path("a.mkv"), data)
    assert (info.video.fps, info.video.duration) == (25, 4)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"streams": [{"codec_type": "audio"}]}, "no video stream"),
        ({"streams": [stream(width=0)]}, "no picture size"),
        ({"streams": [stream(avg_frame_rate="0/0")], "format": {"duration": "1"}}, "no frame rate"),
        ({"streams": [stream()], "format": {"duration": "N/A"}}, "no duration"),
    ],
)
def test_parse_probe_errors_name_the_file(data: dict[str, Any], message: str) -> None:
    with pytest.raises(EraseDubError, match=f"bad.mp4: .*{message}"):
        media.parse_probe(Path("bad.mp4"), data)


def test_probe_reports_ffprobe_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert cmd[0] == "/x/ffprobe"
        assert cmd[-1] == str((tmp_path / "x.mp4").resolve())
        return subprocess.CompletedProcess(cmd, 1, "", "first\nInvalid data found when processing input\n")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(EraseDubError, match=r"x\.mp4: ffprobe cannot read this file: Invalid data found"):
        media.probe(TOOLS, tmp_path / "x.mp4")


def test_find_tools_needs_ffmpeg_and_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware, "detect_ffmpeg", lambda: FfmpegInfo(None, None, False, False, False))
    with pytest.raises(ProviderUnavailableError, match="ffmpeg not found"):
        media.find_tools()
    monkeypatch.setattr(hardware, "detect_ffmpeg", lambda: FfmpegInfo("/x/ffmpeg", "7", False, True, False))
    with pytest.raises(ProviderUnavailableError, match="ffprobe not found next to /x/ffmpeg"):
        media.find_tools()
    good = FfmpegInfo("/x/ffmpeg", "7", False, True, True, "/x/ffprobe")
    monkeypatch.setattr(hardware, "detect_ffmpeg", lambda: good)
    assert media.find_tools() == media.Tools("/x/ffmpeg", "/x/ffprobe", has_libass=False, has_libx264=True)


def test_progress_lines() -> None:
    assert media._progress_seconds("out_time_us", "2500000") == 2.5
    assert media._progress_seconds("out_time_ms", "1000000") == 1.0  # microseconds too
    assert media._progress_seconds("out_time_us", "N/A") is None
    assert media._progress_seconds("frame", "10") is None


class FakeProcess:
    """Stands in for ``subprocess.Popen``: prints progress lines, writes stderr, exits with ``code``."""

    instances: ClassVar[list["FakeProcess"]] = []

    def __init__(self, cmd: list[str], *, lines: list[str], code: int, err: bytes, **kwargs: Any) -> None:
        self.cmd = cmd
        self.kwargs = kwargs
        self.stdout = io.StringIO("".join(lines))
        kwargs["stderr"].write(err)
        self.code = code
        self.returncode: int | None = None
        self.killed = False
        FakeProcess.instances.append(self)

    def wait(self) -> int:
        self.returncode = -9 if self.killed else self.code
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def fake_popen(
    monkeypatch: pytest.MonkeyPatch, *, lines: list[str], code: int = 0, errors: bytes = b""
) -> list[FakeProcess]:
    FakeProcess.instances = []

    def popen(cmd: list[str], **kwargs: Any) -> FakeProcess:
        return FakeProcess(cmd, lines=lines, code=code, err=errors, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    return FakeProcess.instances


def test_run_ffmpeg_reports_progress(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runs = fake_popen(monkeypatch, lines=["frame=1\n", "out_time_us=5000000\n", "progress=end\n"])
    seen: list[tuple[float, str]] = []
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path, on_progress=lambda f, m: seen.append((f, m)))
    media.run_ffmpeg(
        TOOLS, ["-i", "in.mp4", "out.mp4"], ctx=ctx, doing="writing out.mp4", duration=10, cwd=tmp_path
    )
    (run,) = runs
    assert run.cmd[:2] == ["/x/ffmpeg", "-hide_banner"]
    assert run.cmd[-3:] == ["-i", "in.mp4", "out.mp4"]
    assert "-nostdin" in run.cmd and "pipe:1" in run.cmd
    assert run.kwargs["cwd"] == tmp_path
    assert seen == [(0.0, "writing out.mp4"), (0.5, "writing out.mp4"), (1.0, "writing out.mp4")]


def test_run_ffmpeg_failure_quotes_the_last_error_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    errors = "\n".join(f"line {i}" for i in range(12)).encode()
    fake_popen(monkeypatch, lines=[], code=1, errors=errors)
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path)
    with pytest.raises(EraseDubError) as excinfo:
        media.run_ffmpeg(TOOLS, ["x"], ctx=ctx, doing="extracting the audio")
    message = str(excinfo.value)
    assert message.startswith("ffmpeg failed while extracting the audio (exit code 1): line 4 | line 5")
    assert message.endswith("line 11")


def test_run_ffmpeg_stops_ffmpeg_when_cancelled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runs = fake_popen(monkeypatch, lines=["out_time_us=1\n"] * 5)
    flag = {"cancel": False}

    def progress(fraction: float, message: str) -> None:
        flag["cancel"] = True  # the first report "presses" cancel

    ctx = RunContext(
        tmp_dir=tmp_path, cache_dir=tmp_path, on_progress=progress, is_cancelled=lambda: flag["cancel"]
    )
    with pytest.raises(CancelledError):
        media.run_ffmpeg(TOOLS, ["x"], ctx=ctx, doing="writing", duration=1)
    assert runs[0].killed


def test_extract_audio_asks_for_16k_mono_wav(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runs = fake_popen(monkeypatch, lines=[])
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path)
    out = media.extract_audio(TOOLS, tmp_path / "v.mp4", tmp_path / "a" / "speech.wav", ctx=ctx, duration=3)
    assert out == tmp_path / "a" / "speech.wav"
    args = runs[0].cmd
    assert args[args.index("-map") + 1] == "0:a:0"
    assert args[args.index("-ar") + 1] == "16000"
    assert args[-1] == str(out.resolve())
