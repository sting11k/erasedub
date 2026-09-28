from importlib.metadata import version

from packaging.version import Version
from typer.testing import CliRunner

import erasedub
from erasedub.cli import app


def test_version_is_valid_and_matches_metadata() -> None:
    assert str(Version(erasedub.__version__)) == erasedub.__version__
    assert version("erasedub") == erasedub.__version__


def test_cli_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == erasedub.__version__
