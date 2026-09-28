"""Web UI: layout builds without a server, handlers are wired to the planner, Gradio stays optional.

Tests marked ``webui`` need the ``webui`` extra and skip without it. Set ``ERASEDUB_REQUIRE_WEBUI=1`` (or
true/yes, like every EraseDub switch) to make them fail instead, as the CI job for this extra does.
Everything else runs on every install. No server is ever started: ``Blocks.launch`` is replaced where it
is called.
"""

import asyncio
import dataclasses
import importlib
import re
import string
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from erasedub import config, engine, hardware, pipeline, registry, script, webui
from erasedub.errors import CancelledError, EraseDubError, ProviderUnavailableError, ScriptFormatError
from erasedub.hardware import GpuInfo
from erasedub.links import missing_extra
from erasedub.providers import base as base_module
from erasedub.providers.base import Availability, Kind
from erasedub.providers.eraser_lama import LamaEraser
from erasedub.providers.eraser_propainter import ProPainterEraser
from erasedub.script import render_srt
from erasedub.webui import actions
from erasedub.webui.strings import EN, STRINGS, t

GPU = [GpuInfo(name="RTX 4090", memory_mib=24564, driver="580.1")]
VIDEO = "/tmp/clip.mp4"  # never opened: planning does not touch media, and runs use a fake engine
BASE = actions.UiOptions(targets=["vi"])


def ready(kind: Kind, name: str) -> Availability:
    return Availability.ready()


def not_ready(kind: Kind, name: str) -> Availability:
    return Availability(False, f"{name} is missing")


def _opts(**changes: Any) -> actions.UiOptions:
    return dataclasses.replace(BASE, **changes)


# --- Gradio is optional -------------------------------------------------------------------------------


def test_missing_gradio_raises_provider_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "gradio", None)  # makes `import gradio` raise ImportError
    with pytest.raises(ProviderUnavailableError, match=re.escape(missing_extra("webui")) + "$"):
        webui.build_app(config.Config())
    with pytest.raises(ProviderUnavailableError):
        webui.launch(config.Config())


def test_leftover_gradio_folder_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "gradio", ModuleType("gradio"))  # only runtime files left behind
    with pytest.raises(ProviderUnavailableError, match=r"uv sync --extra webui"):
        webui.build_app(config.Config())


def test_broken_gradio_dependency_shows_the_real_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(name: str) -> ModuleType:
        raise ModuleNotFoundError("No module named 'orjson'", name="orjson")

    monkeypatch.setattr(importlib, "import_module", broken)
    with pytest.raises(ProviderUnavailableError, match=r"import failed: No module named 'orjson'"):
        webui.build_app(config.Config())


def test_importing_erasedub_webui_does_not_import_gradio() -> None:
    code = (
        "import sys, erasedub.webui, erasedub.webui.actions, erasedub.webui.strings; "
        "sys.exit('gradio' in sys.modules)"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0  # noqa: S603 — fixed code


# --- Layout and handlers (need Gradio) ----------------------------------------------------------------


@pytest.fixture
def gr() -> ModuleType:
    try:
        module: ModuleType = pytest.importorskip("gradio")
        if not hasattr(module, "Blocks"):  # leftover folder of an uninstalled Gradio
            pytest.skip("gradio is not installed")
    except pytest.skip.Exception:
        if config.env_flag("ERASEDUB_REQUIRE_WEBUI"):
            pytest.fail("ERASEDUB_REQUIRE_WEBUI is set but gradio is not installed")
        raise
    return module


@pytest.fixture
def no_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware, "detect_nvidia_gpus", lambda: [])


