from pathlib import Path

import pytest

from erasedub.context import RunContext


@pytest.fixture(autouse=True)
def _plain_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep CLI output free of colour codes, whatever the terminal (CI sets FORCE_COLOR for readable logs)."""
    for name in ("FORCE_COLOR", "TTY_COMPATIBLE", "TTY_INTERACTIVE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    return RunContext(tmp_dir=tmp_path / "tmp", cache_dir=tmp_path / "cache")
