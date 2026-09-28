import logging
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from erasedub import registry
from erasedub.context import RunContext
from erasedub.errors import ConfigError, ProviderNotFoundError, ProviderUnavailableError
from erasedub.links import missing_extra
from erasedub.models import SynthResult, VideoInfo, Voice
from erasedub.providers import base as base_module
from erasedub.providers.base import KINDS, PLUGIN_API_VERSION, Provider, SpeechSynthesizer, module_available
from erasedub.providers.tts_edge import EdgeTTS
from erasedub.script import Script, ScriptLine

EXPECTED = {
    "eraser": {"sttn", "propainter", "lama", "none"},
    "asr": {"whisperx"},
    "ocr": {"rapidocr"},
    "translator": {"google", "openai", "gemini", "claude", "ollama"},
    "tts": {"edge", "elevenlabs"},
    "layout": {"bottom"},
    "gpu": {"local", "modal"},
}


def test_builtin_providers_are_registered() -> None:
    for kind in KINDS:
        assert set(registry.names(kind)) >= EXPECTED[kind], kind


def test_every_builtin_loads_with_matching_metadata() -> None:
    for kind in KINDS:
        for name in EXPECTED[kind]:
            cls = registry.load_class(kind, name)
            assert issubclass(cls, Provider)
            assert cls.kind == kind
            assert cls.name == name
            assert cls.summary


def test_unknown_provider() -> None:
    with pytest.raises(ProviderNotFoundError, match="installed: "):
        registry.create("tts", "does-not-exist")


def test_status_all_lists_builtins_with_their_distribution() -> None:
    rows = registry.status_all()
    assert {(r.kind, r.name) for r in rows} >= {("tts", "edge"), ("gpu", "modal")}
    assert {r.distribution for r in rows if r.name in {"edge", "sttn"}} == {"erasedub"}


# --- missing packages and keys ----------------------------------------------------------------------------


class _KeyedTTS(SpeechSynthesizer):
    name: ClassVar[str] = "keyed"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    required_env: ClassVar[tuple[str, ...]] = ("FAKE_TTS_API_KEY",)

    def voices(self, language: str, *, ctx: RunContext) -> list[Voice]:
        return []

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
        return SynthResult(audio=output, duration=1)


def test_missing_key_is_reported_by_name_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAKE_TTS_API_KEY", raising=False)
    status = _KeyedTTS().check()
    assert not status.ok
    assert "FAKE_TTS_API_KEY" in status.reason

    monkeypatch.setenv("FAKE_TTS_API_KEY", "sk-very-secret-value")
    assert _KeyedTTS().check().ok


def test_elevenlabs_reports_its_key_when_httpx_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base_module, "module_available", lambda name: True)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    status = registry.create("tts", "elevenlabs").check()
    assert not status.ok
    assert "ELEVENLABS_API_KEY" in status.reason


def test_install_hints_use_the_extra_or_the_pip_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base_module, "module_available", lambda name: False)
    assert missing_extra("asr") in registry.create("asr", "whisperx").check().reason
    assert "pip install edge-tts" in EdgeTTS().check().reason
    assert "pip install deep-translator" in registry.create("translator", "google").check().reason
    assert base_module.pip_name("cv2") == "opencv-python"
    assert base_module.pip_name("some_pkg.sub") == "some-pkg"


def test_module_available_handles_missing_parent() -> None:
    assert module_available("json")
    assert not module_available("surely_not_installed_pkg.sub")


# --- broken and conflicting plugins -----------------------------------------------------------------------


class _FakeEntryPoint:
    def __init__(self, name: str, target: object, dist: str | None = "third-party", group: str = "") -> None:
        self.name = name
        self.group = group
        self.value = f"fake:{name}"
        self._target = target
        self.dist = type("Dist", (), {"name": dist})() if dist else None

    def load(self) -> object:
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


class _RaisingCheck(EdgeTTS):
    name: ClassVar[str] = "raising"

    def check(self) -> base_module.Availability:
        raise RuntimeError("plugin bug")


class _Misnamed(EdgeTTS):
    name: ClassVar[str] = "other-name"


def _install(monkeypatch: pytest.MonkeyPatch, kind: str, eps: Sequence[_FakeEntryPoint]) -> None:
    real = registry._raw_entry_points
    monkeypatch.setattr(registry, "_raw_entry_points", lambda k: list(eps) if k == kind else real(k))


