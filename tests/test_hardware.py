import sys
from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from erasedub import hardware, links
from erasedub.hardware import GpuInfo


def _runner(output: str, seen: list[Sequence[str]] | None = None) -> Callable[[Sequence[str]], str]:
    def run(cmd: Sequence[str]) -> str:
        if seen is not None:
            seen.append(cmd)
        return output

    return run


def _which(found: dict[str, str]) -> Callable[[str], str | None]:
    return lambda name: found.get(name)


# --- nvidia-smi -------------------------------------------------------------------------------------------


def test_detect_nvidia_gpus_parses_csv() -> None:
    out = "NVIDIA GeForce RTX 3090, 24576, 580.65\r\nbroken line\nTesla T4, 15360, 570.1\n"
    gpus = hardware.detect_nvidia_gpus(_runner(out), nvidia_smi="nvidia-smi")
    assert gpus == [GpuInfo("NVIDIA GeForce RTX 3090", 24576, "580.65"), GpuInfo("Tesla T4", 15360, "570.1")]


def test_gpus_with_unknown_memory_are_kept() -> None:
    out = "NVIDIA A100-SXM4-40GB MIG 1g.5gb, [N/A], 550.54\n"
    assert hardware.detect_nvidia_gpus(_runner(out), nvidia_smi="x") == [
        GpuInfo("NVIDIA A100-SXM4-40GB MIG 1g.5gb", None, "550.54")
    ]


def test_detect_nvidia_gpus_none() -> None:
    assert hardware.detect_nvidia_gpus(_runner(""), nvidia_smi="x") == []


def test_detect_nvidia_gpus_runs_the_found_executable() -> None:
    seen: list[Sequence[str]] = []
    hardware.detect_nvidia_gpus(_runner("", seen), nvidia_smi="/usr/lib/wsl/lib/nvidia-smi")
    assert seen[0][0] == "/usr/lib/wsl/lib/nvidia-smi"


@pytest.mark.parametrize(
    ("platform", "env", "existing", "expected"),
    [
        (
            "win32",
            {"ProgramFiles": r"D:\Programs"},
            {r"D:\Programs\NVIDIA Corporation\NVSMI\nvidia-smi.exe"},
            r"D:\Programs\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ),
        (
            "win32",
            {},
            {r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"},
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ),
        ("linux", {}, {"/usr/lib/wsl/lib/nvidia-smi"}, "/usr/lib/wsl/lib/nvidia-smi"),
        ("linux", {}, set(), None),
        ("darwin", {}, {"/usr/lib/wsl/lib/nvidia-smi"}, None),
    ],
)
def test_find_nvidia_smi_fallbacks(
    platform: str, env: dict[str, str], existing: set[str], expected: str | None
) -> None:
    found = hardware.find_nvidia_smi(_which({}), env=env, platform=platform, is_file=existing.__contains__)
    assert found == expected


def test_find_nvidia_smi_prefers_path() -> None:
    assert (
        hardware.find_nvidia_smi(_which({"nvidia-smi": "/bin/nvidia-smi"}), platform="linux")
        == "/bin/nvidia-smi"
    )


def test_run_survives_undecodable_output_and_missing_tools() -> None:
    assert hardware._run(["surely-not-an-installed-tool-xyz"]) == ""
    # A real tool that prints bytes that are not UTF-8: decoded with replacement characters, no exception.
    out = hardware._run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'bad \\xe9 byte')"])
    assert out == "bad \ufffd byte"


# --- ffmpeg -----------------------------------------------------------------------------------------------

FULL = "ffmpeg version n8.1.2 Copyright\nconfiguration: --enable-gpl --enable-libass --enable-libx264\n"


def test_detect_ffmpeg_features() -> None:
    info = hardware.detect_ffmpeg(_runner(FULL), which=lambda name: f"/usr/bin/{name}", env={})
    assert info.version == "n8.1.2"
    assert info.ffprobe_path == "/usr/bin/ffprobe"
    assert info.usable


def test_detect_ffmpeg_without_libass_is_still_usable() -> None:
    out = "ffmpeg version 7.0\nconfiguration: --enable-libx264\n"
    info = hardware.detect_ffmpeg(_runner(out), which=lambda name: f"/usr/bin/{name}", env={})
    assert not info.has_libass
    assert info.usable  # subtitles become a subtitle track
    out = "ffmpeg version 7.0\nconfiguration: --enable-libass\n"
    assert not hardware.detect_ffmpeg(_runner(out), which=lambda name: f"/usr/bin/{name}", env={}).usable


def test_detect_ffmpeg_missing() -> None:
    info = hardware.detect_ffmpeg(_runner(""), which=lambda name: None, env={})
    assert info.path is None
    assert not info.usable


def test_detect_ffmpeg_probes_the_resolved_path() -> None:
    seen: list[Sequence[str]] = []
    which = _which({"/opt/erasedub/ffmpeg": "/opt/erasedub/ffmpeg", "ffprobe": "/usr/bin/ffprobe"})
    info = hardware.detect_ffmpeg(
        _runner(FULL, seen), which=which, env={"ERASEDUB_FFMPEG": "/opt/erasedub/ffmpeg"}
    )
    assert info.path == "/opt/erasedub/ffmpeg"
    assert seen == [["/opt/erasedub/ffmpeg", "-hide_banner", "-version"]]


def test_env_overrides_must_exist() -> None:
    which = _which({"ffmpeg": "/usr/bin/ffmpeg", "/custom/ffprobe": "/custom/ffprobe"})
    env = {"ERASEDUB_FFMPEG": "/missing/ffmpeg", "ERASEDUB_FFPROBE": "/custom/ffprobe"}
    assert hardware.find_ffmpeg(which, env) is None
    assert hardware.find_ffprobe(which, env) == "/custom/ffprobe"
    assert hardware.find_ffmpeg(which, {"ERASEDUB_FFMPEG": "  "}) == "/usr/bin/ffmpeg"


# --- CUDA at render time ----------------------------------------------------------------------------------


def _torch(available: bool, cuda_version: str | None, count: int = 1) -> Callable[[], Any]:
    fake = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: available, device_count=lambda: count),
        version=SimpleNamespace(cuda=cuda_version),
    )
    return lambda: fake


