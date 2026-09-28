"""The engine: runs the plan of ``prepare`` / ``render`` / ``run`` for one video.

The CLI and the web UI both plan a video with :func:`erasedub.pipeline.plan_video` and hand the plan to
:func:`execute`. Every provider is used through :func:`opened`, so it is always closed (models freed) even
when a step fails or the user cancels, and its results are checked, so a provider that breaks its contract
fails with a message that names it.

``prepare``
    extract the audio (ffmpeg) -> transcribe -> detect on-screen text (``regions.json``, when in the plan) ->
    translate -> ``script.<lang>.json`` + ``script.<lang>.srt`` per target language.
``render``
    per video: erase the burned-in text (when in the plan), on a worker thread while the voices are made; per
    target language: read the (edited) script, speak each line and fit it to its time slot
    (:mod:`erasedub.audio`), lay out and draw the subtitles (:mod:`erasedub.subtitles`), mix the audio and
    write ``<stem>.<lang>.mp4`` (H.264 + AAC, ``+faststart``). Without libass in ffmpeg the subtitles become
    a separate subtitle track, with one notice.
``run``
    ``prepare`` then ``render``.

The erased picture and every voice clip are kept in the video's work folder with a fingerprint of what made
them, so rendering again after editing a few lines only redoes what changed.
"""

from __future__ import annotations

import contextvars
import dataclasses
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError

from erasedub import __version__, audio, hardware, languages, media, registry, script, subtitles
from erasedub.context import RunContext, validate_device
from erasedub.errors import ConfigError, EraseDubError, ProviderUnavailableError, describe_validation_error
from erasedub.links import doc
from erasedub.models import SubtitleEvent, SynthResult, TextRegion, Transcript, TranslationStyle
from erasedub.pipeline import VideoPlan, output_path
from erasedub.providers.base import (
    EraserSpec,
    GpuBackend,
    Provider,
    SpeechSynthesizer,
    TextEraser,
    Translator,
)
from erasedub.regions import RegionsFile, load_regions, regions_path, save_regions
from erasedub.script import Script, ScriptLine

P = TypeVar("P", bound=Provider)
T = TypeVar("T")

#: Receives ``(overall fraction 0-1, message)``; may be called from a worker thread.
ProgressFn = Callable[[float, str], None]
#: Receives each notice (something the user should know, not an error) as soon as it is known.
NoticeFn = Callable[[str], None]

#: Environment variable that moves the cache of downloaded models (``RunContext.cache_dir``).
CACHE_ENV = "ERASEDUB_CACHE_DIR"
#: Name of the erased picture kept in the work folder, and of the file describing what made it.
ERASED_NAME = "erased.mp4"
ERASED_KEY_NAME = "erased.json"
#: Folder in the work folder that keeps the voice clips of each language.
VOICE_DIR = "voice"


# --- Provider helpers -------------------------------------------------------------------------------------


@contextmanager
def opened(provider: P, ctx: RunContext) -> Iterator[P]:
    """``open(ctx)`` the provider, yield it, and always ``close()`` it (even if ``open`` failed)."""
    try:
        provider.open(ctx)
        yield provider
    finally:
        provider.close()


def translate_lines(
    translator: Translator,
    lines: Sequence[ScriptLine],
    *,
    target: str,
    source: str | None = None,
    style: TranslationStyle = "faithful",
    glossary: Mapping[str, str] | None = None,
    ctx: RunContext,
) -> list[str]:
    """Call ``translator.translate`` and check it returned exactly one string per line."""
    if not lines:
        return []
    ctx.raise_if_cancelled()
    result = translator.translate(
        lines, target=target, source=source, style=style, glossary=glossary, ctx=ctx
    )
    if not isinstance(result, list) or not all(isinstance(text, str) for text in result):
        raise EraseDubError(
            f"translator '{translator.name}' did not return a list of strings (a bug in that provider)."
        )
    if len(result) != len(lines):
        raise EraseDubError(
            f"translator '{translator.name}' returned {len(result)} translations for {len(lines)} lines. A "
            "translator must return exactly one translation per line, without merging or splitting lines. "
            "This is a bug in that provider; try another translator."
        )
    return result


