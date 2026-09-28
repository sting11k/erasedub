from __future__ import annotations

import argparse
import contextlib
import io
import sys
import threading
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar, Final

from pydantic import Field

from erasedub.context import RunContext
from erasedub.errors import EraseDubError
from erasedub.links import doc
from erasedub.models import TextRegion
from erasedub.providers import _fetch, _video
from erasedub.providers._video import Job, import_module
from erasedub.providers._weights import state_dict
from erasedub.providers.base import PLUGIN_API_VERSION, ProviderOptions, TextEraser

#: Licence of the ProPainter code and weights, as the upstream README and LICENSE state it.
LICENSE: Final = "S-Lab License 1.0"
#: Upstream repository and the commit whose inference code is fetched.
SOURCE_REPO: Final = "https://github.com/sczhou/ProPainter"
SOURCE_COMMIT: Final = "e870e79321c31b733e2031af5aa2fb1fe3ac7eec"
#: GitHub release holding the pretrained weights, and the files inference needs from it.
WEIGHTS_RELEASE: Final = "v0.1.0"
WEIGHTS_FILES: Final = ("ProPainter.pth", "raft-things.pth", "recurrent_flow_completion.pth")

#: The upstream files inference imports (plus the licence), each with its sha256 at :data:`SOURCE_COMMIT`.
SOURCE_FILES: Final = (
    ("LICENSE", "322da691bf0272644565c84830a139d9f3c700c16cb415043a476d50bf7f2f90"),
    ("model/__init__.py", "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b"),
    ("model/misc.py", "84a552689af17aea1ddbdcf3f94ecdacf20c0eaafc87887b36ded7291993c72f"),
    ("model/propainter.py", "267b55e81eab81a923d897c81cf9c603888192d564dd32a5f5097f6aa3ad798e"),
    (
        "model/recurrent_flow_completion.py",
        "ebb714f3fc157fd270c38f1cd005626afd683cdd040c595b30d9b777f4f9e42e",
    ),
    ("model/modules/base_module.py", "495659e684e3177fda3d5f018c65949df8180e074b9d6a4b5a580b9aec333a49"),
    ("model/modules/deformconv.py", "aa14007a58295aca32ae2e43d4bf3cde7d935288b9855989e0c2eaa672f6cbaf"),
    ("model/modules/flow_comp_raft.py", "f682bfa56564d2c1e862c7c430c032308c5ea50374ecb6a06a77e4ba074fa95b"),
    ("model/modules/flow_loss_utils.py", "51f585ee70ac91a1ce1e5785f70b5bc29a37b7d5d4777a4737d90f427a72e023"),
    (
        "model/modules/sparse_transformer.py",
        "e29d3dcda72ee18d7aa897df26b79621b22dcb459f996c569afb4e6fa6e44674",
    ),
    ("model/modules/spectral_norm.py", "1ce8754d2e8fe34b3898a1f9e40c8b2e196da1596e468f0cd8167cace625028f"),
    ("RAFT/__init__.py", "4eef12ccfcfa5793551dd8dcd62037ea0032fb131a1bacf71a0ba1984639805b"),
    ("RAFT/corr.py", "1aa5456656c26f9fe77100edb1f6134ada2c5ef731bd5470efd3cd5962b48333"),
    ("RAFT/extractor.py", "f6b65d4f4df27001d9c11194f71b84a9799de3231f9d1672cbcf3a052fec79dc"),
    ("RAFT/raft.py", "d104c88d546bb30eb0bd115f93fec21480927f32e5eeb67ffea906cb1d444a93"),
    ("RAFT/update.py", "7302f91ffc24c85cf739a25feb17d9d1e537f7344a2ddc331daf8ab9db6c3f74"),
    ("RAFT/utils/__init__.py", "a06f6f6bf8518a4f08e72078e740f59fee7e4963981a384c9398ae8db34140bb"),
    ("RAFT/utils/utils.py", "bfab8e6787032ea5cd15a57cbeee534fd11c454dc2c2babb86138c6fe7642fb0"),
    ("RAFT/utils/flow_viz.py", "72595c16479265fea80add4f1691b4e35321b898f3da0a0f2ee19f6f8d831d26"),
    ("RAFT/utils/frame_utils.py", "ef3b214054e581700451215a72bb4fed202588c8d66483c90249127b6305d9d0"),
)
#: Exact sizes of the release assets. The release publishes no checksums, so each file's sha256 is stored on
#: first download and checked on every later run (see :mod:`._fetch`).
WEIGHTS_SIZES: Final = {
    "ProPainter.pth": 157_780_510,
    "raft-things.pth": 21_108_000,
    "recurrent_flow_completion.pth": 20_348_681,
}
_RAW_URL: Final = "https://raw.githubusercontent.com/sczhou/ProPainter/{commit}/{path}"
_RELEASE_URL: Final = "https://github.com/sczhou/ProPainter/releases/download/{release}/{name}"
#: Top-level package names of the fetched code (it imports itself as ``model`` and ``RAFT``).
_PACKAGES: Final = ("model", "RAFT")
_IMPORT_LOCK = threading.Lock()


