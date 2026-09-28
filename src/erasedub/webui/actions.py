"""What the web UI buttons do, as plain functions that do not import Gradio.

Keeping the logic here means it can be tested without a browser or a server, and the Gradio layer in
:mod:`erasedub.webui.app` stays a thin wiring of components to these functions. *Prepare* and *Render* plan
the video like the CLI and run the same engine (:func:`erasedub.engine.execute`).
"""

from __future__ import annotations

import dataclasses
import functools
import html
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from erasedub import engine, languages, registry
from erasedub.config import Config, from_mapping
from erasedub.errors import (
    CancelledError,
    EraseDubError,
    ProviderNotFoundError,
    ScriptFormatError,
    describe_validation_error,
)
from erasedub.hardware import GpuInfo
from erasedub.pipeline import AvailabilityCheck, Command, Plan, Step, VideoPlan, plan_video, work_folder
from erasedub.providers.base import Availability, Kind
from erasedub.script import (
    Script,
    ScriptLine,
    format_timestamp,
    json_path,
    looks_like_timing,
    parse_srt,
    parse_timestamp,
    read_srt,
    render_srt,
    save,
    srt_name,
    srt_path,
)
from erasedub.webui.strings import DEFAULT_LOCALE, has, t

#: One row of the editable script table: start, end, text (timestamps in SRT form).
Row = list[str]
#: Script rows per target language, e.g. ``{"vi": [...], "en": [...]}``.
Scripts = dict[str, list[Row]]

#: Uploaded SRT files larger than this are refused (they are read into memory; a script is far smaller).
MAX_SRT_BYTES = 10 * 1024 * 1024
#: Receives ``(fraction 0-1, message)`` while the engine runs.
ProgressFn = Callable[[float, str], None]
#: Share of the progress bar given to preparing missing scripts when *Render* has to prepare first.
PREPARE_SHARE = 0.4


@dataclass(frozen=True)
class UiOptions:
    """The values of the option widgets, in the form Gradio hands them over."""

    targets: Sequence[str]
    source: str = "auto"
    erase: str = "auto"
    #: "" = not picked (use the config's backend). Picking one asks for erasing, like the CLI's --gpu.
    gpu: str = ""
    translator: str = "google"
    tts: str = "edge"
    voice: str = "auto"
    subtitles: bool = True
    original_audio: str = "keep"
    music: str | None = None


# --- Choices ---------------------------------------------------------------------------------------------


def language_name(code: str, *, locale: str = DEFAULT_LOCALE) -> str | None:
    """Display name of ``code``'s base language (``zh-TW`` -> Chinese), or None if the UI has none."""
    key = f"lang.{languages.base(code)}"
    return t(key, locale) if has(key) else None


def _normalized(codes: Iterable[str | None]) -> list[str]:
    out = []
    for code in codes:
        if code and code.strip().lower() != "auto":
            try:
                out.append(languages.normalize(code))
            except ValueError:
                continue
    return out


def language_label(code: str, *, locale: str = DEFAULT_LOCALE) -> str:
    tier = t("verified", locale) if languages.is_verified_target(code) else t("unverified", locale)
    name = language_name(code, locale=locale)
    return f"{name} ({code}) · {tier}" if name else f"{code} · {tier}"


def language_choices(extra: Iterable[str] = (), *, locale: str = DEFAULT_LOCALE) -> list[tuple[str, str]]:
    """``(label, code)`` pairs for the target picker, verified languages first.

    ``extra`` adds codes from the config (``it``, ``zh-TW``, ...) so every configured value has a checkbox.
    """
    codes = dict.fromkeys([*languages.LISTED_LANGUAGES, *_normalized(extra)])
    ordered = sorted(codes, key=lambda c: (not languages.is_verified_target(c), c))
    return [(language_label(code, locale=locale), code) for code in ordered]


def source_choices(extra: str | None = None, *, locale: str = DEFAULT_LOCALE) -> list[tuple[str, str]]:
    """``(label, code)`` pairs for the spoken-language picker; ``extra`` is the configured source."""
    codes = dict.fromkeys([*languages.LISTED_LANGUAGES, *_normalized([extra])])
    choices = [(t("source_auto", locale), "auto")]
    for code in codes:
        name = language_name(code, locale=locale)
        choices.append((f"{name} ({code})" if name else code, code))
    return choices


