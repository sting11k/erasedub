"""The editable script produced by ``prepare`` and consumed by ``render``.

Each target language has two files side by side in the video's work directory (``work/<video-stem>/``):

``script.<lang>.json``
    Everything the engine knows about each line: timing, source text, translated text, speaker.
``script.<lang>.srt``
    The translated lines only, meant to be edited by hand (Notepad, Subtitle Edit, ...).

When rendering, the SRT is authoritative for timing and text, because that is what the user edits.
Metadata from the JSON (source text, speaker) is carried over to each SRT cue from the JSON line whose timing
overlaps it by more than half; cues without such a line fall back to the line at the same position when both
files still have the same number of lines. See ``docs/script-format.md``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, ValidationInfo, field_validator

from erasedub import _jsonfile, languages
from erasedub.errors import ScriptFormatError, describe_validation_error
from erasedub.models import FrozenModel, TimeSpan

SCRIPT_VERSION: Literal[1] = 1

# A line is a timing line when it starts like one; the numbers are then checked strictly.
_TIMING_START = re.compile(r"^\s*\d+:\d+:\d+[,.]\d+\s*-->")
_TIMING = re.compile(
    # Text after the second time (positional hints such as "X1:100") is allowed and ignored.
    r"^\s*(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)(?:\s.*)?$"
)
_TIMESTAMP = re.compile(r"^\s*(\d+):(\d+):(\d+)[,.](\d+)\s*$")
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def _ms(seconds: float) -> float:
    return round(seconds * 1000) / 1000


class ScriptLine(TimeSpan):
    """One line of the script. Times are rounded to whole milliseconds, like in the SRT."""

    text: str
    source: str | None = None
    speaker: str | None = None

    @field_validator("start")
    @classmethod
    def _round_start(cls, value: float) -> float:
        return _ms(value)

    @field_validator("end")
    @classmethod
    def _round_end(cls, value: float, info: ValidationInfo) -> float:
        start = info.data.get("start")
        rounded = _ms(value)
        # Keep a line that was shorter than a millisecond, instead of rounding it to zero length.
        if isinstance(start, int | float) and value > start and rounded <= start:
            return _ms(start + 0.001)
        return rounded


class Script(FrozenModel):
    version: Literal[1] = SCRIPT_VERSION
    source_language: str | None = None
    target_language: str
    lines: tuple[ScriptLine, ...] = Field(default=())

    @field_validator("target_language")
    @classmethod
    def _target(cls, value: str) -> str:
        return languages.normalize(value)

    @field_validator("source_language")
    @classmethod
    def _source(cls, value: str | None) -> str | None:
        return None if value is None else languages.normalize(value)


def _tag(lang: str) -> str:
    try:
        return languages.normalize(lang)
    except ValueError as exc:
        raise ScriptFormatError(f"cannot name a script file: {exc}") from exc


def json_name(lang: str) -> str:
    """``"script.vi.json"`` for ``"vi"``."""
    return f"script.{_tag(lang)}.json"


def srt_name(lang: str) -> str:
    """``"script.vi.srt"`` for ``"vi"``."""
    return f"script.{_tag(lang)}.srt"


def json_path(workdir: Path, lang: str) -> Path:
    return workdir / json_name(lang)


def srt_path(workdir: Path, lang: str) -> Path:
    return workdir / srt_name(lang)


# --- SRT -------------------------------------------------------------------------------------------------


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    """Seconds of a timestamp's parts; raises ``ValueError`` when they are out of range."""
    if int(m) >= 60 or int(s) >= 60:
        raise ValueError("minutes and seconds must be 00-59")
    if len(ms) > 3:
        raise ValueError("milliseconds must have 1-3 digits (00:00:01,500)")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def _to_seconds(where: str, h: str, m: str, s: str, ms: str) -> float:
    try:
        return _seconds(h, m, s, ms)
    except ValueError as exc:
        raise ScriptFormatError(f"{where}: {exc}") from exc


