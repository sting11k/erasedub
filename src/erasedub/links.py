"""Links to the documentation and install hints, for user-facing messages.

The URLs are absolute because a relative ``docs/...`` path means nothing when EraseDub runs from another
folder.
"""

from __future__ import annotations

DOCS_URL = "https://github.com/EraseDub/erasedub/blob/main/docs/"


def doc(page: str) -> str:
    """Absolute URL of a documentation page, e.g. ``doc("gpu-rental.md")``."""
    return DOCS_URL + page.lstrip("/")


def install_extra(extra: str) -> str:
    """How to install an optional extra: EraseDub is installed from its source folder, not from PyPI."""
    return f'run `uv sync --extra {extra}` (or `pip install -e ".[{extra}]"`) in the erasedub folder'


def missing_extra(extra: str) -> str:
    """``missing extra 'ocr': run ...`` for a message about an extra that is not installed."""
    return f"missing extra '{extra}': {install_extra(extra)}"