def _availability(availability: AvailabilityCheck, kind: Kind, name: str) -> Availability:
    try:
        return availability(kind, name)
    except ProviderNotFoundError as exc:
        return Availability(False, str(exc))


def provider_choices(
    kind: Kind,
    default: str,
    *,
    availability: AvailabilityCheck = registry.availability,
    locale: str = DEFAULT_LOCALE,
) -> list[tuple[str, str]]:
    """``(label, name)`` for the registered providers of ``kind``; those that cannot run here are marked."""
    names = registry.names(kind)
    if default not in names:
        names = [default, *names]
    choices = []
    for name in names:
        ready = _availability(availability, kind, name).ok
        choices.append((name if ready else t("provider_not_ready", locale, name=name), name))
    return choices


def script_languages(
    targets: Sequence[str] | None, current: str | None, *, locale: str = DEFAULT_LOCALE
) -> tuple[list[tuple[str, str]], str | None]:
    """Choices and value of the *Script language* picker: one entry per selected target."""
    codes = list(dict.fromkeys(_normalized(targets or [])))
    choices = [(language_name(c, locale=locale) or c, c) for c in codes]
    value = current if current in codes else (codes[0] if codes else None)
    return choices, value


# --- Planning --------------------------------------------------------------------------------------------


def to_config(base: Config, options: UiOptions) -> Config:
    """Apply the UI options on top of ``base`` (the loaded ``erasedub.toml``). Raises ``ConfigError``.

    The picked GPU backend is not applied here: :func:`erasedub.pipeline.plan_video` applies it with the
    same rule as the CLI's ``--gpu`` (see :func:`plan_step`).
    """
    data: dict[str, Any] = base.model_dump(mode="python")
    data["general"]["target_languages"] = list(options.targets)
    data["general"]["source_language"] = options.source
    data["erase"]["enabled"] = options.erase
    data["translate"]["provider"] = options.translator
    data["tts"]["provider"] = options.tts
    data["tts"]["voice"] = options.voice.strip() or "auto"
    data["subtitles"]["enabled"] = options.subtitles
    data["audio"]["original"] = options.original_audio
    data["audio"]["music"] = options.music or None
    return from_mapping(data, source="web UI")


def _step_label(name: str, locale: str) -> str:
    key = f"step.{name}"
    return t(key, locale) if has(key) else name


def _format_steps(title: str, steps: Sequence[Step], locale: str) -> list[str]:
    out = [f"**{title}**", ""]
    for step in steps:
        mark = "✓" if step.enabled else "○"
        provider = f" · `{step.provider}`" if step.provider and step.enabled else ""
        state = t("step_on", locale) if step.enabled else t("step_off", locale)
        detail = f" — {step.note}" if step.note else f" ({state})"
        out.append(f"- {mark} **{_step_label(step.name, locale)}**{provider}{detail}")
    return [*out, ""]


# Steps whose provider the planner does not check itself (it checks only erasing and OCR).
_CHECKED_HERE: dict[str, Kind] = {"transcribe": "asr", "translate": "translator", "speak": "tts"}


def provider_notices(
    steps: Sequence[Step], availability: AvailabilityCheck, *, locale: str = DEFAULT_LOCALE
) -> list[str]:
    """One notice per enabled step whose provider cannot run here (missing extra, missing key, ...)."""
    notices = []
    for step in steps:
        kind = _CHECKED_HERE.get(step.name)
        if kind is None or not step.enabled or not step.provider:
            continue
        status = _availability(availability, kind, step.provider)
        if not status.ok:
            label = _step_label(step.name, locale)
            notices.append(t("provider_notice", locale, step=label, name=step.provider, reason=status.reason))
    return notices


def _steps(plan: Plan, command: Command) -> tuple[Step, ...]:
    return (
        plan.prepare
        if command == "prepare"
        else plan.render
        if command == "render"
        else plan.prepare + plan.render
    )