def source_remotes() -> list[tuple[str, _fetch.Remote]]:
    """``(relative directory, remote)`` for each file of :data:`SOURCE_FILES`."""
    out = []
    for path, sha256 in SOURCE_FILES:
        folder, _, name = path.rpartition("/")
        out.append(
            (folder, _fetch.Remote(name, _RAW_URL.format(commit=SOURCE_COMMIT, path=path), sha256=sha256))
        )
    return out


def weight_remotes() -> list[_fetch.Remote]:
    return [
        _fetch.Remote(name, _RELEASE_URL.format(release=WEIGHTS_RELEASE, name=name), size=WEIGHTS_SIZES[name])
        for name in WEIGHTS_FILES
    ]


def fetch_source(root: Path, *, ctx: RunContext) -> Path:
    """Download (once) and verify the pinned inference code into ``root``; return the import root."""
    for folder, remote in source_remotes():
        _fetch.fetch(remote, root / folder if folder else root, ctx=ctx)
    return root


def import_source(root: Path) -> dict[str, Any]:
    """Import the fetched upstream modules from ``root`` (and nowhere else)."""
    with _IMPORT_LOCK:
        for package in _PACKAGES:
            loaded = sys.modules.get(package)
            origin = getattr(loaded, "__file__", None) or ""
            if loaded is not None and not Path(origin).resolve().is_relative_to(root.resolve()):
                raise EraseDubError(
                    f"eraser 'propainter' needs the top-level module name '{package}', but another module "
                    f"with that name is already imported ({origin or 'built-in'})"
                )
        sys.path.insert(0, str(root))
        try:
            modules = {
                name: import_module(name)
                for name in ("model.propainter", "model.recurrent_flow_completion", "RAFT")
            }
        finally:
            sys.path.remove(str(root))
    return modules


def clip_lengths(width: int) -> int:
    """How many frames RAFT estimates flow for at once (upstream's memory-based choice)."""
    if width <= 640:
        return 12
    if width <= 720:
        return 8
    if width <= 1280:
        return 4
    return 2


def reference_ids(length: int, neighbors: Sequence[int], stride: int) -> list[int]:
    taken = set(neighbors)
    return [i for i in range(0, length, stride) if i not in taken]


#: Shortest side ProPainter can process: RAFT halves its 1/8-resolution correlation map three times.
MIN_SIDE: Final = 64