def _checked_list(value: object, item: type[T], what: str) -> list[T]:
    """``value`` as a list of ``item``; anything else is a bug in the provider named by ``what``."""
    if not isinstance(value, list | tuple) or not all(isinstance(v, item) for v in value):
        raise EraseDubError(f"{what} did not return a list of {item.__name__} (a bug in that provider).")
    return list(value)


# --- Machine ----------------------------------------------------------------------------------------------


def cache_dir() -> Path:
    """Where downloaded models are kept between runs: ``$ERASEDUB_CACHE_DIR``, else the user cache folder."""
    override = os.environ.get(CACHE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg) / "erasedub"
    system: str = sys.platform  # a plain str, so type checkers keep every branch
    if system == "win32":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        return (Path(local) if local else Path.home() / "AppData" / "Local") / "EraseDub" / "cache"
    if system == "darwin":
        return Path.home() / "Library" / "Caches" / "EraseDub"
    return Path.home() / ".cache" / "erasedub"


def resolve_device() -> str:
    """The run's device: ``"cuda"`` when an NVIDIA GPU is present and PyTorch can use it, else ``"cpu"``.

    Speech recognition and every other step use it as it is. Erasing may use an Apple GPU instead of the CPU
    (see ``_Job._erase_device``).
    """
    if not hardware.detect_nvidia_gpus():
        return "cpu"
    return "cuda" if hardware.cuda_usable("cuda").usable else "cpu"


# --- Progress ---------------------------------------------------------------------------------------------


class _Progress:
    """Overall progress of a run from the progress of its weighted steps. Thread-safe."""

    def __init__(self, weights: Mapping[str, float], report: ProgressFn) -> None:
        self._weights = dict(weights)
        self._total = sum(self._weights.values()) or 1.0
        self._done = dict.fromkeys(self._weights, 0.0)
        self._report = report
        self._lock = threading.Lock()

    def reporter(self, key: str, label: str) -> ProgressFn:
        def report(fraction: float, message: str) -> None:
            with self._lock:
                if key in self._done:  # a step without weight still shows its message
                    self._done[key] = max(self._done[key], fraction)
                overall = sum(self._weights[k] * v for k, v in self._done.items()) / self._total
            text = label if not message or message == label else f"{label}: {message}"
            self._report(min(1.0, overall), text)

        return report


def _ignore(fraction: float, message: str) -> None:
    return None


# --- Result -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    """What a run wrote."""

    #: ``script.<lang>.srt`` files written by ``prepare``.
    scripts: tuple[Path, ...] = ()
    #: Finished videos written by ``render``, in the order of the target languages.
    outputs: tuple[Path, ...] = ()
    #: Everything reported through ``on_notice``.
    notices: tuple[str, ...] = ()


def _fingerprint(data: Mapping[str, Any]) -> str:
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_id(path: Path) -> list[Any]:
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns]


# --- The run ----------------------------------------------------------------------------------------------


def execute(
    request: VideoPlan,
    video: Path,
    *,
    on_progress: ProgressFn | None = None,
    on_notice: NoticeFn | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    device: str | None = None,
    logger: logging.Logger | None = None,
) -> Result:
    """Run ``request`` (from :func:`erasedub.pipeline.plan_video`) on ``video`` and return what it wrote.

    ``on_progress`` receives the overall progress, ``on_notice`` each notice when it is known, and
    ``is_cancelled`` is polled between steps and by every provider (Ctrl+C in the CLI, *Cancel* in the web
    UI).
    ``device`` overrides :func:`resolve_device`. Raises :class:`~erasedub.errors.EraseDubError` subclasses
    for everything expected (their exit codes are the CLI's), including ``CancelledError``.
    """
    tools = media.find_tools()
    info = media.probe(tools, video)
    abort = threading.Event()
    user_cancelled = is_cancelled or (lambda: False)

    def cancelled() -> bool:
        return abort.is_set() or user_cancelled()

    with tempfile.TemporaryDirectory(prefix="erasedub-", ignore_cleanup_errors=True) as tmp:
        ctx = RunContext(
            tmp_dir=Path(tmp),
            cache_dir=cache_dir(),
            device=device or resolve_device(),
            logger=logger or logging.getLogger("erasedub"),
            is_cancelled=cancelled,
        )
        job = _Job(request, video, info, tools, ctx, abort, on_progress or _ignore, on_notice)
        return job.run()


