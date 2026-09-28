"""On-screen text detection with RapidOCR.

The video is sampled at ``sample_fps`` frames per second with OpenCV; each sampled frame goes through
RapidOCR (detection + recognition). Detections are then followed over time: a box in the next sample that
overlaps it (IoU) and reads about the same text continues the same region, so one subtitle line becomes one
:class:`TextRegion` with the time it was visible and the union of its boxes.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import Field

from erasedub import languages
from erasedub.context import RunContext
from erasedub.errors import ConfigError, EraseDubError
from erasedub.models import Box, RegionKind, TextRegion
from erasedub.providers.base import PLUGIN_API_VERSION, ProviderOptions, TextDetector

#: Recognition languages of the multilingual PP-OCRv6 models (RapidOCR 3.9), by RapidOCR's name.
_V6_LANGS = frozenset(
    [
        "ch",
        "chinese_cht",
        "en",
        "japan",
        "af",
        "az",
        "bs",
        "ca",
        "cs",
        "cy",
        "da",
        "de",
        "es",
        "et",
        "eu",
        "fi",
        "fr",
        "ga",
        "gl",
        "hr",
        "hu",
        "id",
        "is",
        "it",
        "ku",
        "la",
        "lb",
        "lt",
        "lv",
        "mi",
        "ms",
        "mt",
        "nl",
        "no",
        "oc",
        "pl",
        "pt",
        "qu",
        "rm",
        "ro",
        "sk",
        "sl",
        "sq",
        "sv",
        "sw",
        "tl",
        "tr",
        "uz",
        "vi",
    ]
)
#: Scripts PP-OCRv6 does not read: PP-OCRv5 has a model for each (RapidOCR ``LangRec`` values).
_V5_LANGS: dict[str, str] = {
    "ko": "korean",
    "th": "th",
    "el": "el",
    "ar": "arabic",
    "fa": "arabic",
    "ur": "arabic",
    "ug": "arabic",
    "ru": "eslav",
    "uk": "eslav",
    "be": "eslav",
    "bg": "cyrillic",
    "sr": "cyrillic",
    "mk": "cyrillic",
    "mn": "cyrillic",
    "kk": "cyrillic",
    "ky": "cyrillic",
    "tg": "cyrillic",
    "hi": "devanagari",
    "mr": "devanagari",
    "ne": "devanagari",
    "sa": "devanagari",
    "ta": "ta",
    "te": "te",
}
_ALIASES: dict[str, str] = {"zh": "ch", "ja": "japan", "nb": "no", "nn": "no", "fil": "tl"}


class RapidOcrOptions(ProviderOptions):
    #: Frames examined per second of video.
    sample_fps: float = Field(default=1.0, gt=0, le=10)
    #: Lowest recognition score kept (0-1).
    min_score: float = Field(default=0.5, ge=0, le=1)
    #: PP-OCRv6 model size (``tiny`` is faster; it cannot read Japanese).
    model_type: Literal["tiny", "small", "medium"] = "small"
    #: Boxes in consecutive samples belong to one region when they overlap at least this much (IoU).
    min_iou: float = Field(default=0.5, gt=0, le=1)
    #: ... and their texts are at least this similar (0-1).
    min_text_similarity: float = Field(default=0.6, ge=0, le=1)
    #: Samples a region may be missing (a missed detection) before it ends.
    max_gap: int = Field(default=1, ge=0, le=10)
    #: Seconds added before the first and after the last sample a text was seen in; ``None`` = one sample
    #: interval, which covers every frame where the text may have been visible.
    time_padding: float | None = Field(default=None, ge=0, le=5)


@dataclass
class Detection:
    """One text box found in one sampled frame, in pixels (x0, y0, x1, y1)."""

    box: tuple[int, int, int, int]
    text: str
    score: float


@dataclass
class _Track:
    first: float
    last: float
    box: tuple[int, int, int, int]
    last_box: tuple[int, int, int, int]
    text: str
    best_score: float
    scores: list[float] = field(default_factory=list)
    misses: int = 0

    def add(self, time: float, det: Detection) -> None:
        self.last, self.last_box, self.misses = time, det.box, 0
        x0, y0, x1, y1 = self.box
        a0, b0, a1, b1 = det.box
        self.box = (min(x0, a0), min(y0, b0), max(x1, a1), max(y1, b1))
        self.scores.append(det.score)
        if det.score > self.best_score:
            self.text, self.best_score = det.text, det.score


class RapidOcrDetector(TextDetector):
    """RapidOCR (Apache-2.0, PP-OCR models on ONNX Runtime): CPU-friendly text detection.

    Models are downloaded into the cache directory on first use. ``languages`` picks the recognition model:
    PP-OCRv6 for Chinese, Japanese, English and Latin-script languages (Vietnamese included), PP-OCRv5 for
    Korean, Thai, Greek, Arabic, Cyrillic, Devanagari, Tamil and Telugu.
    """

    name: ClassVar[str] = "rapidocr"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = "RapidOCR on-screen text detection (CPU)"
    requires_modules: ClassVar[tuple[str, ...]] = ("rapidocr", "onnxruntime", "cv2")
    extra: ClassVar[str | None] = "ocr"
    Options: ClassVar[type[ProviderOptions]] = RapidOcrOptions

    options: RapidOcrOptions
    _engine: Any = None
    _engine_key: tuple[str, ...] | None = None

    def close(self) -> None:
        self._engine = self._engine_key = None

    def detect(self, video: Path, *, languages: Sequence[str] = (), ctx: RunContext) -> list[TextRegion]:
        import cv2

        engine = self._ocr(languages, ctx)
        capture = cv2.VideoCapture(str(video))
        if not capture.isOpened():
            raise EraseDubError(f"cannot open {video.name} for text detection")
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
            count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            duration = count / fps if fps > 0 and count > 0 else None
            samples = self._samples(capture, cv2, fps, duration, engine, ctx)
            regions = merge_detections(
                samples,
                interval=1 / self.options.sample_fps,
                duration=duration,
                min_iou=self.options.min_iou,
                min_text_similarity=self.options.min_text_similarity,
                max_gap=self.options.max_gap,
                padding=self.options.time_padding,
                frame_size=(
                    int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                ),
            )
        finally:
            capture.release()
        ctx.progress(1.0, f"{len(regions)} text regions found")
        return regions

    def _samples(
        self, capture: Any, cv2: Any, fps: float, duration: float | None, engine: Any, ctx: RunContext
    ) -> list[tuple[float, list[Detection]]]:
        """OCR results of the sampled frames, as ``(time, detections)`` in time order."""
        interval = 1 / self.options.sample_fps
        samples: list[tuple[float, list[Detection]]] = []
        next_time = 0.0
        index = -1
        while capture.grab():
            index += 1
            time = index / fps if fps > 0 else capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if time + 1e-6 < next_time:
                continue
            while next_time <= time + 1e-6:
                next_time += interval
            ctx.raise_if_cancelled()
            ok, frame = capture.retrieve()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            samples.append((time, read_output(engine(frame), width, height, self.options.min_score)))
            if duration:
                ctx.progress(time / duration, f"reading on-screen text {time:.0f}/{duration:.0f} s")
        if not samples:
            raise EraseDubError("no video frames could be read for text detection")
        return samples

    def _ocr(self, hints: Sequence[str], ctx: RunContext) -> Any:
        params = rapidocr_params(hints, self.options.model_type)
        key = tuple(f"{k}={v}" for k, v in sorted(params.items()))
        if self._engine is None or self._engine_key != key:
            from rapidocr import RapidOCR

            base: dict[str, Any] = {
                "Global.model_root_dir": str(ctx.cache_dir / "rapidocr"),
                "Global.log_level": "warning",
                "Global.text_score": self.options.min_score,
            }
            try:
                self._engine = RapidOCR(params={**base, **_to_enums(params)})
            except ValueError as exc:  # a language the chosen model size cannot read
                raise ConfigError(f"RapidOCR: {exc}") from exc
            self._engine_key = key
        return self._engine


def rapidocr_params(hints: Sequence[str], model_type: str) -> dict[str, str]:
    """RapidOCR settings for the first usable language hint (Chinese when there is none).

    Returned as plain strings; :func:`_to_enums` turns them into RapidOCR's enums.
    """
    for hint in hints:
        try:
            tag = languages.normalize(hint)
        except ValueError:
            continue
        base = languages.base(tag)
        if base == "zh":
            traditional = "-Hant" in tag or tag.endswith(("-TW", "-HK", "-MO"))
            return _v6("chinese_cht" if traditional else "ch", model_type)
        name = _ALIASES.get(base, base)
        if name in _V6_LANGS:
            return _v6(name, model_type)
        if base in _V5_LANGS:
            return {
                "Det.ocr_version": "PP-OCRv5",
                "Det.lang_type": "ch",
                "Det.model_type": "mobile",
                "Rec.ocr_version": "PP-OCRv5",
                "Rec.lang_type": _V5_LANGS[base],
                "Rec.model_type": "mobile",
            }
    return _v6("ch", model_type)


def _v6(lang: str, model_type: str) -> dict[str, str]:
    return {
        "Det.ocr_version": "PP-OCRv6",
        "Det.model_type": model_type,
        "Rec.ocr_version": "PP-OCRv6",
        "Rec.model_type": model_type,
        "Rec.lang_type": lang,
    }


def _to_enums(params: dict[str, str]) -> dict[str, Any]:
    from rapidocr import LangDet, LangRec, ModelType, OCRVersion

    out: dict[str, Any] = {}
    for key, value in params.items():
        field_name = key.rsplit(".", 1)[1]
        if field_name == "ocr_version":
            out[key] = OCRVersion(value)
        elif field_name == "model_type":
            out[key] = ModelType(value)
        elif key == "Det.lang_type":
            out[key] = LangDet(value)
        elif key == "Rec.lang_type" and value in {m.value for m in LangRec}:
            out[key] = LangRec(value)
        else:
            out[key] = value  # PP-OCRv6 takes plain language names ("vi", "de", ...)
    return out


def read_output(result: Any, width: int, height: int, min_score: float) -> list[Detection]:
    """Detections from a RapidOCR result: quadrilaterals become boxes clipped to the frame."""
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None) or ()
    scores = getattr(result, "scores", None) or ()
    if boxes is None:
        return []
    out: list[Detection] = []
    for quad, text, score in zip(boxes, texts, scores, strict=False):
        if float(score) < min_score or not str(text).strip():
            continue
        xs = [float(p[0]) for p in quad]
        ys = [float(p[1]) for p in quad]
        x0, y0 = max(0, math.floor(min(xs))), max(0, math.floor(min(ys)))
        x1, y1 = min(width, math.ceil(max(xs))), min(height, math.ceil(max(ys)))
        if x1 > x0 and y1 > y0:
            out.append(Detection(box=(x0, y0, x1, y1), text=str(text).strip(), score=float(score)))
    return out


def merge_detections(
    samples: Iterable[tuple[float, Sequence[Detection]]],
    *,
    interval: float,
    duration: float | None,
    min_iou: float,
    min_text_similarity: float,
    max_gap: int,
    padding: float | None,
    frame_size: tuple[int, int],
) -> list[TextRegion]:
    """Follow detections over the sampled frames and turn each track into a :class:`TextRegion`."""
    open_tracks: list[_Track] = []
    done: list[_Track] = []
    for time, detections in samples:
        pairs = sorted(
            (
                (iou(track.last_box, det.box), t, d)
                for t, track in enumerate(open_tracks)
                for d, det in enumerate(detections)
            ),
            reverse=True,
        )
        used_tracks: set[int] = set()
        used_dets: set[int] = set()
        for overlap, t, d in pairs:
            if overlap < min_iou or t in used_tracks or d in used_dets:
                continue
            if similarity(open_tracks[t].text, detections[d].text) < min_text_similarity:
                continue
            open_tracks[t].add(time, detections[d])
            used_tracks.add(t)
            used_dets.add(d)
        still_open: list[_Track] = []
        for t, track in enumerate(open_tracks):
            if t not in used_tracks:
                track.misses += 1
            (done if track.misses > max_gap else still_open).append(track)
        for d, det in enumerate(detections):
            if d not in used_dets:
                still_open.append(
                    _Track(
                        first=time,
                        last=time,
                        box=det.box,
                        last_box=det.box,
                        text=det.text,
                        best_score=det.score,
                        scores=[det.score],
                    )
                )
        open_tracks = still_open
    done += open_tracks

    pad = interval if padding is None else padding
    regions = []
    for track in sorted(done, key=lambda tr: (tr.first, tr.box[1], tr.box[0])):
        start = max(0.0, track.first - pad)
        end = track.last + pad
        if duration:
            end = min(end, duration)
        if end <= start:
            end = start + interval
        x0, y0, x1, y1 = track.box
        regions.append(
            TextRegion(
                start=round(start, 3),
                end=round(end, 3),
                box=Box(x=x0, y=y0, width=x1 - x0, height=y1 - y0),
                kind=classify(track.box, track.first, track.last, frame_size, duration),
                text=track.text,
                confidence=round(sum(track.scores) / len(track.scores), 4),
            )
        )
    return regions


def classify(
    box: tuple[int, int, int, int],
    first: float,
    last: float,
    frame_size: tuple[int, int],
    duration: float | None,
) -> RegionKind:
    """A rough kind for a region, from where and how long it is visible.

    ``overlay``: on screen for at least 80 % of the video (logo, watermark, channel name). ``subtitle``:
    horizontally centred in the lower 45 % of the frame. ``title``: centred in the upper 30 %. Anything
    else is ``scene`` text (signs, packaging).
    """
    width, height = frame_size
    if duration and last - first >= 0.8 * duration:
        return "overlay"
    if width <= 0 or height <= 0:
        return "subtitle"
    x0, y0, x1, y1 = box
    centred = abs((x0 + x1) / 2 - width / 2) <= 0.15 * width
    middle_y = (y0 + y1) / 2
    if centred and middle_y >= 0.55 * height:
        return "subtitle"
    if centred and middle_y <= 0.30 * height:
        return "title"
    return "scene"


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection over union of two (x0, y0, x1, y1) boxes."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def similarity(a: str, b: str) -> float:
    """How alike two OCR readings are (0-1), ignoring spaces and case."""
    x, y = "".join(a.split()).casefold(), "".join(b.split()).casefold()
    if not x or not y:
        return 1.0
    return SequenceMatcher(None, x, y).ratio()