@pytest.fixture
def ready_everywhere(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every optional extra counts as installed (handlers ask the real registry); ./work is in tmp_path."""
    monkeypatch.setattr(base_module, "module_available", lambda name: True)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> list[pipeline.VideoPlan]:
    """Replace the engine: record each plan; prepare writes one-line scripts, render reports the outputs."""
    calls: list[pipeline.VideoPlan] = []

    def execute(request: pipeline.VideoPlan, video: Path, **kwargs: Any) -> engine.Result:
        calls.append(request)
        kwargs["on_progress"] and kwargs["on_progress"](0.5, "working")
        kwargs["on_notice"]("a notice <from> the engine")
        langs = request.config.general.target_languages
        scripts, outputs = [], []
        if request.command in ("prepare", "run"):
            for lang in langs:
                line = script.ScriptLine(start=1, end=2, text=f"line in {lang}", source="src")
                script.save(script.Script(target_language=lang, lines=(line,)), request.work)
                scripts.append(script.srt_path(request.work, lang))
        if request.command in ("render", "run"):
            outputs = [pipeline.output_path(request.config, video, lang) for lang in langs]
        return engine.Result(scripts=tuple(scripts), outputs=tuple(outputs))

    monkeypatch.setattr(engine, "execute", execute)
    return calls


def build(cfg: config.Config) -> Any:
    """Build inside a running event loop: Gradio then reuses it instead of leaking new ones."""

    async def inner() -> Any:
        return webui.build_app(cfg)

    return asyncio.run(inner())


def block(app: Any, label: str) -> Any:
    return next(b for b in app.blocks.values() if getattr(b, "label", None) == label)


def handler(app: Any, name: str) -> Callable[..., Any]:
    fn: Callable[..., Any] = next(f.fn for f in app.fns.values() if getattr(f.fn, "__name__", "") == name)
    return fn


@pytest.mark.webui
def test_build_app_returns_blocks_without_launching(gr: ModuleType) -> None:
    app = build(config.Config())
    assert isinstance(app, gr.Blocks)
    labels = {getattr(b, "label", None) for b in app.blocks.values()}
    assert {EN["video"], EN["targets"], EN["script_language"], EN["script"], EN["result"]} <= labels
    buttons = {getattr(b, "value", None) for b in app.blocks.values() if isinstance(b, gr.Button)}
    assert {EN["prepare"], EN["render"]} <= buttons
    assert all(f.api_visibility == "private" for f in app.fns.values())


@pytest.mark.webui
def test_config_languages_outside_the_list_still_work(gr: ModuleType) -> None:
    cfg = config.from_mapping({"general": {"target_languages": ["it", "zh-TW"], "source_language": "it"}})
    app = build(cfg)
    targets = block(app, EN["targets"])
    assert targets.value == ["it", "zh-TW"]
    assert targets.preprocess(["it", "zh-TW"]) == ["it", "zh-TW"]  # raised "not in the list of choices"
    assert block(app, EN["source"]).preprocess("it") == "it"
    labels = {code: label for label, code in targets.choices}
    assert labels["it"] == "it · other"
    assert labels["zh-TW"] == "Chinese (zh-TW) · checked each release"


@pytest.mark.webui
def test_missing_music_file_is_a_notice_not_a_crash(gr: ModuleType, tmp_path: Path) -> None:
    app = build(config.from_mapping({"audio": {"music": str(tmp_path / "gone.mp3")}}))
    assert block(app, EN["music"]).value is None
    assert "gone.mp3" in block(app, EN["status"]).value


@pytest.mark.webui
def test_prepare_handler_passes_each_widget_to_its_option(
    gr: ModuleType, no_gpu: None, fake_engine: list[pipeline.VideoPlan], ready_everywhere: None
) -> None:
    on_prepare = handler(build(config.Config()), "on_prepare")
    #          video  targets  source  erase  gpu      translator  tts     voice   subs  original music
    options = (["en"], "auto", "off", "modal", "google", "edge", "auto", True, "keep", None)
    text, stored, rows = on_prepare(VIDEO, *options, {}, "en")
    assert text.startswith("**Scripts ready** (English)")
    (request,) = fake_engine
    assert request.command == "prepare"
    assert not request.plan.enabled("erase")  # erase and gpu swapped would give a config error instead
    assert request.config.translate.provider == "google"
    assert request.config.general.target_languages == ["en"]
    assert stored == {"en": [["00:00:01,000", "00:00:02,000", "line in en"]]}
    assert rows == stored["en"]


@pytest.mark.webui
def test_render_handler_validates_the_script_table(
    gr: ModuleType, no_gpu: None, fake_engine: list[pipeline.VideoPlan], ready_everywhere: None
) -> None:
    on_render = handler(build(config.Config()), "on_render")
    options = (["vi"], "auto", "off", "local", "google", "edge", "auto", False, "mute", None)
    bad = [["00:00:02,000", "00:00:01,000", "backwards"]]
    text, result, download, _, _ = on_render(VIDEO, *options, {}, "vi", bad)
    assert text.startswith("Script problem: Vietnamese, row 1:")
    assert (result, download, fake_engine) == (None, None, [])
    good = [["00:00:01,000", "00:00:02,000", "Xin chào"]]
    text, result, download, stored, rows = on_render(VIDEO, *options, {}, "vi", good)
    assert text.startswith("**Done:** 1 video(s) written.")
    assert result == str(Path(VIDEO).parent / "clip.vi.mp4")
    assert download == [result]
    assert [r.command for r in fake_engine] == ["render"]
    assert not fake_engine[0].plan.enabled("subtitles")
    assert stored == {"vi": good} and rows == good


@pytest.mark.webui
def test_cancel_button_is_not_queued(gr: ModuleType) -> None:
    app = build(config.Config())
    (wired,) = [f for f in app.fns.values() if getattr(f.fn, "__name__", "") == "on_cancel"]
    assert wired.queue is False
    assert handler(app, "on_cancel")() == t("nothing_running")


@pytest.mark.webui
def test_picking_a_gpu_turns_erase_off_into_auto(gr: ModuleType) -> None:
    """Erase starts at the config's "off"; a picked GPU must win over it, as --gpu does."""
    app = build(config.from_mapping({"erase": {"enabled": "off"}}))
    assert block(app, EN["erase"]).value == "off"
    assert block(app, EN["erase"]).info == EN["erase_info"]  # says Auto + a picked GPU means required
    on_gpu = handler(app, "on_gpu")
    assert on_gpu("modal", "off") == "auto"
    assert on_gpu("", "off") == "off"  # back to Default: nothing asks for erasing
    (wired,) = [f for f in app.fns.values() if getattr(f.fn, "__name__", "") == "on_gpu"]
    gpu, erase = block(app, EN["gpu"]), block(app, EN["erase"])
    assert [b._id for b in wired.inputs] == [gpu._id, erase._id]
    assert [b._id for b in wired.outputs] == [erase._id]


@pytest.mark.webui
def test_script_language_follows_the_targets(gr: ModuleType) -> None:
    on_targets = handler(build(config.Config()), "on_targets")
    update = on_targets(["en", "zh"], "vi")
    assert update.value == "en"
    assert [code for _, code in update.choices] == ["en", "zh"]


@pytest.mark.webui
def test_launch_uses_safe_defaults_and_the_theme(gr: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_launch(self: Any, **kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(gr.Blocks, "launch", fake_launch)

    async def inner() -> None:
        webui.launch(config.Config(), port=7999, open_browser=True)

    asyncio.run(inner())
    assert seen["server_name"] == "127.0.0.1"
    assert seen["server_port"] == 7999
    assert seen["share"] is False
    assert seen["mcp_server"] is False
    assert seen["inbrowser"] is True
    assert seen["max_file_size"] == webui.MAX_UPLOAD
    assert isinstance(seen["theme"], gr.themes.ThemeClass)
    assert seen["css"].strip()


@pytest.mark.webui
@pytest.mark.parametrize(
    ("host", "container", "warnings", "notes"),
    [
        ("127.0.0.1", "", 0, 0),
        ("0.0.0.0", "", 1, 0),  # noqa: S104
        ("0.0.0.0", "1", 0, 1),  # noqa: S104
    ],
)
def test_cli_webui_command_launches_the_real_ui(
    gr: ModuleType, monkeypatch: pytest.MonkeyPatch, host: str, container: str, warnings: int, notes: int
) -> None:
    from typer.testing import CliRunner

    from erasedub.cli import app as cli_app

    seen: dict[str, Any] = {}

    def fake_launch(self: Any, **kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(gr.Blocks, "launch", fake_launch)
    monkeypatch.setenv("ERASEDUB_IN_CONTAINER", container)

    async def inner() -> Any:
        return CliRunner().invoke(cli_app, ["webui", "--host", host, "--port", "7999", "--open"])

    result = asyncio.run(inner())
    assert result.exit_code == 0, result.output
    assert (seen["server_name"], seen["server_port"], seen["share"]) == (host, 7999, False)
    assert seen["inbrowser"] is True
    # The CLI prints the warning (or, in a container, the note); the web UI adds nothing.
    assert result.stderr.count("warning:") == warnings
    assert result.stderr.count("-p 127.0.0.1:7999:7999") == notes


# --- Planner wiring (plain functions) -----------------------------------------------------------------


@pytest.fixture
def base(tmp_path: Path) -> config.Config:
    """Default config with its work folder under tmp_path (``regions.json`` lives in ``<workdir>/clip``)."""
    return config.from_mapping({"general": {"workdir": str(tmp_path / "work")}})


def _regions(cfg: config.Config) -> None:
    """Pretend prepare found the on-screen text: planning only checks that regions.json exists."""
    from erasedub import regions

    path = regions.regions_path(cfg.general.workdir / Path(VIDEO).stem)
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")


def test_prepare_shows_the_plan(base: config.Config) -> None:
    out = actions.plan_step(base, VIDEO, _opts(), GPU, "prepare", availability=ready)
    assert "**Transcribe speech** · `whisperx`" in out
    assert "**Detect on-screen text** · `rapidocr`" in out
    assert "Mix audio" not in out  # render steps are listed by the render button


def test_render_without_gpu_explains_erase_is_skipped(base: config.Config) -> None:
    _regions(base)
    out = actions.plan_step(base, VIDEO, _opts(), [], "render", availability=ready)
    assert "skipped: no NVIDIA GPU found" in out
    assert "gpu-rental.md" in out
    assert t("script_missing", lang="Vietnamese") in out


SCRIPT = {"vi": [["00:00:01,000", "00:00:02,000", "Xin chào"]]}


def test_render_erases_only_with_regions_like_the_cli(base: config.Config) -> None:
    out = actions.plan_step(base, VIDEO, _opts(), GPU, "render", scripts=SCRIPT, availability=ready)
    assert "○ **Erase burned-in text** — skipped" in out
    assert "regions.json" in out
    _regions(base)
    out = actions.plan_step(base, VIDEO, _opts(), GPU, "render", scripts=SCRIPT, availability=ready)
    assert "✓ **Erase burned-in text** · `sttn` — on local GPU RTX 4090" in out
    assert t("regions_missing") not in out


def test_render_without_a_script_is_planned_like_run(base: config.Config) -> None:
    """Render prepares a missing script first, so it plans like `erasedub run`, OCR included."""
    out = actions.plan_step(base, VIDEO, _opts(), GPU, "render", availability=ready)
    assert t("script_missing", lang="Vietnamese") in out
    assert f"**{t('plan_prepare')}**" in out  # both phases are shown
    assert "✓ **Detect on-screen text** · `rapidocr`" in out
    assert "✓ **Erase burned-in text** · `sttn` — on local GPU RTX 4090" in out
    assert t("regions_missing") not in out  # prepare writes regions.json first


def test_picked_gpu_asks_for_erasing(base: config.Config) -> None:
    out = actions.plan_step(base, VIDEO, _opts(gpu="modal"), [], "render", scripts=SCRIPT, availability=ready)
    assert "remote GPU backend 'modal'" in out
    assert "gpu-rental" not in out
    assert t("regions_missing") in out  # erasing is required, so the missing regions.json is named
    assert f"**{t('plan_prepare')}**" not in out


def test_picked_gpu_wins_over_erasing_off_in_the_config(base: config.Config) -> None:
    off = base.model_copy(update={"erase": base.erase.model_copy(update={"enabled": "off"})})
    erase = actions.erase_after_gpu_pick("modal", off.erase.enabled)  # what the Erase radio shows then
    out = actions.plan_step(off, VIDEO, _opts(gpu="modal", erase=erase), [], "run", availability=ready)
    assert "✓ **Erase burned-in text** · `sttn` — on remote GPU backend 'modal'" in out


def test_erase_after_gpu_pick() -> None:
    assert actions.erase_after_gpu_pick("modal", "off") == "auto"
    assert actions.erase_after_gpu_pick("local", "on") == "on"
    assert actions.erase_after_gpu_pick("", "off") == "off"
    assert actions.erase_after_gpu_pick(None, "auto") == "auto"


def test_picked_gpu_that_cannot_run_is_an_error(base: config.Config) -> None:
    out = actions.plan_step(base, VIDEO, _opts(gpu="local"), [], "prepare", availability=ready)
    assert out.startswith("Cannot run with these settings:")
    assert "GPU backend 'local' was picked with the GPU option, which asks for erasing, but no NVIDIA" in out
    # The UI cannot "leave out" a radio button: its own wording says how to take the pick back.
    assert "or choose Default under GPU to erase only when it can run" in out
    assert "leave out" not in out
    assert "erase.enabled" not in out


def test_picked_gpu_without_an_eraser_is_an_error(base: config.Config) -> None:
    none = base.model_copy(update={"erase": base.erase.model_copy(update={"provider": "none"})})
    out = actions.plan_step(none, VIDEO, _opts(gpu="modal"), GPU, "prepare", availability=ready)
    assert out.startswith("Cannot run with these settings:")
    assert "erase.provider = 'none' names no eraser" in out
    assert out.endswith("or choose Default under GPU.")


def test_unknown_provider_in_the_config_is_an_error_like_the_cli(base: config.Config) -> None:
    out = actions.plan_step(base, VIDEO, _opts(tts="nope"), GPU, "prepare", availability=ready)
    assert out.startswith("Cannot run with these settings: tts.provider: no tts provider named 'nope'")


def test_default_gpu_that_cannot_run_is_only_a_notice(base: config.Config) -> None:
    _regions(base)
    out = actions.plan_step(base, VIDEO, _opts(gpu=""), [], "render", availability=ready)
    assert "Burned-in text will NOT be erased" in out


def test_erase_off_wins_over_a_picked_gpu(base: config.Config) -> None:
    out = actions.plan_step(base, VIDEO, _opts(gpu="local", erase="off"), [], "render", availability=ready)
    assert "○ **Erase burned-in text** — turned off" in out


def test_erase_on_without_gpu_is_reported_not_raised(base: config.Config) -> None:
    out = actions.plan_step(base, VIDEO, _opts(erase="on"), [], "render", availability=ready)
    assert out.startswith("Cannot run with these settings:")
    assert "erasing is set to 'on' but no NVIDIA GPU" in out
    assert "was picked with" not in out


def test_unready_providers_are_named_in_the_plan() -> None:
    out = actions.plan_step(
        config.Config(), VIDEO, _opts(translator="openai"), GPU, "prepare", availability=not_ready
    )
    assert "Translate: 'openai' is not ready here — openai is missing" in out
    assert "Transcribe speech: 'whisperx' is not ready here" in out


def test_unverified_target_is_flagged() -> None:
    out = actions.plan_step(
        config.Config(), VIDEO, _opts(targets=["vi", "ja", "it"]), GPU, "prepare", availability=ready
    )
    assert "Japanese, it: outside the languages checked for each release" in out
    assert "Vietnamese:" not in out


def test_missing_video_or_target() -> None:
    assert actions.plan_step(config.Config(), None, _opts(), GPU, "prepare") == t("need_video")
    assert actions.plan_step(config.Config(), VIDEO, _opts(targets=[]), GPU, "prepare") == t("need_target")


def test_render_summarises_each_language_script() -> None:
    scripts = {"vi": [["00:00:01,000", "00:00:02,000", "Xin chào"]], "en": []}
    out = actions.plan_step(
        config.Config(),
        VIDEO,
        _opts(targets=["vi", "en"]),
        GPU,
        "render",
        scripts=scripts,
        availability=ready,
    )
    assert t("script_ready", lang="Vietnamese", count=1) in out
    assert t("script_missing", lang="English") in out


def test_to_config_applies_every_option() -> None:
    cfg = actions.to_config(
        config.Config(),
        _opts(
            targets=["en", "zh-TW"],
            source="zh",
            erase="off",
            gpu="modal",
            translator="gemini",
            tts="elevenlabs",
            voice=" ",
            subtitles=False,
            original_audio="mute",
            music="/tmp/music.mp3",
        ),
    )
    assert cfg.general.target_languages == ["en", "zh-TW"]
    assert cfg.general.source_language == "zh"
    # The picked GPU is applied by the planner (pipeline.plan_video), with the CLI's --gpu rule.
    assert (cfg.erase.enabled, cfg.erase.gpu) == ("off", "local")
    assert (cfg.translate.provider, cfg.tts.provider, cfg.tts.voice) == ("gemini", "elevenlabs", "auto")
    assert not cfg.subtitles.enabled
    assert cfg.audio.original == "mute"
    assert cfg.audio.music == Path("/tmp/music.mp3")


def test_to_config_keeps_file_settings_the_ui_does_not_show() -> None:
    base = config.from_mapping({"translate": {"glossary": {"API": "API"}}, "audio": {"music_volume": 0.3}})
    cfg = actions.to_config(base, _opts())
    assert cfg.translate.glossary == {"API": "API"}
    assert cfg.audio.music_volume == 0.3


def test_language_choices_put_verified_first_and_add_config_codes() -> None:
    choices = actions.language_choices(["it", "vi", "zh_tw", "not a code"])
    codes = [code for _, code in choices]
    assert codes[:4] == ["en", "vi", "zh", "zh-TW"]
    assert all(label.endswith(" · checked each release") for label, _ in choices[:4])
    assert all(label.endswith(" · other") for label, _ in choices[4:])
    assert "it" in codes
    assert codes.count("vi") == 1


def test_source_choices_include_the_configured_source() -> None:
    choices = actions.source_choices("it")
    assert choices[0] == (EN["source_auto"], "auto")
    assert ("it", "it") in choices


def test_provider_choices_mark_unready_providers() -> None:
    choices = {name: label for label, name in actions.provider_choices("tts", "edge", availability=not_ready)}
    assert choices["edge"] == "edge (not ready here)"
    assert actions.provider_choices("tts", "not-installed", availability=ready)[0] == (
        "not-installed",
        "not-installed",
    )


def test_script_languages() -> None:
    assert actions.script_languages(["vi", "en"], "en") == ([("Vietnamese", "vi"), ("English", "en")], "en")
    assert actions.script_languages(["vi"], "en")[1] == "vi"
    assert actions.script_languages([], "en") == ([], None)


def test_scripts_are_stored_per_language() -> None:
    scripts = actions.remember_rows({}, "vi", [["00:00:01,000", "00:00:02,000", None]])
    scripts = actions.remember_rows(scripts, "en", [])
    assert actions.show_rows(scripts, "vi") == [["00:00:01,000", "00:00:02,000", ""]]
    assert actions.show_rows(scripts, "en") == []
    assert actions.show_rows(scripts, "zh") == []


# --- Script table <-> SRT -----------------------------------------------------------------------------


def _srt(rows: list[list[str | None]] | list[list[str]]) -> str:
    return render_srt(actions.script_lines(rows))


SRT = "1\n00:00:01,000 --> 00:00:02,500\nXin chào\n\n2\n00:00:03,000 --> 00:00:04,000\n你好\n"


def test_srt_round_trip_through_table() -> None:
    rows = actions.rows_from_srt(SRT)
    assert rows == [["00:00:01,000", "00:00:02,500", "Xin chào"], ["00:00:03,000", "00:00:04,000", "你好"]]
    assert _srt(rows) == SRT


def test_table_rows_tolerate_none_blank_rows_and_blank_lines_in_cells() -> None:
    rows: list[list[str | None]] = [
        [None, None, None],
        ["00:00:01,000", "00:00:02,000", "a\n\nb"],
        ["", "", ""],
    ]
    assert _srt(rows) == "1\n00:00:01,000 --> 00:00:02,000\na\nb\n"


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (["00:00:02,000", "00:00:01,000", "backwards"], "row 2: "),
        (["1.5", "00:00:02,000", "x"], "row 2: bad time '1.5'"),
        (
            ["00:00:01,000", "00:00:02,000", "00:00:05,000 --> 00:00:06,000"],
            "row 2: the text contains a timing",
        ),
        ([None, "00:00:02,000", "no start"], "row 2: bad time ''"),
    ],
)
def test_table_errors_name_the_row(row: list[str | None], message: str) -> None:
    rows: list[list[str | None]] = [["00:00:00,000", "00:00:00,500", "ok"], row]
    with pytest.raises(ScriptFormatError, match=message):
        actions.script_lines(rows)


def test_upload_and_export_srt_files(tmp_path: Path) -> None:
    src = tmp_path / "in.srt"
    src.write_text("﻿" + SRT, encoding="utf-8")
    rows, message = actions.load_srt_file(str(src), "vi")
    assert len(rows) == 2
    assert message == t("srt_loaded", count=2, lang="Vietnamese")

    path, message = actions.export_srt_file(rows, "vi")
    assert path is not None
    assert Path(path).name == "script.vi.srt"
    assert Path(path).read_text(encoding="utf-8") == SRT
    assert message == t("srt_exported", count=2, name="script.vi.srt")
    other, _ = actions.export_srt_file(rows, "vi")
    assert other != path  # two exports never overwrite each other
    assert actions.export_srt_file([], "vi") == (None, t("srt_empty"))


def test_utf16_srt_upload_is_accepted(tmp_path: Path) -> None:
    src = tmp_path / "in.srt"
    src.write_bytes(SRT.encode("utf-16"))
    rows, _ = actions.load_srt_file(str(src), "zh")
    assert rows[1][2] == "你好"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not a subtitle file\n", "Script problem: bad.srt:1: text before the first timing line"),
        (SRT.encode("gbk"), "Script problem: bad.srt: the file is not UTF-8 text"),
    ],
)
def test_bad_srt_upload_is_reported(tmp_path: Path, content: bytes, message: str) -> None:
    src = tmp_path / "bad.srt"
    src.write_bytes(content)
    rows, status = actions.load_srt_file(str(src), "vi")
    assert rows == []
    assert status.startswith(message)


