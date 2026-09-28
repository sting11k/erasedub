"""The erasers' runtime pieces that need no PyTorch: planning, ffmpeg commands, pins, device choice, and the
local and Modal GPU backends (Modal is faked; nothing is uploaded)."""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import types
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any, ClassVar

import pytest

from erasedub.context import RunContext
from erasedub.errors import CancelledError, ConfigError, EraseDubError, ProviderUnavailableError
from erasedub.hardware import CudaStatus, FfmpegInfo
from erasedub.models import Box, TextRegion
from erasedub.providers import (
    _fetch,
    _modal_remote,
    _video,
    eraser_lama,
    eraser_propainter,
    eraser_sttn,
    gpu_local,
    gpu_modal,
)
from erasedub.providers._video import Job, Rect
from erasedub.providers.base import EraserSpec, TextEraser
from erasedub.providers.eraser_none import NoEraser

SHA256 = re.compile(r"^[0-9a-f]{64}$")


def region(x: int, y: int, w: int, h: int, start: float, end: float) -> TextRegion:
    return TextRegion(start=start, end=end, box=Box(x=x, y=y, width=w, height=h))


def plan(regions: Sequence[TextRegion], **kwargs: Any) -> list[Job]:
    defaults: dict[str, Any] = {
        "width": 100,
        "height": 200,
        "fps": 10.0,
        "frame_count": 100,
        "dilate": 0,
        "context": 0,
        "max_frames": 50,
    }
    return _video.plan_jobs(regions, **{**defaults, **kwargs})


# --- planning ----------------------------------------------------------------------------------------------


def test_one_region_becomes_one_job_with_its_frames() -> None:
    (job,) = plan([region(10, 150, 30, 20, start=1.0, end=1.5)], dilate=2, context=5)
    assert (job.start, job.end) == (10, 15)
    assert job.rect == Rect(3, 143, 47, 177)  # box grown by dilate, then by context
    assert job.masks == ((Rect(5, 5, 39, 29),),) * 5  # relative to the crop


def test_overlapping_areas_merge_and_distant_ones_do_not() -> None:
    subtitle = region(10, 170, 80, 20, 0.0, 1.0)
    nearby = region(10, 150, 80, 15, 0.0, 1.0)
    title = region(10, 5, 80, 20, 0.0, 1.0)
    jobs = plan([subtitle, nearby, title], context=4)
    assert sorted(job.rect.y0 for job in jobs) == [1, 146]
    bottom = next(job for job in jobs if job.rect.y0 == 146)
    assert len(bottom.masks[0]) == 2


def test_time_gaps_split_jobs_unless_within_gap() -> None:
    regions = [region(0, 0, 10, 10, 0.0, 0.3), region(0, 0, 10, 10, 0.6, 0.9)]
    assert [(j.start, j.end) for j in plan(regions)] == [(0, 3), (6, 9)]
    (merged,) = plan(regions, gap=3)
    assert (merged.start, merged.end) == (0, 9)
    assert merged.masks[4] == ()  # a frame between the two regions: context only


def test_jobs_never_exceed_max_frames() -> None:
    jobs = plan([region(0, 0, 10, 10, 0.0, 5.0)], max_frames=20)
    assert [(j.start, j.end) for j in jobs] == [(0, 20), (20, 40), (40, 50)]


def test_regions_are_clamped_to_the_video() -> None:
    jobs = plan([region(90, 190, 50, 50, 9.5, 20.0), region(500, 500, 5, 5, 0.0, 1.0)], frame_count=98)
    (job,) = jobs
    assert job.rect == Rect(90, 190, 100, 200)
    assert (job.start, job.end) == (95, 98)


def test_no_regions_no_jobs() -> None:
    assert plan([]) == []


def test_max_frames_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_frames"):
        plan([], max_frames=0)


def test_truncated_job_keeps_its_first_masks() -> None:
    (job,) = plan([region(0, 0, 10, 10, 0.0, 1.0)])
    short = job.truncated(4)
    assert (short.start, short.end, len(short.masks)) == (0, 4, 4)


# --- ffprobe / ffmpeg --------------------------------------------------------------------------------------