GPU = [GpuInfo("RTX 4090", 24564, "580.1")]


def test_cuda_usable() -> None:
    assert hardware.cuda_usable(import_torch=_torch(True, "12.8"), gpus=lambda: GPU).usable
    assert hardware.cuda_usable("cuda:1", import_torch=_torch(True, "12.8", count=2), gpus=lambda: GPU).usable


def test_cpu_only_torch_next_to_an_nvidia_gpu_points_to_the_windows_guide() -> None:
    status = hardware.cuda_usable(import_torch=_torch(False, None), gpus=lambda: GPU)
    assert not status.usable
    assert "CPU-only build" in status.reason
    assert links.doc("install/windows.md") in status.reason


def test_cuda_unusable_reasons() -> None:
    def no_torch() -> Any:
        raise ModuleNotFoundError("No module named 'torch'", name="torch")

    assert "uv sync --extra erase" in hardware.cuda_usable(import_torch=no_torch).reason
    assert "no NVIDIA GPU" in hardware.cuda_usable(import_torch=_torch(False, None), gpus=list).reason
    assert (
        "CUDA_VISIBLE_DEVICES"
        in hardware.cuda_usable(import_torch=_torch(False, "12.8"), gpus=lambda: GPU).reason
    )
    status = hardware.cuda_usable("cuda:2", import_torch=_torch(True, "12.8", count=2), gpus=lambda: GPU)
    assert "cuda:2 does not exist" in status.reason


@pytest.mark.parametrize(
    "error",
    [
        OSError("[WinError 126] The specified module could not be found. Error loading c10.dll"),
        ImportError("DLL load failed while importing _C: The specified procedure could not be found."),
        ModuleNotFoundError("No module named 'typing_extensions'", name="typing_extensions"),
        RuntimeError("partially initialized module"),
    ],
)
def test_broken_torch_is_a_reason_not_a_traceback(error: Exception) -> None:
    def broken() -> Any:
        raise error

    status = hardware.cuda_usable(import_torch=broken, gpus=lambda: GPU)
    assert not status.usable
    assert f"PyTorch is installed but failed to load ({type(error).__name__}" in status.reason
    assert links.doc("install/windows.md") in status.reason


def test_nvidia_smi_runs_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Sequence[str]] = []

    def fake_run(cmd: Sequence[str]) -> str:
        calls.append(cmd)
        return "Tesla T4, 15360, 570.1\n"

    monkeypatch.setattr(hardware, "_run", fake_run)
    hardware.clear_gpu_cache()
    try:
        first = hardware.detect_nvidia_gpus(nvidia_smi="/usr/bin/nvidia-smi")
        first.clear()  # callers get their own list
        again = hardware.detect_nvidia_gpus(nvidia_smi="/usr/bin/nvidia-smi")
        assert again == [GpuInfo("Tesla T4", 15360, "570.1")]
        assert len(calls) == 1
        hardware.clear_gpu_cache()
        hardware.detect_nvidia_gpus(nvidia_smi="/usr/bin/nvidia-smi")
        assert len(calls) == 2
        hardware.detect_nvidia_gpus(_runner(""), nvidia_smi="/usr/bin/nvidia-smi")  # injected: never cached
        assert len(calls) == 2
    finally:
        hardware.clear_gpu_cache()


def test_mps_usable() -> None:
    class Mps:
        def __init__(self, ok: bool) -> None:
            self.backends = type("B", (), {"mps": type("M", (), {"is_available": staticmethod(lambda: ok)})})

    def sonoma() -> int:
        return 14

    assert hardware.mps_usable(import_torch=lambda: Mps(True), macos_major=sonoma)
    assert not hardware.mps_usable(import_torch=lambda: Mps(False), macos_major=sonoma)

    def missing() -> object:
        raise ModuleNotFoundError("torch")

    assert not hardware.mps_usable(import_torch=missing, macos_major=sonoma)


@pytest.mark.parametrize("major", [0, 12, 13])  # 0: not macOS
def test_mps_needs_macos_14(major: int) -> None:
    """PyTorch reports MPS on macOS 13, but its FFT kernels (LaMa) refuse to run there."""

    def torch() -> object:
        raise AssertionError("torch must not be imported below macOS 14")

    assert not hardware.mps_usable(import_torch=torch, macos_major=lambda: major)