def test_srt_upload_error_escapes_the_file_name(tmp_path: Path) -> None:
    src = tmp_path / "<img src=x>.srt"  # Gradio keeps the uploaded file's own name
    src.write_bytes(b"not a subtitle file\n")
    _, status = actions.load_srt_file(str(src), "vi")
    assert "&lt;img src=x&gt;.srt:1:" in status
    assert "<img" not in status


def test_too_big_srt_upload_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "big.srt"
    src.write_text(SRT, encoding="utf-8")
    monkeypatch.setattr(actions, "MAX_SRT_BYTES", len(SRT.encode()) - 1)
    rows, status = actions.load_srt_file(str(src), "vi")
    assert rows == []
    assert status == t("srt_too_big", limit=0)


# --- Same inputs, same plan: the CLI and the web UI -----------------------------------------------------


def _first_sentence(text: str) -> str:
    return " ".join(text.split()).split(". ")[0]


def _cli_erase(output: str, exit_code: int) -> tuple[str, str]:
    """``yes``/``no`` from the CLI's erase row, or ``error`` and the error's first sentence when it stops
    before showing a plan."""
    for line in output.splitlines():
        cells = line.split()
        if len(cells) > 3 and cells[1] == "erase":
            return cells[3], ""
    assert exit_code in (2, 3), output
    return "error", _first_sentence(output.split("error:", 1)[1])


