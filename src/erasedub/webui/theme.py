"""Look of the web UI: a configured Gradio Soft theme and a few lines of CSS.

Fonts come from the user's system (no web-font download, works offline). The stack lists fonts with full
Vietnamese coverage first, then CJK fonts for each OS; browsers fall back glyph by glyph along the list.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

SANS = (
    "Segoe UI",  # Windows, Vietnamese included
    "Helvetica Neue",  # macOS
    "Noto Sans",  # Linux / Android
    "Roboto",
    "PingFang SC",  # macOS Chinese
    "Hiragino Sans GB",
    "Microsoft YaHei",  # Windows Chinese
    "Noto Sans CJK SC",  # Linux Chinese
    "Source Han Sans SC",
    "sans-serif",
)
MONO = ("Cascadia Mono", "SF Mono", "Menlo", "Consolas", "Noto Sans Mono CJK SC", "monospace")

CSS = """
.ed-header h1 { margin-bottom: 0; letter-spacing: -0.02em; }
.ed-header p { margin-top: 0.25rem; opacity: 0.8; }
.ed-badge { display: inline-block; padding: 0.1rem 0.6rem; border-radius: 999px;
            background: var(--color-accent-soft); color: var(--color-accent); font-size: 0.85em; }
.ed-step { border-left: 3px solid var(--color-accent); padding-left: 0.75rem; }
footer { opacity: 0.6; }
"""


def make_theme(gr: ModuleType) -> Any:
    """Build the EraseDub theme. ``gr`` is the already imported ``gradio`` module."""
    return gr.themes.Soft(
        primary_hue="teal",
        secondary_hue="amber",
        neutral_hue="slate",
        radius_size="lg",
        font=SANS,
        font_mono=MONO,
    ).set(
        body_background_fill="*neutral_50",
        body_background_fill_dark="*neutral_950",
        block_title_text_weight="600",
        button_primary_background_fill="*primary_600",
        button_primary_background_fill_hover="*primary_500",
        button_primary_text_color="white",
    )


def launch_kwargs(gr: ModuleType) -> dict[str, Any]:
    """``theme`` and ``css`` for ``Blocks.launch()`` (Gradio 6 takes them there, not in ``Blocks()``).

    Anyone serving :func:`erasedub.webui.build_app` another way (a screenshot script,
    ``gr.mount_gradio_app``) should pass these too, or the app shows Gradio's default look.
    """
    return {"theme": make_theme(gr), "css": CSS}
