import json
import re
from pathlib import Path

import pytest

from erasedub import script
from erasedub.errors import ScriptFormatError
from erasedub.script import Script, ScriptLine


def _sample(lang: str = "vi") -> Script:
    return Script(
        source_language="zh",
        target_language=lang,
        lines=(
            ScriptLine(start=0.0, end=1.5, text="Xin chào", source="你好", speaker="A"),
            ScriptLine(start=2.0, end=3.25, text="Dòng hai\ncó hai hàng", source="第二行", speaker="B"),
        ),
    )


def _cues(lines: list[ScriptLine]) -> list[tuple[float, float, str]]:
    return [(x.start, x.end, x.text) for x in lines]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00,000"),
        (3.5, "00:00:03,500"),
        (3725.042, "01:02:05,042"),
        (59.9996, "00:01:00,000"),
        (360000, "100:00:00,000"),
        (3_600_000, "1000:00:00,000"),
    ],
)
def test_format_timestamp(seconds: float, expected: str) -> None:
    assert script.format_timestamp(seconds) == expected


# --- parse_srt: accepted inputs ---------------------------------------------------------------------------

A_B = [(1.0, 2.0, "A"), (3.0, 4.0, "B")]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(
            "1\n00:00:01,000 --> 00:00:02,000\nA\n\n2\n00:00:03,000 --> 00:00:04,000\nB\n", A_B, id="plain"
        ),
        pytest.param(
            "1\n00:00:01,000 --> 00:00:02,000\nA\n2\n00:00:03,000 --> 00:00:04,000\nB",
            A_B,
            id="no-blank-line",
        ),
        pytest.param(
            "00:00:01,000 --> 00:00:02,000\nA\n00:00:03,000 --> 00:00:04,000\nB", A_B, id="no-index-no-blank"
        ),
        pytest.param(
            "﻿1\r\n00:00:01,000 --> 00:00:02,000\r\nA\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nB\r\n",
            A_B,
            id="bom-crlf",
        ),
        pytest.param(
            "1\r00:00:01,000 --> 00:00:02,000\rA\r\r2\r00:00:03,000 --> 00:00:04,000\rB\r", A_B, id="cr-only"
        ),
        pytest.param(
            "00:00:01.0 --> 00:00:02.00\nA\n\n00:00:03,000 --> 00:00:04,000\nB\n", A_B, id="dot-short-ms"
        ),
        pytest.param(
            "\n\n1\n00:00:01,000 --> 00:00:02,000 X1:10 X2:20\nA\n\n\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nB\n\n",
            A_B,
            id="suffix-extra-blanks",
        ),
        pytest.param(
            "1\n00:00:01,000 --> 00:00:02,000\nA\n\nB\n\n2\n00:00:03,000 --> 00:00:04,000\nC\n",
            [(1.0, 2.0, "A\nB"), (3.0, 4.0, "C")],
            id="blank-inside-cue",
        ),
        pytest.param(
            "1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\nB\n",
            [(1.0, 2.0, ""), (3.0, 4.0, "B")],
            id="empty-text",
        ),
        pytest.param("1\n100:00:01,000 --> 100:00:02,000\nA\n", [(360001.0, 360002.0, "A")], id="100-hours"),
        pytest.param(
            "1\n00:00:01,000 --> 00:00:02,000\nroom 42\n", [(1.0, 2.0, "room 42")], id="digits-in-text"
        ),
        pytest.param("", [], id="empty-file"),
    ],
)
def test_parse_srt_accepts(raw: str, expected: list[tuple[float, float, str]]) -> None:
    assert _cues(script.parse_srt(raw)) == expected


