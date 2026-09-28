"""User-facing strings in the package must print on any console code page (the CLI output is ASCII-safe).

The browser-only web UI modules are exempt (see ``BROWSER_ONLY``).
"""

import ast
from pathlib import Path

import pytest

import erasedub

PACKAGE = Path(erasedub.__file__).parent
#: Web UI text is rendered as HTML in a browser, never printed to a console, and its translations (vi, zh)
#: cannot be ASCII. webui/__init__.py stays checked: its "needs Gradio" error is printed by the CLI.
BROWSER_ONLY = {PACKAGE / "webui" / name for name in ("actions.py", "app.py", "strings.py")}
SOURCES = sorted(p for p in PACKAGE.rglob("*.py") if p not in BROWSER_ONLY)
#: Non-ASCII literals that are data, not text shown to users.
NOT_MESSAGES = {"\ufeff"}  # the byte order mark stripped by the SRT parser


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _non_ascii(path: Path) -> list[str]:
    """The non-ASCII string literals of ``path`` that are shown to users (docstrings are not)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstrings(tree)
    return [
        f"{path.name}:{node.lineno}: {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and node.value not in NOT_MESSAGES
        and not node.value.isascii()
    ]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_string_literals_are_ascii(path: Path) -> None:
    assert _non_ascii(path) == []


@pytest.mark.parametrize("path", sorted(BROWSER_ONLY), ids=lambda p: p.name)
def test_browser_only_modules_still_need_the_exemption(path: Path) -> None:
    # A rename must not silently drop the exemption, and a module that no longer needs it leaves the list.
    assert path.is_file()
    assert _non_ascii(path), f"{path.name} has no non-ASCII text: remove it from BROWSER_ONLY"
