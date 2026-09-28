from __future__ import annotations

import shutil
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar, Final, Literal

from pydantic import Field

from erasedub import hardware
from erasedub.context import RunContext
from erasedub.errors import EraseDubError, ProviderUnavailableError
from erasedub.links import doc
from erasedub.models import TextRegion
from erasedub.providers import _fetch, _video
from erasedub.providers._video import Job, import_module
from erasedub.providers._weights import load_checkpoint, state_dict
from erasedub.providers.base import PLUGIN_API_VERSION, ProviderOptions, TextEraser

#: Upstream repository (Apache-2.0) and the commit the vendored generator in ``_vendor/lama.py`` comes from.
SOURCE_REPO: Final = "https://github.com/advimman/lama"
SOURCE_COMMIT: Final = "786f5936b27fb3dacd2b1ad799e4de968ea697e7"
#: The big-lama weights, from the location the upstream README gives (Hugging Face ``smartywu/big-lama``),
#: pinned to a repository commit; the sha256 is the one Hugging Face publishes for the file.
WEIGHTS: Final = _fetch.Remote(
    name="big-lama.zip",
    url="https://huggingface.co/smartywu/big-lama/resolve/05cb2be7f8dbe6ca7c6e78f4fc827a4b2baaa4a9/big-lama.zip",
    sha256="f1b358ca24093b93a106183b98a3dea6e8ed09f3b43ea7251eb2c81e7b4575f6",
    size=381_428_720,
)
#: The checkpoint inside :data:`WEIGHTS`.
CHECKPOINT_MEMBER: Final = "big-lama/models/best.ckpt"

Device = Literal["auto", "cpu", "mps", "cuda"]


class LamaOptions(ProviderOptions):
    device: Device = Field(
        default="auto",
        description="auto = the run's CUDA device if it has one, else Apple MPS when available, else the CPU",
    )
    dilate: int = Field(default=6, ge=0, le=64, description="grow each text box by this many pixels")
    context: int = Field(default=128, ge=8, le=1024, description="pixels of picture kept around the text")


def resolve_device(choice: Device, ctx_device: str, *, mps_available: bool) -> str:
    """The torch device LaMa runs on for option ``choice`` and the run's ``ctx.device``."""
    if choice == "auto":
        if ctx_device.startswith("cuda"):
            return ctx_device
        return "mps" if mps_available else "cpu"
    if choice == "cuda":
        return ctx_device if ctx_device.startswith("cuda") else "cuda"
    if choice == "mps" and not mps_available:
        raise ProviderUnavailableError(
            "eraser 'lama': device 'mps' was requested but no Apple GPU is usable: it needs macOS "
            f"{hardware.MPS_MIN_MACOS} or newer and a PyTorch build with MPS"
        )
    return choice


def extract_checkpoint(archive: Path, directory: Path) -> Path:
    """Copy :data:`CHECKPOINT_MEMBER` out of the verified ``archive`` into ``directory``."""
    target = directory / "best.ckpt"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf, zf.open(CHECKPOINT_MEMBER) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
    except (KeyError, zipfile.BadZipFile) as exc:
        raise EraseDubError(f"{archive} does not contain {CHECKPOINT_MEMBER}: {exc}") from exc
    return target


