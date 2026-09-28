import json
from pathlib import Path

import pytest

from erasedub import regions
from erasedub.errors import ScriptFormatError
from erasedub.models import Box, TextRegion, VideoInfo


def _sample(tmp_path: Path) -> regions.RegionsFile:
    return regions.RegionsFile(
        video=VideoInfo(path=tmp_path / "clip.mp4", width=1080, height=1920, duration=12.5, fps=30),
        regions=(
            TextRegion(
                start=0, end=2.5, box=Box(x=40, y=1600, width=1000, height=140), text="你好", confidence=0.93
            ),
            TextRegion(start=3, end=5, box=Box(x=0, y=80, width=1080, height=120), kind="title"),
        ),
    )


def test_regions_round_trip(tmp_path: Path) -> None:
    sample = _sample(tmp_path)
    path = regions.save_regions(sample, tmp_path / "work" / "clip")
    assert path.name == "regions.json"
    assert "你好" in path.read_text(encoding="utf-8")  # readable, not \\u-escaped
    assert regions.load_regions(tmp_path / "work" / "clip") == sample


def test_missing_regions_file(tmp_path: Path) -> None:
    with pytest.raises(ScriptFormatError, match=r"regions\.json not found"):
        regions.load_regions(tmp_path)


def test_newer_regions_version_is_explained(tmp_path: Path) -> None:
    data = _sample(tmp_path).model_dump(mode="json") | {"version": 7}
    regions.regions_path(tmp_path).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ScriptFormatError, match="newer EraseDub"):
        regions.load_regions(tmp_path)


def test_invalid_region_is_reported_without_values(tmp_path: Path) -> None:
    data = _sample(tmp_path).model_dump(mode="json")
    data["regions"][0]["box"]["width"] = 0
    regions.regions_path(tmp_path).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ScriptFormatError, match=r"regions\.0\.box\.width: Input should be greater than 0"):
        regions.load_regions(tmp_path)
