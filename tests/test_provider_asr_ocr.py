"""WhisperX and RapidOCR providers, with the libraries replaced by fakes (no model is loaded)."""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from erasedub import registry
from erasedub.context import RunContext
from erasedub.errors import EraseDubError
from erasedub.models import VideoInfo
from erasedub.providers import asr_whisperx, ocr_rapidocr
from erasedub.providers.ocr_rapidocr import Detection
from erasedub.script import Script, ScriptLine

# --- WhisperX ---------------------------------------------------------------------------------------------

SEGMENTS = [
    {"start": 0.5, "end": 2.0, "text": " 你好 "},
    {"start": 2.0, "end": 2.0, "text": "短"},
    {"start": 3.0, "end": 4.0, "text": "   "},
]


def _fake_whisperx(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    class Model:
        def transcribe(self, audio: Any, **kwargs: Any) -> dict[str, Any]:
            calls["transcribe"] = kwargs
            kwargs["progress_callback"](50.0)
            return {"segments": [dict(s) for s in SEGMENTS], "language": kwargs["language"] or "zh"}

    def load_model(arch: str, device: str, **kwargs: Any) -> Model:
        calls["load_model"] = {"arch": arch, "device": device, **kwargs}
        return Model()

    def load_align_model(**kwargs: Any) -> tuple[str, dict[str, str]]:
        calls["load_align_model"] = kwargs
        if kwargs["language_code"] == "xx":
            raise ValueError("No default align-model for language: xx")
        return "align-model", {"language": kwargs["language_code"]}

    def align(
        segments: list[dict[str, Any]], model: Any, metadata: Any, audio: Any, device: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["align"] = {"device": device, "model": model}
        return {
            "segments": [
                {
                    "start": 0.5,
                    "end": 2.0,
                    "text": "你好",
                    "words": [
                        {"word": "你", "start": 0.5, "end": 1.0, "score": 0.9},
                        {"word": "好", "start": float("nan"), "end": float("nan")},
                    ],
                }
            ],
            "word_segments": [],
        }

    def assign_word_speakers(diarized: Any, result: dict[str, Any]) -> dict[str, Any]:
        calls["assign"] = diarized
        return {**result, "segments": [{**s, "speaker": "SPEAKER_00"} for s in result["segments"]]}

    class DiarizationPipeline:
        def __init__(self, **kwargs: Any) -> None:
            calls["diarizer"] = kwargs

        def __call__(self, audio: Any, **kwargs: Any) -> str:
            calls["diarize"] = kwargs
            return "diarized"

    module = types.ModuleType("whisperx")
    module.load_model = load_model  # type: ignore[attr-defined]
    module.load_audio = lambda path: f"wave:{path}"  # type: ignore[attr-defined]
    module.load_align_model = load_align_model  # type: ignore[attr-defined]
    module.align = align  # type: ignore[attr-defined]
    module.assign_word_speakers = assign_word_speakers  # type: ignore[attr-defined]
    diarize = types.ModuleType("whisperx.diarize")
    diarize.DiarizationPipeline = DiarizationPipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "whisperx", module)
    monkeypatch.setitem(sys.modules, "whisperx.diarize", diarize)
    return calls


def test_whisperx_auto_model_on_cpu_is_small_int8(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext, tmp_path: Path
) -> None:
    calls = _fake_whisperx(monkeypatch)
    progress: list[float] = []
    run = RunContext(
        tmp_dir=ctx.tmp_dir, cache_dir=ctx.cache_dir, on_progress=lambda f, m: progress.append(f)
    )
    asr = registry.create("asr", "whisperx")
    asr.open(run)
    transcript = asr.transcribe(tmp_path / "a.wav", ctx=run)
    asr.close()
    assert calls["load_model"] == {
        "arch": "small",
        "device": "cpu",
        "device_index": 0,
        "compute_type": "int8",
        "download_root": str(ctx.cache_dir / "whisperx"),
    }
    assert calls["transcribe"]["language"] is None
    assert transcript.language == "zh"
    assert [(s.start, s.end, s.text) for s in transcript.segments] == [(0.5, 2.0, "你好"), (2.0, 2.01, "短")]
    assert all(s.words == () for s in transcript.segments)
    assert "align" not in calls
    assert progress[0] == pytest.approx(0.3) and progress[-1] == 1.0


def test_whisperx_on_cuda_uses_large_float16_and_the_device_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake_whisperx(monkeypatch)
    run = RunContext(tmp_dir=tmp_path / "t", cache_dir=tmp_path / "c", device="cuda:1")
    asr = registry.create("asr", "whisperx", {"model": "medium", "batch_size": 16})
    asr.transcribe(tmp_path / "a.wav", language="zh-TW", ctx=run)  # opens itself when needed
    assert calls["load_model"]["arch"] == "medium"
    assert calls["load_model"]["device"] == "cuda"
    assert calls["load_model"]["device_index"] == 1
    assert calls["load_model"]["compute_type"] == "float16"
    assert calls["transcribe"]["language"] == "zh"
    assert calls["transcribe"]["batch_size"] == 16

    auto = registry.create("asr", "whisperx")
    auto.open(run)
    assert calls["load_model"]["arch"] == asr_whisperx.AUTO_MODEL_GPU


def test_whisperx_align_gives_word_timestamps(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext, tmp_path: Path
) -> None:
    calls = _fake_whisperx(monkeypatch)
    transcript = registry.create("asr", "whisperx", {"align": True}).transcribe(
        tmp_path / "a.wav", language="zh", ctx=ctx
    )
    assert calls["load_align_model"]["language_code"] == "zh"
    assert calls["load_align_model"]["model_dir"] == str(ctx.cache_dir / "whisperx-align")
    words = transcript.segments[0].words
    assert [(w.text, w.start, w.end, w.score) for w in words] == [
        ("你", 0.5, 1.0, 0.9),
        ("好", None, None, None),
    ]


def test_whisperx_align_without_a_model_for_the_language(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext, tmp_path: Path
) -> None:
    _fake_whisperx(monkeypatch)
    with pytest.raises(EraseDubError, match="no alignment model for language 'xx'"):
        registry.create("asr", "whisperx", {"align": True}).transcribe(
            tmp_path / "a.wav", language="xx", ctx=ctx
        )


def test_whisperx_diarize_needs_hf_token(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    calls = _fake_whisperx(monkeypatch)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    asr = registry.create("asr", "whisperx", {"diarize": True, "max_speakers": 2})
    with caplog.at_level(logging.WARNING, logger="erasedub"):
        transcript = asr.transcribe(tmp_path / "a.wav", ctx=ctx)
    assert "set HF_TOKEN" in caplog.text
    assert "diarizer" not in calls
    assert transcript.segments[0].speaker is None

    monkeypatch.setenv("HF_TOKEN", "hf-test-not-real")
    transcript = asr.transcribe(tmp_path / "a.wav", ctx=ctx)
    assert calls["diarizer"]["token"] == "hf-test-not-real"  # noqa: S105 - a fake value
    assert calls["diarize"] == {"min_speakers": None, "max_speakers": 2}
    assert transcript.segments[0].speaker == "SPEAKER_00"


def test_whisperx_check_explains_python_314(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asr_whisperx, "_unsupported_python", lambda: True)
    monkeypatch.setattr("erasedub.providers.base.module_available", lambda name: False)
    status = registry.create("asr", "whisperx").check()
    assert not status.ok and "Python 3.11-3.13" in status.reason


# --- RapidOCR: tracking -----------------------------------------------------------------------------------


def _det(box: tuple[int, int, int, int], text: str = "你好", score: float = 0.9) -> Detection:
    return Detection(box=box, text=text, score=score)


def _merge(samples: list[tuple[float, list[Detection]]], **kwargs: Any) -> list[Any]:
    options: dict[str, Any] = {
        "interval": 1.0,
        "duration": 10.0,
        "min_iou": 0.5,
        "min_text_similarity": 0.6,
        "max_gap": 1,
        "padding": None,
        "frame_size": (1000, 1000),
    }
    options.update(kwargs)
    return ocr_rapidocr.merge_detections(samples, **options)


def test_merge_follows_one_subtitle_over_time() -> None:
    sub = (400, 850, 600, 900)
    regions = _merge([(0.0, []), (1.0, [_det(sub)]), (2.0, [_det((402, 848, 604, 902), "你好!")]), (3.0, [])])
    assert len(regions) == 1
    region = regions[0]
    assert (region.start, region.end) == (0.0, 3.0)  # padded by one sample interval on both sides
    assert (region.box.x, region.box.y, region.box.width, region.box.height) == (400, 848, 204, 54)
    assert region.kind == "subtitle"
    assert region.confidence == pytest.approx(0.9)


def test_merge_splits_when_the_text_changes_and_bridges_a_missed_sample() -> None:
    sub = (400, 850, 600, 900)
    samples = [
        (0.0, [_det(sub, "第一句话")]),
        (1.0, []),  # missed once: still the same region
        (2.0, [_det(sub, "第一句话")]),
        (3.0, [_det(sub, "完全不同的内容")]),
    ]
    regions = _merge(samples, padding=0.0)
    assert [(r.start, r.end, r.text) for r in regions] == [
        (0.0, 2.0, "第一句话"),
        (3.0, 3.0 + 1.0, "完全不同的内容"),
    ]


def test_merge_ends_a_region_after_max_gap_and_clamps_to_duration() -> None:
    sub = (400, 850, 600, 900)
    samples = [(0.0, [_det(sub)]), (1.0, []), (2.0, []), (3.0, [_det(sub)])]
    regions = _merge(samples, duration=3.5, padding=0.5, max_gap=1)
    assert [(r.start, r.end) for r in regions] == [(0.0, 0.5), (2.5, 3.5)]


def test_classify_kinds() -> None:
    size = (1000, 1000)
    assert ocr_rapidocr.classify((400, 850, 600, 900), 1, 2, size, 10) == "subtitle"
    assert ocr_rapidocr.classify((400, 50, 600, 100), 1, 2, size, 10) == "title"
    assert ocr_rapidocr.classify((10, 10, 100, 40), 1, 2, size, 10) == "scene"
    assert ocr_rapidocr.classify((10, 10, 100, 40), 0, 9, size, 10) == "overlay"


def test_read_output_clips_boxes_and_drops_low_scores() -> None:
    result = types.SimpleNamespace(
        boxes=[
            [[-5.2, 10.0], [120.7, 10.0], [120.7, 40.1], [-5.2, 40.1]],
            [[0, 0], [10, 0], [10, 10], [0, 10]],
            [[50, 50], [50, 50], [50, 50], [50, 50]],
        ],
        txts=("字幕", "low", "dot"),
        scores=(0.95, 0.2, 0.99),
    )
    dets = ocr_rapidocr.read_output(result, 100, 100, 0.5)
    assert [(d.box, d.text) for d in dets] == [((0, 10, 100, 41), "字幕")]
    assert ocr_rapidocr.read_output(types.SimpleNamespace(boxes=None), 100, 100, 0.5) == []


@pytest.mark.parametrize(
    ("hints", "rec_version", "rec_lang"),
    [
        ((), "PP-OCRv6", "ch"),
        (("zh",), "PP-OCRv6", "ch"),
        (("zh-TW",), "PP-OCRv6", "chinese_cht"),
        (("vi",), "PP-OCRv6", "vi"),
        (("ja",), "PP-OCRv6", "japan"),
        (("ko",), "PP-OCRv5", "korean"),
        (("ru",), "PP-OCRv5", "eslav"),
        (("not a code", "th"), "PP-OCRv5", "th"),
        (("xx",), "PP-OCRv6", "ch"),
    ],
)
def test_language_hints_pick_the_recognition_model(
    hints: tuple[str, ...], rec_version: str, rec_lang: str
) -> None:
    params = ocr_rapidocr.rapidocr_params(hints, "small")
    assert params["Rec.ocr_version"] == rec_version
    assert params["Rec.lang_type"] == rec_lang


# --- RapidOCR: detect() with fake OpenCV and RapidOCR -----------------------------------------------------


class _Frame:
    shape = (1000, 1000, 3)


def _fake_cv2(monkeypatch: pytest.MonkeyPatch, frames: int, fps: float) -> dict[str, int]:
    state = {"index": -1, "retrieved": 0}

    class VideoCapture:
        def __init__(self, path: str) -> None:
            state["index"] = -1

        def isOpened(self) -> bool:  # noqa: N802 - OpenCV's name
            return True

        def get(self, prop: int) -> float:
            return {5: fps, 7: float(frames), 3: 1000.0, 4: 1000.0}.get(prop, 0.0)

        def grab(self) -> bool:
            state["index"] += 1
            return state["index"] < frames

        def retrieve(self) -> tuple[bool, _Frame]:
            state["retrieved"] += 1
            return True, _Frame()

        def release(self) -> None:
            pass

    cv2 = types.SimpleNamespace(
        VideoCapture=VideoCapture,
        CAP_PROP_FPS=5,
        CAP_PROP_FRAME_COUNT=7,
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        CAP_PROP_POS_MSEC=0,
    )
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    return state


def _fake_rapidocr(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []

    class RapidOCR:
        def __init__(self, params: dict[str, Any]) -> None:
            created.append(params)

        def __call__(self, frame: Any) -> Any:
            quad = [[400, 850], [600, 850], [600, 900], [400, 900]]
            return types.SimpleNamespace(boxes=[quad], txts=("字幕",), scores=(0.9,))

    monkeypatch.setitem(sys.modules, "rapidocr", types.SimpleNamespace(RapidOCR=RapidOCR))
    return created


def test_detect_samples_one_frame_per_second(monkeypatch: pytest.MonkeyPatch, ctx: RunContext) -> None:
    state = _fake_cv2(monkeypatch, frames=100, fps=25.0)  # 4 s
    monkeypatch.setattr(ocr_rapidocr, "_to_enums", lambda params: dict(params))
    created = _fake_rapidocr(monkeypatch)
    ocr = registry.create("ocr", "rapidocr")
    regions = ocr.detect(Path("v.mp4"), languages=["vi"], ctx=ctx)
    assert state["retrieved"] == 4  # t = 0, 1, 2, 3
    assert created[0]["Rec.lang_type"] == "vi"
    assert created[0]["Global.model_root_dir"] == str(ctx.cache_dir / "rapidocr")
    assert len(regions) == 1
    assert (regions[0].start, regions[0].end, regions[0].text) == (0.0, 4.0, "字幕")
    ocr.detect(Path("v.mp4"), languages=["vi"], ctx=ctx)
    assert len(created) == 1  # the engine is reused for the same language


def test_detect_reports_an_unreadable_video(monkeypatch: pytest.MonkeyPatch, ctx: RunContext) -> None:
    _fake_cv2(monkeypatch, frames=0, fps=25.0)
    monkeypatch.setattr(ocr_rapidocr, "_to_enums", lambda params: dict(params))
    _fake_rapidocr(monkeypatch)
    with pytest.raises(EraseDubError, match="no video frames"):
        registry.create("ocr", "rapidocr").detect(Path("v.mp4"), ctx=ctx)


# --- Bottom layout ----------------------------------------------------------------------------------------


def test_bottom_layout_wraps_inside_a_centred_box(ctx: RunContext) -> None:
    layout = registry.create("layout", "bottom")
    script = Script(target_language="vi", lines=(ScriptLine(start=0, end=1, text=" Xin chào "),))
    video = VideoInfo(path=Path("v.mp4"), width=1080, height=1920, duration=2, fps=30)
    (event,) = layout.layout(script, video=video, ctx=ctx)
    assert event.text == "Xin chào"
    assert event.margin_v == 154
    assert event.box is not None
    assert (event.box.x, event.box.width) == (54, 972)
    assert event.box.y + event.box.height == 1920 - 154
