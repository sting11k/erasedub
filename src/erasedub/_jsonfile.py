"""Reading and writing the versioned JSON files kept in a video's work directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from erasedub.errors import ScriptFormatError, describe_validation_error

M = TypeVar("M", bound=BaseModel)


def write(path: Path, model: BaseModel) -> None:
    """Write ``model`` as indented UTF-8 JSON (non-ASCII text kept readable)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def read(path: Path, model: type[M], *, supported_version: int) -> M:
    """Read ``path`` into ``model``; every problem becomes a :class:`ScriptFormatError` naming the file.

    A ``version`` newer than ``supported_version`` gets its own message, because the fix is to upgrade
    EraseDub, not to edit the file.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ScriptFormatError(f"{path} not found") from exc
    except UnicodeDecodeError as exc:
        raise ScriptFormatError(f"{path}: not UTF-8 text") from exc
    except json.JSONDecodeError as exc:
        raise ScriptFormatError(f"{path}:{exc.lineno}: not valid JSON ({exc.msg})") from exc
    version = data.get("version") if isinstance(data, dict) else None
    if isinstance(version, int) and not isinstance(version, bool) and version > supported_version:
        raise ScriptFormatError(
            f"{path} was written by a newer EraseDub (file format version {version}; this EraseDub reads "
            f"version {supported_version}). Update EraseDub: run `git pull` in the erasedub folder, then "
            "`uv sync` with the extras you use (or `pip install -e .`)"
        )
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ScriptFormatError(f"{path}: {describe_validation_error(exc)}") from exc
