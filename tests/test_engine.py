"""The engine end to end with fake providers and a fake ffmpeg: no media is read or written."""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest

from erasedub import audio, config, engine, hardware, media, pipeline, registry, script
from erasedub.context import RunContext
from erasedub.errors import CancelledError, ConfigError, EraseDubError, ProviderUnavailableError
from erasedub.hardware import GpuInfo
from erasedub.models import (
    Box,
    SubtitleEvent,
    SynthResult,
    TextRegion,
    Transcript,
    TranscriptSegment,
    TranslationStyle,
    VideoInfo,
    Voice,
)
from erasedub.providers.base import (
    PLUGIN_API_VERSION,
    Availability,
    EraserSpec,
    GpuBackend,
    Provider,
    ProviderOptions,
    SpeechSynthesizer,
    SubtitleLayout,
    TextDetector,
    Transcriber,
    Translator,
)
from erasedub.regions import load_regions, regions_path
from erasedub.script import Script, ScriptLine

RTX = GpuInfo(name="RTX 4090", memory_mib=24564, driver="580.1")
DURATION = 10.0


class Log:
    """What the fakes were asked to do."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.ffmpeg: list[tuple[list[str], str, Path | None]] = []
        self.options: dict[str, Mapping[str, Any]] = {}
        self.ocr_languages: list[Sequence[str]] = []
        self.spoken: list[tuple[str, str, float | None]] = []
        self.erased: list[tuple[EraserSpec, int]] = []
        self.erase_devices: list[str] = []
        self.tts_duration = 2.5
        self.translate: Callable[[Sequence[ScriptLine], str], list[str]] = lambda lines, target: [
            f"{target}:{line.text}" for line in lines
        ]


LOG = Log()


class _Fake(Provider):
    api_version: ClassVar[int] = PLUGIN_API_VERSION

    def open(self, ctx: RunContext) -> None:
        LOG.events.append(f"open {self.kind}")

    def close(self) -> None:
        LOG.events.append(f"close {self.kind}")


class FakeAsr(_Fake, Transcriber):
    name = "fake-asr"

    class Options(ProviderOptions):
        model: str = "auto"
        batch_size: int = 8

    def transcribe(self, audio: Path, *, language: str | None = None, ctx: RunContext) -> Transcript:
        assert audio.is_file()
        ctx.progress(0.5, "listening")
        segments = (
            TranscriptSegment(start=2, end=3, text=" 第二 ", speaker="B"),
            TranscriptSegment(start=0, end=1, text="第一"),
            TranscriptSegment(start=4, end=5, text="  "),
        )
        return Transcript(language="zh", segments=segments)


class FakeOcr(_Fake, TextDetector):
    name = "fake-ocr"

    def detect(self, video: Path, *, languages: Sequence[str] = (), ctx: RunContext) -> list[TextRegion]:
        LOG.ocr_languages.append(list(languages))
        return [TextRegion(start=0, end=5, box=Box(x=10, y=1700, width=1000, height=120), text="字幕")]


class FakeTranslator(_Fake, Translator):
    name = "fake-translator"

    def translate(
        self,
        lines: Sequence[ScriptLine],
        *,
        target: str,
        source: str | None = None,
        style: TranslationStyle = "faithful",
        glossary: Mapping[str, str] | None = None,
        ctx: RunContext,
    ) -> list[str]:
        return LOG.translate(lines, target)


class FakeTts(_Fake, SpeechSynthesizer):
    name = "fake-tts"

    def voices(self, language: str, *, ctx: RunContext) -> list[Voice]:
        return [Voice(id=f"{language}-1", language=language, name="One")]

    def synthesize(
        self,
        text: str,
        *,
        voice: str,
        language: str,
        output: Path,
        max_duration: float | None = None,
        ctx: RunContext,
    ) -> SynthResult:
        LOG.spoken.append((text, voice, max_duration))
        mp3 = output.with_suffix(".mp3")
        mp3.write_bytes(b"mp3")
        return SynthResult(audio=mp3, duration=LOG.tts_duration)


class FakeLayout(_Fake, SubtitleLayout):
    name = "fake-layout"

    def layout(
        self, script: Script, *, video: VideoInfo, regions: Sequence[TextRegion] = (), ctx: RunContext
    ) -> list[SubtitleEvent]:
        return [SubtitleEvent(start=line.start, end=line.end, text=line.text) for line in script.lines]


class FakeGpu(_Fake, GpuBackend):
    name = "fake-gpu"

    def run_eraser(
        self, spec: EraserSpec, video: Path, regions: Sequence[TextRegion], output: Path, *, ctx: RunContext
    ) -> Path:
        LOG.erased.append((spec, len(regions)))
        LOG.erase_devices.append(ctx.device)
        ctx.progress(0.5, "erasing")
        output.write_bytes(b"clean")
        return output


FAKES: dict[str, type[Provider]] = {
    "asr": FakeAsr,
    "ocr": FakeOcr,
    "translator": FakeTranslator,
    "tts": FakeTts,
    "layout": FakeLayout,
    "gpu": FakeGpu,
}


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> Log:
    global LOG
    LOG = Log()

    def create(kind: str, name: str, options: Mapping[str, Any] | None = None) -> Provider:
        LOG.options[kind] = dict(options or {})
        return FAKES[kind](options)

    real_load = registry.load_class

    def load_class(kind: Any, name: str) -> type[Provider]:
        return FAKES[kind] if kind == "asr" else real_load(kind, name)

    def run_ffmpeg(
        tools: media.Tools,
        args: Sequence[str],
        *,
        ctx: RunContext,
        doing: str,
        duration: float | None = None,
        cwd: Path | None = None,
    ) -> None:
        ctx.raise_if_cancelled()
        LOG.ffmpeg.append((list(args), doing, cwd))
        out = Path(args[-1])
        if "s16le" in args:  # a voice clip converted for the track: one second of silence
            out.write_bytes(bytes(2 * audio.SAMPLE_RATE))
        else:
            out.write_bytes(b"media")
        ctx.progress(1.0, doing)

    def probe(tools: media.Tools, path: Path) -> media.MediaInfo:
        info = VideoInfo(path=path, width=1080, height=1920, duration=DURATION, fps=30)
        return media.MediaInfo(info, "h264", "yuv420p")

    monkeypatch.setattr(registry, "create", create)
    monkeypatch.setattr(registry, "load_class", load_class)
    monkeypatch.setattr(media, "run_ffmpeg", run_ffmpeg)
    monkeypatch.setattr(media, "probe", probe)
    monkeypatch.setattr(media, "find_tools", lambda: media.Tools("/x/ffmpeg", "/x/ffprobe"))
    return LOG


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "in" / "clip.mp4"
    path.parent.mkdir()
    path.write_bytes(b"")  # never decoded: probe and ffmpeg are fakes
    return path


def make_config(tmp_path: Path, **sections: Any) -> config.Config:
    general = {
        "workdir": str(tmp_path / "work"),
        "output_dir": str(tmp_path / "out"),
        **sections.pop("general", {}),
    }
    return config.from_mapping({"general": general, **sections})


def ready(kind: Any, name: str) -> Availability:
    return Availability.ready()


def plan(cfg: config.Config, video: Path, command: pipeline.Command, gpus: Sequence[GpuInfo] = (RTX,)) -> Any:
    return pipeline.plan_video(cfg, video, command, list(gpus), ready)


def execute(request: Any, video: Path, **kwargs: Any) -> engine.Result:
    return engine.execute(request, video, device="cpu", **kwargs)


# --- prepare ----------------------------------------------------------------------------------------------


def test_prepare_writes_the_scripts_and_the_regions(tmp_path: Path, video: Path, fakes: Log) -> None:
    cfg = make_config(tmp_path, general={"target_languages": ["vi", "en"]})
    seen: list[float] = []
    result = execute(plan(cfg, video, "prepare"), video, on_progress=lambda f, m: seen.append(f))
    work = tmp_path / "work" / "clip"
    assert result.scripts == (script.srt_path(work, "vi"), script.srt_path(work, "en"))
    assert result.outputs == ()
    vi = script.load_json(script.json_path(work, "vi"))
    assert vi.source_language == "zh"
    assert [(line.start, line.text, line.source, line.speaker) for line in vi.lines] == [
        (0, "vi:第一", "第一", None),
        (2, "vi:第二", "第二", "B"),
    ]
    assert "en:第一" in script.srt_path(work, "en").read_text(encoding="utf-8")
    found = load_regions(work)
    assert (found.video.width, found.video.height, len(found.regions)) == (1080, 1920, 1)
    assert fakes.ocr_languages == [["zh"]]
    extract = fakes.ffmpeg[0][0]
    assert extract[extract.index("-ar") + 1] == "16000"
    assert fakes.options["asr"] == {"model": "auto"}  # asr.model reaches the provider
    for kind in ("asr", "ocr", "translator"):
        assert fakes.events.count(f"open {kind}") == fakes.events.count(f"close {kind}") == 1
    assert seen == sorted(seen) and seen[-1] == pytest.approx(1.0)


def test_asr_model_and_options(
    tmp_path: Path, video: Path, fakes: Log, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_config(tmp_path, asr={"model": "small", "options": {"batch_size": 4}})
    execute(plan(cfg, video, "prepare", gpus=()), video)
    assert fakes.options["asr"] == {"model": "small", "batch_size": 4}
    cfg = make_config(tmp_path, asr={"model": "small", "options": {"model": "medium"}})
    execute(plan(cfg, video, "prepare", gpus=()), video)
    assert fakes.options["asr"]["model"] == "medium"  # asr.options.model wins

    class NoModel(FakeAsr):
        Options = ProviderOptions  # type: ignore[assignment]

    monkeypatch.setattr(registry, "load_class", lambda kind, name: NoModel)
    with pytest.raises(ConfigError, match=r"asr\.model: the asr provider 'whisperx' has no model option"):
        execute(plan(cfg, video, "prepare", gpus=()), video)


def test_same_spoken_and_target_language_needs_no_translator(tmp_path: Path, video: Path, fakes: Log) -> None:
    cfg = make_config(tmp_path, general={"source_language": "zh", "target_languages": ["zh"]})
    execute(plan(cfg, video, "prepare", gpus=()), video)
    assert "translator" not in fakes.options
    zh = script.load_json(script.json_path(tmp_path / "work" / "clip", "zh"))
    assert [line.text for line in zh.lines] == ["第一", "第二"]


def test_a_translator_that_drops_lines_is_an_error(tmp_path: Path, video: Path, fakes: Log) -> None:
    fakes.translate = lambda lines, target: ["only one"]
    with pytest.raises(EraseDubError, match="returned 1 translations for 2 lines"):
        execute(plan(make_config(tmp_path), video, "prepare", gpus=()), video)
    assert fakes.events[-1] == "close translator"


def test_video_without_audio_is_an_error(
    tmp_path: Path, video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def probe(tools: media.Tools, path: Path) -> media.MediaInfo:
        info = VideoInfo(path=path, width=640, height=360, duration=3, fps=25, has_audio=False)
        return media.MediaInfo(info, "h264", "yuv420p")

    monkeypatch.setattr(media, "probe", probe)
    with pytest.raises(EraseDubError, match=r"clip\.mp4 has no audio track"):
        execute(plan(make_config(tmp_path), video, "prepare", gpus=()), video)


# --- render -----------------------------------------------------------------------------------------------


def _mux(fakes: Log) -> tuple[list[str], Path | None]:
    (args, _, cwd) = next(entry for entry in fakes.ffmpeg if entry[1].startswith("writing "))
    return args, cwd


def test_run_erases_speaks_and_writes_the_video(tmp_path: Path, video: Path, fakes: Log) -> None:
    cfg = make_config(tmp_path)
    notices: list[str] = []
    result = execute(plan(cfg, video, "run"), video, on_notice=notices.append)
    target = tmp_path / "out" / "clip.vi.mp4"
    assert result.outputs == (target,)
    assert target.read_bytes() == b"media"
    assert not list(target.parent.glob(".*.part"))
    work = tmp_path / "work" / "clip"
    assert (work / engine.ERASED_NAME).read_bytes() == b"clean"
    assert fakes.erased == [(EraserSpec(name=cfg.erase.provider, options={}), 1)]
    # Voices: the first line's slot runs until the next line (2 s); a 2.5 s clip is sped up 1.25x.
    assert fakes.spoken == [("vi:第一", "vi-1", 2.0), ("vi:第二", "vi-1", DURATION - 2)]
    fits = [args for args, doing, _ in fakes.ffmpeg if doing.startswith("fitting voice")]
    assert "atempo=1.2500" in fits[0] and "-af" not in fits[1]
    args, cwd = _mux(fakes)
    graph = args[args.index("-filter_complex") + 1]
    assert (
        "[0:v:0]ass=filename=subtitles.vi.ass,crop=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p[vout]" in graph
    )
    assert cwd is not None and cwd.name.startswith("erasedub-")  # ffmpeg runs in the run's temp folder
    assert "acrossover=split='150 6000'" in graph  # band gate under the voice
    assert args.count("-i") == 3  # erased picture, original sound, voice track
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-c:a") + 1] == "aac"
    assert "+faststart" in args
    assert notices == []
    for kind in ("gpu", "tts", "layout"):
        assert fakes.events.count(f"open {kind}") == fakes.events.count(f"close {kind}") == 1


def test_render_again_reuses_the_erased_picture_and_the_voice_clips(
    tmp_path: Path, video: Path, fakes: Log
) -> None:
    cfg = make_config(tmp_path)
    execute(plan(cfg, video, "run"), video)
    work = tmp_path / "work" / "clip"
    srt = script.srt_path(work, "vi")
    srt.write_text(srt.read_text(encoding="utf-8").replace("vi:第二", "đã sửa"), encoding="utf-8")
    fakes.ffmpeg.clear()
    execute(plan(cfg, video, "render"), video)
    assert len(fakes.erased) == 1  # same video, same regions, same eraser
    assert [text for text, _, _ in fakes.spoken] == ["vi:第一", "vi:第二", "đã sửa"]  # only the edit is new
    # A new regions.json means a new erase.
    regions_path(work).write_text(regions_path(work).read_text(encoding="utf-8").replace("1700", "1600"))
    execute(plan(cfg, video, "render"), video)
    assert len(fakes.erased) == 2


def test_without_libass_subtitles_become_a_track_with_one_notice(
    tmp_path: Path, video: Path, fakes: Log, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media, "find_tools", lambda: media.Tools("/x/ffmpeg", "/x/ffprobe", has_libass=False))
    cfg = make_config(tmp_path, general={"target_languages": ["vi", "en"]})
    notices: list[str] = []
    result = execute(plan(cfg, video, "run", gpus=()), video, on_notice=notices.append)
    assert len(result.outputs) == 2
    assert len([n for n in notices if "without libass" in n]) == 1
    args, cwd = _mux(fakes)
    assert cwd is None
    assert args[args.index("-c:s") + 1] == "mov_text"
    assert args[args.index("-c:v") + 1] == "copy"  # nothing drawn on an H.264 picture: no re-encode
    assert args[args.index("-map") + 1] == "0:v:0"
    assert "[vout]" not in args[args.index("-filter_complex") + 1]


def test_a_picture_that_is_not_h264_is_encoded_with_an_even_size(
    tmp_path: Path, video: Path, fakes: Log, monkeypatch: pytest.MonkeyPatch
) -> None:
    def probe(tools: media.Tools, path: Path) -> media.MediaInfo:
        info = VideoInfo(path=path, width=719, height=1279, duration=DURATION, fps=30)
        return media.MediaInfo(info, "hevc", "yuv420p10le")

    monkeypatch.setattr(media, "probe", probe)
    cfg = make_config(tmp_path, erase={"enabled": "off"}, subtitles={"enabled": False})
    execute(plan(cfg, video, "run", gpus=()), video)
    args, _ = _mux(fakes)
    graph = args[args.index("-filter_complex") + 1]
    # x264 rejects an odd width or height in yuv420p: the last column and row are cropped, nothing is scaled.
    assert "[0:v:0]crop=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p[vout]" in graph
    assert args[args.index("-map") + 1] == "[vout]"
    assert args[args.index("-c:v") + 1] == "libx264"
    assert "-pix_fmt" not in args


def test_long_voices_are_named_in_a_notice(tmp_path: Path, video: Path, fakes: Log) -> None:
    fakes.tts_duration = 4.0  # the first slot is 2 s: 1.35x is not enough
    notices: list[str] = []
    execute(plan(make_config(tmp_path), video, "run", gpus=()), video, on_notice=notices.append)
    (notice,) = notices
    assert notice.startswith("script.vi.srt: the voice of 1 line(s)")


def test_voice_off_and_original_muted_give_a_silent_video(tmp_path: Path, video: Path, fakes: Log) -> None:
    cfg = make_config(
        tmp_path, tts={"enabled": False}, subtitles={"enabled": False}, audio={"original": "mute"}
    )
    execute(plan(cfg, video, "run", gpus=()), video)
    args, _ = _mux(fakes)
    assert "-an" in args and "-filter_complex" not in args
    assert "tts" not in fakes.options and "layout" not in fakes.options


def test_render_checks_the_regions_were_measured_on_this_video(
    tmp_path: Path, video: Path, fakes: Log, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_config(tmp_path)
    execute(plan(cfg, video, "prepare"), video)

    def probe(tools: media.Tools, path: Path) -> media.MediaInfo:
        info = VideoInfo(path=path, width=720, height=1280, duration=DURATION, fps=30)
        return media.MediaInfo(info, "h264", "yuv420p")

    monkeypatch.setattr(media, "probe", probe)
    with pytest.raises(EraseDubError, match=r"made for a 1080x1920 video, but clip\.mp4 is 720x1280"):
        execute(plan(cfg, video, "render"), video)


def test_no_voice_for_the_language_is_a_clear_error(
    tmp_path: Path, video: Path, fakes: Log, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeTts, "voices", lambda self, language, *, ctx: [])
    with pytest.raises(ProviderUnavailableError, match=r"has no voice for vi\. Set tts\.voice"):
        execute(plan(make_config(tmp_path), video, "run", gpus=()), video)
    assert fakes.events[-1] == "close tts"


# --- cancel and errors ------------------------------------------------------------------------------------


def test_cancel_stops_the_run_and_closes_the_providers(tmp_path: Path, video: Path, fakes: Log) -> None:
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return any(e == "open tts" for e in fakes.events)

    with pytest.raises(CancelledError):
        execute(plan(make_config(tmp_path), video, "run"), video, is_cancelled=cancelled)
    assert fakes.events.count("open tts") == fakes.events.count("close tts") == 1
    assert fakes.events.count("open gpu") == fakes.events.count("close gpu")
    assert not (tmp_path / "out" / "clip.vi.mp4").exists()


def test_an_eraser_failure_stops_the_render(
    tmp_path: Path, video: Path, fakes: Log, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_config(tmp_path)
    execute(plan(cfg, video, "prepare"), video)

    def broken(
        self: Any, spec: EraserSpec, video: Path, regions: Any, output: Path, *, ctx: RunContext
    ) -> Path:
        raise ProviderUnavailableError("out of GPU memory")

    monkeypatch.setattr(FakeGpu, "run_eraser", broken)
    with pytest.raises(ProviderUnavailableError, match="out of GPU memory"):
        execute(plan(cfg, video, "render"), video)
    assert not (tmp_path / "out" / "clip.vi.mp4").exists()


def test_an_eraser_that_returns_no_file_is_named(
    tmp_path: Path, video: Path, fakes: Log, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_config(tmp_path)
    execute(plan(cfg, video, "prepare"), video)
    monkeypatch.setattr(FakeGpu, "run_eraser", lambda self, spec, video, regions, output, *, ctx: output)
    with pytest.raises(EraseDubError, match="did not return an existing video file"):
        execute(plan(cfg, video, "render"), video)


# --- machine ----------------------------------------------------------------------------------------------


def test_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(engine.CACHE_ENV, str(tmp_path / "models"))
    assert engine.cache_dir() == tmp_path / "models"
    monkeypatch.delenv(engine.CACHE_ENV)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert engine.cache_dir() == tmp_path / "xdg" / "erasedub"


def test_resolve_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware, "detect_nvidia_gpus", lambda: [])
    assert engine.resolve_device() == "cpu"
    monkeypatch.setattr(hardware, "detect_nvidia_gpus", lambda: [RTX])
    monkeypatch.setattr(hardware, "cuda_usable", lambda device: hardware.CudaStatus(True))
    assert engine.resolve_device() == "cuda"
    monkeypatch.setattr(hardware, "cuda_usable", lambda device: hardware.CudaStatus(False, "CPU-only torch"))
    assert engine.resolve_device() == "cpu"


@pytest.mark.parametrize(
    ("eraser", "options", "run_device", "mps", "expected"),
    [
        ("sttn", {}, "cuda", True, "cuda"),  # CUDA when there is one
        # A GPU eraser asks for CUDA even when PyTorch cannot use it, so the backend explains why.
        ("sttn", {}, "cpu", True, "cuda"),
        ("propainter", {}, "cpu", False, "cuda"),
        ("lama", {}, "cuda:1", True, "cuda:1"),
        ("lama", {}, "cpu", True, "mps"),  # no CUDA: an eraser that supports MPS gets it when available
        ("lama", {}, "cpu", False, "cpu"),
        ("lama", {"device": "auto"}, "cpu", True, "mps"),
        ("lama", {"device": "cpu"}, "cuda", True, "cpu"),  # an explicit device option wins
        ("lama", {"device": "mps"}, "cuda", True, "mps"),
    ],
)
def test_erase_device(
    tmp_path: Path,
    video: Path,
    fakes: Log,
    monkeypatch: pytest.MonkeyPatch,
    eraser: str,
    options: dict[str, Any],
    run_device: str,
    mps: bool,
    expected: str,
) -> None:
    monkeypatch.setattr(hardware, "mps_usable", lambda: mps)
    cfg = make_config(tmp_path, erase={"provider": eraser, "options": options})
    execute(plan(cfg, video, "prepare"), video)
    engine.execute(plan(cfg, video, "render"), video, device=run_device)
    assert fakes.erase_devices == [expected]
    assert fakes.spoken  # other steps still ran, on the run's device


def test_a_gpu_eraser_never_falls_back_to_the_cpu(
    tmp_path: Path, video: Path, fakes: Log, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[bool] = []

    def mps_usable() -> bool:
        asked.append(True)
        return True

    monkeypatch.setattr(hardware, "mps_usable", mps_usable)
    cfg = make_config(tmp_path, erase={"provider": "sttn"})
    execute(plan(cfg, video, "prepare"), video)
    execute(plan(cfg, video, "render"), video)
    assert fakes.erase_devices == ["cuda"]  # the local backend then checks that PyTorch can use it
    assert asked == []  # MPS is never considered for it


def test_speech_recognition_never_gets_mps(
    tmp_path: Path, video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(hardware, "mps_usable", lambda: True)
    monkeypatch.setattr(hardware, "detect_nvidia_gpus", lambda: [])
    real = FakeAsr.transcribe

    def transcribe(self: FakeAsr, audio: Path, *, language: str | None = None, ctx: RunContext) -> Transcript:
        seen.append(ctx.device)
        return real(self, audio, language=language, ctx=ctx)

    monkeypatch.setattr(FakeAsr, "transcribe", transcribe)
    engine.execute(plan(make_config(tmp_path), video, "prepare", gpus=()), video)  # device resolved here
    assert seen == ["cpu"]