def pad_to_multiple(size: int, multiple: int = 8) -> int:
    return -(-size // multiple) * multiple


class LamaEraser(TextEraser):
    """LaMa image inpainting, one frame at a time. Opt-in eraser for machines without an NVIDIA GPU.

    Runs on the ``device`` option: by default the run's CUDA device when there is one, else Apple MPS when
    PyTorch offers it, else the CPU. Frames are inpainted independently, so the result may flicker, and
    repeating patterned backgrounds tend to come out flattened; the video erasers (sttn, propainter) do not
    have these weaknesses but need an NVIDIA GPU.

    The big-lama generator is vendored from :data:`SOURCE_REPO` at :data:`SOURCE_COMMIT` (``lama`` on PyPI is
    an unrelated project). The weights (:data:`WEIGHTS`) are downloaded on first use into ``ctx.cache_dir``
    and checked against their pinned sha256; the checkpoint is read without executing pickled code (see
    :mod:`._weights`). Only a crop around each group of text boxes is processed.
    """

    name: ClassVar[str] = "lama"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = (
        "LaMa image inpainting - CPU or Apple GPU, per-frame: may flicker and flatten repeating patterns"
    )
    requires_modules: ClassVar[tuple[str, ...]] = ("torch", "cv2")
    extra: ClassVar[str | None] = "lama"
    requires_gpu: ClassVar[bool] = False
    supports_mps: ClassVar[bool] = True
    notice: ClassVar[str] = (
        "Eraser 'lama' erases each frame on its own: the result may flicker, and repeating patterned "
        "backgrounds may come out flattened. Without an NVIDIA GPU it runs on the CPU or an Apple GPU, "
        f"slower than a GPU eraser. See {doc('models-and-licenses.md')}."
    )
    Options: ClassVar[type[ProviderOptions]] = LamaOptions

    options: LamaOptions
    _model: Any = None
    _device: str = "cpu"

    def open(self, ctx: RunContext) -> None:
        torch = import_module("torch")
        from erasedub.providers._vendor.lama import big_lama

        self._device = resolve_device(self.options.device, ctx.device, mps_available=hardware.mps_usable())
        archive = _fetch.fetch(WEIGHTS, ctx.cache_dir / "lama", ctx=ctx)
        checkpoint = extract_checkpoint(archive, ctx.tmp_dir / "lama")
        try:
            weights = state_dict(load_checkpoint(checkpoint), key="state_dict", prefix="generator.")
        finally:
            checkpoint.unlink(missing_ok=True)
        model = big_lama()
        model.load_state_dict(weights)
        ctx.logger.info("lama runs on %s", self._device)
        self._model = model.to(torch.device(self._device)).eval()

    def close(self) -> None:
        if self._model is not None:
            self._model = None
            if self._device.startswith("cuda"):
                import_module("torch").cuda.empty_cache()

    def erase(self, video: Path, regions: Sequence[TextRegion], output: Path, *, ctx: RunContext) -> Path:
        stream = _video.probe(video)
        jobs = _video.plan_jobs(
            regions,
            width=stream.width,
            height=stream.height,
            fps=stream.fps,
            frame_count=stream.frame_count,
            dilate=self.options.dilate,
            context=self.options.context,
            max_frames=1,
        )
        if not jobs:
            return video
        return _video.transform_video(
            video, output, jobs, stream, self._inpaint_job, ctx=ctx, message="erasing text (lama)"
        )

    def _inpaint_job(self, job: Job, frames: list[Any]) -> None:
        masks = _video.job_masks(job)
        r = job.rect
        crops = [self._inpaint(f[r.y0 : r.y1, r.x0 : r.x1], m) for f, m in zip(frames, masks, strict=True)]
        _video.paste(frames, job, crops, masks)

    def _inpaint(self, image: Any, mask: Any) -> Any:
        """Upstream ``predict.py`` for one RGB uint8 crop: pad to a multiple of 8, fill, crop back."""
        np = import_module("numpy")
        torch = import_module("torch")
        height, width = mask.shape
        pad = ((0, pad_to_multiple(height) - height), (0, pad_to_multiple(width) - width))
        img = np.pad(image.astype(np.float32) / 255, (*pad, (0, 0)), mode="symmetric")
        msk = np.pad(mask.astype(np.float32), pad, mode="symmetric")
        with torch.no_grad():
            x = torch.from_numpy(img).permute(2, 0, 1)[None].to(self._device)
            m = torch.from_numpy(msk)[None, None].to(self._device)
            pred = self._model(torch.cat([x * (1 - m), m], dim=1))
            out = m * pred + (1 - m) * x
            result = out[0].permute(1, 2, 0).cpu().numpy()[:height, :width]
        return np.clip(result * 255, 0, 255).astype(np.uint8)
