"""Turns subtitle events into an ASS file for ffmpeg's libass filter (or SRT for a soft subtitle track).

Layouts (:class:`~erasedub.providers.base.SubtitleLayout`) only describe each subtitle; this module draws them
the same way for every layout: white text with a black outline, sized from the shorter side of the picture
unless ``subtitles.font_size`` is set, in a font that covers the target language unless ``subtitles.font`` is
set (libass falls back to another installed font for missing characters).
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence

from erasedub import languages
from erasedub.models import SubtitleEvent, VideoInfo
from erasedub.script import ScriptLine, render_srt

#: Font size as a share of the picture's shorter side when ``subtitles.font_size = 0``.
SIZE_RATIO = 0.055
#: A subtitle that must fit a ``box`` may shrink down to this share of its size.
MIN_SHRINK = 0.6
#: Line height as a multiple of the font size, used to check that text fits a box.
LINE_HEIGHT = 1.25

_CJK_FONTS = {"zh": "Noto Sans CJK SC", "ja": "Noto Sans CJK JP", "ko": "Noto Sans CJK KR"}
_TRADITIONAL = ("zh-Hant", "zh-TW", "zh-HK", "zh-MO")
#: U+2060 WORD JOINER: invisible, and keeps libass from reading a literal backslash as an override code.
_WORD_JOINER = chr(0x2060)


def default_font(language: str) -> str:
    """A font family that covers ``language`` (the Noto families the container image ships)."""
    tag = languages.normalize(language)
    if any(tag == t or tag.startswith(t + "-") for t in _TRADITIONAL):
        return "Noto Sans CJK TC"
    base = languages.base(tag)
    if base in _CJK_FONTS:
        return _CJK_FONTS[base]
    if base == "th":
        return "Noto Sans Thai"
    return "Noto Sans"


def default_size(video: VideoInfo) -> int:
    """Font size in pixels for ``subtitles.font_size = 0``: scales with the picture."""
    return max(12, round(min(video.width, video.height) * SIZE_RATIO))


def _timestamp(seconds: float) -> str:
    """ASS time, ``H:MM:SS.cc``."""
    total = max(0, round(seconds * 100))
    h, rest = divmod(total, 360_000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape(text: str) -> str:
    """Event text as ASS: line breaks become ``\\N``; braces and backslashes stay literal."""
    rows = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for row in rows:
        # A backslash followed by a word joiner is never an override code; braces are escaped as libass reads.
        out.append(row.replace("\\", "\\" + _WORD_JOINER).replace("{", "\\{").replace("}", "\\}"))
    return "\\N".join(out)


def _char_width(char: str) -> float:
    """Rough width of a character as a share of the font size."""
    if unicodedata.east_asian_width(char) in ("W", "F"):
        return 1.0
    return 0.3 if char.isspace() else 0.55


def fits(text: str, size: int, width: int, height: int) -> bool:
    """Whether ``text`` at ``size`` px, wrapped at ``width``, stays within ``height`` (an estimate)."""
    if width <= 0 or height <= 0:
        return False
    rows = 0
    for row in text.split("\n"):
        row_width = sum(_char_width(c) for c in row) * size
        rows += max(1, math.ceil(row_width / width))
    return rows * size * LINE_HEIGHT <= height


def _shrink(text: str, size: int, width: int, height: int) -> int:
    """The largest size down to ``MIN_SHRINK * size`` at which ``text`` fits the box."""
    smallest = max(8, math.ceil(size * MIN_SHRINK))
    for candidate in range(size, smallest - 1, -1):
        if fits(text, candidate, width, height):
            return candidate
    return smallest


def _event_line(event: SubtitleEvent, video: VideoInfo, size: int) -> str:
    tags = [f"\\an{event.alignment}"]
    margin_l = margin_r = 0
    margin_v = event.margin_v
    event_size = event.font_size or size
    if event.box is not None:
        box = event.box
        margin_l = box.x
        margin_r = max(0, video.width - box.x - box.width)
        if event.alignment in (1, 2, 3):
            margin_v = max(0, video.height - box.y - box.height)
        elif event.alignment in (7, 8, 9):
            margin_v = box.y
        elif event.position is None:  # middle row: anchor in the middle of the box
            tags.append(f"\\pos({box.x + box.width // 2},{box.y + box.height // 2})")
        event_size = _shrink(event.text, event_size, box.width, box.height)
    if event.position is not None:
        tags.append(f"\\pos({event.position[0]},{event.position[1]})")
    if event_size != size:
        tags.append(f"\\fs{event_size}")
    text = "{" + "".join(tags) + "}" + escape(event.text)
    return (
        f"Dialogue: 0,{_timestamp(event.start)},{_timestamp(event.end)},Default,,"
        f"{margin_l},{margin_r},{margin_v},,{text}"
    )


def build_ass(events: Sequence[SubtitleEvent], *, video: VideoInfo, font: str, size: int) -> str:
    """The complete ASS file for ``events`` on ``video`` (pixel coordinates of the source video)."""
    outline = max(1, round(size * 0.07))
    shadow = max(1, round(size * 0.03))
    margin_lr = round(video.width * 0.05)
    margin_v = round(video.height * 0.06)
    font_name = font.replace(",", " ")
    header = [
        "[Script Info]",
        "; Written by EraseDub",
        "ScriptType: v4.00+",
        f"PlayResX: {video.width}",
        f"PlayResY: {video.height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},{size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_lr},{margin_lr},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    body = [_event_line(e, video, size) for e in events if e.text.strip()]
    return "\n".join([*header, *body]) + "\n"


def build_srt(events: Sequence[SubtitleEvent]) -> str:
    """The events as SRT text, for a soft subtitle track when ffmpeg cannot burn subtitles."""
    lines = [ScriptLine(start=e.start, end=e.end, text=e.text) for e in events if e.text.strip()]
    return render_srt(sorted(lines, key=lambda line: (line.start, line.end)))