def _ui_erase(out: str) -> str:
    if out.startswith("Cannot run with these settings"):
        return "error"
    return "yes" if "✓ **Erase burned-in text**" in out else "no"


OFF = '[erase]\nenabled = "off"\n'
ON = '[erase]\nenabled = "on"\n'
LAMA = '[erase]\nprovider = "lama"\n'
PARITY = [
    # (config, CLI arguments, UI clicks after the page loaded, has a script, regions.json exists, NVIDIA GPU)
    ("", ["run"], {}, False, False, True),  # UI Render without a script = run
    (OFF, ["run", "--gpu", "modal"], {"gpu": "modal"}, False, False, True),  # a picked GPU beats config off
    (OFF, ["render", "--gpu", "local"], {"gpu": "local"}, True, True, True),
    ("", ["run", "--gpu", "modal", "--no-erase"], {"gpu": "modal", "erase": "off"}, False, False, True),
    ("", ["render"], {}, True, False, True),
    ("", ["render"], {}, True, True, True),
    ("", ["run"], {}, False, True, True),
    ('[erase]\nprovider = "none"\n', ["run", "--gpu", "modal"], {"gpu": "modal"}, False, False, True),
    ('[erase]\nenabled = "on"\ngpu = "cloud"\n', ["run"], {}, False, False, True),
    ('[erase]\ngpu = "modal"\n', ["render"], {}, True, True, True),
    # No NVIDIA GPU on this machine
    ("", ["run"], {}, False, False, False),  # auto: skipped with a notice
    ("", ["render", "--gpu", "local"], {"gpu": "local"}, True, True, False),
    ("", ["run", "--gpu", "local"], {"gpu": "local"}, False, False, False),
    (OFF, ["run", "--gpu", "local"], {"gpu": "local"}, False, False, False),
    ("", ["run", "--gpu", "modal"], {"gpu": "modal"}, False, False, False),  # remote: no local GPU needed
    (ON, ["run"], {}, False, False, False),
    (ON, ["run", "--no-erase"], {"erase": "off"}, False, False, False),
    # lama needs no NVIDIA GPU: it erases on the CPU
    (LAMA, ["run"], {}, False, False, False),
    (LAMA, ["run"], {}, False, False, True),
    (LAMA + 'enabled = "on"\n', ["render"], {}, True, True, False),
    (LAMA, ["run", "--gpu", "local"], {"gpu": "local"}, False, False, False),
    (LAMA, ["run", "--gpu", "modal"], {"gpu": "modal"}, False, False, False),
    ('[erase]\nprovider = "propainter"\n', ["run"], {}, False, False, False),  # skipped, suggests lama
    ('[erase]\nprovider = "propainter"\n', ["run", "--gpu", "local"], {"gpu": "local"}, False, False, False),
]


