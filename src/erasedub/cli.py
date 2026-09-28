"""Command-line interface: ``prepare`` / ``render`` / ``run`` / ``webui`` / ``doctor`` / ``plugins``.

The contract is ``docs/cli.md``. Output is plain ASCII apart from file names and provider messages, and every
file name or message is markup-escaped, so names like ``Episode 3 [eng sub].mp4`` print as they are.
"""

from __future__ import annotations

import contextlib
import functools
import importlib
import importlib.metadata
import ipaddress
import os
import platform
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any, NoReturn, ParamSpec, TypeVar

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Column, Table

from erasedub import __version__, config, engine, hardware, languages, pipeline, regions, registry, script
from erasedub.errors import (
    CancelledError,
    ConfigError,
    EraseDubError,
    ProviderUnavailableError,
    ScriptFormatError,
)
from erasedub.hardware import FfmpegInfo, GpuInfo
from erasedub.links import doc, install_extra, missing_extra
from erasedub.pipeline import Command, Plan, Step
from erasedub.providers.base import Availability, Kind, module_available

app = typer.Typer(
    name="erasedub",
    help="Remove burned-in subtitles, translate and dub videos locally.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    # An unexpected traceback must never print local variables: they can hold config data or API clients.
    pretty_exceptions_show_locals=False,
    pretty_exceptions_short=True,
)
out = Console(highlight=False)
err = Console(stderr=True, highlight=False)

# API keys EraseDub may use. `doctor` only ever reports whether each one is set.
KNOWN_ENV_KEYS = (
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ELEVENLABS_API_KEY",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "HF_TOKEN",
)
# Optional packages whose versions `doctor` reports (read from metadata, never imported).
EXTRA_DISTRIBUTIONS = (
    "whisperx",
    "rapidocr",
    "onnxruntime",
    "torch",
    "torchvision",
    "opencv-python",
    "kornia",
    "gradio",
    "modal",
    "openai",
    "google-genai",
    "anthropic",
    "ollama",
    "httpx",
)
# Config field set by each command-line option, for error messages that name the option.
OPTION_LABELS: dict[str, str] = {
    "general.target_languages": "--to",
    "general.source_language": "--from",
    "general.workdir": "--workdir",
    "general.output_dir": "--out",
    "erase.gpu": "--gpu",
    "tts.enabled": "--no-voice",
    "subtitles.enabled": "--no-subs",
    "audio.music": "--music",
}
TORCH_CPU_ONLY_HELP = doc("troubleshooting.md#doctor-says-torch-is-a-cpu-only-build-typical-on-windows")

P = ParamSpec("P")
R = TypeVar("R")


def _fail(message: str, code: int) -> NoReturn:
    err.print(f"[bold red]error:[/] {escape(message)}")
    raise typer.Exit(code)