# --- parse_srt: rejected inputs ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        pytest.param(
            "1\nnot a timing line\ntext\n", r"script\.vi\.srt:2: text before the first timing", id="no-timing"
        ),
        pytest.param(
            "1\n00:00:05,000 --> 00:00:01,000\ntext\n",
            r"script\.vi\.srt:2: the cue ends before",
            id="reversed",
        ),
        pytest.param(
            "1\n00:75:01,000 --> 00:75:02,000\nA\n", r":2: minutes and seconds must be 00-59", id="minutes-75"
        ),
        pytest.param(
            "1\n00:00:61,000 --> 00:01:02,000\nA\n", r":2: minutes and seconds must be 00-59", id="seconds-61"
        ),
        pytest.param(
            "1\n00:00:01,5000 --> 00:00:02,000\nA\n",
            r":2: milliseconds must have 1-3 digits",
            id="ms-4-digits",
        ),
        pytest.param("1\n00:00:01,000 --> 2s\nA\n", r":2: bad timing line", id="bad-end"),
        pytest.param(
            "\n\n1\n00:00:01,000 --> 00:00:02,000\nA\n\n2\n00:00:09,000 --> 00:00:03,000\nB\n",
            r"script\.vi\.srt:8:",
            id="line-number",
        ),
    ],
)
def test_parse_srt_rejects_with_line_numbers(raw: str, message: str) -> None:
    with pytest.raises(ScriptFormatError, match=message):
        script.parse_srt(raw, source="script.vi.srt")


def test_zero_length_cue_is_dropped_with_a_warning() -> None:
    warnings: list[str] = []
    raw = "1\n00:00:01,000 --> 00:00:01,000\nA\n\n2\n00:00:03,000 --> 00:00:04,000\nB\n"
    assert _cues(script.parse_srt(raw, source="s.srt", warnings=warnings)) == [(3.0, 4.0, "B")]
    assert warnings == ["s.srt:2: zero-length cue dropped"]


# --- render_srt always reads back -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        pytest.param(ScriptLine(start=0, end=1.5, text="Hi"), (0.0, 1.5, "Hi"), id="plain"),
        pytest.param(ScriptLine(start=0, end=1, text="A\n\nB"), (0.0, 1.0, "A\nB"), id="blank-line-in-text"),
        pytest.param(ScriptLine(start=0, end=1, text="A\r\nB\r"), (0.0, 1.0, "A\nB"), id="carriage-returns"),
        pytest.param(ScriptLine(start=0, end=1, text="  \n"), (0.0, 1.0, ""), id="blank-text"),
        pytest.param(ScriptLine(start=1.0001, end=1.0004, text="x"), (1.0, 1.001, "x"), id="sub-millisecond"),
        pytest.param(
            ScriptLine(start=3600 * 1000, end=3600 * 1000 + 1, text="x"),
            (3_600_000.0, 3_600_001.0, "x"),
            id="1000-hours",
        ),
        pytest.param(ScriptLine(start=0, end=1, text="12"), (0.0, 1.0, "12"), id="digits-only-text"),
    ],
)
def test_render_srt_round_trips(line: ScriptLine, expected: tuple[float, float, str]) -> None:
    after = ScriptLine(start=5000, end=5001, text="next")
    parsed = script.parse_srt(script.render_srt([line, after]))
    assert _cues(parsed) == [expected, (5000.0, 5001.0, "next")]


def test_script_line_times_are_whole_milliseconds() -> None:
    line = ScriptLine(start=1.23456, end=2.34567, text="x")
    assert (line.start, line.end) == (1.235, 2.346)
    with pytest.raises(ValueError, match="must be after start"):
        ScriptLine(start=1.0, end=1.0, text="x")


def test_render_srt_never_writes_zero_length_cues() -> None:
    # A line built without validation (model_construct) still produces a readable file.
    line = ScriptLine.model_construct(start=1.0, end=1.0, text="x", source=None, speaker=None)
    assert "00:00:01,000 --> 00:00:01,001" in script.render_srt([line])


# --- sorting and overlaps ---------------------------------------------------------------------------------


def test_sort_lines_sorts_and_reports_overlaps() -> None:
    lines = [
        ScriptLine(start=5, end=6, text="c"),
        ScriptLine(start=0, end=4, text="a"),
        ScriptLine(start=1, end=2, text="b"),
        ScriptLine(start=3, end=5.5, text="d"),
    ]
    ordered, warnings = script.sort_lines(lines, source="s.srt")
    assert [x.text for x in ordered] == ["a", "b", "d", "c"]
    assert warnings[0] == "s.srt: cues were not in time order; they were sorted by start time"
    assert [w.split(" overlap")[0] for w in warnings[1:]] == [
        "s.srt: cues 2 and 3",
        "s.srt: cues 2 and 4",
        "s.srt: cues 4 and 1",
    ]