def _plan_both(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    toml: str,
    args: list[str],
    ui: dict[str, str],
    has_script: bool,
    has_regions: bool,
    has_gpu: bool,
) -> tuple[tuple[str, str], str, str]:
    """Plan the same inputs with the CLI and the web UI: (CLI erase result, CLI output, UI output)."""
    from rich.console import Console
    from typer.testing import CliRunner

    from erasedub import cli, regions, script
    from erasedub.providers import base as provider_base
    from erasedub.providers import gpu_local

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MODAL_TOKEN_ID", "id-for-tests")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "secret-for-tests")
    gpus = GPU if has_gpu else []
    monkeypatch.setattr(hardware, "detect_nvidia_gpus", lambda *a, **k: gpus)
    monkeypatch.setattr(gpu_local, "detect_nvidia_gpus", lambda *a, **k: gpus)
    ffmpeg = hardware.FfmpegInfo("/usr/bin/ffmpeg", "7.1", True, True, True, "/usr/bin/ffprobe")
    monkeypatch.setattr(hardware, "detect_ffmpeg", lambda *a, **k: ffmpeg)
    monkeypatch.setattr(provider_base, "module_available", lambda name: True)
    monkeypatch.setattr(cli, "out", Console(highlight=False, width=300))
    monkeypatch.setattr(cli, "err", Console(stderr=True, highlight=False, width=300))
    Path(config.CONFIG_NAME).write_text(toml, encoding="utf-8")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    work = Path("work/clip")
    work.mkdir(parents=True)
    if has_script:
        line = script.ScriptLine(start=1, end=2, text="Xin chào")
        script.save(script.Script(target_language="vi", lines=(line,)), work)
    if has_regions:
        regions.regions_path(work).write_text("{}", encoding="utf-8")

    command, *cli_options = args
    result = CliRunner().invoke(cli.app, [command, str(video), *cli_options, "--dry-run"])
    cli_says = _cli_erase(result.output, result.exit_code)
    rows = {"vi": [["00:00:01,000", "00:00:02,000", "Xin chào"]]} if has_script else {}
    cfg = config.load()
    gpu = ui.get("gpu", "")
    # Erase starts at the config's value and follows a GPU pick (app.py on_gpu), unless clicked after it.
    erase = ui.get("erase", actions.erase_after_gpu_pick(gpu, cfg.erase.enabled))
    options = _opts(gpu=gpu, erase=erase)
    # `run` and `render` are both the Render button: without a script it prepares first.
    out = actions.plan_step(
        cfg, str(video), options, gpus, "render", scripts=rows, availability=registry.availability
    )
    return cli_says, result.output, out