class _Job:
    """One ``execute`` call: the plan, the video, and what has been written so far."""

    def __init__(
        self,
        request: VideoPlan,
        video: Path,
        info: media.MediaInfo,
        tools: media.Tools,
        ctx: RunContext,
        abort: threading.Event,
        report: ProgressFn,
        on_notice: NoticeFn | None,
    ) -> None:
        self.config = request.config
        self.plan = request.plan
        self.command = request.command
        self.work = request.work
        self.video = video
        self.info = info
        self.tools = tools
        self.ctx = ctx
        self.abort = abort
        self.on_notice = on_notice
        self.notices: list[str] = []
        self._notice_lock = threading.Lock()
        self.languages: list[str] = list(self.config.general.target_languages)
        self.progress = _Progress(self._weights(), report)

    # -- bookkeeping

    def notice(self, text: str) -> None:
        with self._notice_lock:
            if text in self.notices:
                return
            self.notices.append(text)
        self.ctx.logger.info("notice: %s", text)
        if self.on_notice is not None:
            self.on_notice(text)

    def step(self, key: str, label: str) -> RunContext:
        """The run context for one step: its progress counts toward the whole run."""
        self.ctx.raise_if_cancelled()
        return dataclasses.replace(self.ctx, on_progress=self.progress.reporter(key, label))

    def _weights(self) -> dict[str, float]:
        weights: dict[str, float] = {}
        if self.command in ("prepare", "run"):
            weights["extract"] = 2
            weights["transcribe"] = 30
            if self.plan.enabled("detect-text"):
                weights["detect"] = 20
            for lang in self.languages:
                weights[f"translate:{lang}"] = 4
        if self.command in ("render", "run"):
            if self.plan.enabled("erase"):
                weights["erase"] = 40
            for lang in self.languages:
                if self.plan.enabled("speak"):
                    weights[f"speak:{lang}"] = 15
                if self.plan.enabled("subtitles"):
                    weights[f"subtitles:{lang}"] = 1
                weights[f"mux:{lang}"] = 12
        return weights

    def run(self) -> Result:
        scripts: list[Path] = []
        outputs: list[Path] = []
        if self.command in ("prepare", "run"):
            scripts = self.prepare()
        if self.command in ("render", "run"):
            outputs = self.render()
        return Result(scripts=tuple(scripts), outputs=tuple(outputs), notices=tuple(self.notices))

    # -- prepare

    def prepare(self) -> list[Path]:
        cfg = self.config
        if not self.info.video.has_audio:
            raise EraseDubError(f"{self.video.name} has no audio track, so there is no speech to transcribe.")
        self.work.mkdir(parents=True, exist_ok=True)
        ctx = self.step("extract", "Extracting the audio")
        wav = media.extract_audio(
            self.tools, self.video, ctx.tmp_dir / "speech.wav", ctx=ctx, duration=self.info.video.duration
        )
        transcript = self.transcribe(wav)
        source = cfg.general.source_language or _language_or_none(transcript.language)
        lines = [
            ScriptLine(start=seg.start, end=seg.end, text=seg.text.strip(), source=seg.text.strip(),
                       speaker=seg.speaker)
            for seg in sorted(transcript.segments, key=lambda s: (s.start, s.end))
            if seg.text.strip()
        ]  # fmt: skip
        if not lines:
            self.notice(f"No speech was recognised in {self.video.name}; the scripts are empty.")
        if self.plan.enabled("detect-text"):
            self.detect_text(source)
        return self.translate(lines, source)

    def _asr_options(self) -> dict[str, Any]:
        """``asr.options`` plus ``asr.model`` as the ``model`` option (``asr.options.model`` wins)."""
        cfg = self.config.asr
        if "model" in registry.load_class("asr", cfg.provider).Options.model_fields:
            return {"model": cfg.model, **cfg.options}
        if cfg.model != "auto":
            raise ConfigError(f"asr.model: the asr provider '{cfg.provider}' has no model option")
        return dict(cfg.options)

    def transcribe(self, wav: Path) -> Transcript:
        cfg = self.config
        ctx = self.step("transcribe", "Transcribing speech")
        asr = registry.create("asr", cfg.asr.provider, self._asr_options())
        with opened(asr, ctx):
            transcript = asr.transcribe(wav, language=cfg.general.source_language, ctx=ctx)
        if not isinstance(transcript, Transcript):
            raise EraseDubError(
                f"asr provider '{asr.name}' did not return a Transcript (a bug in that provider)."
            )
        ctx.progress(1.0, "")
        return transcript

    def detect_text(self, source: str | None) -> None:
        cfg = self.config
        ctx = self.step("detect", "Detecting on-screen text")
        ocr = registry.create("ocr", cfg.ocr.provider, cfg.ocr.options)
        with opened(ocr, ctx):
            found = ocr.detect(self.video, languages=[source] if source else [], ctx=ctx)
        found_regions = _checked_list(found, TextRegion, f"ocr provider '{ocr.name}'")
        save_regions(RegionsFile(video=self.info.video, regions=tuple(found_regions)), self.work)
        if not found_regions:
            self.notice(f"No on-screen text was found in {self.video.name}; there is nothing to erase.")
        ctx.progress(1.0, "")

    def translate(self, lines: list[ScriptLine], source: str | None) -> list[Path]:
        cfg = self.config
        written: list[Path] = []
        translator: Translator | None = None
        needs = [lang for lang in self.languages if not _same_language(source, lang)]
        if needs and lines:
            translator = registry.create("translator", cfg.translate.provider, cfg.translate.options)
        with ExitStack() as stack:
            if translator is not None:
                stack.enter_context(opened(translator, self.ctx))
            for lang in self.languages:
                ctx = self.step(f"translate:{lang}", f"Translating into {lang}")
                texts = [line.text for line in lines]
                if translator is not None and lang in needs:
                    texts = translate_lines(
                        translator,
                        lines,
                        target=lang,
                        source=source,
                        style=cfg.translate.style,
                        glossary=cfg.translate.glossary,
                        ctx=ctx,
                    )
                translated = tuple(
                    line.model_copy(update={"text": text.strip()})
                    for line, text in zip(lines, texts, strict=True)
                )
                result = Script(source_language=source, target_language=lang, lines=translated)
                script.save(result, self.work)
                written.append(script.srt_path(self.work, lang))
                ctx.progress(1.0, "")
        return written

    # -- render

    def render(self) -> list[Path]:
        scripts: dict[str, Script] = {}
        for lang in self.languages:  # read every script first: a bad edit fails before any GPU time
            loaded, warnings = script.load_for_render(self.work, lang)
            for warning in warnings:
                self.notice(warning)
            scripts[lang] = loaded
        outputs: list[Path] = []
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="erasedub-erase") as pool:
            picture: Future[Path] | None = None
            if self.plan.enabled("erase"):
                # In the caller's context: progress callbacks may rely on context variables (the web UI's do).
                picture = pool.submit(contextvars.copy_context().run, self.erase)
            try:
                for lang in self.languages:
                    outputs.append(self.render_language(lang, scripts[lang], picture))
            except BaseException:
                self.abort.set()  # stop the eraser too; leaving the pool waits for it
                raise
        return outputs

    def _regions(self) -> RegionsFile | None:
        return load_regions(self.work) if regions_path(self.work).is_file() else None

    def erase(self) -> Path:
        """The clean picture: erased by the configured eraser on the configured GPU backend."""
        cfg = self.config
        ctx = self.step("erase", "Erasing burned-in text")
        found = load_regions(self.work)
        measured, current = found.video, self.info.video
        if (measured.width, measured.height) != (current.width, current.height):
            raise EraseDubError(
                f"{regions_path(self.work)} was made for a {measured.width}x{measured.height} video, but "
                f"{self.video.name} is {current.width}x{current.height}. Run `erasedub prepare` on this "
                "video again."
            )
        if not found.regions:
            self.notice(
                f"{regions_path(self.work).name} lists no on-screen text; the picture is kept as it is."
            )
            ctx.progress(1.0, "")
            return self.video
        try:
            spec = EraserSpec(name=cfg.erase.provider, options=cfg.erase.options)
        except ValidationError as exc:
            raise ConfigError(f"erase.options: {describe_validation_error(exc)}") from exc
        kept, key_file = self.work / ERASED_NAME, self.work / ERASED_KEY_NAME
        key = _fingerprint(
            {
                "video": _file_id(self.video),
                "regions": hashlib.sha256(regions_path(self.work).read_bytes()).hexdigest(),
                "eraser": spec.model_dump(mode="json"),
                "erasedub": __version__,
            }
        )
        if kept.is_file() and key_file.is_file() and key_file.read_text(encoding="utf-8").strip() == key:
            self.ctx.logger.info("reusing the erased picture %s", kept)
            ctx.progress(1.0, "reusing the erased picture from the last render")
            return kept
        backend = registry.create("gpu", cfg.erase.gpu)
        ctx = dataclasses.replace(ctx, device=self._erase_device(spec, backend))
        ctx.logger.info("erasing with %s on %s (%s)", spec.name, backend.name, ctx.device)
        with opened(backend, ctx):
            clean = backend.run_eraser(spec, self.video, found.regions, ctx.tmp_dir / ERASED_NAME, ctx=ctx)
        if not isinstance(clean, Path) or not clean.is_file():
            raise EraseDubError(
                f"eraser '{spec.name}' on GPU backend '{cfg.erase.gpu}' did not return an existing video "
                "file (a bug in that provider)."
            )
        if clean.resolve() == self.video.resolve():
            return self.video
        key_file.unlink(missing_ok=True)
        shutil.move(str(clean), kept)
        key_file.write_text(key + "\n", encoding="utf-8")
        ctx.progress(1.0, "")
        return kept

    def _erase_device(self, spec: EraserSpec, backend: GpuBackend) -> str:
        """The device for erasing on this machine.

        An explicit ``device`` in the eraser's options wins (the eraser checks it too). Otherwise: the run's
        CUDA device when there is one; for an eraser that needs an NVIDIA GPU, ``"cuda"`` anyway, so the local
        backend reports why PyTorch cannot use the GPU (a CPU-only build, say) instead of erasing on the CPU;
        else ``"mps"`` for an eraser that supports Apple GPUs when :func:`hardware.mps_usable`; else the CPU.
        A remote backend picks the device on its own machine.
        """
        if backend.remote:
            return self.ctx.device
        explicit = spec.options.get("device")
        if isinstance(explicit, str) and explicit.strip().lower() not in ("", "auto"):
            try:
                return validate_device(explicit)
            except ConfigError:
                return self.ctx.device  # not a device name the engine knows: the eraser validates its option
        if self.ctx.device.startswith("cuda"):
            return self.ctx.device
        eraser = registry.load_class("eraser", spec.name)
        if eraser.requires_gpu:
            return "cuda"
        if issubclass(eraser, TextEraser) and eraser.supports_mps and hardware.mps_usable():
            return "mps"
        return "cpu"

    def render_language(self, lang: str, loaded: Script, picture: Future[Path] | None) -> Path:
        target = output_path(self.config, self.video, lang)
        voice = self.speak(lang, loaded) if self.plan.enabled("speak") else None
        subs = self.draw_subtitles(lang, loaded) if self.plan.enabled("subtitles") else None
        clean = picture.result() if picture is not None else self.video
        self.mux(lang, clean, voice, subs, target)
        return target

    # -- voice

    def _voice(self, tts: SpeechSynthesizer, lang: str, ctx: RunContext) -> str:
        configured = self.config.tts.voice.strip()
        if configured and configured != "auto":
            return configured
        voices = tts.voices(lang, ctx=ctx)
        if not voices:
            raise ProviderUnavailableError(
                f"tts provider '{tts.name}' has no voice for {lang}. Set tts.voice to a voice id, or use "
                "another tts provider."
            )
        return voices[0].id

    def speak(self, lang: str, loaded: Script) -> Path | None:
        """The voice track of ``lang`` (a WAV starting at 0 s), or None when no line has text."""
        cfg = self.config
        ctx = self.step(f"speak:{lang}", f"Voice-over ({lang})")
        spoken = [line for line in loaded.lines if line.text.strip()]
        if not spoken:
            ctx.progress(1.0, "")
            return None
        slots = audio.slots([(line.start, line.end) for line in spoken], video_end=self.info.video.duration)
        folder = self.work / VOICE_DIR / lang
        folder.mkdir(parents=True, exist_ok=True)
        tts = registry.create("tts", cfg.tts.provider, cfg.tts.options)
        clips: list[tuple[audio.Slot, SynthResult]] = []
        with opened(tts, ctx):
            voice = self._voice(tts, lang, ctx)
            for i, (line, slot) in enumerate(zip(spoken, slots, strict=True)):
                ctx.raise_if_cancelled()
                ctx.progress(0.8 * i / len(spoken), f"line {i + 1}/{len(spoken)}")
                clips.append((slot, self._clip(tts, voice, lang, line.text, slot, folder, ctx)))
        placements = audio.place([(slot, clip.duration) for slot, clip in clips])
        notice = audio.timing_notice(placements, script_name=script.srt_name(lang))
        if notice:
            self.notice(notice)
        pieces: list[tuple[float, Path]] = []
        for i, (placed, (_, clip)) in enumerate(zip(placements, clips, strict=True)):
            pcm = ctx.tmp_dir / f"voice-{lang}-{i:05d}.pcm"
            args = ["-i", str(clip.audio.resolve())]
            tempo = audio.atempo_filter(placed.tempo)
            if tempo:
                args += ["-af", tempo]
            args += ["-ac", "1", "-ar", str(audio.SAMPLE_RATE), "-f", "s16le", "-c:a", "pcm_s16le", str(pcm)]
            media.run_ffmpeg(
                self.tools, args, ctx=_quiet(ctx), doing=f"fitting voice line {placed.slot.number}"
            )
            pieces.append((placed.start, pcm))
            ctx.progress(0.8 + 0.2 * (i + 1) / len(clips), "building the voice track")
        track = ctx.tmp_dir / f"voice-{lang}.wav"
        audio.write_track(pieces, track)
        for _, pcm in pieces:
            pcm.unlink(missing_ok=True)
        return track

    def _clip(
        self,
        tts: SpeechSynthesizer,
        voice: str,
        lang: str,
        text: str,
        slot: audio.Slot,
        folder: Path,
        ctx: RunContext,
    ) -> SynthResult:
        """One line's voice, from the work folder when the same text was already spoken the same way."""
        cfg = self.config.tts
        key = _fingerprint(
            {
                "provider": cfg.provider,
                "options": cfg.options,
                "voice": voice,
                "language": lang,
                "text": text,
                "max_duration": round(slot.length, 2),
            }
        )[:24]
        meta = folder / f"{key}.json"
        if meta.is_file():
            try:
                known = json.loads(meta.read_text(encoding="utf-8"))
                cached = folder / str(known["file"])
                if cached.is_file():
                    return SynthResult(audio=cached, duration=float(known["duration"]))
            except (ValueError, KeyError, TypeError, ValidationError):
                pass  # an unreadable entry is simply made again
        result = tts.synthesize(
            text, voice=voice, language=lang, output=folder / f"{key}.mp3", max_duration=slot.length, ctx=ctx
        )
        if not isinstance(result, SynthResult) or not result.audio.is_file():
            raise EraseDubError(
                f"tts provider '{tts.name}' did not return an existing audio file (a bug in that provider)."
            )
        audio_file = result.audio
        if audio_file.parent.resolve() != folder.resolve():  # keep it with the other clips
            audio_file = folder / f"{key}{result.audio.suffix or '.audio'}"
            shutil.copyfile(result.audio, audio_file)
        meta.write_text(
            json.dumps({"file": audio_file.name, "duration": result.duration}) + "\n", encoding="utf-8"
        )
        return SynthResult(audio=audio_file, duration=result.duration)

    # -- subtitles

    def draw_subtitles(self, lang: str, loaded: Script) -> tuple[Path, bool] | None:
        """``(subtitle file, burn it in)`` for ``lang``, or None when there is no subtitle text."""
        cfg = self.config.subtitles
        ctx = self.step(f"subtitles:{lang}", f"Subtitles ({lang})")
        found = self._regions()
        layout = registry.create("layout", cfg.layout, cfg.options)
        with opened(layout, ctx):
            laid = layout.layout(
                loaded, video=self.info.video, regions=found.regions if found else (), ctx=ctx
            )
        events = [e for e in _checked_list(laid, SubtitleEvent, f"layout '{layout.name}'") if e.text.strip()]
        ctx.progress(1.0, "")
        if not events:
            return None
        if not self.tools.has_libass:
            self.notice(
                f"{self.tools.ffmpeg} was built without libass, so subtitles are added as a subtitle track "
                "that players can turn on, not burned into the picture. Install an ffmpeg with libass to "
                f"burn them in ({doc('troubleshooting.md')})."
            )
            srt = ctx.tmp_dir / f"subtitles.{lang}.srt"
            srt.write_text(subtitles.build_srt(events), encoding="utf-8")
            return srt, False
        size = cfg.font_size or subtitles.default_size(self.info.video)
        font = cfg.font or subtitles.default_font(lang)
        ass = ctx.tmp_dir / f"subtitles.{lang}.ass"
        ass.write_text(
            subtitles.build_ass(events, video=self.info.video, font=font, size=size), encoding="utf-8"
        )
        return ass, True

    # -- final video

    def mux(
        self, lang: str, picture: Path, voice: Path | None, subs: tuple[Path, bool] | None, target: Path
    ) -> None:
        cfg = self.config.audio
        ctx = self.step(f"mux:{lang}", f"Writing {target.name}")
        duration = self.info.video.duration
        args: list[str] = ["-i", str(picture.resolve())]
        count = 1

        def add(*more: str) -> int:
            nonlocal count
            args.extend(more)
            count += 1
            return count - 1

        original: int | None = None
        if cfg.original == "keep" and self.info.video.has_audio:
            original = (
                0 if picture.resolve() == self.video.resolve() else add("-i", str(self.video.resolve()))
            )
        voice_input = add("-i", str(voice.resolve())) if voice is not None else None
        music_input = None
        if cfg.music is not None:
            music_input = add("-stream_loop", "-1", "-i", str(cfg.music.resolve()))
        soft_input = None
        if subs is not None and not subs[1]:
            soft_input = add("-i", str(subs[0].resolve()))

        graph: list[str] = []
        burn = subs is not None and subs[1]
        encode = burn or not self._is_h264(picture)
        if encode:
            if not self.tools.has_libx264:
                raise ProviderUnavailableError(
                    f"{self.tools.ffmpeg} was built without libx264, so it cannot write H.264 video. Install "
                    f"an ffmpeg with libx264 ({doc('troubleshooting.md')})."
                )
            # The ASS file is referred to by name, with ffmpeg running in its folder: no path escaping needed.
            burn_filter = f"ass=filename={subs[0].name}," if burn and subs is not None else ""
            # x264 needs an even width and height for yuv420p: drop the odd last row or column, never scale.
            graph.append(f"[0:v:0]{burn_filter}crop=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p[vout]")
        mix = audio.mix_graph(
            audio.MixInputs(original=original, voice=voice_input, music=music_input), cfg, duration=duration
        )
        if mix:
            graph.append(mix)
        if graph:
            args += ["-filter_complex", ";".join(graph)]
        args += ["-map", "[vout]" if encode else "0:v:0"]
        args += ["-map", "[aout]"] if mix else ["-an"]
        if soft_input is not None:
            args += ["-map", f"{soft_input}:s:0", "-c:s", "mov_text"]
        if encode:
            args += ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
        else:
            args += ["-c:v", "copy"]
        if mix:
            args += ["-c:a", "aac", "-b:a", "192k"]
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.part")
        args += ["-t", f"{duration:.3f}", "-movflags", "+faststart", "-f", "mp4", str(partial.resolve())]
        try:
            media.run_ffmpeg(
                self.tools,
                args,
                ctx=ctx,
                doing=f"writing {target.name}",
                duration=duration,
                cwd=subs[0].parent if burn and subs is not None else None,
            )
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)

    def _is_h264(self, picture: Path) -> bool:
        if picture.resolve() == self.video.resolve():
            return self.info.video_codec == "h264" and self.info.pix_fmt in ("yuv420p", "yuvj420p")
        found = media.probe(self.tools, picture)
        return found.video_codec == "h264" and found.pix_fmt in ("yuv420p", "yuvj420p")


# --- Small helpers ----------------------------------------------------------------------------------------


def _language_or_none(code: str | None) -> str | None:
    """A detected language as a normalized tag, or None when the transcriber gave something else."""
    if not code:
        return None
    try:
        return languages.normalize(code)
    except ValueError:
        return None


def _same_language(source: str | None, target: str) -> bool:
    """Whether the script needs no translation: the spoken language is the target language itself."""
    return source is not None and languages.normalize(source) == languages.normalize(target)


def _quiet(ctx: RunContext) -> RunContext:
    """``ctx`` without progress reports (for short helper commands inside a step)."""
    return dataclasses.replace(ctx, on_progress=_ignore)