def describe_plan(
    plan: Plan,
    config: Config,
    *,
    command: Command,
    notices: Sequence[str] = (),
    details: Sequence[str] = (),
    locale: str = DEFAULT_LOCALE,
) -> str:
    """Markdown summary of ``plan`` for ``command`` (``run`` shows both phases).

    ``notices`` are shown as warnings after the planner's own; ``details`` as plain lines after those.
    """
    targets = config.general.target_languages
    names = [language_name(c, locale=locale) or c for c in targets]
    lines = [t("plan_targets", locale, targets=", ".join(names)), ""]
    if command != "render":
        lines += _format_steps(t("plan_prepare", locale), plan.prepare, locale)
    if command != "prepare":
        lines += _format_steps(t("plan_render", locale), plan.render, locale)
    all_notices = [*plan.notices, *notices]
    unverified = [
        language_name(c, locale=locale) or c for c in targets if not languages.is_verified_target(c)
    ]
    if unverified:
        all_notices.append(t("unverified_notice", locale, names=", ".join(unverified)))
    for notice in all_notices:  # one quote block each; adjacent ">" lines would merge into one paragraph
        lines += [f"> ⚠ {notice}", ""]
    for detail in details:
        lines += [detail, ""]
    return "\n".join(lines).rstrip() + "\n"


def erase_after_gpu_pick(gpu: str | None, erase: str) -> str:
    """The *Erase* value once a GPU backend is picked: a picked backend asks for erasing, so Off becomes Auto.

    *Erase* starts at the config's ``erase.enabled``. Without this, ``off`` in the config would beat a picked
    GPU, while the CLI's explicit ``--gpu`` wins over it (the command line beats the config). Choosing Off
    again afterwards wins, like ``--no-erase --gpu``.
    """
    return "auto" if gpu and erase == "off" else erase


@dataclass(frozen=True)
class Planned:
    """The outcome of planning a click: the plan (None when the settings are invalid) and its summary."""

    request: VideoPlan | None
    #: Markdown for the status box: the plan, or why there is none.
    text: str
    #: Whether the engine may start: every enabled step can run here and the render inputs exist.
    runnable: bool = False


def _plan(
    base: Config,
    video: str | None,
    options: UiOptions,
    gpus: Sequence[GpuInfo],
    stage: Command,
    *,
    scripts: Mapping[str, Sequence[Sequence[Any]]] | None,
    availability: AvailabilityCheck,
    locale: str,
) -> Planned:
    if not video:
        return Planned(None, t("need_video", locale))
    if not options.targets:
        return Planned(None, t("need_target", locale))
    try:
        config = to_config(base, options)
    except EraseDubError as exc:
        return Planned(None, t("error", locale, message=str(exc)))
    command: Command = stage
    details = []
    if stage == "render":
        work = work_folder(config, Path(video))
        for lang in config.general.target_languages:
            name = language_name(lang, locale=locale) or lang
            try:
                lines = script_lines((scripts or {}).get(lang, []), locale=locale)
            except ScriptFormatError as exc:
                return Planned(None, t("srt_error", locale, message=f"{name}, {exc}"))
            if lines:
                details.append(t("script_ready", locale, lang=name, count=len(lines)))
            elif json_path(work, lang).is_file():
                details.append(t("script_saved", locale, lang=name))
            else:
                details.append(t("script_missing", locale, lang=name))
                command = "run"
    try:
        request = plan_video(
            config,
            Path(video),
            command,
            gpus,
            availability,
            gpu=options.gpu or None,
            no_erase=options.erase == "off",
            labels={"erase.gpu": t("gpu_option", locale)},
            gpu_undo=t("gpu_undo", locale),
        )
    except EraseDubError as exc:
        return Planned(None, t("error", locale, message=str(exc)))
    if request.regions_missing:
        details.insert(0, t("regions_missing", locale))
    notices = provider_notices(_steps(request.plan, command), availability, locale=locale)
    text = describe_plan(
        request.plan, request.config, command=command, notices=notices, details=details, locale=locale
    )
    return Planned(request, text, runnable=not notices and not request.regions_missing)


