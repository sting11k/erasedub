from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from erasedub.context import RunContext
from erasedub.engine import opened
from erasedub.errors import ProviderUnavailableError
from erasedub.hardware import cuda_usable, detect_nvidia_gpus
from erasedub.links import doc
from erasedub.models import TextRegion
from erasedub.providers.base import PLUGIN_API_VERSION, Availability, EraserSpec, GpuBackend
from erasedub.registry import create_eraser


class LocalGpu(GpuBackend):
    """Runs erasing on this machine's NVIDIA GPU.

    ``check()`` only asks ``nvidia-smi`` (cheap, no torch import). Before erasing on a CUDA device,
    :meth:`run_eraser` also confirms that PyTorch can use it (:func:`erasedub.hardware.cuda_usable`), because
    a CPU-only PyTorch build next to an NVIDIA GPU is common on Windows.
    """

    name: ClassVar[str] = "local"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = "this machine's NVIDIA GPU"
    remote: ClassVar[bool] = False

    def check(self) -> Availability:
        if not detect_nvidia_gpus():
            return Availability(
                False,
                "no NVIDIA GPU found (nvidia-smi); only GPU erasers need one, lama runs without it - see "
                f"{doc('gpu-rental.md')}",
            )
        return Availability.ready()

    def run_eraser(
        self, spec: EraserSpec, video: Path, regions: Sequence[TextRegion], output: Path, *, ctx: RunContext
    ) -> Path:
        if ctx.device.startswith("cuda"):
            status = cuda_usable(ctx.device)
            if not status.usable:
                raise ProviderUnavailableError(f"cannot erase on {ctx.device}: {status.reason}")
        eraser = create_eraser(spec)
        with opened(eraser, ctx):
            return eraser.erase(video, regions, output, ctx=ctx)
