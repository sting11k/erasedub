import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from erasedub.models import Box, SubtitleEvent, TextRegion, TimeSpan, TranscriptSegment, VideoInfo, Word


@pytest.mark.parametrize("bad", [math.inf, math.nan, -1.0])
def test_times_must_be_finite_and_non_negative(bad: float) -> None:
    with pytest.raises(ValidationError):
        TimeSpan(start=0, end=bad)
    with pytest.raises(ValidationError):
        VideoInfo(path=Path("v.mp4"), width=1, height=1, duration=bad, fps=30)


def test_timespan_needs_positive_duration() -> None:
    assert TimeSpan(start=1, end=2.5).duration == 1.5
    with pytest.raises(ValidationError, match="must be after start"):
        TimeSpan(start=2, end=2)


def test_words_may_be_untimed_or_zero_length() -> None:
    untimed = Word(text="2024")
    assert untimed.start is None and untimed.end is None
    assert Word(text="a", start=1.0, end=1.0).end == 1.0
    assert Word(text="a", start=1.0).end is None
    with pytest.raises(ValidationError, match="must not be before"):
        Word(text="a", start=2.0, end=1.0)
    with pytest.raises(ValidationError):
        Word(text="a", start=math.nan)
    segment = TranscriptSegment(
        start=0, end=2, text="in 2024", words=(Word(text="in", start=0, end=0.4), untimed)
    )
    assert segment.words[1].start is None


def test_subtitle_event_drawing_fields_are_optional() -> None:
    plain = SubtitleEvent(start=0, end=1, text="Hi")
    assert (plain.alignment, plain.position, plain.box, plain.font_size, plain.style) == (
        2,
        None,
        None,
        None,
        None,
    )
    placed = SubtitleEvent(
        start=0,
        end=1,
        text="Hi",
        alignment=8,
        position=(540, 200),
        box=Box(x=40, y=150, width=1000, height=120),
        font_size=48,
        style="emphasis",
    )
    assert placed.position == (540, 200)
    assert SubtitleEvent.model_validate_json(placed.model_dump_json()) == placed


@pytest.mark.parametrize(
    "fields",
    [{"position": (-1, 0)}, {"font_size": 0}, {"style": ""}, {"alignment": 10}, {"unknown_field": True}],
)
def test_subtitle_event_rejects_bad_drawing_fields(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SubtitleEvent.model_validate({"start": 0, "end": 1, "text": "Hi", **fields})


def test_text_region_round_trips() -> None:
    region = TextRegion(
        start=0.5, end=2, box=Box(x=0, y=1700, width=1080, height=160), text="字幕", confidence=0.9
    )
    assert TextRegion.model_validate_json(region.model_dump_json()) == region