def plan_step(
    base: Config,
    video: str | None,
    options: UiOptions,
    gpus: Sequence[GpuInfo],
    stage: Command,
    *,
    scripts: Mapping[str, Sequence[Sequence[Any]]] | None = None,
    availability: AvailabilityCheck = registry.availability,
    locale: str = DEFAULT_LOCALE,
) -> str:
    """The plan of a click on *Prepare* or *Render*, as Markdown: validate, build the plan, and explain it.

    Planning goes through :func:`erasedub.pipeline.plan_video`, like the CLI command of the same name: a
    picked GPU is ``--gpu``, *Erase* = Off is ``--no-erase``, and ``regions.json`` is looked up in the
    video's work folder. For *Render*, ``scripts`` (table rows per target language) are validated and
    summarised; a language with neither rows nor a saved script is prepared first, so it is planned like
    ``erasedub run``.
    """
    return _plan(
        base, video, options, gpus, stage, scripts=scripts, availability=availability, locale=locale
    ).text


# --- Running ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """What a *Prepare* or *Render* click produced."""

    #: Markdown for the status box.
    status: str
    #: Script rows per language after the click (None: leave the table as it is).
    scripts: Scripts | None = None
    #: Finished videos, in the order of the target languages.
    videos: tuple[str, ...] = ()


_RUNNING: set[threading.Event] = set()
_RUNNING_LOCK = threading.Lock()


@contextmanager
def _running() -> Iterator[threading.Event]:
    """A cancel flag for one run, set by :func:`cancel_all` while the run is registered."""
    flag = threading.Event()
    with _RUNNING_LOCK:
        _RUNNING.add(flag)
    try:
        yield flag
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(flag)


def cancel_all(*, locale: str = DEFAULT_LOCALE) -> str:
    """Handle *Cancel*: ask every running job to stop (the engine stops at the next check)."""
    with _RUNNING_LOCK:
        flags = list(_RUNNING)
    for flag in flags:
        flag.set()
    return t("cancelling" if flags else "nothing_running", locale)


def _span(on_progress: ProgressFn | None, start: float, end: float) -> ProgressFn | None:
    """``on_progress`` for one part of a click that runs the engine more than once."""
    if on_progress is None:
        return None
    report = on_progress

    def scaled(fraction: float, message: str) -> None:
        report(start + (end - start) * fraction, message)

    return scaled


def _notices(notices: Sequence[str]) -> list[str]:
    return [f"> ⚠ {html.escape(n)}\n" for n in dict.fromkeys(notices)]


def _failure(exc: EraseDubError, notices: Sequence[str], locale: str) -> str:
    if isinstance(exc, CancelledError):
        head = t("cancelled", locale)
    else:
        head = t("run_failed", locale, message=html.escape(str(exc)))
    return "\n".join([head, "", *_notices(notices)]).rstrip() + "\n"


def _rows_of(work: Path, langs: Iterable[str]) -> Scripts:
    """The saved ``script.<lang>.srt`` of each language as table rows."""
    return {lang: rows_from_srt(read_srt(srt_path(work, lang)), source=srt_name(lang)) for lang in langs}


def _files(label: str, paths: Iterable[Path]) -> list[str]:
    return [f"- {label} `{html.escape(str(p))}`" for p in paths]


def prepare_video(
    base: Config,
    video: str | None,
    options: UiOptions,
    gpus: Sequence[GpuInfo],
    *,
    availability: AvailabilityCheck = registry.availability,
    locale: str = DEFAULT_LOCALE,
    on_progress: ProgressFn | None = None,
) -> Outcome:
    """Handle *Prepare*: plan like ``erasedub prepare``, run the engine, and return the new scripts."""
    planned = _plan(
        base, video, options, gpus, "prepare", scripts=None, availability=availability, locale=locale
    )
    if planned.request is None or video is None:
        return Outcome(planned.text)
    if not planned.runnable:
        return Outcome(f"{planned.text}\n{t('cannot_start', locale)}")
    request, notices = planned.request, list[str]()
    with _running() as flag:
        try:
            result = engine.execute(
                request,
                Path(video),
                on_progress=on_progress,
                on_notice=notices.append,
                is_cancelled=flag.is_set,
            )
            rows = _rows_of(request.work, request.config.general.target_languages)
        except EraseDubError as exc:
            return Outcome(_failure(exc, notices, locale))
    names = [language_name(c, locale=locale) or c for c in rows]
    lines = [t("prepare_done", locale, langs=", ".join(names)), "", *_notices(notices)]
    lines += _files(t("file_script", locale), result.scripts)
    return Outcome("\n".join(lines).rstrip() + "\n", scripts=rows)