# --- files ------------------------------------------------------------------------------------------------


def test_files_are_named_per_language(tmp_path: Path) -> None:
    script.save(_sample("vi"), tmp_path)
    script.save(_sample("zh-tw"), tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "script.vi.json",
        "script.vi.srt",
        "script.zh-TW.json",
        "script.zh-TW.srt",
    ]
    assert script.load_for_render(tmp_path, "ZH_tw")[0].target_language == "zh-TW"
    with pytest.raises(ScriptFormatError, match="cannot name a script file"):
        script.srt_name("../etc")


def test_untouched_prepare_output_renders(tmp_path: Path) -> None:
    tricky = Script(
        target_language="vi",
        lines=(
            ScriptLine(start=1.0001, end=1.0004, text="A\n\nB\r"),
            ScriptLine(start=2, end=3, text="C"),
        ),
    )
    script.save(tricky, tmp_path)
    loaded, warnings = script.load_for_render(tmp_path, "vi")
    assert warnings == []
    assert _cues(list(loaded.lines)) == [(1.0, 1.001, "A\nB"), (2.0, 3.0, "C")]


def test_edited_text_keeps_metadata(tmp_path: Path) -> None:
    script.save(_sample(), tmp_path)
    srt = script.srt_path(tmp_path, "vi")
    srt.write_text(srt.read_text(encoding="utf-8").replace("Xin chào", "Chào bạn"), encoding="utf-8")

    loaded, warnings = script.load_for_render(tmp_path, "vi")

    assert warnings == []
    assert loaded.lines[0].text == "Chào bạn"
    assert (loaded.lines[0].source, loaded.lines[0].speaker) == ("你好", "A")


def test_metadata_follows_timing_when_a_line_is_deleted_and_another_added(tmp_path: Path) -> None:
    script.save(_sample(), tmp_path)
    # Same number of cues, but the first line was deleted and a new one added at the end.
    script.srt_path(tmp_path, "vi").write_text(
        "1\n00:00:02,000 --> 00:00:03,250\nDòng hai\n\n2\n00:00:08,000 --> 00:00:09,000\nDòng mới\n",
        encoding="utf-8",
    )
    loaded, warnings = script.load_for_render(tmp_path, "vi")
    assert (loaded.lines[0].source, loaded.lines[0].speaker) == ("第二行", "B")
    # No overlap for the new line; the count rule gives it line 2's metadata.
    assert loaded.lines[1].speaker == "B"
    assert warnings == []


def test_metadata_dropped_for_unmatched_lines_when_counts_differ(tmp_path: Path) -> None:
    script.save(_sample(), tmp_path)
    with script.srt_path(tmp_path, "vi").open("a", encoding="utf-8") as f:
        f.write("\n3\n00:00:04,000 --> 00:00:05,000\nDòng mới\n")

    loaded, warnings = script.load_for_render(tmp_path, "vi")

    assert len(loaded.lines) == 3
    assert loaded.lines[0].speaker == "A"
    assert loaded.lines[2].source is None
    assert len(warnings) == 1
    assert "1 of 3 lines" in warnings[0]


def test_merge_by_overlap_needs_more_than_half() -> None:
    old = [
        ScriptLine(start=0, end=2, text="x", speaker="A"),
        ScriptLine(start=2, end=4, text="y", speaker="B"),
    ]
    new = [ScriptLine(start=1, end=3, text="half and half"), ScriptLine(start=1.5, end=3.9, text="mostly B")]
    merged, unmatched = script.merge_metadata(new, old)
    assert merged[1].speaker == "B"
    assert merged[0].speaker == "A"  # 50% is not a match; the count rule applies
    assert unmatched == 0
    merged, unmatched = script.merge_metadata(new[:1], old)
    assert merged[0].speaker is None
    assert unmatched == 1


