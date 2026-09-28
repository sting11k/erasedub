from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from erasedub.context import RunContext
from erasedub.models import TextRegion
from erasedub.providers.base import PLUGIN_API_VERSION, TextEraser


class NoEraser(TextEraser):
    """Keeps the original picture. Used when erasing is off or no GPU is available."""

    name: ClassVar[str] = "none"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = "keep the original picture (no erasing)"

    def erase(self, video: Path, regions: Sequence[TextRegion], output: Path, *, ctx: RunContext) -> Path:
        # Nothing to write: the contract allows returning a path other than ``output``.
        return video