def parse_timestamp(text: str) -> float:
    """Seconds of one SRT timestamp, e.g. ``"00:00:03,500"`` -> ``3.5``, with the rules of :func:`parse_srt`.

    ``.`` is accepted before the milliseconds and spaces around the value are ignored. Raises ``ValueError``
    with a short reason (callers add where the value came from).
    """
    match = _TIMESTAMP.match(text)
    if match is None:
        raise ValueError(f"bad time {text.strip()!r}, expected 00:00:01,500")
    return _seconds(*match.groups())


def looks_like_timing(line: str) -> bool:
    """Whether ``line`` starts like an SRT timing line (``00:00:01,000 --> ...``)."""
    return _TIMING_START.match(line) is not None


def format_timestamp(seconds: float) -> str:
    """Format seconds as an SRT timestamp, e.g. ``3.5`` -> ``"00:00:03,500"``.

    Rounds to the nearest millisecond (Python's ``round``: exact halves go to the even millisecond).
    """
    return _format_ms(round(seconds * 1000))


def _format_ms(total_ms: int) -> str:
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str, *, source: str = "SRT", warnings: list[str] | None = None) -> list[ScriptLine]:
    """Parse SRT text into lines, in file order.

    Parsing is line based, so a missing blank line between cues is harmless: every timing line starts a new
    cue, and a digits-only line right before it is that cue's index. Blank lines inside a cue are dropped.
    Tolerates a BOM, CRLF or CR line ends, missing index numbers, ``.`` before the milliseconds and text
    after the timing (positional hints). A zero-length cue is dropped with a warning.

    Errors name ``source`` and the line number, e.g. ``script.vi.srt:123: ...``; warnings are appended to
    ``warnings`` when it is given.
    """
    rows = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[tuple[int, str, list[str]]] = []  # (line number of the timing line, timing line, body rows)
    for number, row in enumerate(rows, start=1):
        if _TIMING_START.match(row):
            body = cues[-1][2] if cues else []
            if body and body[-1].strip().isdigit():
                body.pop()  # the row right before is this cue's index (even with no blank line before it)
            cues.append((number, row, []))
        elif cues:
            cues[-1][2].append(row)
        elif row.strip() and not row.strip().isdigit():
            raise ScriptFormatError(
                f"{source}:{number}: text before the first timing line ('00:00:01,000 --> ...')"
            )

    lines: list[ScriptLine] = []
    for number, timing, body in cues:
        where = f"{source}:{number}"
        match = _TIMING.match(timing)
        if match is None:
            raise ScriptFormatError(f"{where}: bad timing line, expected '00:00:01,000 --> 00:00:02,500'")
        g = match.groups()
        start, end = _to_seconds(where, *g[:4]), _to_seconds(where, *g[4:])
        if end < start:
            raise ScriptFormatError(f"{where}: the cue ends before it starts")
        if end == start:
            if warnings is not None:
                warnings.append(f"{where}: zero-length cue dropped")
            continue
        body_text = "\n".join(row.strip() for row in body if row.strip())
        try:
            lines.append(ScriptLine(start=start, end=end, text=body_text))
        except ValidationError as exc:
            raise ScriptFormatError(f"{where}: {describe_validation_error(exc)}") from exc
    return lines


def _srt_text(text: str) -> str:
    """Text as written into an SRT cue: no CR, no blank lines (a blank line would end the cue)."""
    rows = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(row.rstrip() for row in rows if row.strip())


def render_srt(lines: Sequence[ScriptLine]) -> str:
    """Serialize lines to SRT text that :func:`parse_srt` always reads back (LF line ends)."""
    out = []
    for i, line in enumerate(lines, start=1):
        start_ms = round(line.start * 1000)
        end_ms = max(round(line.end * 1000), start_ms + 1)
        out.append(f"{i}\n{_format_ms(start_ms)} --> {_format_ms(end_ms)}\n{_srt_text(line.text)}\n")
    return "\n".join(out)


