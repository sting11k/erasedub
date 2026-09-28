"""The on-screen text regions found by ``prepare`` and erased by ``render``.

``work/<video-stem>/regions.json`` is shared by all target languages. It holds the video it was measured on
and every region found by the OCR step, so ``render`` can run on another machine or backend (``--gpu modal``)
without detecting text again, and users can inspect or edit what will be erased.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from erasedub import _jsonfile
from erasedub.models import FrozenModel, TextRegion, VideoInfo

REGIONS_VERSION: Literal[1] = 1
REGIONS_NAME = "regions.json"


class RegionsFile(FrozenModel):
    version: Literal[1] = REGIONS_VERSION
    video: VideoInfo
    regions: tuple[TextRegion, ...] = ()


def regions_path(workdir: Path) -> Path:
    return workdir / REGIONS_NAME


def save_regions(regions: RegionsFile, workdir: Path) -> Path:
    """Write ``regions.json`` into ``workdir`` and return its path."""
    path = regions_path(workdir)
    _jsonfile.write(path, regions)
    return path


def load_regions(workdir: Path) -> RegionsFile:
    """Read ``regions.json`` from ``workdir``; raises ``ScriptFormatError`` if it is missing or invalid."""
    return _jsonfile.read(regions_path(workdir), RegionsFile, supported_version=REGIONS_VERSION)
