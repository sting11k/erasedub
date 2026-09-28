from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar, Final

from pydantic import Field

from erasedub.context import RunContext
from erasedub.models import TextRegion
from erasedub.providers import _fetch, _video
from erasedub.providers._video import Job, import_module
from erasedub.providers._weights import load_checkpoint, state_dict
from erasedub.providers.base import PLUGIN_API_VERSION, ProviderOptions, TextEraser

#: Upstream repository (MIT) and the commit the vendored generator in ``_vendor/sttn.py`` comes from.
SOURCE_REPO: Final = "https://github.com/researchmm/STTN"
SOURCE_COMMIT: Final = "f39f62c5bbbe3e3eba084c487353a2c651bfdcde"
#: The pretrained ``sttn.pth`` the upstream README links to (Google Drive). No checksum is published, so the
#: file is pinned by size and its sha256 is stored on first download (see :mod:`._fetch`).
WEIGHTS: Final = _fetch.Remote(
    name="sttn.pth",
    url="https://drive.usercontent.google.com/download?id=1ZAMV8547wmZylKRt5qR_tC5VlosXD4Wv&export=download&confirm=t",
    size=66_252_587,
)
#: STTN input sizes are multiples of these: the quarter-resolution feature map must split into the
#: ``108 x 60`` patches of the coarsest attention head.
UNIT_WIDTH: Final = 432
UNIT_HEIGHT: Final = 240


class SttnOptions(ProviderOptions):
    dilate: int = Field(default=4, ge=0, le=64, description="grow each text box by this many pixels")
    context: int = Field(default=48, ge=8, le=512, description="pixels of picture kept around the text")
    max_frames: int = Field(
        default=120, ge=10, le=1000, description="longest run of frames inpainted at once"
    )
    neighbor_stride: int = Field(default=5, ge=1, le=20, description="half-width of the local frame window")
    ref_length: int = Field(default=10, ge=1, le=100, description="spacing of the reference frames")


def model_size(width: int, height: int) -> tuple[int, int]:
    """The ``432*k x 240*m`` size STTN runs a ``width x height`` crop at (close to its aspect ratio)."""
    rows = max(1, round(height / UNIT_HEIGHT))
    cols = max(1, round(width * (rows * UNIT_HEIGHT / height) / UNIT_WIDTH))
    return cols * UNIT_WIDTH, rows * UNIT_HEIGHT


def reference_ids(length: int, neighbors: Sequence[int], ref_length: int) -> list[int]:
    """Frames every ``ref_length`` that are not already neighbours (STTN's global references)."""
    taken = set(neighbors)
    return [i for i in range(0, length, ref_length) if i not in taken]


class SttnEraser(TextEraser):
    """STTN video inpainting (MIT). Default eraser: fast, good on subtitle bands.

    The pretrained generator (vendored from :data:`SOURCE_REPO` at :data:`SOURCE_COMMIT`) fills each group
    of text boxes using the surrounding frames. Only a crop around the text is processed, at a size that is a
    multiple of STTN's ``432 x 240`` input; the filled pixels are scaled back and pasted into the mask only.
    Weights are downloaded on first use into ``ctx.cache_dir``.
    """

    name: ClassVar[str] = "sttn"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = "STTN video inpainting - fast default (NVIDIA GPU)"
    requires_modules: ClassVar[tuple[str, ...]] = ("torch", "cv2")
    extra: ClassVar[str | None] = "erase"
    requires_gpu: ClassVar[bool] = True
    Options: ClassVar[type[ProviderOptions]] = SttnOptions

    options: SttnOptions
    _model: Any = None
    _device: str = "cpu"

    def open(self, ctx: RunContext) -> None:
        torch = import_module("torch")
        from erasedub.providers._vendor.sttn import InpaintGenerator

        weights = _fetch.fetch(WEIGHTS, ctx.cache_dir / "sttn", ctx=ctx)
        model = InpaintGenerator()
        model.load_state_dict(state_dict(load_checkpoint(weights), key="netG"))
        self._device = ctx.device
        self._model = model.to(torch.device(ctx.device)).eval()

    def close(self) -> None:
        if self._model is not None:
            self._model = None
            if self._device.startswith("cuda"):
                import_module("torch").cuda.empty_cache()

    def erase(self, video: Path, regions: Sequence[TextRegion], output: Path, *, ctx: RunContext) -> Path:
        stream = _video.probe(video)
        opts = self.options
        jobs = _video.plan_jobs(
            regions,
            width=stream.width,
            height=stream.height,
            fps=stream.fps,
            frame_count=stream.frame_count,
            dilate=opts.dilate,
            context=opts.context,
            max_frames=opts.max_frames,
            gap=opts.neighbor_stride * 2,
        )
        if not jobs:
            return video
        return _video.transform_video(
            video, output, jobs, stream, self._inpaint_job, ctx=ctx, message="erasing text (sttn)"
        )

    def _inpaint_job(self, job: Job, frames: list[Any]) -> None:
        cv2 = import_module("cv2")
        masks = _video.job_masks(job)
        r = job.rect
        size = model_size(r.width, r.height)
        kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
        small = [cv2.resize(f[r.y0 : r.y1, r.x0 : r.x1], size, interpolation=cv2.INTER_AREA) for f in frames]
        small_masks = [
            cv2.dilate(cv2.resize(m, size, interpolation=cv2.INTER_NEAREST), kernel, iterations=4)
            for m in masks
        ]
        filled = self._inpaint(small, small_masks)
        crops = [cv2.resize(f, (r.width, r.height), interpolation=cv2.INTER_LINEAR) for f in filled]
        _video.paste(frames, job, crops, masks)

    def _inpaint(self, frames: list[Any], masks: list[Any]) -> list[Any]:
        """Upstream ``test.py``: fill ``frames`` (RGB uint8) where ``masks`` is 1; returns uint8 frames."""
        np = import_module("numpy")
        torch = import_module("torch")
        model = self._model
        device = torch.device(self._device)
        stride, ref_length = self.options.neighbor_stride, self.options.ref_length
        length = len(frames)
        rgb = np.stack(frames).astype(np.float32)
        binary = np.stack(masks).astype(np.float32)[..., None]
        comp: list[Any] = [None] * length
        with torch.no_grad():
            x = torch.from_numpy(rgb).permute(0, 3, 1, 2).to(device) / 127.5 - 1
            m = torch.from_numpy(binary).permute(0, 3, 1, 2).to(device)
            feats = torch.cat([model.encoder(chunk) for chunk in torch.split(x * (1 - m), 16)])
            for f in range(0, length, stride):
                neighbors = list(range(max(0, f - stride), min(length, f + stride + 1)))
                ids = neighbors + reference_ids(length, neighbors, ref_length)
                pred = torch.tanh(model.decoder(model.infer(feats[ids])[: len(neighbors)]))
                pred = ((pred + 1) / 2 * 255).permute(0, 2, 3, 1).cpu().numpy()
                for i, idx in enumerate(neighbors):
                    img = pred[i] * binary[idx] + rgb[idx] * (1 - binary[idx])
                    comp[idx] = img if comp[idx] is None else comp[idx] * 0.5 + img * 0.5
        return [np.clip(c, 0, 255).astype(np.uint8) for c in comp]