def _save_rows(config: Config, work: Path, lang: str, lines: Sequence[ScriptLine]) -> None:
    """Write the table of ``lang`` where ``render`` reads it: the SRT, next to the prepared JSON."""
    if json_path(work, lang).is_file():
        srt_path(work, lang).write_text(render_srt(lines), encoding="utf-8")
        return
    # Typed in or loaded from an SRT without Prepare: there is no metadata to keep.
    work.mkdir(parents=True, exist_ok=True)
    source = config.general.source_language
    save(Script(source_language=source, target_language=lang, lines=tuple(lines)), work)


def render_video(
    base: Config,
    video: str | None,
    options: UiOptions,
    gpus: Sequence[GpuInfo],
    *,
    scripts: Mapping[str, Sequence[Sequence[Any]]] | None = None,
    availability: AvailabilityCheck = registry.availability,
    locale: str = DEFAULT_LOCALE,
    on_progress: ProgressFn | None = None,
) -> Outcome:
    """Handle *Render*: save the edited tables, prepare the languages that have no script, then render.

    The table rows of a language win over its saved ``script.<lang>.srt``; a language with neither is
    prepared first (like ``erasedub run``), and its new script is returned for the table.
    """
    planned = _plan(
        base, video, options, gpus, "render", scripts=scripts, availability=availability, locale=locale
    )
    if planned.request is None or video is None:
        return Outcome(planned.text)
    if not planned.runnable:
        return Outcome(f"{planned.text}\n{t('cannot_start', locale)}")
    config, work = planned.request.config, planned.request.work
    rows = dict(scripts or {})
    missing = []
    for lang in config.general.target_languages:
        lines = script_lines(rows.get(lang, []), locale=locale)  # already validated by _plan
        if lines:
            _save_rows(config, work, lang, lines)
        elif not json_path(work, lang).is_file():
            missing.append(lang)
    notices: list[str] = []
    scripts_out: Scripts = {k: clean_rows(v) for k, v in rows.items()}
    with _running() as flag:
        try:
            share = PREPARE_SHARE if missing else 0.0
            if missing:
                first = _plan(
                    base,
                    video,
                    dataclasses.replace(options, targets=missing),
                    gpus,
                    "prepare",
                    scripts=None,
                    availability=availability,
                    locale=locale,
                )
                if first.request is None or not first.runnable:
                    return Outcome(f"{first.text}\n{t('cannot_start', locale)}")
                engine.execute(
                    first.request,
                    Path(video),
                    on_progress=_span(on_progress, 0.0, share),
                    on_notice=notices.append,
                    is_cancelled=flag.is_set,
                )
                scripts_out.update(_rows_of(work, missing))
            # Planned again: the prepare step above may have written regions.json, so erasing may now run.
            second = _plan(
                base,
                video,
                options,
                gpus,
                "render",
                scripts=scripts_out,
                availability=availability,
                locale=locale,
            )
            if second.request is None or not second.runnable:
                return Outcome(f"{second.text}\n{t('cannot_start', locale)}", scripts=scripts_out)
            result = engine.execute(
                second.request,
                Path(video),
                on_progress=_span(on_progress, share, 1.0),
                on_notice=notices.append,
                is_cancelled=flag.is_set,
            )
        except EraseDubError as exc:
            return Outcome(_failure(exc, notices, locale), scripts=scripts_out)
    report = [t("render_done", locale, count=len(result.outputs)), "", *_notices(notices)]
    report += _files(t("file_video", locale), result.outputs)
    videos = tuple(str(p) for p in result.outputs)
    return Outcome("\n".join(report).rstrip() + "\n", scripts=scripts_out, videos=videos)


# --- Script table <-> SRT --------------------------------------------------------------------------------


def _cell(value: object) -> str:
    return "" if value is None else str(value)


def clean_rows(rows: Sequence[Sequence[Any]] | None) -> list[Row]:
    """Rows as lists of exactly three strings; ``None`` cells become empty strings."""
    return [([_cell(c) for c in row[:3]] + ["", "", ""])[:3] for row in rows or []]