def probe_data(**video: Any) -> dict[str, Any]:
    stream = {"codec_type": "video", "width": 1080, "height": 1920, "avg_frame_rate": "30000/1001"} | video
    return {"streams": [stream, {"codec_type": "audio"}]}


def test_parse_probe_reads_size_rate_count_and_audio() -> None:
    info = _video.parse_probe(probe_data(nb_frames="300"))
    assert (info.width, info.height, info.rate, info.frame_count, info.has_audio) == (
        1080,
        1920,
        Fraction(30000, 1001),
        300,
        True,
    )
    assert info.fps == pytest.approx(29.97, abs=0.01)


@pytest.mark.parametrize(
    "extra",
    [{"side_data_list": [{"rotation": -90}]}, {"tags": {"rotate": "90"}}],
)
def test_parse_probe_swaps_size_for_rotated_video(extra: dict[str, Any]) -> None:
    info = _video.parse_probe(probe_data(**extra))
    assert (info.width, info.height) == (1920, 1080)


def test_parse_probe_falls_back_to_r_frame_rate_and_unknown_count() -> None:
    info = _video.parse_probe(probe_data(avg_frame_rate="0/0", r_frame_rate="25/1", nb_frames="N/A"))
    assert info.rate == 25
    assert info.frame_count is None


def test_parse_probe_errors() -> None:
    with pytest.raises(EraseDubError, match="no video stream"):
        _video.parse_probe({"streams": [{"codec_type": "audio"}]})
    with pytest.raises(EraseDubError, match="frame rate"):
        _video.parse_probe(probe_data(avg_frame_rate="0/0"))


def test_probe_runs_ffprobe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(probe_data()), "")

    monkeypatch.setattr(_video, "find_ffprobe", lambda: "/opt/ffprobe")
    monkeypatch.setattr(subprocess, "run", run)
    assert _video.probe(tmp_path / "in.mp4").width == 1080
    assert calls[0][0] == "/opt/ffprobe"


def test_probe_without_ffprobe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_video, "find_ffprobe", lambda: None)
    with pytest.raises(ProviderUnavailableError, match="ffprobe not found"):
        _video.probe(tmp_path / "in.mp4")


def test_encode_keeps_rate_and_copies_audio(tmp_path: Path) -> None:
    info = _video.parse_probe(probe_data())
    cmd = _video.encode_command("ffmpeg", tmp_path / "in.mp4", tmp_path / "out.mp4", info)
    joined = " ".join(cmd)
    assert "-framerate 30000/1001 -i -" in joined
    assert "-s 1080x1920" in joined
    assert "-map 0:v:0 -map 1:a?" in joined
    assert "-c:a copy" in joined
    assert cmd[-1] == str(tmp_path / "out.mp4")


def test_decode_passes_every_frame_through(tmp_path: Path) -> None:
    cmd = _video.decode_command("ffmpeg", tmp_path / "in.mp4", "7.1")
    assert "-fps_mode passthrough" in " ".join(cmd)
    assert cmd[-3:] == ["-pix_fmt", "rgb24", "-"]
    old = _video.decode_command("ffmpeg", tmp_path / "in.mp4", "4.4.2-0ubuntu0.22.04.1")  # Ubuntu 22.04
    assert "-vsync passthrough" in " ".join(old)
    assert "-fps_mode" not in old


@pytest.mark.parametrize(
    ("version", "option"),
    [
        ("5.1.6-0+deb12u1", "-fps_mode"),
        ("n7.1", "-fps_mode"),
        ("6", "-fps_mode"),
        ("5.0.1", "-vsync"),
        ("n4.4.2", "-vsync"),
        ("N-117000-g1234abcd", "-fps_mode"),  # git build: recent
        (None, "-fps_mode"),
    ],
)
def test_passthrough_option_follows_the_ffmpeg_version(version: str | None, option: str) -> None:
    assert _video.passthrough_option(version) == option


@pytest.mark.parametrize(("width", "height"), [(853, 480), (720, 1279)])
def test_encode_crops_odd_sizes_to_even(tmp_path: Path, width: int, height: int) -> None:
    info = _video.parse_probe(probe_data(width=width, height=height))
    cmd = _video.encode_command("ffmpeg", tmp_path / "in.mp4", tmp_path / "out.mp4", info)
    assert cmd[cmd.index("-vf") + 1] == "crop=trunc(iw/2)*2:trunc(ih/2)*2"  # the engine's crop
    assert cmd[cmd.index("-pix_fmt", cmd.index("-c:v")) + 1] == "yuv420p"