def test_broken_plugins_do_not_break_status_all(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "tts",
        [
            _FakeEntryPoint("edge", EdgeTTS, dist="erasedub"),
            _FakeEntryPoint("broken-import", ImportError("No module named 'nope'")),
            _FakeEntryPoint("raising", _RaisingCheck),
            _FakeEntryPoint("not-a-class", object()),
        ],
    )
    rows = {r.name: r for r in registry.status_all() if r.kind == "tts"}
    assert rows["edge"].distribution == "erasedub"
    assert not rows["broken-import"].availability.ok
    assert "No module named 'nope'" in rows["broken-import"].availability.reason
    assert rows["raising"].availability.reason == "check failed: RuntimeError: plugin bug"
    assert rows["raising"].distribution == "third-party"
    assert "does not point to a SpeechSynthesizer subclass" in rows["not-a-class"].availability.reason


def test_broken_import_is_a_provider_unavailable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "tts", [_FakeEntryPoint("broken", ImportError("boom"))])
    with pytest.raises(ProviderUnavailableError, match=r"failed to import: ImportError: boom"):
        registry.create("tts", "broken")
    assert registry.availability("tts", "broken").ok is False
    with pytest.raises(ProviderNotFoundError):
        registry.availability("tts", "missing")


def test_availability_never_raises_for_a_broken_check(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "tts", [_FakeEntryPoint("raising", _RaisingCheck)])
    assert registry.availability("tts", "raising").reason == "check failed: RuntimeError: plugin bug"


@pytest.mark.parametrize("builtin_first", [True, False])
def test_builtins_win_over_plugins_with_the_same_name(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, builtin_first: bool
) -> None:
    builtin = _FakeEntryPoint("edge", EdgeTTS, dist="erasedub")
    shadow = _FakeEntryPoint("edge", _RaisingCheck, dist="evil-edge")
    _install(monkeypatch, "tts", [builtin, shadow] if builtin_first else [shadow, builtin])
    registry._warned.clear()
    with caplog.at_level(logging.WARNING, logger="erasedub.registry"):
        assert registry.load_class("tts", "edge") is EdgeTTS
    assert "registered by both 'erasedub' and 'evil-edge'; using the one from 'erasedub'" in caplog.text


def test_same_distribution_twice_is_not_a_conflict(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install(monkeypatch, "tts", [_FakeEntryPoint("x", EdgeTTS), _FakeEntryPoint("x", EdgeTTS)])
    with caplog.at_level(logging.WARNING, logger="erasedub.registry"):
        registry.names("tts")
    assert caplog.text == ""


def test_name_mismatch_is_logged(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _install(monkeypatch, "tts", [_FakeEntryPoint("alias", _Misnamed)])
    registry._warned.clear()
    with caplog.at_level(logging.WARNING, logger="erasedub.registry"):
        assert registry.load_class("tts", "alias") is _Misnamed
    assert "says its name is 'other-name'; it is used as 'alias'" in caplog.text


# --- typed creation ---------------------------------------------------------------------------------------


def test_create_validates_provider_options() -> None:
    provider = registry.create("asr", "whisperx", {"model": "medium", "align": True})
    assert provider.options.model_dump()["model"] == "medium"
    with pytest.raises(ConfigError, match="options of asr provider 'whisperx'"):
        registry.create("asr", "whisperx", {"no_such_option": 1})


def test_bottom_layout_positions_nonempty_lines(ctx: RunContext) -> None:
    layout = registry.create("layout", "bottom")
    script = Script(
        target_language="en",
        lines=(ScriptLine(start=0, end=1, text="Hi"), ScriptLine(start=1, end=2, text="  ")),
    )
    video = VideoInfo(path=Path("v.mp4"), width=1080, height=1920, duration=2, fps=30)
    events = layout.layout(script, video=video, ctx=ctx)
    assert len(events) == 1
    assert events[0].alignment == 2
    assert events[0].margin_v == round(1920 * 0.08)


def test_no_eraser_returns_input(tmp_path: Path, ctx: RunContext) -> None:
    eraser = registry.create("eraser", "none")
    src = tmp_path / "in.mp4"
    assert eraser.erase(src, [], tmp_path / "out.mp4", ctx=ctx) == src
