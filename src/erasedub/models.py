"""Data passed between pipeline steps and providers.

These models are the stable contract between the engine and providers (built-in or third-party).
Times are in seconds from the start of the video; boxes are in pixels of the source video. Every float must
be finite (no ``inf``/``nan``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, model_validator


class FrozenModel(BaseModel):
    """Immutable model that rejects unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class TimeSpan(FrozenModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> TimeSpan:
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be after start ({self.start})")
        return self

    @property
    def duration(self) -> float:
        return self.end - self.start


class Word(FrozenModel):
    """One recognised word. Timing is optional: aligners leave some words (often numbers) untimed."""

    text: str
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)
    score: float | None = None

    @model_validator(mode="after")
    def _check_order(self) -> Word:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError(f"end ({self.end}) must not be before start ({self.start})")
        return self


class TranscriptSegment(TimeSpan):
    """One recognised segment (roughly a sentence) with its own timing.

    ``words`` is optional and usually empty: it is only filled by transcribers that produce word timestamps
    (for WhisperX, only with the ``align`` option). Nothing in the pipeline may require it.
    """

    text: str
    speaker: str | None = None
    words: tuple[Word, ...] = ()


class Transcript(FrozenModel):
    """Result of speech recognition. Segment timing is always present; word timing is optional."""

    language: str
    segments: tuple[TranscriptSegment, ...]


class Box(FrozenModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


RegionKind = Literal["subtitle", "title", "overlay", "scene"]

#: How closely a translation follows the source: ``faithful`` keeps the meaning close, ``natural`` lets
#: the translator rewrite for fluency.
TranslationStyle = Literal["faithful", "natural"]


class TextRegion(TimeSpan):
    """On-screen text found by OCR: where it is and when it is visible."""

    box: Box
    kind: RegionKind = "subtitle"
    text: str | None = None
    confidence: float | None = None


class VideoInfo(FrozenModel):
    path: Path
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration: float = Field(gt=0)
    fps: float = Field(gt=0)
    has_audio: bool = True


class Voice(FrozenModel):
    id: str
    language: str
    name: str
    gender: Literal["female", "male", "neutral"] | None = None
    preview_url: str | None = None


class SynthResult(FrozenModel):
    audio: Path
    duration: float = Field(gt=0)


class SubtitleEvent(TimeSpan):
    """One subtitle: its text and where and how to draw it.

    Only ``text`` and the timing are required; every drawing field left at its default means "use the
    renderer's default". Coordinates are pixels of the source video, origin top-left.

    ``alignment``
        ASS numpad code for the anchor point (1-3 bottom, 4-6 middle, 7-9 top; 2 = bottom centre).
    ``margin_v``
        Distance from the top or bottom edge in pixels (ignored when ``position`` is set).
    ``position``
        Absolute ``(x, y)`` of the anchor point (like ASS ``\\pos``); overrides the margins.
    ``box``
        Area the text should stay inside; the renderer wraps and shrinks the text to fit.
    ``font_size``
        Font size in pixels; ``None`` = the configured size.
    ``style``
        Name of a style the renderer knows; ``None`` = the default style.

    The engine's subtitle renderer turns events into ASS and burns them in; layouts only describe them.
    """

    text: str
    alignment: int = Field(default=2, ge=1, le=9)
    margin_v: int = Field(default=0, ge=0)
    position: tuple[NonNegativeInt, NonNegativeInt] | None = None
    box: Box | None = None
    font_size: int | None = Field(default=None, gt=0)
    style: str | None = Field(default=None, min_length=1)