def read_srt(path: Path) -> str:
    """Read an SRT file as UTF-8 (with or without BOM) or UTF-16 with BOM."""
    raw = path.read_bytes()
    try:
        if raw.startswith(_UTF16_BOMS):
            return raw.decode("utf-16")
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ScriptFormatError(
            f"{path.name}: the file is not UTF-8 text. Save it again as UTF-8 (Notepad: File > Save as > "
            "Encoding: UTF-8; Subtitle Edit: File > Save as, encoding UTF-8)."
        ) from exc


def sort_lines(lines: Sequence[ScriptLine], *, source: str = "SRT") -> tuple[list[ScriptLine], list[str]]:
    """Sort by start time and describe overlaps (overlapping cues mean overlapping voices).

    Cue numbers in the warnings are positions in the original order, starting at 1.
    """
    order = sorted(range(len(lines)), key=lambda i: (lines[i].start, lines[i].end))
    warnings: list[str] = []
    if order != list(range(len(lines))):
        warnings.append(f"{source}: cues were not in time order; they were sorted by start time")
    latest: int | None = None  # the cue seen so far that ends last
    for cur in order:
        if latest is not None and lines[cur].start < lines[latest].end:
            warnings.append(
                f"{source}: cues {latest + 1} and {cur + 1} overlap "
                f"({format_timestamp(lines[cur].start)} < {format_timestamp(lines[latest].end)}); "
                "their voices will overlap"
            )
        if latest is None or lines[cur].end > lines[latest].end:
            latest = cur
    return [lines[i] for i in order], warnings


def _overlap(a: TimeSpan, b: TimeSpan) -> float:
    return max(0.0, min(a.end, b.end) - max(a.start, b.start))


def merge_metadata(
    edited: Sequence[ScriptLine], original: Sequence[ScriptLine]
) -> tuple[list[ScriptLine], int]:
    """Copy ``source`` and ``speaker`` from ``original`` onto the edited lines.

    Each edited line takes the original line that overlaps it the most, if that overlap covers more than
    half of the edited line. Lines without such a match take the line at the same position when both lists
    have the same length. Returns the merged lines and how many lines got no metadata.
    """
    same_count = len(edited) == len(original)
    merged: list[ScriptLine] = []
    unmatched = 0
    for i, new in enumerate(edited):
        best = max(original, key=lambda old: _overlap(new, old), default=None)
        if best is None or _overlap(new, best) <= new.duration / 2:
            best = original[i] if same_count else None
        if best is None:
            unmatched += 1
            merged.append(new)
        else:
            merged.append(new.model_copy(update={"source": best.source, "speaker": best.speaker}))
    return merged, unmatched


# --- Files -----------------------------------------------------------------------------------------------


def save(script: Script, workdir: Path) -> None:
    """Write ``script.<lang>.json`` and ``script.<lang>.srt`` for ``script.target_language``."""
    _jsonfile.write(json_path(workdir, script.target_language), script)
    srt = srt_path(workdir, script.target_language)
    srt.write_text(render_srt(script.lines), encoding="utf-8")


def load_json(path: Path) -> Script:
    return _jsonfile.read(path, Script, supported_version=SCRIPT_VERSION)


def load_for_render(workdir: Path, lang: str) -> tuple[Script, list[str]]:
    """Load the ``lang`` script the user may have edited. Returns the script and human-readable warnings."""
    json_file, srt_file = json_path(workdir, lang), srt_path(workdir, lang)
    if not json_file.exists():
        raise ScriptFormatError(f"{json_file} not found - run `erasedub prepare --to {_tag(lang)}` first")
    script = load_json(json_file)
    if not srt_file.exists():
        return script, []

    warnings: list[str] = []
    parsed = parse_srt(read_srt(srt_file), source=srt_file.name, warnings=warnings)
    ordered, order_warnings = sort_lines(parsed, source=srt_file.name)
    warnings += order_warnings
    merged, unmatched = merge_metadata(ordered, script.lines)
    if unmatched:
        warnings.append(
            f"{srt_file.name}: {unmatched} of {len(merged)} lines no longer match a line of {json_file.name} "
            "by timing; their source text and speaker labels were dropped for this render."
        )
    return script.model_copy(update={"lines": tuple(merged)}), warnings
