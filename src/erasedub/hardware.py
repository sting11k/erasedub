"""Detects the external tools and hardware EraseDub depends on. Read-only, no downloads."""

from __future__ import annotations

import importlib
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from erasedub.links import doc, missing_extra

Runner = Callable[[Sequence[str]], str]
Which = Callable[[str], str | None]

#: Environment variables that point EraseDub at a specific ffmpeg / ffprobe executable.
FFMPEG_ENV = "ERASEDUB_FFMPEG"
FFPROBE_ENV = "ERASEDUB_FFPROBE"


def _run(cmd: Sequence[str]) -> str:
    """Run a command and return stdout; empty string if it is missing, fails, hangs or prints garbage."""
    exe = shutil.which(cmd[0])
    if exe is None:
        return ""
    try:
        done = subprocess.run(
            [exe, *cmd[1:]],
            capture_output=True,
            encoding="utf-8",
            errors="replace",  # Windows code pages and odd driver output must not crash `doctor`
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    return done.stdout if done.returncode == 0 else ""


@dataclass(frozen=True)
class GpuInfo:
    name: str
    #: Total memory in MiB; ``None`` when the driver reports ``[N/A]`` (MIG, Jetson, some virtual GPUs).
    memory_mib: int | None
    driver: str


def find_nvidia_smi(
    which: Which = shutil.which,
    *,
    env: Mapping[str, str] | None = None,
    platform: str = sys.platform,
    is_file: Callable[[str], bool] = os.path.isfile,
) -> str | None:
    """Path of ``nvidia-smi``: on ``PATH``, else where drivers put it outside ``PATH``.

    Older Windows drivers use ``%ProgramFiles%\\NVIDIA Corporation\\NVSMI``; WSL has it in
    ``/usr/lib/wsl/lib``.
    """
    found = which("nvidia-smi")
    if found:
        return found
    env = os.environ if env is None else env
    candidates: list[str] = []
    if platform == "win32":
        program_files = env.get("ProgramFiles", r"C:\Program Files")
        candidates.append(program_files + r"\NVIDIA Corporation\NVSMI\nvidia-smi.exe")
    elif platform.startswith("linux"):
        candidates.append("/usr/lib/wsl/lib/nvidia-smi")
    return next((c for c in candidates if is_file(c)), None)


def _memory_mib(text: str) -> int | None:
    try:
        return int(float(text))
    except ValueError:
        return None


#: ``nvidia-smi`` output per executable, for the default runner only (it takes up to a second per call).
_GPU_CACHE: dict[str, str] = {}


def clear_gpu_cache() -> None:
    """Forget the cached ``nvidia-smi`` output, so the next :func:`detect_nvidia_gpus` asks again."""
    _GPU_CACHE.clear()


def detect_nvidia_gpus(run: Runner | None = None, *, nvidia_smi: str | None = None) -> list[GpuInfo]:
    """NVIDIA GPUs visible through ``nvidia-smi`` (empty list if none or no driver).

    Without an injected ``run``, the answer is cached for the life of the process (one command asks several
    times: the planner, the local GPU backend's ``check()``, ``doctor``); see :func:`clear_gpu_cache`.
    """
    exe = nvidia_smi or find_nvidia_smi() or "nvidia-smi"
    cmd = [exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"]
    if run is None:
        if exe not in _GPU_CACHE:
            _GPU_CACHE[exe] = _run(cmd)
        out = _GPU_CACHE[exe]
    else:
        out = run(cmd)
    gpus = []
    for row in out.strip().splitlines():
        parts = [p.strip() for p in row.split(",")]
        if len(parts) != 3 or not parts[0]:
            continue
        name, memory, driver = parts
        gpus.append(GpuInfo(name=name, memory_mib=_memory_mib(memory), driver=driver))
    return gpus


@dataclass(frozen=True)
class FfmpegInfo:
    path: str | None
    version: str | None
    has_libass: bool
    has_libx264: bool
    has_ffprobe: bool
    ffprobe_path: str | None = None

    @property
    def usable(self) -> bool:
        """ffmpeg can encode H.264 and ffprobe exists (without libass, subtitles become a subtitle track)."""
        return bool(self.path) and self.has_libx264 and self.has_ffprobe


def _find_tool(name: str, env_var: str, which: Which, env: Mapping[str, str]) -> str | None:
    override = env.get(env_var, "").strip()
    return which(override) if override else which(name)


def find_ffmpeg(which: Which = shutil.which, env: Mapping[str, str] | None = None) -> str | None:
    """The ffmpeg to use: ``$ERASEDUB_FFMPEG`` if set (must exist), else ``ffmpeg`` on ``PATH``."""
    return _find_tool("ffmpeg", FFMPEG_ENV, which, os.environ if env is None else env)


def find_ffprobe(which: Which = shutil.which, env: Mapping[str, str] | None = None) -> str | None:
    """The ffprobe to use: ``$ERASEDUB_FFPROBE`` if set (must exist), else ``ffprobe`` on ``PATH``."""
    return _find_tool("ffprobe", FFPROBE_ENV, which, os.environ if env is None else env)


def detect_ffmpeg(
    run: Runner = _run, which: Which = shutil.which, env: Mapping[str, str] | None = None
) -> FfmpegInfo:
    """Probe the ffmpeg EraseDub will actually run (the resolved path, not whatever is first on PATH)."""
    path = find_ffmpeg(which, env)
    ffprobe = find_ffprobe(which, env)
    if path is None:
        return FfmpegInfo(None, None, False, False, ffprobe is not None, ffprobe)
    out = run([path, "-hide_banner", "-version"])
    first = out.splitlines()[0] if out else ""
    version = (
        first.split(" ")[2] if first.startswith("ffmpeg version ") and len(first.split(" ")) > 2 else None
    )
    return FfmpegInfo(
        path=path,
        version=version,
        has_libass="--enable-libass" in out,
        has_libx264="--enable-libx264" in out,
        has_ffprobe=ffprobe is not None,
        ffprobe_path=ffprobe,
    )


def python_ok() -> bool:
    return sys.version_info >= (3, 11)


@dataclass(frozen=True)
class CudaStatus:
    """Whether PyTorch can use the requested CUDA device. ``reason`` says what to do when it cannot."""

    usable: bool
    reason: str = ""


def _import_torch() -> Any:
    return importlib.import_module("torch")


def _torch_broken(exc: Exception) -> CudaStatus:
    return CudaStatus(
        False,
        f"PyTorch is installed but failed to load ({type(exc).__name__}: {exc}). On Windows this is usually "
        "a missing Microsoft Visual C++ Redistributable or a mix of CPU and CUDA packages; reinstall "
        f"PyTorch as described in {doc('install/windows.md')}",
    )


#: Oldest macOS whose MPS backend runs PyTorch's FFT operations (LaMa's Fourier units need them).
MPS_MIN_MACOS = 14


def _macos_major() -> int:
    release = platform.mac_ver()[0]
    try:
        return int(release.split(".")[0])
    except ValueError:  # not macOS
        return 0


def mps_usable(
    *, import_torch: Callable[[], Any] = _import_torch, macos_major: Callable[[], int] = _macos_major
) -> bool:
    """Whether erasing can use an Apple GPU: PyTorch reports MPS available and macOS is 14 or newer.

    ``torch.backends.mps.is_available()`` is already true on macOS 13, but PyTorch's MPS FFT kernels refuse
    to run before macOS 14.
    """
    if macos_major() < MPS_MIN_MACOS:
        return False
    try:
        torch = import_torch()
        return bool(torch.backends.mps.is_available())
    except Exception:  # no torch, or a broken one: erase on the CPU
        return False


def cuda_usable(
    device: str = "cuda",
    *,
    import_torch: Callable[[], Any] = _import_torch,
    gpus: Callable[[], list[GpuInfo]] = detect_nvidia_gpus,
) -> CudaStatus:
    """Check at render time that PyTorch can really use ``device`` (``"cuda"`` or ``"cuda:N"``).

    ``nvidia-smi`` seeing a GPU is not enough: the PyTorch wheels on PyPI are CPU-only on Windows, and
    ``CUDA_VISIBLE_DEVICES`` can hide every GPU. Imports torch, so never call it from ``check()``.
    """
    try:
        torch = import_torch()
    except ModuleNotFoundError as exc:
        if exc.name == "torch":
            return CudaStatus(False, f"PyTorch is not installed - {missing_extra('erase')}")
        return _torch_broken(exc)
    except Exception as exc:  # a broken install: "DLL load failed" (OSError/ImportError) on Windows
        return _torch_broken(exc)
    if not torch.cuda.is_available():
        if getattr(torch.version, "cuda", None) is None:
            if gpus():
                return CudaStatus(
                    False,
                    "an NVIDIA GPU is present but the installed PyTorch is a CPU-only build (the default on "
                    f"Windows). Install the CUDA build of PyTorch: {doc('install/windows.md')}",
                )
            return CudaStatus(False, f"no NVIDIA GPU found - see {doc('gpu-rental.md')}")
        return CudaStatus(
            False,
            f"PyTorch (CUDA {torch.version.cuda}) cannot use the GPU: the NVIDIA driver may be too old, or "
            f"CUDA_VISIBLE_DEVICES hides it. See {doc('troubleshooting.md')}",
        )
    index = int(device.partition(":")[2] or 0)
    count = int(torch.cuda.device_count())
    if index >= count:
        return CudaStatus(
            False, f"{device} does not exist: PyTorch sees {count} GPU(s), cuda:0 to cuda:{count - 1}"
        )
    return CudaStatus(True)
