import logging
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar

import pytest

import erasedub.plugin as plugin_api
from erasedub import engine, registry
from erasedub.context import RunContext, validate_device
from erasedub.errors import CancelledError, ConfigError, EraseDubError, ProviderUnavailableError
from erasedub.hardware import CudaStatus
from erasedub.models import TextRegion, TranslationStyle
from erasedub.plugin import PLUGIN_API_VERSION, EraserSpec, ProviderOptions, TextEraser, Translator
from erasedub.providers import gpu_local
from erasedub.providers.asr_whisperx import WhisperXTranscriber
from erasedub.providers.base import KINDS
from erasedub.providers.gpu_local import LocalGpu
from erasedub.providers.layout_bottom import BottomLayout
from erasedub.script import ScriptLine


@pytest.mark.parametrize(
    ("device", "expected"),
    [("cpu", "cpu"), ("CUDA", "cuda"), (" cuda:1 ", "cuda:1"), ("cuda:10", "cuda:10"), ("MPS", "mps")],
)
def test_valid_devices(device: str, expected: str) -> None:
    assert validate_device(device) == expected


@pytest.mark.parametrize("device", ["", "gpu", "cuda:", "cuda:x", "mps:0", "cuda:0:1", "auto"])
def test_invalid_devices(device: str, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"'cpu', 'cuda', 'cuda:N' \(for example 'cuda:1'\) or 'mps'"):
        validate_device(device)
    with pytest.raises(ConfigError):
        RunContext(tmp_dir=tmp_path, cache_dir=tmp_path, device=device)


def test_run_context_progress_and_cancel(tmp_path: Path) -> None:
    seen: list[tuple[float, str]] = []
    cancel = {"now": False}
    ctx = RunContext(
        tmp_dir=tmp_path,
        cache_dir=tmp_path,
        device="cuda:1",
        on_progress=lambda f, m: seen.append((f, m)),
        is_cancelled=lambda: cancel["now"],
    )
    ctx.progress(0.5, "half")
    ctx.progress(7, "over")
    ctx.progress(-1)
    ctx.progress(math.nan)
    assert seen == [(0.5, "half"), (1.0, "over"), (0.0, ""), (0.0, "")]
    ctx.raise_if_cancelled()
    cancel["now"] = True
    assert ctx.cancelled()
    with pytest.raises(CancelledError) as excinfo:
        ctx.raise_if_cancelled()
    assert excinfo.value.exit_code == 130
    assert ctx.device == "cuda:1"
    assert isinstance(ctx.logger, logging.Logger)


def test_default_context_ignores_progress(ctx: RunContext) -> None:
    ctx.progress(0.3, "nobody listens")
    assert not ctx.cancelled()
    assert ctx.device == "cpu"


# --- options ----------------------------------------------------------------------------------------------


def test_providers_without_options_reject_any_option() -> None:
    with pytest.raises(ConfigError, match=r"options of tts provider 'edge': colour: Extra inputs"):
        registry.create("tts", "edge", {"colour": "blue"})


def test_bottom_layout_options_are_validated() -> None:
    layout = BottomLayout({"margin_ratio": 0.1})
    assert layout.options.margin_ratio == 0.1
    assert BottomLayout().options.margin_ratio == 0.08
    with pytest.raises(ConfigError, match="margin_ratio"):
        BottomLayout({"margin_ratio": "abc"})
    with pytest.raises(ConfigError, match="margin_ratio"):
        BottomLayout({"margin_ratio": 0.9})
    with pytest.raises(ConfigError, match="margn_ratio"):
        BottomLayout({"margn_ratio": 0.1})


def test_whisperx_alignment_is_off_by_default() -> None:
    assert WhisperXTranscriber().options.align is False
    assert WhisperXTranscriber({"align": True}).options.align is True


# --- api version ------------------------------------------------------------------------------------------


class _FakeEntryPoint:
    def __init__(self, name: str, target: object, dist: str = "some-plugin") -> None:
        self.name = name
        self.value = f"{dist}:{name}"
        self.group = "erasedub.eraser"
        self._target = target
        self.dist = type("Dist", (), {"name": dist})()

    def load(self) -> object:
        return self._target


class _Eraser(TextEraser):
    name: ClassVar[str] = "fake"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = "fake eraser"
    calls: ClassVar[list[str]] = []

    class Options(ProviderOptions):
        strength: int = 1

    def open(self, ctx: RunContext) -> None:
        self.calls.append("open")

    def close(self) -> None:
        self.calls.append("close")

    def erase(self, video: Path, regions: Sequence[TextRegion], output: Path, *, ctx: RunContext) -> Path:
        self.calls.append(f"erase:{ctx.device}:{self.options.model_dump()}")
        return output


class _OldEraser(_Eraser):
    api_version: ClassVar[int] = 0


class _UndeclaredEraser(TextEraser):
    name: ClassVar[str] = "undeclared"

    def erase(self, video: Path, regions: Sequence[TextRegion], output: Path, *, ctx: RunContext) -> Path:
        return output


def _install(monkeypatch: pytest.MonkeyPatch, **targets: object) -> None:
    eps = {name: _FakeEntryPoint(name, target) for name, target in targets.items()}
    monkeypatch.setattr(registry, "_entry_points", lambda kind: eps if kind == "eraser" else {})


