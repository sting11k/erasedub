"""The part of the Modal backend that runs inside the Modal container.

:mod:`erasedub.providers.gpu_modal` ships this function (by reference: the container gets the ``erasedub``
package as a local Python source) together with the eraser's class path, its options, the regions and the
video bytes. It runs the same eraser code as a local run, on the container's GPU, and returns the result.
"""

from __future__ import annotations

import importlib
import json
import logging
import tempfile
from pathlib import Path

from erasedub.context import RunContext
from erasedub.engine import opened
from erasedub.models import TextRegion
from erasedub.providers.base import TextEraser

#: Where the model volume is mounted in the container (the eraser cache survives between runs).
CACHE_MOUNT = "/cache"
#: Name of the Modal volume holding downloaded model files.
VOLUME_NAME = "erasedub-models"


def load_eraser(target: str, options_json: str) -> TextEraser:
    """Build the eraser from ``"module:Class"`` and its JSON options."""
    module, _, attr = target.partition(":")
    cls = getattr(importlib.import_module(module), attr)
    if not (isinstance(cls, type) and issubclass(cls, TextEraser)):
        raise TypeError(f"{target} is not a text eraser")
    eraser: TextEraser = cls(json.loads(options_json))
    return eraser


def erase_remote(target: str, options_json: str, regions_json: str, video: bytes, suffix: str) -> bytes:
    """Run the eraser on ``video`` (the file's bytes) and return the erased video's bytes."""
    logging.basicConfig(level=logging.INFO)
    eraser = load_eraser(target, options_json)
    regions = [TextRegion.model_validate(item) for item in json.loads(regions_json)]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        source = work / f"input{suffix}"
        source.write_bytes(video)
        (work / "tmp").mkdir()
        ctx = RunContext(tmp_dir=work / "tmp", cache_dir=Path(CACHE_MOUNT), device="cuda")
        try:
            with opened(eraser, ctx):
                result = eraser.erase(source, regions, work / f"output{suffix}", ctx=ctx)
        finally:
            _commit_cache()
        return result.read_bytes()


def _commit_cache() -> None:
    """Persist newly downloaded model files in the Modal volume."""
    modal = importlib.import_module("modal")
    modal.Volume.from_name(VOLUME_NAME).commit()