def test_overlaps_are_sorted_and_warned_when_rendering(tmp_path: Path) -> None:
    script.save(_sample(), tmp_path)
    script.srt_path(tmp_path, "vi").write_text(
        "1\n00:00:02,000 --> 00:00:03,250\nB\n\n2\n00:00:00,000 --> 00:00:02,500\nA\n", encoding="utf-8"
    )
    loaded, warnings = script.load_for_render(tmp_path, "vi")
    assert [x.text for x in loaded.lines] == ["A", "B"]
    assert any("sorted" in w for w in warnings)
    assert any("cues 2 and 1 overlap" in w for w in warnings)


def test_utf16_srt_is_read(tmp_path: Path) -> None:
    script.save(_sample(), tmp_path)
    srt = script.srt_path(tmp_path, "vi")
    srt.write_bytes(srt.read_text(encoding="utf-8").encode("utf-16"))  # with BOM, like Notepad "Unicode"
    loaded, _ = script.load_for_render(tmp_path, "vi")
    assert loaded.lines[0].text == "Xin chào"


def test_ansi_srt_gets_utf8_advice(tmp_path: Path) -> None:
    script.save(_sample(), tmp_path)
    script.srt_path(tmp_path, "vi").write_bytes("1\n00:00:00,000 --> 00:00:01,000\n中文\n".encode("gbk"))
    with pytest.raises(ScriptFormatError, match=r"script\.vi\.srt: .*Save it again as UTF-8"):
        script.load_for_render(tmp_path, "vi")


def test_srt_errors_name_the_file_and_line(tmp_path: Path) -> None:
    script.save(_sample(), tmp_path)
    script.srt_path(tmp_path, "vi").write_text("1\n00:00:01,000 --> 00:00:61,000\nA\n", encoding="utf-8")
    with pytest.raises(ScriptFormatError, match=r"^script\.vi\.srt:2: minutes and seconds"):
        script.load_for_render(tmp_path, "vi")


def test_load_for_render_requires_prepare(tmp_path: Path) -> None:
    with pytest.raises(ScriptFormatError, match=r"prepare --to vi"):
        script.load_for_render(tmp_path, "vi")


def test_newer_script_version_is_explained(tmp_path: Path) -> None:
    path = script.json_path(tmp_path, "vi")
    path.write_text(json.dumps({"version": 2, "target_language": "vi", "lines": [], "new_field": 1}), "utf-8")
    with pytest.raises(ScriptFormatError, match="newer EraseDub"):
        script.load_json(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"version": 0, "target_language": "vi"}', "version"),
        ('{"version": 1}', "target_language"),
        ('{"version": 1, "target_language": "vi",\n "lines": [}', r":2: not valid JSON"),
    ],
)
def test_bad_script_json(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "script.vi.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ScriptFormatError, match=message):
        script.load_json(path)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("00:00:03,500", 3.5), (" 01:02:03.4 ", 3723.4), ("0:0:0,000", 0.0), ("100:00:00,000", 360000.0)],
)
def test_parse_timestamp(text: str, seconds: float) -> None:
    assert script.parse_timestamp(text) == pytest.approx(seconds)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("1.5", "bad time '1.5', expected 00:00:01,500"),
        ("", "bad time ''"),
        ("00:00:01,000 --> 00:00:02,000", "bad time"),
        ("00:60:00,000", "minutes and seconds must be 00-59"),
        ("00:00:01,5000", "milliseconds must have 1-3 digits"),
    ],
)
def test_parse_timestamp_rejects(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(message)}"):
        script.parse_timestamp(text)


def test_parse_timestamp_follows_the_srt_parser() -> None:
    """The web UI table and the SRT reader must agree on every timestamp."""
    for text in ("00:00:03,500", "00:00:03.5", "1:2:3,04"):
        cue = script.parse_srt(f"{text} --> 99:00:00,000\nx\n")[0]
        assert script.parse_timestamp(text) == cue.start


def test_looks_like_timing() -> None:
    assert script.looks_like_timing("00:00:01,000 --> 00:00:02,000")
    assert script.looks_like_timing("  1:2:3.4-->")
    assert not script.looks_like_timing("Xin chào --> tạm biệt")
    assert not script.looks_like_timing("00:00:01,000")