@pytest.mark.parametrize(("toml", "args", "ui", "has_script", "has_regions", "has_gpu"), PARITY)
def test_cli_and_web_ui_plan_erasing_the_same_way(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    toml: str,
    args: list[str],
    ui: dict[str, str],
    has_script: bool,
    has_regions: bool,
    has_gpu: bool,
) -> None:
    """Both front ends call pipeline.plan_video, so the CLI and the web UI must plan every case alike."""
    cli_says, _, out = _plan_both(tmp_path, monkeypatch, toml, args, ui, has_script, has_regions, has_gpu)
    assert _ui_erase(out) == cli_says[0], out
    if cli_says[1]:  # same error, apart from the words for the option and how to take it back
        ui_error = out.removeprefix("Cannot run with these settings: ").replace("the GPU option", "--gpu")
        assert _first_sentence(ui_error) == cli_says[1]


def _flat(text: str) -> str:
    return " ".join(text.split())


PROPAINTER = '[erase]\nprovider = "propainter"\n'
NOTICES = [
    # (config, CLI arguments, UI clicks, NVIDIA GPU, texts both front ends must show)
    (PROPAINTER, ["run"], {}, True, [ProPainterEraser.notice]),
    (PROPAINTER, ["run"], {}, False, [pipeline.CPU_ERASER_HINT, ProPainterEraser.notice]),
    (PROPAINTER, ["run", "--gpu", "modal"], {"gpu": "modal"}, False, [ProPainterEraser.notice]),
    (LAMA, ["run"], {}, False, [LamaEraser.notice]),
    (LAMA, ["run"], {}, True, [LamaEraser.notice]),
    ("", ["run"], {}, False, [pipeline.CPU_ERASER_HINT]),
]