def _handle_errors(func: Callable[P, R]) -> Callable[P, R]:
    """Print expected errors as one clean line and exit with the error's code (Ctrl+C -> 130)."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except CancelledError as exc:
            err.print(f"[yellow]cancelled[/] {escape(str(exc))}".rstrip())
            raise typer.Exit(exc.exit_code) from exc
        except EraseDubError as exc:
            _fail(str(exc), exc.exit_code)
        except KeyboardInterrupt as exc:
            err.print("[yellow]cancelled[/]")
            raise typer.Exit(CancelledError.exit_code) from exc

    return wrapper


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


ConfigOpt = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        help="Config file (default: ./erasedub.toml if present). Wins over one given before the command.",
    ),
]
# `plugins` and `version` read no config; they accept -c anyway, so it works after every command.
UnusedConfigOpt = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Accepted so -c works after every command; not used here."),
]


@app.callback()
def _root(
    ctx: typer.Context,
    config_file: ConfigOpt = None,
    version: Annotated[
        bool,
        typer.Option(
            "--version", help="Print the version and exit.", callback=_version_callback, is_eager=True
        ),
    ] = False,
) -> None:
    """Remove burned-in subtitles, translate and dub videos locally."""
    ctx.obj = config_file


# ---------------------------------------------------------------- option types

VideoArg = Annotated[
    Path, typer.Argument(exists=True, dir_okay=False, readable=True, help="Input video file.")
]
ToOpt = Annotated[
    str | None,
    typer.Option("--to", "-t", help="Target language(s), comma-separated, e.g. vi or vi,en,zh."),
]
FromOpt = Annotated[
    str | None, typer.Option("--from", "-f", help="Language spoken in the video, or 'auto' (default).")
]
WorkdirOpt = Annotated[
    Path | None,
    typer.Option("--workdir", file_okay=False, help="Folder for per-video working files (default: ./work)."),
]
DryRunOpt = Annotated[
    bool,
    typer.Option("--dry-run", help="Check the setup and show the plan, then exit without touching media."),
]
GpuOpt = Annotated[
    str | None,
    typer.Option(
        "--gpu",
        help="Where erasing runs: local or modal (your own Modal account). Giving it asks for erasing: "
        "if that backend cannot run, the command stops (exit 3) instead of skipping erasing.",
    ),
]
NoEraseOpt = Annotated[bool, typer.Option("--no-erase", help="Keep the original picture.")]
NoVoiceOpt = Annotated[bool, typer.Option("--no-voice", help="Do not generate a dubbed voice.")]
NoSubsOpt = Annotated[bool, typer.Option("--no-subs", help="Do not burn in subtitles.")]
MusicOpt = Annotated[
    Path | None,
    typer.Option("--music", exists=True, dir_okay=False, help="Background music file to mix in."),
]
OutOpt = Annotated[
    Path | None,
    typer.Option("--out", "-o", file_okay=False, help="Output folder (default: next to the input video)."),
]


# ---------------------------------------------------------------- configuration


def _overrides(
    *,
    to: str | None = None,
    source: str | None = None,
    workdir: Path | None = None,
    out_dir: Path | None = None,
    no_voice: bool = False,
    no_subs: bool = False,
    music: Path | None = None,
) -> dict[str, Any]:
    """Command-line options as dotted config fields; options that were not given are left out.

    ``--gpu`` and ``--no-erase`` are not here: :func:`erasedub.pipeline.plan_video` applies them, with the
    same rule as the web UI.
    """
    values: dict[str, Any] = {}
    if to is not None:
        codes = [code.strip() for code in to.split(",") if code.strip()]
        if not codes:
            raise ConfigError("--to: empty value; give language codes such as vi or vi,en")
        values["general.target_languages"] = codes
    if source is not None:
        if not source.strip():
            raise ConfigError("--from: empty value; give a language code such as zh, or auto")
        values["general.source_language"] = source.strip()
    if no_voice:
        values["tts.enabled"] = False
    if no_subs:
        values["subtitles.enabled"] = False
    for field, path in (
        ("general.workdir", workdir),
        ("general.output_dir", out_dir),
        ("audio.music", music),
    ):
        if path is not None:
            values[field] = path
    return values


def _config_path(ctx: typer.Context, config_file: Path | None) -> Path | None:
    """``--config`` after the command wins over ``--config`` before it."""
    root = ctx.obj if isinstance(ctx.obj, Path) else None
    return config_file or root


def _load(
    ctx: typer.Context, config_file: Path | None, overrides: Mapping[str, Any] | None = None
) -> config.Config:
    return config.load(_config_path(ctx, config_file), overrides=overrides, labels=OPTION_LABELS)


# ---------------------------------------------------------------- readiness

# Step name -> provider kind and the config section holding its options.
_STEP_KINDS: dict[str, tuple[Kind, str]] = {
    "transcribe": ("asr", "asr"),
    "detect-text": ("ocr", "ocr"),
    "translate": ("translator", "translate"),
    "erase": ("eraser", "erase"),
    "speak": ("tts", "tts"),
    "subtitles": ("layout", "subtitles"),
}


def _ffmpeg_ready(step: str, ff: FfmpegInfo, cfg: config.Config) -> Availability:
    fix = f"install ffmpeg or set {hardware.FFMPEG_ENV} ({doc('troubleshooting.md')})"
    if ff.path is None:
        return Availability(False, f"ffmpeg not found - {fix}")
    missing = []
    if not ff.has_ffprobe:
        missing.append("ffprobe (install it next to ffmpeg)")
    if step == "mix-and-mux" and not ff.has_libx264:
        missing.append("an ffmpeg built with libx264 (H.264)")
    if missing:
        return Availability(False, f"{ff.path} lacks: {', '.join(missing)} - see {doc('troubleshooting.md')}")
    if step == "mix-and-mux" and cfg.subtitles.enabled and not ff.has_libass:
        # Not a blocker: the engine adds the subtitles as a track players can turn on instead.
        return Availability(True, "no libass: subtitles become a subtitle track, not burned in")
    return Availability.ready()


def _step_ready(step: Step, cfg: config.Config, ffmpeg: Callable[[], FfmpegInfo]) -> Availability:
    """Can this enabled step run here? Bad provider options raise ConfigError (exit 2)."""
    if step.provider == "ffmpeg":
        return _ffmpeg_ready(step.name, ffmpeg(), cfg)
    if step.provider is None or step.name not in _STEP_KINDS:
        return Availability.ready()
    kind, section = _STEP_KINDS[step.name]
    options = getattr(cfg, section).options
    try:
        provider = registry.create(kind, step.provider, options)  # also validates the options
    except ProviderUnavailableError as exc:
        return Availability(False, str(exc))
    if step.name == "erase":
        # The planner already checked the GPU backend, and the eraser where it runs (it may be remote).
        return Availability.ready()
    try:
        return provider.check()
    except Exception as exc:  # a broken third-party check() is reported, not raised
        return Availability(False, f"check failed: {type(exc).__name__}: {exc}")


def _cached_availability() -> Callable[[Kind, str], Availability]:
    """``registry.availability`` that asks each provider once per command (some checks run nvidia-smi)."""
    return functools.cache(registry.availability)


# ---------------------------------------------------------------- plan


def _wrapped(header: str) -> Column:
    """A column that wraps long words (URLs, paths) instead of cutting them off with an ellipsis."""
    return Column(header, overflow="fold")


def _show_plan(
    cfg: config.Config, plan: Plan, stages: tuple[str, ...], ready: Mapping[str, Availability]
) -> None:
    table = Table("stage", "step", "provider", "runs", "ready", _wrapped("note"), box=None, pad_edge=False)
    for stage in stages:
        for step in getattr(plan, stage):
            status = ready.get(step.name)
            note = step.note
            if status is not None and status.reason:
                note = f"{note}; {status.reason}" if note else status.reason
            table.add_row(
                stage,
                step.name,
                escape(step.provider or "-"),
                "[green]yes[/]" if step.enabled else "[dim]no[/]",
                "-" if status is None else "[green]yes[/]" if status.ok else "[red]NO[/]",
                escape(note),
            )
    out.print(table)
    for notice in plan.notices:
        out.print(f"[yellow]notice:[/] {escape(notice)}")


def _show_files(video: Path, request: pipeline.VideoPlan, stages: tuple[str, ...]) -> list[str]:
    """Print the files each target language reads and writes.

    Returns the render inputs that are missing or unreadable (exit 2, after "not ready" steps, exit 3).
    """
    cfg, plan, work = request.config, request.plan, request.work
    missing: list[str] = []
    table = Table("", _wrapped(""), box=None, show_header=False, pad_edge=False)
    for lang in cfg.general.target_languages:
        srt, json_file = script.srt_path(work, lang), script.json_path(work, lang)
        if stages == ("prepare",):
            table.add_row(f"  {lang}", f"writes {escape(str(srt))} and {escape(json_file.name)}")
            continue
        target = escape(str(pipeline.output_path(cfg, video, lang)))
        if stages == ("prepare", "render"):
            table.add_row(f"  {lang}", f"{escape(str(srt))} -> {target}")
            continue
        if not json_file.is_file():
            missing.append(f"{json_file} not found - run `erasedub prepare --to {lang}` first")
            table.add_row(f"  {lang}", f"[red]missing[/] {escape(str(json_file))} -> {target}")
            continue
        broken = json_file
        try:
            script.load_json(json_file)
            broken = srt  # the JSON is fine, so the edited SRT is what failed
            _, warnings = script.load_for_render(work, lang)
        except ScriptFormatError as exc:  # reported with the other inputs, so one precedence rule applies
            missing.append(str(exc))
            table.add_row(f"  {lang}", f"[red]unreadable[/] {escape(str(broken))} -> {target}")
            continue
        table.add_row(f"  {lang}", f"reads {escape(str(srt))} -> {target}")
        for warning in warnings:
            table.add_row("", f"[yellow]warning:[/] {escape(warning)}")
    regions_file = request.regions_file
    if "prepare" in stages and plan.enabled("detect-text"):
        table.add_row(
            "  text", f"{'writes' if stages == ('prepare',) else ''} {escape(str(regions_file))}".strip()
        )
    elif "render" in stages and plan.enabled("erase"):
        # Under "auto" the planner erases in render only when regions.json exists; under "on" (or --gpu) it
        # is planned anyway and a missing file is reported here.
        if request.regions_missing:
            missing.append(
                f"{regions_file} not found - erasing needs the on-screen text found by `prepare`. Run "
                "`erasedub prepare` without --no-erase (install the ocr extra if prepare said text "
                "detection was skipped), or render with --no-erase"
            )
            table.add_row("  text", f"[red]missing[/] {escape(str(regions_file))}")
        else:
            try:
                regions.load_regions(work)
            except ScriptFormatError as exc:
                missing.append(str(exc))
                table.add_row("  text", f"[red]unreadable[/] {escape(str(regions_file))}")
            else:
                table.add_row("  text", f"reads {escape(str(regions_file))}")
    if "render" in stages and cfg.audio.music is not None:
        table.add_row("  music", escape(str(cfg.audio.music)))
    out.print(table)
    return missing


_STAGES: dict[Command, tuple[str, ...]] = {
    "prepare": ("prepare",),
    "render": ("render",),
    "run": ("prepare", "render"),
}


def _execute(
    ctx: typer.Context,
    video: Path,
    command: Command,
    *,
    dry_run: bool,
    config_file: Path | None,
    overrides: Mapping[str, Any],
    gpu: str | None = None,
    no_erase: bool = False,
) -> None:
    stages = _STAGES[command]
    cfg = _load(ctx, config_file, overrides)
    if "render" in stages and cfg.audio.music is not None and not cfg.audio.music.is_file():
        raise ConfigError(f"audio.music: {cfg.audio.music} is not a file")
    request = pipeline.plan_video(
        cfg,
        video,
        command,
        hardware.detect_nvidia_gpus(),  # cached: the local GPU backend's check() asks again
        _cached_availability(),
        gpu=gpu,
        no_erase=no_erase,
        labels=OPTION_LABELS,
    )
    cfg, plan, work = request.config, request.plan, request.work
    ffmpeg = functools.cache(hardware.detect_ffmpeg)
    ready = {
        step.name: _step_ready(step, cfg, ffmpeg)
        for stage in stages
        for step in getattr(plan, stage)
        if step.enabled
    }

    targets = cfg.general.target_languages
    source = cfg.general.source_language or "auto"
    out.print(
        f"[bold]{escape(video.name)}[/]: {source} -> {', '.join(targets)}   work dir: {escape(str(work))}"
    )
    unverified = [t for t in targets if not languages.is_verified_target(t)]
    if unverified:
        out.print(
            f"[yellow]note:[/] {', '.join(unverified)} is outside the languages checked for each release "
            "(vi, en, zh)."
        )
    _show_plan(cfg, plan, stages, ready)
    missing = _show_files(video, request, stages)

    not_ready = [name for name, status in ready.items() if not status.ok]
    if not_ready:
        also = f" Also {len(missing)} render input(s) missing or unreadable, listed above." if missing else ""
        raise ProviderUnavailableError(
            f"cannot run here: {', '.join(not_ready)} (see the ready column above for the fix). "
            f"`erasedub doctor` checks the whole setup.{also}"
        )
    if missing:
        raise ScriptFormatError("; ".join(missing))
    if dry_run:
        return
    result = _process(request, video)
    for path in result.scripts:
        out.print(f"[green]script:[/] {escape(str(path))}")
    for path in result.outputs:
        out.print(f"[green]video:[/] {escape(str(path))}")
    if "render" not in stages and result.scripts:
        out.print(
            "Edit the scripts if needed, then run "
            f"`erasedub render {escape(str(video))}` with the same options."
        )


def _process(request: pipeline.VideoPlan, video: Path) -> engine.Result:
    """Run the engine with a progress bar on stderr; notices are printed as they come."""
    progress = Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=err,
        transient=True,
    )
    task = progress.add_task("starting", total=1.0)

    def on_progress(fraction: float, message: str) -> None:
        progress.update(task, completed=fraction, description=escape(message))

    def on_notice(message: str) -> None:
        progress.console.print(f"[yellow]notice:[/] {escape(message)}")

    with progress:
        return engine.execute(request, video, on_progress=on_progress, on_notice=on_notice)


# -------------------------------------------------------------------- commands


@app.command()
@_handle_errors
def prepare(
    ctx: typer.Context,
    video: VideoArg,
    to: ToOpt = None,
    source: FromOpt = None,
    gpu: GpuOpt = None,
    no_erase: NoEraseOpt = False,
    workdir: WorkdirOpt = None,
    config_file: ConfigOpt = None,
    dry_run: DryRunOpt = False,
) -> None:
    """Transcribe, detect on-screen text and translate into editable scripts (script.<lang>.srt)."""
    overrides = _overrides(to=to, source=source, workdir=workdir)
    _execute(
        ctx, video, "prepare", dry_run=dry_run, config_file=config_file, overrides=overrides,
        gpu=gpu, no_erase=no_erase,
    )  # fmt: skip


@app.command()
@_handle_errors
def render(
    ctx: typer.Context,
    video: VideoArg,
    to: ToOpt = None,
    gpu: GpuOpt = None,
    no_erase: NoEraseOpt = False,
    no_voice: NoVoiceOpt = False,
    no_subs: NoSubsOpt = False,
    music: MusicOpt = None,
    out_dir: OutOpt = None,
    workdir: WorkdirOpt = None,
    config_file: ConfigOpt = None,
    dry_run: DryRunOpt = False,
) -> None:
    """Erase burned-in text, dub and subtitle the video from the (edited) scripts."""
    overrides = _overrides(
        to=to, no_voice=no_voice, no_subs=no_subs, music=music, out_dir=out_dir, workdir=workdir
    )
    _execute(
        ctx, video, "render", dry_run=dry_run, config_file=config_file, overrides=overrides,
        gpu=gpu, no_erase=no_erase,
    )  # fmt: skip


@app.command()
@_handle_errors
def run(
    ctx: typer.Context,
    video: VideoArg,
    to: ToOpt = None,
    source: FromOpt = None,
    gpu: GpuOpt = None,
    no_erase: NoEraseOpt = False,
    no_voice: NoVoiceOpt = False,
    no_subs: NoSubsOpt = False,
    music: MusicOpt = None,
    out_dir: OutOpt = None,
    workdir: WorkdirOpt = None,
    config_file: ConfigOpt = None,
    dry_run: DryRunOpt = False,
) -> None:
    """prepare + render in one go, without stopping to edit the scripts."""
    overrides = _overrides(
        to=to, source=source, no_voice=no_voice, no_subs=no_subs, music=music, out_dir=out_dir,
        workdir=workdir,
    )  # fmt: skip
    _execute(
        ctx, video, "run", dry_run=dry_run, config_file=config_file, overrides=overrides,
        gpu=gpu, no_erase=no_erase,
    )  # fmt: skip


def _is_loopback(host: str) -> bool:
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False  # a host name that may resolve to anything


#: Set to 1 by the EraseDub container image, which must listen on 0.0.0.0 inside the container.
IN_CONTAINER_ENV = "ERASEDUB_IN_CONTAINER"


def _in_container() -> bool:
    return config.env_flag(IN_CONTAINER_ENV)


def _import_webui() -> ModuleType:
    try:  # imported lazily: Gradio is optional and slow to import
        return importlib.import_module("erasedub.webui")
    except ModuleNotFoundError as exc:
        if exc.name == "erasedub.webui":
            raise ProviderUnavailableError(
                "the web UI package is missing from this copy of EraseDub - run `git pull` in the erasedub "
                f"folder (or clone it again), then {install_extra('webui')}"
            ) from exc
        if exc.name == "gradio" or (exc.name or "").startswith("gradio."):
            raise ProviderUnavailableError(f"the web UI needs Gradio - {missing_extra('webui')}") from exc
        raise  # a real bug inside the web UI package: let it show


@app.command()
@_handle_errors
def webui(
    ctx: typer.Context,
    host: Annotated[str, typer.Option(help="Interface to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port to listen on.")] = 7860,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the web UI in your default browser.")
    ] = False,
    config_file: ConfigOpt = None,
) -> None:
    """Open the local web interface (needs the 'webui' extra)."""
    cfg = _load(ctx, config_file)
    ui = _import_webui()
    if not _is_loopback(host) and _in_container():
        # Inside the image 0.0.0.0 is required; whether it is local is decided by how the port is published.
        err.print(
            f"note: Running in a container: publish the port as -p 127.0.0.1:{port}:{port} to keep it local.",
            soft_wrap=True,
        )
    elif not _is_loopback(host):
        err.print(
            f"[yellow]warning:[/] listening on {escape(host)}: other computers can reach the web UI and "
            "it has no login, so anyone on this network can run jobs with your API keys. Use it only on a "
            "network you trust."
        )
    ui.launch(cfg, host=host, port=port, open_browser=open_browser)


def _plugins_table() -> Table:
    table = Table("kind", "name", "ready", "from", _wrapped("details"), box=None, pad_edge=False)
    for row in registry.status_all():
        ready = "[green]yes[/]" if row.availability.ok else "[red]no[/]"
        details = row.summary  # always shown: it carries licence limits and weaknesses
        if not row.availability.ok:
            details = f"{details}; {row.availability.reason}" if details else row.availability.reason
        table.add_row(row.kind, escape(row.name), ready, escape(row.distribution or "?"), escape(details))
    return table


@app.command()
@_handle_errors
def plugins(config_file: UnusedConfigOpt = None) -> None:
    """List installed providers and whether each one can run here."""
    out.print(_plugins_table())


# ---------------------------------------------------------------- doctor

# A doctor row: (ok, text). ok=None means "not needed / not installed", shown neutrally.
Row = tuple[bool | None, str]


def _python_row() -> Row:
    ok = hardware.python_ok()
    text = f"{platform.python_version()} ({sys.executable})"
    return ok, text if ok else f"{text} - EraseDub needs Python 3.11 or newer"


def _encoding_row() -> Row:
    stdout = getattr(sys.stdout, "encoding", None) or "?"
    utf8 = os.environ.get("PYTHONUTF8", "-")
    return True, f"stdout {stdout}, files {sys.getfilesystemencoding()}, PYTHONUTF8={utf8}"


def _config_row(path: Path | None) -> Row:
    shown = path if path is not None else Path(config.CONFIG_NAME)
    if path is None and not shown.is_file():
        return True, f"defaults (no ./{config.CONFIG_NAME})"
    try:
        config.load(path)
    except ConfigError as exc:
        return False, str(exc)
    return True, f"{shown} (valid)"


def _ffmpeg_row(ff: FfmpegInfo) -> Row:
    if ff.path is None:
        return False, f"not found - install it or set {hardware.FFMPEG_ENV} ({doc('troubleshooting.md')})"
    flags = f"libass={'yes' if ff.has_libass else 'NO'} libx264={'yes' if ff.has_libx264 else 'NO'}"
    text = f"{ff.path}, version {ff.version or '?'}, {flags}"
    if not ff.has_libass:
        text += " (subtitles are added as a subtitle track, not burned in)"
    return ff.has_libx264, text


def _ffprobe_row(ff: FfmpegInfo) -> Row:
    if ff.ffprobe_path is None:
        return False, f"not found - install it next to ffmpeg or set {hardware.FFPROBE_ENV}"
    return True, ff.ffprobe_path


def _gpu_text(gpu: GpuInfo) -> str:
    memory = "?" if gpu.memory_mib is None else str(gpu.memory_mib)
    return f"{gpu.name}, {memory} MiB, driver {gpu.driver}"


def _smi_row(smi: str | None, gpus: list[GpuInfo]) -> Row:
    if smi is None:
        return None, "not found (no NVIDIA driver)"
    if not gpus:
        return False, f"{smi} found, but it reports no GPU (a driver problem?)"
    return True, smi


def _gpu_row(gpus: list[GpuInfo]) -> Row:
    if gpus:
        return True, "; ".join(_gpu_text(g) for g in gpus)
    return None, (
        f"none - erasing is skipped unless you use --gpu modal ({doc('gpu-rental.md')}) or an eraser that "
        "needs no NVIDIA GPU (lama)"
    )


def _torch_row(gpus: list[GpuInfo]) -> Row:
    if not module_available("torch"):
        return None, f"not installed (only needed to erase on this machine; {missing_extra('erase')})"
    try:  # a broken torch (DLL load failed, WinError 126/1114) is the case doctor exists for
        torch = importlib.import_module("torch")
        version = str(torch.__version__)
        cuda = torch.version.cuda
        available = bool(torch.cuda.is_available())
    except Exception as exc:
        return False, f"import failed: {type(exc).__name__}: {exc} - see {doc('troubleshooting.md')}"
    if available:
        return True, f"{version}, CUDA {cuda}"
    if cuda is None and gpus:
        return False, (
            f"torch {version} is a CPU-only build but an NVIDIA GPU is present - install the CUDA build "
            f"({TORCH_CPU_ONLY_HELP})"
        )
    if cuda is None:
        return True, f"{version} (CPU-only build)"
    if gpus:
        return False, (
            f"{version} (CUDA {cuda}) cannot use the GPU: the driver may be too old, or CUDA_VISIBLE_DEVICES "
            f"hides it ({doc('troubleshooting.md')})"
        )
    return True, f"{version} (CUDA {cuda}, no GPU here)"


def _eraser_row(path: Path | None, gpus: list[GpuInfo]) -> Row:
    """The configured eraser as ``run`` plans it, so doctor and the commands cannot disagree.

    Built with :func:`pipeline.build_plan` (the planner behind ``plan_video``). OCR is left out
    (``regions_ready``): this row is about erasing, and the plugins table shows OCR.
    """
    try:
        cfg = config.load(path)
    except ConfigError:
        return None, "unknown: the config is invalid (see the config row)"
    erase = cfg.erase
    if erase.enabled == "off" or erase.provider == "none":
        return None, "erasing is off"
    for field, kind, name in pipeline.used_providers(cfg):  # unknown names: `run` exits 2
        if field.startswith("erase.") and name not in registry.names(kind):
            return False, f"{field}: no {kind} provider named {name!r} (`erasedub plugins` lists them)"
    try:
        summary = registry.load_class("eraser", erase.provider).summary
    except ProviderUnavailableError:  # a broken plugin: the plan below says why
        summary = ""
    head = f"{erase.provider} ({summary})" if summary else erase.provider
    try:
        plan = pipeline.build_plan(cfg, gpus, registry.availability, command="run", regions_ready=True)
    except EraseDubError as exc:  # `run` stops here too (exit 3)
        return False, f"{head}; {exc}"
    step = plan.step("erase")
    if step.enabled:
        return True, "; ".join([head, f"erases {step.note}", *plan.notices])
    return None, "; ".join([head, *plan.notices])  # auto: skipped, and the first notice says why


def _extras_row() -> Row:
    found = []
    for name in EXTRA_DISTRIBUTIONS:
        try:
            found.append(f"{name} {importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            continue
    return True, ", ".join(found) or "none installed"


def _keys_row() -> Row:
    return True, ", ".join(f"{k}={'set' if os.environ.get(k) else '-'}" for k in KNOWN_ENV_KEYS)


def _mark(ok: bool | None) -> str:
    return "[dim]--[/]" if ok is None else "[green]ok[/]" if ok else "[red]!![/]"


def _add_row(table: Table, name: str, probe: Callable[[], Row]) -> None:
    """Add one doctor row; a check that crashes becomes a red row instead of a traceback."""
    try:
        ok, text = probe()
    except Exception as exc:
        ok, text = False, f"check failed: {type(exc).__name__}: {exc}"
    table.add_row(_mark(ok), name, escape(text))


@app.command()
@_handle_errors
def doctor(ctx: typer.Context, config_file: ConfigOpt = None) -> None:
    """Check Python, ffmpeg, GPU, providers and API keys. Paste this output into bug reports."""
    table = Table("", "", _wrapped(""), box=None, show_header=False, pad_edge=False)
    _add_row(table, "erasedub", lambda: (True, __version__))
    _add_row(table, "python", _python_row)
    _add_row(
        table, "platform", lambda: (True, f"{platform.system()} {platform.release()} {platform.machine()}")
    )
    _add_row(table, "encoding", _encoding_row)
    _add_row(table, "config", lambda: _config_row(_config_path(ctx, config_file)))
    ff = functools.cache(hardware.detect_ffmpeg)
    _add_row(table, "ffmpeg", lambda: _ffmpeg_row(ff()))
    _add_row(table, "ffprobe", lambda: _ffprobe_row(ff()))
    gpus: list[GpuInfo] = []

    def nvidia_smi() -> Row:
        smi = hardware.find_nvidia_smi()
        gpus.extend(hardware.detect_nvidia_gpus(nvidia_smi=smi) if smi else [])
        return _smi_row(smi, gpus)

    _add_row(table, "nvidia-smi", nvidia_smi)
    _add_row(table, "nvidia gpu", lambda: _gpu_row(gpus))
    _add_row(table, "torch", lambda: _torch_row(gpus))
    _add_row(table, "eraser", lambda: _eraser_row(_config_path(ctx, config_file), gpus))
    _add_row(table, "extras", _extras_row)
    _add_row(table, "api keys", _keys_row)
    out.print(table)
    out.print()
    try:
        out.print(_plugins_table())
    except Exception as exc:
        out.print(f"[red]!![/] plugins: {escape(f'{type(exc).__name__}: {exc}')}")
    if not hardware.python_ok():
        raise typer.Exit(1)


@app.command()
def version(config_file: UnusedConfigOpt = None) -> None:
    """Print the EraseDub version."""
    typer.echo(__version__)


def _safe_streams() -> None:
    """Never crash on a character the console cannot show (Windows code pages such as cp1252/cp1258)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(errors="replace")


def main() -> None:
    _safe_streams()
    try:
        app()
    except KeyboardInterrupt:  # Ctrl+C outside a command (while importing, for example)
        sys.exit(CancelledError.exit_code)
