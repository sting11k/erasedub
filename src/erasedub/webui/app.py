"""Gradio layout of the web UI. Imported only after Gradio is known to be installed."""

from __future__ import annotations

import html
from collections.abc import Sequence
from types import ModuleType
from typing import Any

from erasedub import __version__, hardware
from erasedub.config import Config
from erasedub.webui import actions
from erasedub.webui.strings import t

#: Uploaded files older than this are removed from Gradio's cache (checked every hour).
DELETE_CACHE = (3600, 86400)


def build(gr: ModuleType, config: Config, *, locale: str) -> Any:
    """Create the ``gr.Blocks`` app. No server is started here; the buttons run the engine."""

    def tr(key: str, **values: object) -> str:
        return t(key, locale, **values)

    music_value = None
    idle = tr("status_idle")
    if config.audio.music is not None:
        if config.audio.music.is_file():
            music_value = str(config.audio.music)
        else:  # Gradio would crash on a missing initial file; say so instead
            idle += "\n\n> ⚠ " + tr("music_missing", name=html.escape(config.audio.music.name))

    targets_value = list(config.general.target_languages)
    lang_choices, lang_value = actions.script_languages(targets_value, None, locale=locale)

    with gr.Blocks(title=tr("title"), analytics_enabled=False, delete_cache=DELETE_CACHE) as app:
        with gr.Column(elem_classes="ed-header"):
            gr.Markdown(f"# {tr('title')}\n{tr('tagline')}")
            gr.HTML(f'<span class="ed-badge">{html.escape(tr("version", version=__version__))}</span>')

        with gr.Row(equal_height=False):
            with gr.Column(scale=5):
                video = gr.Video(label=tr("video"), sources=["upload"])
                targets = gr.CheckboxGroup(
                    choices=actions.language_choices(targets_value, locale=locale),
                    value=targets_value,
                    label=tr("targets"),
                    info=tr("targets_info"),
                )
                source = gr.Dropdown(
                    choices=actions.source_choices(config.general.source_language, locale=locale),
                    value=config.general.source_language or "auto",
                    label=tr("source"),
                )
                with gr.Accordion(tr("options"), open=True):
                    with gr.Row():
                        erase = gr.Radio(
                            choices=[
                                (tr("erase_auto"), "auto"),
                                (tr("erase_on"), "on"),
                                (tr("erase_off"), "off"),
                            ],
                            value=config.erase.enabled,
                            label=tr("erase"),
                            info=tr("erase_info"),
                        )
                        gpu = gr.Radio(
                            choices=[
                                (tr("gpu_default", name=config.erase.gpu), ""),
                                (tr("gpu_local"), "local"),
                                (tr("gpu_modal"), "modal"),
                            ],
                            value="",
                            label=tr("gpu"),
                            info=tr("gpu_info"),
                        )
                    translator = gr.Dropdown(
                        choices=actions.provider_choices(
                            "translator", config.translate.provider, locale=locale
                        ),
                        value=config.translate.provider,
                        label=tr("translator"),
                    )
                    with gr.Row():
                        tts = gr.Dropdown(
                            choices=actions.provider_choices("tts", config.tts.provider, locale=locale),
                            value=config.tts.provider,
                            label=tr("tts"),
                        )
                        voice = gr.Textbox(value=config.tts.voice, label=tr("voice"), info=tr("voice_info"))
                    with gr.Row():
                        subtitles = gr.Checkbox(value=config.subtitles.enabled, label=tr("subtitles"))
                        original = gr.Radio(
                            choices=[(tr("original_keep"), "keep"), (tr("original_mute"), "mute")],
                            value=config.audio.original,
                            label=tr("original_audio"),
                        )
                    music = gr.File(
                        label=tr("music"), file_types=["audio"], type="filepath", value=music_value
                    )

            with gr.Column(scale=7):
                with gr.Group(elem_classes="ed-step"):
                    gr.Markdown(f"### {tr('step1')}")
                    prepare_btn = gr.Button(tr("prepare"), variant="primary")
                    script_lang = gr.Dropdown(
                        choices=lang_choices, value=lang_value, label=tr("script_language")
                    )
                    script = gr.Dataframe(
                        headers=[tr("col_start"), tr("col_end"), tr("col_text")],
                        datatype=["str", "str", "str"],
                        type="array",
                        interactive=True,
                        wrap=True,
                        column_widths=["18%", "18%", "64%"],
                        label=tr("script"),
                    )
                    with gr.Row():
                        srt_upload = gr.UploadButton(tr("srt_upload"), file_types=[".srt"], type="filepath")
                        srt_export = gr.Button(tr("srt_export"))
                    srt_file = gr.File(label=tr("srt_file"), interactive=False)
                with gr.Group(elem_classes="ed-step"):
                    gr.Markdown(f"### {tr('step2')}")
                    with gr.Row():
                        render_btn = gr.Button(tr("render"), variant="primary", scale=3)
                        cancel_btn = gr.Button(tr("cancel"), variant="stop", scale=1)
                status = gr.Markdown(idle, label=tr("status"), container=True)
                result = gr.Video(label=tr("result"), interactive=False)
                download = gr.File(label=tr("download"), interactive=False, file_count="multiple")

        #: Script rows per target language; the table shows the one picked in *Script language*.
        scripts = gr.State({})
        option_inputs = [targets, source, erase, gpu, translator, tts, voice, subtitles, original, music]

        def collect(values: Sequence[Any]) -> actions.UiOptions:
            tg, src, er, gp, tn, tt, vo, sb, og, mu = values
            return actions.UiOptions(
                targets=tg or [],
                source=src or "auto",
                erase=er,
                gpu=gp or "",
                translator=tn,
                tts=tt,
                voice=vo or "auto",
                subtitles=bool(sb),
                original_audio=og,
                music=mu,
            )

        def reporter(progress: Any) -> actions.ProgressFn:
            def report(fraction: float, message: str) -> None:
                progress(fraction, desc=message)

            return report

        # hardware.detect_nvidia_gpus() asks nvidia-smi once per process: restart the UI after adding a GPU.
        def on_prepare(
            video_path: str | None,
            tg: Any,
            src: Any,
            er: Any,
            gp: Any,
            tn: Any,
            tt: Any,
            vo: Any,
            sb: Any,
            og: Any,
            mu: Any,
            stored: actions.Scripts | None,
            lang: str | None,
            progress: Any = gr.Progress(),  # noqa: B008 - Gradio reads the default to inject the tracker
        ) -> tuple[str, actions.Scripts, list[actions.Row]]:
            done = actions.prepare_video(
                config,
                video_path,
                collect((tg, src, er, gp, tn, tt, vo, sb, og, mu)),
                hardware.detect_nvidia_gpus(),
                locale=locale,
                on_progress=reporter(progress),
            )
            current = done.scripts if done.scripts is not None else dict(stored or {})
            return done.status, current, actions.show_rows(current, lang)

        def on_render(
            video_path: str | None,
            tg: Any,
            src: Any,
            er: Any,
            gp: Any,
            tn: Any,
            tt: Any,
            vo: Any,
            sb: Any,
            og: Any,
            mu: Any,
            stored: actions.Scripts | None,
            lang: str | None,
            rows: Any,
            progress: Any = gr.Progress(),  # noqa: B008 - Gradio reads the default to inject the tracker
        ) -> tuple[str, str | None, list[str] | None, actions.Scripts, list[actions.Row]]:
            current = actions.remember_rows(stored, lang, rows)
            done = actions.render_video(
                config,
                video_path,
                collect((tg, src, er, gp, tn, tt, vo, sb, og, mu)),
                hardware.detect_nvidia_gpus(),
                scripts=current,
                locale=locale,
                on_progress=reporter(progress),
            )
            if done.scripts is not None:
                current = done.scripts
            first = done.videos[0] if done.videos else None
            return done.status, first, list(done.videos) or None, current, actions.show_rows(current, lang)

        def on_cancel() -> str:
            return actions.cancel_all(locale=locale)

        def on_gpu(picked: str | None, current: str) -> str:
            return actions.erase_after_gpu_pick(picked, current)

        def on_targets(selected: list[str] | None, lang: str | None) -> Any:
            choices, value = actions.script_languages(selected, lang, locale=locale)
            return gr.Dropdown(choices=choices, value=value)

        def on_script_language(stored: actions.Scripts, lang: str | None) -> list[actions.Row]:
            return actions.show_rows(stored, lang)

        def on_table(stored: actions.Scripts, lang: str | None, rows: Any) -> actions.Scripts:
            return actions.remember_rows(stored, lang, rows)

        def on_srt_upload(path: str | None, lang: str | None) -> tuple[list[actions.Row], str]:
            return actions.load_srt_file(path, lang, locale=locale)

        def on_srt_export(rows: Any, lang: str | None) -> tuple[str | None, str]:
            return actions.export_srt_file(rows, lang, locale=locale)

        # The handlers are for this page only, not a public API (and never MCP tools).
        private: dict[str, Any] = {"api_visibility": "private"}
        prepare_btn.click(
            on_prepare,
            inputs=[video, *option_inputs, scripts, script_lang],
            outputs=[status, scripts, script],
            **private,
        )
        render_btn.click(
            on_render,
            inputs=[video, *option_inputs, scripts, script_lang, script],
            outputs=[status, result, download, scripts, script],
            **private,
        )
        # Not queued: it must get through while Prepare or Render holds the queue.
        cancel_btn.click(on_cancel, outputs=status, queue=False, **private)
        gpu.change(on_gpu, inputs=[gpu, erase], outputs=erase, **private)
        targets.change(on_targets, inputs=[targets, script_lang], outputs=script_lang, **private)
        script_lang.change(on_script_language, inputs=[scripts, script_lang], outputs=script, **private)
        script.change(on_table, inputs=[scripts, script_lang, script], outputs=scripts, **private)
        srt_upload.upload(
            on_srt_upload, inputs=[srt_upload, script_lang], outputs=[script, status], **private
        )
        srt_export.click(on_srt_export, inputs=[script, script_lang], outputs=[srt_file, status], **private)
    return app
