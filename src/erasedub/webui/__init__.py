"""Local web UI (Gradio). Needs the ``webui`` extra: ``uv sync --extra webui`` in the erasedub folder.

Gradio is imported only when :func:`build_app` or :func:`launch` is called, so the rest of EraseDub works
without it. Warning about a non-loopback ``host`` is the caller's job (the CLI does it), so it is shown once.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import TYPE_CHECKING, Any

from erasedub.config import Config
from erasedub.errors import ProviderUnavailableError
from erasedub.links import missing_extra
from erasedub.webui.strings import DEFAULT_LOCALE

if TYPE_CHECKING:
    import gradio

__all__ = ["MAX_UPLOAD", "build_app", "launch"]

#: Largest upload the server accepts (Gradio size string).
MAX_UPLOAD = "4gb"

# ASCII like the rest of the CLI output; double quotes because single quotes break in Windows cmd.exe.
_MISSING = f"the web UI needs Gradio - {missing_extra('webui')}"


def _import_gradio() -> ModuleType:
    try:
        gr = importlib.import_module("gradio")
    except ImportError as exc:
        if exc.name not in (None, "gradio"):  # Gradio is there but one of its dependencies is broken
            raise ProviderUnavailableError(f"{_MISSING} (import failed: {exc})") from exc
        raise ProviderUnavailableError(_MISSING) from exc
    # Gradio writes files into its package folder at runtime; after `pip uninstall gradio` that folder can
    # remain and import as an empty namespace package.
    if not hasattr(gr, "Blocks"):
        raise ProviderUnavailableError(_MISSING)
    return gr


def build_app(config: Config, *, locale: str = DEFAULT_LOCALE) -> gradio.Blocks:
    """Build the web UI as a ``gradio.Blocks`` app without starting a server.

    Serve it with :func:`launch`. If you serve it another way, also pass
    :func:`erasedub.webui.theme.launch_kwargs`, or it shows Gradio's default look.
    """
    gr = _import_gradio()
    from erasedub.webui.app import build

    app: Any = build(gr, config, locale=locale)
    return app


def launch(
    config: Config,
    *,
    host: str = "127.0.0.1",
    port: int = 7860,
    open_browser: bool = False,
    locale: str = DEFAULT_LOCALE,
) -> None:
    """Build the web UI and serve it on ``host:port`` until interrupted.

    Never creates a public share link and never exposes the handlers as MCP tools, whatever the
    ``GRADIO_*`` environment variables say. ``open_browser`` opens the page in the default browser.
    """
    gr = _import_gradio()
    from erasedub.webui.theme import launch_kwargs

    # Finished videos are served from the output folder (by default they are written next to the upload,
    # in Gradio's own folder, which is always allowed).
    allowed = [str(config.general.output_dir.resolve())] if config.general.output_dir else []
    build_app(config, locale=locale).launch(
        allowed_paths=allowed,
        server_name=host,
        server_port=port,
        share=False,
        mcp_server=False,
        inbrowser=open_browser,
        max_file_size=MAX_UPLOAD,
        **launch_kwargs(gr),
    )