def test_encode_leaves_even_sizes_alone(tmp_path: Path) -> None:
    info = _video.parse_probe(probe_data())
    assert "-vf" not in _video.encode_command("ffmpeg", tmp_path / "in.mp4", tmp_path / "out.mp4", info)


def test_read_exact_joins_short_reads() -> None:
    class Trickle(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            return super().read(min(size or 1, 3))

    assert _video._read_exact(Trickle(b"abcdefgh"), 7) == b"abcdefg"
    assert _video._read_exact(Trickle(b"ab"), 7) == b"ab"


# --- erasers -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("size", "expected"), [((1080, 150), (1728, 240)), ((200, 500), (432, 480))])
def test_sttn_model_size_is_a_multiple_of_its_unit(size: tuple[int, int], expected: tuple[int, int]) -> None:
    assert eraser_sttn.model_size(*size) == expected


def test_reference_frames_skip_the_neighbours() -> None:
    assert eraser_sttn.reference_ids(35, [8, 9, 10, 11, 12], 10) == [0, 20, 30]
    assert eraser_propainter.reference_ids(35, [0, 1], 10) == [10, 20, 30]


@pytest.mark.parametrize("name", ["sttn", "lama", "propainter"])
def test_erasers_return_the_input_when_nothing_is_visible(
    name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_video, "probe", lambda video: _video.parse_probe(probe_data(nb_frames="30")))
    eraser = {"sttn": eraser_sttn.SttnEraser, "lama": eraser_lama.LamaEraser}.get(
        name, eraser_propainter.ProPainterEraser
    )()
    video = tmp_path / "in.mp4"
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path)
    late = region(0, 0, 10, 10, start=60.0, end=61.0)  # after the last of 30 frames
    assert eraser.erase(video, [late], tmp_path / "out.mp4", ctx=ctx) == video


