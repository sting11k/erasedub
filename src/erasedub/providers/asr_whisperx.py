from __future__ import annotations

import gc
import os
import sys
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import Field

from erasedub import languages
from erasedub.context import RunContext
from erasedub.errors import EraseDubError
from erasedub.models import Transcript, TranscriptSegment, Word
from erasedub.providers.base import PLUGIN_API_VERSION, Availability, ProviderOptions, Transcriber

# whisperx imports pyannote-audio, which warns that torchcodec cannot load FFmpeg 4-7 shared libraries (the
# Docker image and the Windows bundle ship a static FFmpeg 8). That only affects decoding from file paths;
# this provider always passes in-memory waveforms, so the warning does not apply.
warnings.filterwarnings("ignore", message=r"\s*torchcodec is not installed correctly", category=UserWarning)

#: Models picked by ``model = "auto"``: large on an NVIDIA GPU, small on the CPU.
AUTO_MODEL_GPU = "large-v3"
AUTO_MODEL_CPU = "small"

ComputeType = Literal["auto", "float16", "float32", "int8", "int8_float16"]


class WhisperXOptions(ProviderOptions):
    model: str = Field(default="auto", min_length=1)
    """Whisper model: ``tiny``, ``base``, ``small``, ``medium``, ``large-v3``, ... or a local path.

    ``auto`` picks ``large-v3`` on an NVIDIA GPU and ``small`` on the CPU. The engine passes
    ``asr.model`` from the configuration here.
    """
    compute_type: ComputeType = "auto"
    """``auto`` = ``float16`` on CUDA, ``int8`` on the CPU."""
    batch_size: int = Field(default=8, ge=1, le=64)
    align: bool = False
    """Run WhisperX forced alignment to get word timestamps (``TranscriptSegment.words``).

    Off by default: some per-language alignment models are licensed for non-commercial use only (the
    default Vietnamese wav2vec2 model is CC BY-NC 4.0). Segment timestamps are enough for plain subtitles
    and dubbing. Turn it on only after checking the licence of the alignment model for your language.
    """
    align_model: str | None = None
    """Alignment model to use instead of WhisperX's default for the language (a Hugging Face model id)."""
    diarize: bool = False
    """Label speakers with pyannote (``TranscriptSegment.speaker``). Needs ``HF_TOKEN`` with access to the
    gated model; without it, speakers are left empty and a warning is logged."""
    min_speakers: int | None = Field(default=None, ge=1)
    max_speakers: int | None = Field(default=None, ge=1)


