from pathlib import Path

import pytest

from erasedub.context import RunContext


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    return RunContext(tmp_dir=tmp_path / "tmp", cache_dir=tmp_path / "cache")