@pytest.mark.parametrize(
    ("cls", "options"),
    [
        (eraser_sttn.SttnEraser, {"max_frames": 5}),
        (eraser_lama.LamaEraser, {"device": "rocm"}),
        (eraser_propainter.ProPainterEraser, {"max_frames": 200}),
        (gpu_modal.ModalGpu, {"timeout": 1}),
    ],
)
def test_bad_options_are_config_errors(cls: type[Any], options: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        cls(options)


@pytest.mark.parametrize(
    ("choice", "ctx_device", "mps", "expected"),
    [
        ("auto", "cuda:1", True, "cuda:1"),
        ("auto", "cpu", True, "mps"),
        ("auto", "cpu", False, "cpu"),
        ("cpu", "cuda", True, "cpu"),
        ("cuda", "cpu", False, "cuda"),
        ("cuda", "cuda:2", False, "cuda:2"),
        ("mps", "cpu", True, "mps"),
    ],
)
def test_lama_device(choice: Any, ctx_device: str, mps: bool, expected: str) -> None:
    assert eraser_lama.resolve_device(choice, ctx_device, mps_available=mps) == expected


def test_lama_mps_requested_but_missing() -> None:
    with pytest.raises(ProviderUnavailableError, match="needs macOS 14"):
        eraser_lama.resolve_device("mps", "cpu", mps_available=False)


def test_lama_extracts_the_checkpoint(tmp_path: Path) -> None:
    archive = tmp_path / "big-lama.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("big-lama/config.yaml", "kind: ffc_resnet\n")
        zf.writestr(eraser_lama.CHECKPOINT_MEMBER, b"ckpt")
    target = eraser_lama.extract_checkpoint(archive, tmp_path / "out")
    assert target.read_bytes() == b"ckpt"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("other.txt", "x")
    with pytest.raises(EraseDubError, match="does not contain"):
        eraser_lama.extract_checkpoint(archive, tmp_path / "out")


def test_lama_pads_to_multiples_of_eight() -> None:
    assert [eraser_lama.pad_to_multiple(n) for n in (1, 8, 9, 250)] == [8, 8, 16, 256]


def test_weights_are_pinned() -> None:
    lama = eraser_lama.WEIGHTS
    assert lama.sha256 is not None and SHA256.match(lama.sha256)
    assert "/resolve/05cb2be7f8dbe6ca7c6e78f4fc827a4b2baaa4a9/" in lama.url
    assert eraser_sttn.WEIGHTS.size == 66_252_587
    assert eraser_sttn.WEIGHTS.url.startswith("https://")
    remotes = eraser_propainter.weight_remotes()
    assert [r.name for r in remotes] == list(eraser_propainter.WEIGHTS_FILES)
    assert all(r.size and "/releases/download/v0.1.0/" in r.url for r in remotes)


def test_propainter_source_files_are_pinned_to_the_commit() -> None:
    remotes = eraser_propainter.source_remotes()
    assert len(remotes) == len(eraser_propainter.SOURCE_FILES)
    for folder, remote in remotes:
        assert remote.sha256 is not None and SHA256.match(remote.sha256)
        assert f"/{eraser_propainter.SOURCE_COMMIT}/" in remote.url
        assert remote.url.endswith(f"{folder}/{remote.name}" if folder else remote.name)
    assert any(r.name == "LICENSE" for _, r in remotes)  # the licence travels with the code


def test_propainter_fetches_the_code_into_its_folders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fetched: list[Path] = []

    def fake_fetch(remote: _fetch.Remote, directory: Path, **kwargs: Any) -> Path:
        fetched.append(directory / remote.name)
        return directory / remote.name

    monkeypatch.setattr(_fetch, "fetch", fake_fetch)
    root = eraser_propainter.fetch_source(tmp_path, ctx=RunContext(tmp_dir=tmp_path, cache_dir=tmp_path))
    assert root == tmp_path
    assert tmp_path / "RAFT" / "utils" / "flow_viz.py" in fetched
    assert tmp_path / "LICENSE" in fetched


def test_propainter_refuses_a_foreign_model_package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    foreign = types.ModuleType("model")
    foreign.__file__ = str(tmp_path / "elsewhere" / "model" / "__init__.py")
    monkeypatch.setitem(sys.modules, "model", foreign)
    with pytest.raises(EraseDubError, match="top-level module name 'model'"):
        eraser_propainter.import_source(tmp_path / "src")
    assert str(tmp_path / "src") not in sys.path


def test_propainter_imports_from_the_fetched_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[str] = []

    def fake_import(name: str) -> str:
        seen.append(sys.path[0])
        return name

    monkeypatch.setattr(eraser_propainter, "import_module", fake_import)
    for package in ("model", "RAFT"):
        monkeypatch.delitem(sys.modules, package, raising=False)
    modules = eraser_propainter.import_source(tmp_path)
    assert set(modules) == {"model.propainter", "model.recurrent_flow_completion", "RAFT"}
    assert seen == [str(tmp_path)] * 3
    assert str(tmp_path) not in sys.path


@pytest.mark.parametrize(("width", "clip"), [(432, 12), (720, 8), (1080, 4), (1920, 2)])
def test_propainter_flow_clip_length(width: int, clip: int) -> None:
    assert eraser_propainter.clip_lengths(width) == clip


def test_propainter_process_size() -> None:
    assert eraser_propainter.process_size(1083, 201, 960) == (960, 176)
    assert eraser_propainter.process_size(1920, 112, 960) == (1096, 64)  # a thin full-width band
    assert eraser_propainter.process_size(100, 3, 960) == (96, 64)


# --- local GPU backend -------------------------------------------------------------------------------------


class RecordingEraser(TextEraser):
    name: ClassVar[str] = "recording"
    api_version: ClassVar[int] = 1
    events: ClassVar[list[str]] = []

    def open(self, ctx: RunContext) -> None:
        self.events.append("open")

    def close(self) -> None:
        self.events.append("close")

    def erase(self, video: Path, regions: Sequence[TextRegion], output: Path, *, ctx: RunContext) -> Path:
        self.events.append(f"erase {len(regions)}")
        return output


def test_local_backend_opens_erases_and_closes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    RecordingEraser.events = []
    specs: list[EraserSpec] = []

    def create(spec: EraserSpec) -> TextEraser:
        specs.append(spec)
        return RecordingEraser()

    monkeypatch.setattr(gpu_local, "create_eraser", create)
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path)
    spec = EraserSpec(name="recording", options={"a": 1})
    out = gpu_local.LocalGpu().run_eraser(
        spec, tmp_path / "in.mp4", [region(0, 0, 5, 5, 0, 1)], tmp_path / "o.mp4", ctx=ctx
    )
    assert out == tmp_path / "o.mp4"
    assert specs == [spec]
    assert RecordingEraser.events == ["open", "erase 1", "close"]


