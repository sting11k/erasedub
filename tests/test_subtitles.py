from pathlib import Path

from erasedub import subtitles
from erasedub.models import Box, SubtitleEvent, VideoInfo

VIDEO = VideoInfo(path=Path("clip.mp4"), width=1080, height=1920, duration=10, fps=30)


def test_default_font_covers_the_language() -> None:
    assert subtitles.default_font("vi") == "Noto Sans"
    assert subtitles.default_font("zh") == "Noto Sans CJK SC"
    assert subtitles.default_font("zh-TW") == "Noto Sans CJK TC"
    assert subtitles.default_font("zh-Hant-HK") == "Noto Sans CJK TC"
    assert subtitles.default_font("ja") == "Noto Sans CJK JP"
    assert subtitles.default_font("th") == "Noto Sans Thai"


def test_default_size_follows_the_shorter_side() -> None:
    assert subtitles.default_size(VIDEO) == 59  # 1080 * 0.055
    small = VIDEO.model_copy(update={"width": 100, "height": 80})
    assert subtitles.default_size(small) == 12


def test_escape_keeps_braces_and_backslashes_literal() -> None:
    assert subtitles.escape("a\nb") == "a\\Nb"
    assert subtitles.escape("{\\b1}") == "\\{\\⁠b1\\}"


def test_build_ass_draws_every_event() -> None:
    events = [
        SubtitleEvent(start=1, end=2.5, text="Xin chào"),
        SubtitleEvent(start=3, end=4, text="   "),  # blank: skipped
        SubtitleEvent(start=4, end=5, text="trên", alignment=8, margin_v=40, font_size=30),
        SubtitleEvent(start=5, end=6, text="ở đây", position=(540, 300)),
    ]
    ass = subtitles.build_ass(events, video=VIDEO, font="Noto Sans", size=59)
    assert "PlayResX: 1080\nPlayResY: 1920" in ass
    assert "Style: Default,Noto Sans,59," in ass
    dialogue = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert dialogue == [
        "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\an2}Xin chào",
        "Dialogue: 0,0:00:04.00,0:00:05.00,Default,,0,0,40,,{\\an8\\fs30}trên",
        "Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,{\\an2\\pos(540,300)}ở đây",
    ]


def test_a_box_sets_the_margins_and_shrinks_long_text() -> None:
    box = Box(x=100, y=1500, width=880, height=100)
    short = SubtitleEvent(start=0, end=1, text="ok", box=box)
    long = SubtitleEvent(start=0, end=1, text="một câu rất dài " * 6, box=box)
    ass = subtitles.build_ass([short, long], video=VIDEO, font="Noto Sans", size=59)
    first, second = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert ",100,100,320,," in first  # left, right, and bottom margin from the box
    assert "\\fs" not in first
    assert "\\fs37" in second  # the largest size at which the estimate fits the 100 px box


def test_build_srt_orders_the_events() -> None:
    events = [SubtitleEvent(start=2, end=3, text="b"), SubtitleEvent(start=0, end=1, text="a")]
    assert subtitles.build_srt(events).startswith("1\n00:00:00,000 --> 00:00:01,000\na\n")