class WhisperXTranscriber(Transcriber):
    """WhisperX (BSD-2-Clause): faster-whisper speech recognition, optional forced alignment (``align``)
    and speaker labels (``diarize``). Models are downloaded into the cache directory on first use."""

    name: ClassVar[str] = "whisperx"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = "WhisperX speech recognition (faster-whisper)"
    requires_modules: ClassVar[tuple[str, ...]] = ("whisperx",)
    extra: ClassVar[str | None] = "asr"
    Options: ClassVar[type[ProviderOptions]] = WhisperXOptions

    options: WhisperXOptions
    _model: Any = None
    _align: tuple[str, Any, Any] | None = None  # (language, model, metadata)
    _diarizer: Any = None

    def check(self) -> Availability:
        status = super().check()
        if not status.ok and _unsupported_python():
            return Availability(False, "WhisperX supports Python 3.11-3.13; install EraseDub on one of those")
        return status

    def open(self, ctx: RunContext) -> None:
        import whisperx

        device, index = _split_device(ctx.device)
        model = self.options.model
        if model == "auto":
            model = AUTO_MODEL_GPU if device == "cuda" else AUTO_MODEL_CPU
        compute = self.options.compute_type
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        ctx.logger.info("WhisperX: model %s on %s (%s)", model, ctx.device, compute)
        self._model = whisperx.load_model(
            model,
            device,
            device_index=index,
            compute_type=compute,
            download_root=str(ctx.cache_dir / "whisperx"),
        )

    def close(self) -> None:
        had_models = any(m is not None for m in (self._model, self._align, self._diarizer))
        self._model = self._align = self._diarizer = None
        if had_models:
            gc.collect()
            _free_cuda_memory()

    def transcribe(self, audio: Path, *, language: str | None = None, ctx: RunContext) -> Transcript:
        import whisperx

        if self._model is None:
            self.open(ctx)
        wave = whisperx.load_audio(str(audio))
        hint = languages.base(language) if language else None

        def on_asr(percent: float) -> None:
            ctx.raise_if_cancelled()
            ctx.progress(0.6 * percent / 100, "recognising speech")

        result: dict[str, Any] = self._model.transcribe(
            wave, batch_size=self.options.batch_size, language=hint, progress_callback=on_asr
        )
        detected = str(result.get("language") or hint or "und")

        if self.options.align and result.get("segments"):
            ctx.raise_if_cancelled()
            ctx.progress(0.6, "aligning words")
            model, metadata = self._align_model(detected, ctx)

            def on_align(percent: float) -> None:
                ctx.raise_if_cancelled()
                ctx.progress(0.6 + 0.25 * percent / 100, "aligning words")

            aligned = whisperx.align(
                result["segments"], model, metadata, wave, ctx.device, progress_callback=on_align
            )
            result = {**aligned, "language": detected}

        if self.options.diarize:
            result = self._label_speakers(wave, result, ctx)

        ctx.progress(1.0, "speech recognised")
        return to_transcript(result.get("segments") or [], detected)

    def _align_model(self, language: str, ctx: RunContext) -> tuple[Any, Any]:
        import whisperx

        if self._align is None or self._align[0] != language:
            self._align = None
            try:
                model, metadata = whisperx.load_align_model(
                    language_code=language,
                    device=ctx.device,
                    model_name=self.options.align_model,
                    model_dir=str(ctx.cache_dir / "whisperx-align"),
                )
            except ValueError as exc:  # no default alignment model for this language
                raise EraseDubError(
                    f"WhisperX has no alignment model for language '{language}': set asr.options.align_model "
                    "to a wav2vec2 model for it, or turn asr.options.align off"
                ) from exc
            self._align = (language, model, metadata)
        return self._align[1], self._align[2]

    def _label_speakers(self, wave: Any, result: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        token = os.environ.get("HF_TOKEN")
        if not token:
            ctx.logger.warning("speaker labels skipped: set HF_TOKEN to use the gated pyannote model")
            return result
        import whisperx
        from whisperx.diarize import DiarizationPipeline

        ctx.raise_if_cancelled()
        ctx.progress(0.85, "labelling speakers")
        if self._diarizer is None:
            self._diarizer = DiarizationPipeline(
                token=token, device=ctx.device, cache_dir=str(ctx.cache_dir / "pyannote")
            )
        segments = self._diarizer(
            wave, min_speakers=self.options.min_speakers, max_speakers=self.options.max_speakers
        )
        labelled: dict[str, Any] = whisperx.assign_word_speakers(segments, result)
        return labelled


def to_transcript(segments: Sequence[Mapping[str, Any]], language: str) -> Transcript:
    """Build a :class:`Transcript` from WhisperX segments (aligned or not).

    Empty segments are dropped; a segment whose end is not after its start gets 10 ms, and words without
    timing (WhisperX leaves some numbers untimed) keep ``start``/``end`` empty.
    """
    out: list[TranscriptSegment] = []
    for seg in segments:
        text = str(seg.get("text") or "").strip()
        start, end = _time(seg.get("start")), _time(seg.get("end"))
        if not text or start is None:
            continue
        if end is None or end <= start:
            end = start + 0.01
        words = tuple(_word(w) for w in seg.get("words") or () if str(w.get("word") or "").strip())
        speaker = seg.get("speaker")
        out.append(
            TranscriptSegment(
                start=start,
                end=end,
                text=text,
                speaker=str(speaker) if speaker else None,
                words=words,
            )
        )
    try:
        code = languages.normalize(language)
    except ValueError:
        code = language
    return Transcript(language=code, segments=tuple(out))


def _word(item: Mapping[str, Any]) -> Word:
    start, end = _time(item.get("start")), _time(item.get("end"))
    if start is not None and end is not None and end < start:
        end = start
    score = item.get("score")
    return Word(
        text=str(item["word"]).strip(),
        start=start,
        end=end,
        score=float(score) if isinstance(score, int | float) and score == score else None,
    )


def _time(value: object) -> float | None:
    """A finite, non-negative time in seconds, or ``None`` (missing or NaN in WhisperX output)."""
    if not isinstance(value, int | float) or value != value or value in (float("inf"), float("-inf")):
        return None
    return max(0.0, float(value))


def _unsupported_python() -> bool:
    """WhisperX (and PyTorch under it) publishes no wheels for Python 3.14 yet."""
    return sys.version_info >= (3, 14)


def _split_device(device: str) -> tuple[str, int]:
    """``"cuda:1"`` -> ``("cuda", 1)``, ``"cpu"`` -> ``("cpu", 0)`` (WhisperX takes them separately)."""
    name, _, index = device.partition(":")
    return name, int(index) if index else 0


def _free_cuda_memory() -> None:
    """Return cached GPU memory to the driver after the models are dropped."""
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