def test_local_backend_refuses_an_unusable_cuda_device(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(gpu_local, "cuda_usable", lambda device: CudaStatus(False, "CPU-only PyTorch build"))
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path, device="cuda")
    with pytest.raises(ProviderUnavailableError, match="CPU-only PyTorch build"):
        gpu_local.LocalGpu().run_eraser(
            EraserSpec(name="sttn"), tmp_path / "in.mp4", [], tmp_path / "o.mp4", ctx=ctx
        )


# --- Modal backend (faked) ---------------------------------------------------------------------------------


def test_requirements_take_core_and_the_extra_only() -> None:
    requires = [
        "typer>=0.20",
        "pydantic>=2.7",
        'torch>=2.4; extra == "erase"',
        'opencv-python-headless>=4.10; extra == "erase"',
        'scipy>=1.11; extra == "propainter"',
        'erasedub[asr,ocr]; extra == "full"',
        "whisperx>=3.4; python_version < '3.14' and extra == 'asr'",
    ]
    assert gpu_modal.requirements("erase", requires) == [
        "typer>=0.20",
        "pydantic>=2.7",
        "torch>=2.4",
        "opencv-python-headless>=4.10",
    ]
    assert gpu_modal.requirements(None, requires) == ["typer>=0.20", "pydantic>=2.7"]


def test_requirements_from_the_installed_metadata() -> None:
    packages = gpu_modal.requirements("erase")
    assert any(p.startswith("pydantic") for p in packages)
    assert any(p.startswith("torch") for p in packages)
    assert not any(p.startswith("modal") for p in packages)


def test_eraser_target_validates_before_uploading() -> None:
    cls, target = gpu_modal.eraser_target(EraserSpec(name="sttn"))
    assert cls is eraser_sttn.SttnEraser
    assert target == "erasedub.providers.eraser_sttn:SttnEraser"
    with pytest.raises(ConfigError):
        gpu_modal.eraser_target(EraserSpec(name="sttn", options={"max_frames": "many"}))


class FakeCall:
    def __init__(self, result: bytes, pending: int = 1) -> None:
        self.result, self.pending, self.cancelled = result, pending, False

    def get(self, timeout: float | None = None) -> bytes:
        if self.pending:
            self.pending -= 1
            raise TimeoutError
        return self.result

    def cancel(self) -> None:
        self.cancelled = True


class FakeModalError(Exception):
    """Stands in for ``modal.exception.Error``."""


class FailingCall(FakeCall):
    def __init__(self, error: Exception) -> None:
        super().__init__(b"", pending=0)
        self.error = error

    def get(self, timeout: float | None = None) -> bytes:
        raise self.error


class FakeModal:
    """Just enough of the modal SDK surface that gpu_modal uses."""

    def __init__(self, call: FakeCall) -> None:
        self.log: list[tuple[str, Any]] = []
        self.call = call
        self.exception = types.SimpleNamespace(Error=FakeModalError)
        fake = self

        class Image:
            def __init__(self) -> None:
                self.steps: list[tuple[str, Any]] = []

            @classmethod
            def debian_slim(cls, python_version: str) -> Image:
                image = cls()
                image.steps.append(("debian_slim", python_version))
                return image

            def apt_install(self, *packages: str) -> Image:
                self.steps.append(("apt_install", packages))
                return self

            def pip_install(self, *packages: str) -> Image:
                self.steps.append(("pip_install", packages))
                return self

            def add_local_python_source(self, *modules: str) -> Image:
                self.steps.append(("add_local_python_source", modules))
                fake.log.append(("image", self.steps))
                return self

        class Volume:
            @staticmethod
            def from_name(name: str, create_if_missing: bool = False) -> str:
                fake.log.append(("volume", (name, create_if_missing)))
                return f"volume:{name}"

        class Function:
            def spawn(self, *args: Any) -> FakeCall:
                fake.log.append(("spawn", args))
                return fake.call

        class App:
            def __init__(self, name: str) -> None:
                fake.log.append(("app", name))

            def function(self, **kwargs: Any) -> Any:
                fake.log.append(("function", kwargs))

                def register(f: Any) -> Function:
                    fake.log.append(("registered", f))
                    return Function()

                return register

            @contextmanager
            def run(self) -> Iterator[None]:
                fake.log.append(("run", "start"))
                yield
                fake.log.append(("run", "end"))

        self.Image, self.Volume, self.App = Image, Volume, App

    def entries(self, kind: str) -> list[Any]:
        return [value for key, value in self.log if key == kind]