@pytest.mark.parametrize(("toml", "args", "ui", "has_gpu", "notices"), NOTICES)
def test_cli_and_web_ui_show_the_same_eraser_notices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    toml: str,
    args: list[str],
    ui: dict[str, str],
    has_gpu: bool,
    notices: list[str],
) -> None:
    """The licence, weakness and "try lama" notices come from plan_video, so both front ends show them."""
    _, cli_out, ui_out = _plan_both(tmp_path, monkeypatch, toml, args, ui, False, False, has_gpu)
    for text in notices:
        assert _flat(text) in _flat(cli_out), cli_out
        assert _flat(text) in _flat(ui_out), ui_out


# --- Strings ------------------------------------------------------------------------------------------


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_locale_matches_the_english_keys_and_placeholders() -> None:
    for locale, table in STRINGS.items():
        assert set(table) <= set(EN), locale
        for key, text in table.items():
            assert _fields(text) == _fields(EN[key]), f"{locale}:{key}"
    assert t("need_video", locale="xx") == EN["need_video"]


def test_every_language_and_step_has_a_label() -> None:
    from erasedub.languages import LISTED_LANGUAGES

    assert all(f"lang.{code}" in EN for code in LISTED_LANGUAGES)
    plan_steps = ("extract-audio", "transcribe", "detect-text", "translate", "erase", "speak", "subtitles")
    assert all(f"step.{name}" in EN for name in (*plan_steps, "mix-and-mux"))


# --- Running the engine (plain functions, fake engine) ---------------------------------------------------