def script_lines(rows: Sequence[Sequence[Any]], *, locale: str = DEFAULT_LOCALE) -> list[ScriptLine]:
    """Validate table rows one by one. Blank rows are ignored; blank lines inside a cell are dropped.

    Raises :class:`ScriptFormatError` naming the first bad row (1-based, as counted in the table).
    """
    lines = []
    for number, (start, end, text) in enumerate(clean_rows(rows), start=1):
        if not (start.strip() or end.strip() or text.strip()):
            continue
        text_rows = [r.strip() for r in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        try:
            if any(looks_like_timing(r) for r in text_rows):
                raise ValueError("the text contains a timing line ('... --> ...')")
            body = "\n".join(r for r in text_rows if r)
            lines.append(ScriptLine(start=parse_timestamp(start), end=parse_timestamp(end), text=body))
        except ValidationError as exc:
            message = describe_validation_error(exc)
            raise ScriptFormatError(t("srt_row", locale, row=number, message=message)) from exc
        except ValueError as exc:
            raise ScriptFormatError(t("srt_row", locale, row=number, message=str(exc))) from exc
    return lines


def rows_from_srt(text: str, *, source: str = "SRT") -> list[Row]:
    """Parse SRT text into table rows. Raises ``ScriptFormatError`` with ``source:line`` in the message."""
    return [
        [format_timestamp(line.start), format_timestamp(line.end), line.text]
        for line in parse_srt(text, source=source)
    ]


def remember_rows(scripts: Mapping[str, list[Row]] | None, lang: str | None, rows: Any) -> Scripts:
    """Store the table's rows under ``lang``; returns a new dict (Gradio state is not mutated in place)."""
    out = dict(scripts or {})
    if lang:
        out[lang] = clean_rows(rows)
    return out


def show_rows(scripts: Mapping[str, list[Row]] | None, lang: str | None) -> list[Row]:
    """The stored rows of ``lang`` (empty if none yet)."""
    return list((scripts or {}).get(lang or "", []))


def load_srt_file(
    path: str | None, lang: str | None, *, locale: str = DEFAULT_LOCALE
) -> tuple[list[Row], str]:
    """Handle *Upload SRT*: return the table rows and a status message.

    Uses the same reader as ``erasedub render`` (UTF-8, or UTF-16 with a BOM), so the web UI accepts
    exactly the files the CLI accepts.
    """
    if not path:
        return [], t("status_idle", locale)
    file = Path(path)
    try:
        if file.stat().st_size > MAX_SRT_BYTES:
            return [], t("srt_too_big", locale, limit=MAX_SRT_BYTES // (1024 * 1024))
        rows = rows_from_srt(read_srt(file), source=file.name)
    except ScriptFormatError as exc:
        # The message names the uploaded file (Gradio keeps its own name) and goes into Markdown: escape it.
        return [], t("srt_error", locale, message=html.escape(str(exc)))
    except OSError as exc:
        return [], t("srt_error", locale, message=exc.strerror or type(exc).__name__)
    name = (language_name(lang, locale=locale) or lang) if lang else "?"
    return rows, t("srt_loaded", locale, count=len(rows), lang=name)


@functools.cache
def _export_root() -> tempfile.TemporaryDirectory[str]:
    """One temporary folder per process for exported scripts; removed when the process exits."""
    return tempfile.TemporaryDirectory(prefix="erasedub-webui-")


def export_srt_file(
    rows: Sequence[Sequence[Any]] | None, lang: str | None, *, locale: str = DEFAULT_LOCALE
) -> tuple[str | None, str]:
    """Handle *Export SRT*: write the table to ``script.<lang>.srt``; return its path and a status."""
    try:
        lines = script_lines(rows or [], locale=locale)
        name = srt_name(lang or "und")
    except ScriptFormatError as exc:
        return None, t("srt_error", locale, message=str(exc))
    if not lines:
        return None, t("srt_empty", locale)
    # A fresh sub-folder per export, so two browser tabs never overwrite each other's file.
    path = Path(tempfile.mkdtemp(dir=_export_root().name)) / name
    path.write_text(render_srt(lines), encoding="utf-8")
    return str(path), t("srt_exported", locale, count=len(lines), name=name)