def test_modal_runs_the_eraser_remotely_and_downloads_the_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    modal = FakeModal(FakeCall(b"erased video"))
    monkeypatch.setattr(gpu_modal, "import_module", lambda name: modal)
    video = tmp_path / "in.mp4"
    video.write_bytes(b"source video")
    output = tmp_path / "out" / "clean.mp4"
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path)
    spec = EraserSpec(name="sttn", options={"max_frames": 60})
    backend = gpu_modal.ModalGpu({"gpu": "L4"})
    assert backend.run_eraser(spec, video, [region(1, 2, 3, 4, 0.5, 1.5)], output, ctx=ctx) == output
    assert output.read_bytes() == b"erased video"

    (steps,) = modal.entries("image")
    assert steps[1] == ("apt_install", ("ffmpeg",))
    assert any(p.startswith("torch") for p in steps[2][1])
    assert steps[3] == ("add_local_python_source", ("erasedub",))
    (options,) = modal.entries("function")
    assert options["gpu"] == "L4"
    assert options["serialized"] is True
    assert options["volumes"] == {"/cache": "volume:erasedub-models"}
    assert modal.entries("registered") == [_modal_remote.erase_remote]
    (args,) = modal.entries("spawn")
    target, options_json, regions_json, data, suffix = args
    assert target == "erasedub.providers.eraser_sttn:SttnEraser"
    assert json.loads(options_json) == {"max_frames": 60}
    assert json.loads(regions_json)[0]["box"] == {"x": 1, "y": 2, "width": 3, "height": 4}
    assert (data, suffix) == (b"source video", ".mp4")
    assert modal.entries("run") == ["start", "end"]


def test_modal_cancels_the_remote_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    call = FakeCall(b"never", pending=10)
    monkeypatch.setattr(gpu_modal, "import_module", lambda name: FakeModal(call))
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path, is_cancelled=lambda: True)
    with pytest.raises(CancelledError):
        gpu_modal.ModalGpu().run_eraser(EraserSpec(name="sttn"), video, [], tmp_path / "o.mp4", ctx=ctx)
    assert call.cancelled
    assert not (tmp_path / "o.mp4").exists()


def test_modal_refuses_plugin_erasers(monkeypatch: pytest.MonkeyPatch) -> None:
    class PluginEraser(NoEraser):
        pass

    PluginEraser.__module__ = "third_party.erasers"
    monkeypatch.setattr("erasedub.registry.load_class", lambda kind, name: PluginEraser)
    with pytest.raises(ConfigError, match="only the built-in erasers"):
        gpu_modal.eraser_target(EraserSpec(name="plugin"))


def test_remote_side_builds_the_eraser_and_returns_the_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    commits: list[bool] = []
    monkeypatch.setattr(_modal_remote, "_commit_cache", lambda: commits.append(True))
    monkeypatch.setattr(_modal_remote, "CACHE_MOUNT", "/nonexistent-cache")
    result = _modal_remote.erase_remote(
        "erasedub.providers.eraser_none:NoEraser", "{}", "[]", b"video bytes", ".mp4"
    )
    assert result == b"video bytes"  # the none eraser returns its input
    assert commits == [True]


def test_remote_side_rejects_non_erasers() -> None:
    with pytest.raises(TypeError, match="not a text eraser"):
        _modal_remote.load_eraser("erasedub.providers.gpu_local:LocalGpu", "{}")


