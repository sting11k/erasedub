from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from typing import Any, ClassVar, Final

from pydantic import Field

from erasedub.context import RunContext
from erasedub.errors import ConfigError, EraseDubError, ProviderUnavailableError
from erasedub.links import doc
from erasedub.models import TextRegion
from erasedub.providers import _modal_remote
from erasedub.providers._video import import_module
from erasedub.providers.base import (
    PLUGIN_API_VERSION,
    Availability,
    EraserSpec,
    GpuBackend,
    ProviderOptions,
    TextEraser,
)

#: Name of the ephemeral Modal app each run creates.
APP_NAME: Final = "erasedub-erase"
_EXTRA_MARKER = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")
_POLL_SECONDS: Final = 5.0


class ModalOptions(ProviderOptions):
    gpu: str = Field(default="T4", min_length=1, description="Modal GPU type, for example T4, L4, A10G, A100")
    timeout: int = Field(
        default=3600, ge=60, le=86400, description="longest a run may take on Modal, seconds"
    )


def eraser_target(spec: EraserSpec) -> tuple[type[TextEraser], str]:
    """The eraser class for ``spec`` and its ``"module:Class"`` path, validating the options here first.

    Only erasers that ship with EraseDub run on Modal: the container gets the ``erasedub`` package, not
    third-party plugins.
    """
    from erasedub.registry import load_class

    cls = load_class("eraser", spec.name)
    if not (isinstance(cls, type) and issubclass(cls, TextEraser)):
        raise EraseDubError(f"'{spec.name}' is not a text eraser")
    if not cls.__module__.startswith("erasedub.providers."):
        raise ConfigError(
            f"eraser '{spec.name}' comes from a plugin; the Modal backend runs only the built-in erasers"
        )
    cls(spec.options)  # raises ConfigError for bad options before anything is uploaded
    return cls, f"{cls.__module__}:{cls.__qualname__}"


def requirements(extra: str | None, requires: Sequence[str] | None = None) -> list[str]:
    """pip requirements of ``erasedub`` itself plus those of ``extra``, from the installed metadata."""
    entries = metadata.requires("erasedub") if requires is None else requires
    out = []
    for entry in entries or ():
        spec, _, marker = entry.partition(";")
        match = _EXTRA_MARKER.search(marker)
        if match and match.group(1) != extra:
            continue
        name = re.split(r"[\s<>=!~\[;(]", spec.strip(), maxsplit=1)[0]
        if name.lower() == "erasedub":
            continue
        out.append(spec.strip())
    return out


def build_app(modal: Any, packages: Sequence[str], options: ModalOptions) -> tuple[Any, Any]:
    """Define the Modal app: image with ffmpeg, the eraser's packages and ``erasedub``; one GPU function."""
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    image = (
        modal.Image.debian_slim(python_version=python)
        .apt_install("ffmpeg")
        .pip_install(*packages)
        .add_local_python_source("erasedub")
    )
    volume = modal.Volume.from_name(_modal_remote.VOLUME_NAME, create_if_missing=True)
    app = modal.App(APP_NAME)
    function = app.function(
        image=image,
        gpu=options.gpu,
        timeout=options.timeout,
        volumes={_modal_remote.CACHE_MOUNT: volume},
        serialized=True,
    )(_modal_remote.erase_remote)
    return app, function


class ModalGpu(GpuBackend):
    """Runs only the erase step on Modal (https://modal.com) with the user's own Modal account.

    Authentication is Modal's own: ``modal token new`` writes ``~/.modal.toml``, or set
    ``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET``. EraseDub never stores or logs the token. Each run starts an
    ephemeral Modal app whose image has ffmpeg, the eraser's Python packages and this ``erasedub`` package;
    the video, the regions and the :class:`EraserSpec` are uploaded, the same eraser runs on a Modal GPU, and
    the result is downloaded to ``output``. Model files are kept in the Modal volume ``erasedub-models`` so
    later runs do not download them again. This machine needs neither a GPU nor the eraser's extra.
    """

    name: ClassVar[str] = "modal"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = "Modal cloud GPU, billed to your own Modal account"
    requires_modules: ClassVar[tuple[str, ...]] = ("modal",)
    extra: ClassVar[str | None] = "modal"
    remote: ClassVar[bool] = True
    Options: ClassVar[type[ProviderOptions]] = ModalOptions

    options: ModalOptions

    def check(self) -> Availability:
        base = super().check()
        if not base.ok:
            return base
        has_env = bool(os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"))
        if not has_env and not (Path.home() / ".modal.toml").exists():
            return Availability(False, f"no Modal token - run `modal token new` (see {doc('gpu-rental.md')})")
        return Availability.ready()

    def run_eraser(
        self, spec: EraserSpec, video: Path, regions: Sequence[TextRegion], output: Path, *, ctx: RunContext
    ) -> Path:
        cls, target = eraser_target(spec)
        modal = import_module("modal")
        try:
            packages = requirements(cls.extra)
        except metadata.PackageNotFoundError as exc:
            raise ProviderUnavailableError(
                "the Modal backend needs erasedub installed as a package (pip install -e . for a checkout)"
            ) from exc
        regions_json = json.dumps([region.model_dump(mode="json") for region in regions])
        options_json = json.dumps(spec.options)
        data = video.read_bytes()
        try:
            app, function = build_app(modal, packages, self.options)
            ctx.progress(0.0, f"uploading to Modal ({self.options.gpu})")
            ctx.logger.info("running eraser '%s' on Modal GPU %s", spec.name, self.options.gpu)
            with app.run():
                call = function.spawn(target, options_json, regions_json, data, output.suffix or ".mp4")
                ctx.progress(0.05, f"erasing on Modal ({self.options.gpu})")
                result = self._wait(call, ctx)
        except modal.exception.Error as exc:
            # Auth, invalid GPU type, function timeout, expired output... An EraseDubError raised remotely
            # comes back as itself and passes through unchanged.
            raise EraseDubError(f"Modal: {type(exc).__name__}: {exc}") from exc
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(result)
        ctx.progress(1.0, "downloaded from Modal")
        return output

    @staticmethod
    def _wait(call: Any, ctx: RunContext) -> bytes:
        """Wait for the remote call, cancelling it if the user cancels the run."""
        while True:
            try:
                ctx.raise_if_cancelled()
            except BaseException:
                call.cancel()
                raise
            try:
                result = call.get(timeout=_POLL_SECONDS)
            except TimeoutError:  # only the poll timed out; the function's own timeout is a Modal error
                continue
            if not isinstance(result, bytes):
                raise EraseDubError("the Modal function returned no video")
            return result