def process_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    """Scale the longer side down to ``max_side`` but keep the shorter one at least :data:`MIN_SIDE`, then
    round to multiples of 8 as the upstream network needs (down, but never below :data:`MIN_SIDE`)."""
    scale = min(1.0, max(max_side / max(width, height), MIN_SIDE / min(width, height)))

    def side(length: int) -> int:
        return max(MIN_SIDE, int(length * scale) // 8 * 8)

    return side(width), side(height)


class ProPainterOptions(ProviderOptions):
    dilate: int = Field(default=4, ge=0, le=64, description="grow each text box by this many pixels")
    mask_dilation: int = Field(default=4, ge=0, le=32, description="upstream --mask_dilation")
    context: int = Field(default=64, ge=8, le=512, description="pixels of picture kept around the text")
    max_frames: int = Field(default=80, ge=10, le=80, description="longest run of frames inpainted at once")
    max_side: int = Field(default=960, ge=64, le=4096, description="crops are scaled down to this size")
    neighbor_length: int = Field(default=10, ge=2, le=40, description="upstream --neighbor_length")
    ref_stride: int = Field(default=10, ge=1, le=100, description="upstream --ref_stride")
    raft_iter: int = Field(default=20, ge=1, le=50, description="upstream --raft_iter")
    fp16: bool = Field(default=False, description="half precision on CUDA (less memory)")


class ProPainterEraser(TextEraser):
    """ProPainter video inpainting (S-Lab License 1.0: non-commercial use only). Opt-in, NVIDIA GPU.

    Neither the code nor the weights ship with EraseDub. On first use the eraser fetches the inference code of
    :data:`SOURCE_REPO` at :data:`SOURCE_COMMIT` (every file checked against its pinned sha256) and the
    :data:`WEIGHTS_FILES` of release :data:`WEIGHTS_RELEASE` into ``ctx.cache_dir``. The release publishes no
    checksums, so the weights are pinned by size and their sha256 is stored on first download; a later
    mismatch is an error, never a silent re-download. Inference follows upstream ``inference_propainter.py``
    (RAFT flow, flow completion, image propagation, transformer) on a crop around each group of text boxes.
    """

    name: ClassVar[str] = "propainter"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = f"ProPainter video inpainting - non-commercial ({LICENSE}) (NVIDIA GPU)"
    requires_modules: ClassVar[tuple[str, ...]] = ("torch", "torchvision", "cv2", "scipy", "einops", "PIL")
    extra: ClassVar[str | None] = "propainter"
    requires_gpu: ClassVar[bool] = True
    notice: ClassVar[str] = (
        f"Eraser 'propainter' is licensed under the {LICENSE} (code and weights): non-commercial use only. "
        f"See {doc('models-and-licenses.md')}."
    )
    Options: ClassVar[type[ProviderOptions]] = ProPainterOptions

    options: ProPainterOptions
    _raft: Any = None
    _flow: Any = None
    _model: Any = None
    _device: str = "cpu"

    def open(self, ctx: RunContext) -> None:
        torch = import_module("torch")
        cache = ctx.cache_dir / "propainter"
        root = fetch_source(cache / f"src-{SOURCE_COMMIT[:12]}", ctx=ctx)
        paths = dict(
            zip(
                WEIGHTS_FILES,
                _fetch.fetch_all(weight_remotes(), cache / WEIGHTS_RELEASE, ctx=ctx),
                strict=True,
            )
        )
        modules = import_source(root)

        def weights(name: str) -> Any:
            return state_dict(torch.load(str(paths[name]), map_location="cpu", weights_only=True))

        device = torch.device(ctx.device)
        with contextlib.redirect_stdout(io.StringIO()):  # upstream constructors print parameter counts
            raft = modules["RAFT"].RAFT(
                argparse.Namespace(small=False, mixed_precision=False, alternate_corr=False)
            )
            flow = modules["model.recurrent_flow_completion"].RecurrentFlowCompleteNet()
            model = modules["model.propainter"].InpaintGenerator(init_weights=False)
        raft.load_state_dict(weights("raft-things.pth"))
        flow.load_state_dict(weights("recurrent_flow_completion.pth"))
        model.load_state_dict(weights("ProPainter.pth"))
        for net in (raft, flow):
            for p in net.parameters():
                p.requires_grad = False
        self._device = ctx.device
        self._raft = raft.to(device).eval()
        self._flow = flow.to(device).eval()
        self._model = model.to(device).eval()

    def close(self) -> None:
        loaded = self._model is not None
        self._raft = self._flow = self._model = None
        if loaded and self._device.startswith("cuda"):
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
            gap=opts.neighbor_length,
        )
        if not jobs:
            return video
        return _video.transform_video(
            video, output, jobs, stream, self._inpaint_job, ctx=ctx, message="erasing text (propainter)"
        )

    def _inpaint_job(self, job: Job, frames: list[Any]) -> None:
        cv2 = import_module("cv2")
        masks = _video.job_masks(job)
        r = job.rect
        size = process_size(r.width, r.height, self.options.max_side)
        small = [cv2.resize(f[r.y0 : r.y1, r.x0 : r.x1], size, interpolation=cv2.INTER_AREA) for f in frames]
        small_masks = [cv2.resize(m, size, interpolation=cv2.INTER_NEAREST) for m in masks]
        with warnings.catch_warnings():
            # Upstream RAFT uses the deprecated torch.cuda.amp.autocast and torch.meshgrid without indexing.
            warnings.simplefilter("ignore", FutureWarning)
            warnings.filterwarnings("ignore", message="torch.meshgrid", category=UserWarning)
            filled = self._inpaint(small, small_masks)
        crops = [cv2.resize(f, (r.width, r.height), interpolation=cv2.INTER_CUBIC) for f in filled]
        _video.paste(frames, job, crops, masks)

    def _inpaint(self, frames: list[Any], masks: list[Any]) -> list[Any]:
        """Upstream ``inference_propainter.py`` (video inpainting mode) for one clip of at most 80 frames."""
        if len(frames) == 1:  # flow needs two frames: pair the frame with itself
            return self._inpaint(frames * 2, masks * 2)[:1]
        np = import_module("numpy")
        torch = import_module("torch")
        cv2 = import_module("cv2")
        opts = self.options
        device = torch.device(self._device)
        length = len(frames)
        height, width = masks[0].shape
        kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
        dilated = np.stack(
            [cv2.dilate(m, kernel, iterations=opts.mask_dilation) if opts.mask_dilation else m for m in masks]
        ).astype(np.float32)
        use_half = opts.fp16 and self._device.startswith("cuda")

        with torch.no_grad():
            x = torch.from_numpy(np.stack(frames)).permute(0, 3, 1, 2).float().div(255).mul(2).sub(1)
            x = x[None].to(device)
            masks_t = torch.from_numpy(dilated)[None, :, None].to(device)
            flow_masks = masks_t

            # ---- flow (fp32) ----
            clip = clip_lengths(width)
            forward, backward = [], []
            for f in range(0, length, clip):
                end = min(length, f + clip)
                part = x[:, f:end] if f == 0 else x[:, f - 1 : end]
                flow_f, flow_b = self._bidirectional_flow(part)
                forward.append(flow_f)
                backward.append(flow_b)
            gt_flows = (torch.cat(forward, dim=1), torch.cat(backward, dim=1))

            flow_net, model = self._flow, self._model
            if use_half:
                x, flow_masks, masks_t = x.half(), flow_masks.half(), masks_t.half()
                gt_flows = (gt_flows[0].half(), gt_flows[1].half())
                flow_net, model = flow_net.half(), model.half()

            # ---- complete flow ----
            pred_flows, _ = flow_net.forward_bidirect_flow(gt_flows, flow_masks)
            pred_flows = flow_net.combine_flow(gt_flows, pred_flows, flow_masks)

            # ---- image propagation ----
            masked = x * (1 - masks_t)
            prop, updated_local = model.img_propagation(masked, pred_flows, masks_t, "nearest")
            updated_frames = x * (1 - masks_t) + prop.view(1, length, 3, height, width) * masks_t
            updated_masks = updated_local.view(1, length, 1, height, width)

            # ---- feature propagation + transformer ----
            comp: list[Any] = [None] * length
            originals = [f.astype(np.float32) for f in frames]
            stride = opts.neighbor_length // 2
            for f in range(0, length, stride):
                neighbors = list(range(max(0, f - stride), min(length, f + stride + 1)))
                ids = neighbors + reference_ids(length, neighbors, opts.ref_stride)
                flows = (pred_flows[0][:, neighbors[:-1]], pred_flows[1][:, neighbors[:-1]])
                pred = model(
                    updated_frames[:, ids], flows, masks_t[:, ids], updated_masks[:, ids], len(neighbors)
                )
                pred = ((pred.view(-1, 3, height, width) + 1) / 2).float().cpu().permute(
                    0, 2, 3, 1
                ).numpy() * 255
                for i, idx in enumerate(neighbors):
                    keep = dilated[idx][..., None]
                    img = pred[i] * keep + originals[idx] * (1 - keep)
                    comp[idx] = img if comp[idx] is None else comp[idx] * 0.5 + img * 0.5
            if use_half:
                self._flow, self._model = flow_net.float(), model.float()
        return [np.clip(c, 0, 255).astype(np.uint8) for c in comp]

    def _bidirectional_flow(self, frames: Any) -> tuple[Any, Any]:
        """Upstream ``RAFT_bi.forward``: forward and backward flow between consecutive frames."""
        b, t, c, h, w = frames.size()
        first = frames[:, :-1].reshape(-1, c, h, w)
        second = frames[:, 1:].reshape(-1, c, h, w)
        iters = self.options.raft_iter
        _, flow_f = self._raft(first, second, iters=iters, test_mode=True)
        _, flow_b = self._raft(second, first, iters=iters, test_mode=True)
        return flow_f.view(b, t - 1, 2, h, w), flow_b.view(b, t - 1, 2, h, w)