# --- frame streaming (needs NumPy; ffmpeg is faked with in-memory pipes) -----------------------------------


class FakeProcess:
    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None  # type: ignore[method-assign]  # keep the bytes readable
        self.stderr = io.BytesIO(b"")
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode

    def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        pass


def _stream(monkeypatch: pytest.MonkeyPatch, frames: int, width: int = 4, height: int = 2) -> FakeProcess:
    np = pytest.importorskip("numpy")
    raw = b"".join(np.full((height, width, 3), i, dtype=np.uint8).tobytes() for i in range(frames))
    decoder, encoder = FakeProcess(raw), FakeProcess()
    procs = iter([decoder, encoder])
    monkeypatch.setattr(
        _video, "detect_ffmpeg", lambda: FfmpegInfo("ffmpeg", "7.1", True, True, True, "ffprobe")
    )
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: next(procs))
    return encoder


def test_transform_video_inpaints_only_the_masks_and_keeps_every_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    np = pytest.importorskip("numpy")
    encoder = _stream(monkeypatch, frames=6)
    info = _video.VideoStream(width=4, height=2, rate=Fraction(10), frame_count=6, has_audio=False)
    jobs = plan(
        [region(0, 0, 2, 1, 0.2, 0.4), region(0, 0, 2, 1, 0.5, 0.9)], width=4, height=2, frame_count=6
    )
    seen: list[tuple[int, int]] = []

    def inpaint(job: Job, frames: list[Any]) -> None:
        seen.append((job.start, job.end))
        masks = _video.job_masks(job)
        _video.paste(frames, job, [np.full_like(m[..., None].repeat(3, 2), 255) for m in masks], masks)

    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path)
    _video.transform_video(
        tmp_path / "in.mp4", tmp_path / "out.mp4", jobs, info, inpaint, ctx=ctx, message=""
    )
    out = np.frombuffer(encoder.stdin.getvalue(), dtype=np.uint8).reshape(6, 2, 4, 3)
    assert seen == [(2, 4), (5, 6)]  # 0.5-0.9 s at 10 fps is frames 5-8; the video ends at frame 6
    assert [int(f[0, 0, 0]) for f in out] == [0, 1, 255, 255, 4, 255]
    assert [int(f[1, 3, 0]) for f in out] == [0, 1, 2, 3, 4, 5]  # outside the mask: untouched


def test_transform_video_fails_on_an_empty_decode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stream(monkeypatch, frames=0)
    info = _video.VideoStream(width=4, height=2, rate=Fraction(10), frame_count=None, has_audio=False)
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path)
    with pytest.raises(EraseDubError, match="no frames"):
        _video.transform_video(
            tmp_path / "in.mp4", tmp_path / "o.mp4", [], info, lambda j, f: None, ctx=ctx, message=""
        )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FakeModalError("Function timed out"), "Modal: FakeModalError: Function timed out"),
        (EraseDubError("no frames could be decoded"), "no frames could be decoded"),  # raised remotely
    ],
)
def test_modal_errors_become_one_line_erasedub_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception, expected: str
) -> None:
    monkeypatch.setattr(gpu_modal, "import_module", lambda name: FakeModal(FailingCall(error)))
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path)
    with pytest.raises(EraseDubError) as info:
        gpu_modal.ModalGpu().run_eraser(EraserSpec(name="sttn"), video, [], tmp_path / "o.mp4", ctx=ctx)
    assert str(info.value) == expected


def test_modal_setup_errors_are_wrapped_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    modal = FakeModal(FakeCall(b""))

    def bad_gpu(self: Any, **kwargs: Any) -> Any:
        raise FakeModalError("invalid GPU type 'T5'")

    monkeypatch.setattr(modal.App, "function", bad_gpu)
    monkeypatch.setattr(gpu_modal, "import_module", lambda name: modal)
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path)
    with pytest.raises(EraseDubError, match=r"^Modal: FakeModalError: invalid GPU type 'T5'$"):
        gpu_modal.ModalGpu({"gpu": "T5"}).run_eraser(
            EraserSpec(name="sttn"), video, [], tmp_path / "o.mp4", ctx=ctx
        )