def test_prepare_runs_the_engine_and_fills_the_table(
    base: config.Config, fake_engine: list[pipeline.VideoPlan]
) -> None:
    seen: list[float] = []
    done = actions.prepare_video(
        base,
        VIDEO,
        _opts(targets=["vi", "en"]),
        GPU,
        availability=ready,
        on_progress=lambda f, m: seen.append(f),
    )
    assert [r.command for r in fake_engine] == ["prepare"]
    assert done.scripts == {
        "vi": [["00:00:01,000", "00:00:02,000", "line in vi"]],
        "en": [["00:00:01,000", "00:00:02,000", "line in en"]],
    }
    assert done.status.startswith("**Scripts ready** (Vietnamese, English)")
    assert "> ⚠ a notice &lt;from&gt; the engine" in done.status  # notices are escaped for Markdown
    assert "script.en.srt" in done.status
    assert seen == [0.5]


def test_nothing_starts_while_a_provider_is_not_ready(
    base: config.Config, fake_engine: list[pipeline.VideoPlan]
) -> None:
    done = actions.prepare_video(base, VIDEO, _opts(), GPU, availability=not_ready)
    assert done.status.rstrip().endswith(t("cannot_start"))
    assert "is not ready here" in done.status
    assert (done.scripts, fake_engine) == (None, [])
    done = actions.render_video(base, VIDEO, _opts(), GPU, scripts=SCRIPT, availability=not_ready)
    assert done.status.rstrip().endswith(t("cannot_start"))
    assert fake_engine == []


def test_render_needs_regions_before_it_erases(
    base: config.Config, fake_engine: list[pipeline.VideoPlan]
) -> None:
    done = actions.render_video(base, VIDEO, _opts(erase="on"), GPU, scripts=SCRIPT, availability=ready)
    assert t("regions_missing") in done.status
    assert fake_engine == []


def test_render_saves_the_table_and_prepares_missing_languages_first(
    base: config.Config, fake_engine: list[pipeline.VideoPlan]
) -> None:
    seen: list[float] = []
    done = actions.render_video(
        base,
        VIDEO,
        _opts(targets=["vi", "en"], erase="off"),
        GPU,
        scripts=SCRIPT,
        availability=ready,
        on_progress=lambda f, m: seen.append(f),
    )
    prepare, render = fake_engine
    assert (prepare.command, prepare.config.general.target_languages) == ("prepare", ["en"])
    assert (render.command, render.config.general.target_languages) == ("render", ["vi", "en"])
    work = prepare.work
    vi = script.load_json(script.json_path(work, "vi"))  # typed into the table: saved as a new script
    assert [line.text for line in vi.lines] == ["Xin chào"]
    assert done.scripts is not None and done.scripts["en"][0][2] == "line in en"
    assert done.videos == (str(Path(VIDEO).parent / "clip.vi.mp4"), str(Path(VIDEO).parent / "clip.en.mp4"))
    assert done.status.startswith("**Done:** 2 video(s) written.")
    assert seen == [pytest.approx(actions.PREPARE_SHARE / 2), pytest.approx((1 + actions.PREPARE_SHARE) / 2)]


def test_render_keeps_the_prepared_script_and_writes_only_the_srt(
    base: config.Config, fake_engine: list[pipeline.VideoPlan]
) -> None:
    work = pipeline.work_folder(base, Path(VIDEO))
    line = script.ScriptLine(start=1, end=2, text="cũ", source="旧", speaker="A")
    script.save(script.Script(source_language="zh", target_language="vi", lines=(line,)), work)
    before = script.json_path(work, "vi").read_bytes()
    done = actions.render_video(base, VIDEO, _opts(erase="off"), GPU, scripts=SCRIPT, availability=ready)
    assert done.videos
    assert script.json_path(work, "vi").read_bytes() == before  # source text and speaker stay
    assert "Xin chào" in script.srt_path(work, "vi").read_text(encoding="utf-8")
    # With no rows in the table, the saved script is used as it is.
    out = actions.plan_step(base, VIDEO, _opts(erase="off"), GPU, "render", scripts={}, availability=ready)
    assert t("script_saved", lang="Vietnamese") in out


def test_engine_errors_are_reported_with_the_notices_so_far(
    base: config.Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def execute(request: pipeline.VideoPlan, video: Path, **kwargs: Any) -> engine.Result:
        kwargs["on_notice"]("voice lines run long")
        raise EraseDubError("ffmpeg failed while writing clip.vi.mp4 (exit code 1): <bad>")

    monkeypatch.setattr(engine, "execute", execute)
    done = actions.render_video(base, VIDEO, _opts(erase="off"), GPU, scripts=SCRIPT, availability=ready)
    assert done.status.startswith(
        "**Failed:** ffmpeg failed while writing clip.vi.mp4 (exit code 1): &lt;bad&gt;"
    )
    assert "> ⚠ voice lines run long" in done.status
    assert done.videos == ()


def test_cancel_stops_the_running_job(base: config.Config, monkeypatch: pytest.MonkeyPatch) -> None:
    def execute(request: pipeline.VideoPlan, video: Path, **kwargs: Any) -> engine.Result:
        assert not kwargs["is_cancelled"]()
        assert actions.cancel_all() == t("cancelling")
        assert kwargs["is_cancelled"]()
        raise CancelledError("cancelled by the user")

    monkeypatch.setattr(engine, "execute", execute)
    done = actions.prepare_video(base, VIDEO, _opts(), GPU, availability=ready)
    assert done.status.startswith(t("cancelled"))
    assert actions.cancel_all() == t("nothing_running")  # the finished job left the registry