def test_registry_rejects_other_plugin_api_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, old=_OldEraser, undeclared=_UndeclaredEraser)
    with pytest.raises(ProviderUnavailableError, match="written for plugin API 0"):
        registry.load_class("eraser", "old")
    with pytest.raises(ProviderUnavailableError, match="no plugin API version"):
        registry.load_class("eraser", "undeclared")


def test_every_builtin_declares_the_current_api_version() -> None:
    for kind in KINDS:
        for name in registry.names(kind):
            assert registry.load_class(kind, name).api_version == PLUGIN_API_VERSION


# --- eraser spec and local backend ------------------------------------------------------------------------


def test_eraser_spec_is_serializable() -> None:
    spec = EraserSpec(name="sttn", options={"a": [1, 2], "b": {"c": None}})
    assert EraserSpec.model_validate_json(spec.model_dump_json()) == spec
    assert spec.api_version == PLUGIN_API_VERSION


def test_local_gpu_builds_the_eraser_from_the_spec(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, fake=_Eraser)
    _Eraser.calls.clear()
    monkeypatch.setattr(gpu_local, "cuda_usable", lambda device: CudaStatus(True))
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path, device="cuda:1")
    out = LocalGpu().run_eraser(
        EraserSpec(name="fake", options={"strength": 3}),
        tmp_path / "in.mp4",
        [],
        tmp_path / "out.mp4",
        ctx=ctx,
    )
    assert out == tmp_path / "out.mp4"
    assert _Eraser.calls == ["open", "erase:cuda:1:{'strength': 3}", "close"]


def test_create_eraser_rejects_a_spec_from_another_api_version(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, fake=_Eraser)
    with pytest.raises(ProviderUnavailableError, match="same EraseDub"):
        registry.create_eraser(EraserSpec(name="fake", api_version=PLUGIN_API_VERSION + 1))


def test_backends_declare_where_they_run() -> None:
    assert registry.create("gpu", "local").remote is False
    assert registry.create("gpu", "modal").remote is True


# --- lifecycle --------------------------------------------------------------------------------------------


def test_opened_closes_even_when_the_work_fails(ctx: RunContext) -> None:
    _Eraser.calls.clear()
    with pytest.raises(RuntimeError), engine.opened(_Eraser(), ctx):
        raise RuntimeError("boom")
    assert _Eraser.calls == ["open", "close"]


def test_default_lifecycle_hooks_do_nothing(ctx: RunContext) -> None:
    layout = BottomLayout()
    layout.open(ctx)
    layout.close()
    layout.close()


# --- translator contract ----------------------------------------------------------------------------------


class _Translator(Translator):
    name: ClassVar[str] = "echo"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    drop: ClassVar[int] = 0

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
        return [f"{target}:{line.speaker}:{line.duration}:{line.text}" for line in lines][self.drop :]


LINES = [ScriptLine(start=0, end=1.5, text="你好", speaker="A"), ScriptLine(start=2, end=3, text="再见")]


def test_translate_lines_passes_timing_and_speaker(ctx: RunContext) -> None:
    out = engine.translate_lines(_Translator(), LINES, target="vi", style="natural", ctx=ctx)
    assert out == ["vi:A:1.5:你好", "vi:None:1.0:再见"]
    assert engine.translate_lines(_Translator(), [], target="vi", ctx=ctx) == []


def test_translate_lines_enforces_one_output_per_line(ctx: RunContext) -> None:
    class Merging(_Translator):
        drop: ClassVar[int] = 1

    with pytest.raises(EraseDubError, match="returned 1 translations for 2 lines"):
        engine.translate_lines(Merging(), LINES, target="vi", ctx=ctx)


def test_translate_lines_checks_cancellation(tmp_path: Path) -> None:
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path, is_cancelled=lambda: True)
    with pytest.raises(CancelledError):
        engine.translate_lines(_Translator(), LINES, target="vi", ctx=ctx)


# --- facade -----------------------------------------------------------------------------------------------


def test_plugin_facade_exports_the_whole_contract() -> None:
    for name in plugin_api.__all__:
        assert hasattr(plugin_api, name), name
    for needed in (
        "RunContext",
        "Provider",
        "TextEraser",
        "GpuBackend",
        "EraserSpec",
        "ScriptLine",
        "SubtitleEvent",
        "CancelledError",
        "PLUGIN_API_VERSION",
        "ProviderOptions",
        "create_eraser",
    ):
        assert needed in plugin_api.__all__


def test_local_gpu_refuses_cpu_only_torch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, fake=_Eraser)
    _Eraser.calls.clear()
    monkeypatch.setattr(gpu_local, "cuda_usable", lambda device: CudaStatus(False, "CPU-only build"))
    ctx = RunContext(tmp_dir=tmp_path, cache_dir=tmp_path, device="cuda")
    with pytest.raises(ProviderUnavailableError, match="cannot erase on cuda: CPU-only build"):
        LocalGpu().run_eraser(EraserSpec(name="fake"), tmp_path / "in.mp4", [], tmp_path / "o.mp4", ctx=ctx)
    assert _Eraser.calls == []
