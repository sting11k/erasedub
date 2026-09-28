"""Edge-TTS and ElevenLabs providers, with the libraries and the network replaced by fakes."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from erasedub import registry
from erasedub.context import RunContext
from erasedub.errors import EraseDubError
from erasedub.providers import _audio, _retry, tts_edge, tts_elevenlabs


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_retry, "_sleep", lambda seconds, ctx: None)


# --- Edge-TTS ---------------------------------------------------------------------------------------------

VOICES = [
    {"ShortName": "en-US-AriaNeural", "Locale": "en-US", "Gender": "Female", "FriendlyName": "Aria"},
    {"ShortName": "vi-VN-NamMinhNeural", "Locale": "vi-VN", "Gender": "Male", "FriendlyName": "NamMinh"},
    {"ShortName": "zh-CN-XiaoxiaoNeural", "Locale": "zh-CN", "Gender": "Female", "FriendlyName": "Xiaoxiao"},
    {
        "ShortName": "zh-TW-HsiaoChenNeural",
        "Locale": "zh-TW",
        "Gender": "Female",
        "FriendlyName": "HsiaoChen",
    },
    {"ShortName": "vi-VN-HoaiMyNeural", "Locale": "vi-VN", "Gender": "Female", "FriendlyName": ""},
]


class _NetworkError(Exception):
    pass


_NetworkError.__module__ = "aiohttp.client_exceptions"


def _fake_edge(monkeypatch: pytest.MonkeyPatch, *, fail_first: int = 0) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    failures = {"left": fail_first}

    class Communicate:
        def __init__(self, text: str, voice: str, *, rate: str, volume: str, pitch: str) -> None:
            calls.append({"text": text, "voice": voice, "rate": rate, "volume": volume, "pitch": pitch})

        async def save(self, path: str) -> None:
            if failures["left"]:
                failures["left"] -= 1
                raise _NetworkError("connection reset")
            Path(path).write_bytes(b"\xff\xfb" * 100)

    async def list_voices() -> list[dict[str, Any]]:
        return VOICES

    module = types.ModuleType("edge_tts")
    module.Communicate = Communicate  # type: ignore[attr-defined]
    module.list_voices = list_voices  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edge_tts", module)
    return calls


def test_edge_voices_filter_by_language_exact_region_first(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext
) -> None:
    _fake_edge(monkeypatch)
    edge = registry.create("tts", "edge")
    vi = edge.voices("vi", ctx=ctx)
    assert [v.id for v in vi] == ["vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"]
    assert vi[0].gender == "male" and vi[0].language == "vi-VN" and vi[0].name == "NamMinh"
    assert vi[1].name == "vi-VN-HoaiMyNeural"  # no friendly name: the id
    zh_tw = edge.voices("zh-TW", ctx=ctx)
    assert [v.id for v in zh_tw] == ["zh-TW-HsiaoChenNeural", "zh-CN-XiaoxiaoNeural"]


def test_edge_synthesize_writes_mp3_and_reports_real_duration(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext, tmp_path: Path
) -> None:
    calls = _fake_edge(monkeypatch)
    monkeypatch.setattr(tts_edge, "audio_duration", lambda path, cbr_kbps: 1.25)
    result = registry.create("tts", "edge").synthesize(
        "Xin chào", voice="vi-VN-HoaiMyNeural", language="vi", output=tmp_path / "out" / "1.wav", ctx=ctx
    )
    assert result.audio == tmp_path / "out" / "1.mp3"
    assert result.audio.read_bytes()
    assert result.duration == 1.25
    assert calls == [
        {"text": "Xin chào", "voice": "vi-VN-HoaiMyNeural", "rate": "+0%", "volume": "+0%", "pitch": "+0Hz"}
    ]


def test_edge_speaks_faster_to_fit_max_duration(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext, tmp_path: Path
) -> None:
    calls = _fake_edge(monkeypatch)
    durations = iter([3.0, 2.05, 1.95])
    monkeypatch.setattr(tts_edge, "audio_duration", lambda path, cbr_kbps: next(durations))
    result = registry.create("tts", "edge", {"max_rate": 80}).synthesize(
        "long line", voice="v", language="en", output=tmp_path / "a.mp3", max_duration=2.0, ctx=ctx
    )
    rates = [c["rate"] for c in calls]
    assert rates[0] == "+0%"
    assert rates[1] == "+52%"  # 3.0 s into 2.0 s needs 1.5x, plus 2 points of margin
    assert int(rates[2].rstrip("%")) > 52
    assert result.duration == 1.95


def test_edge_speed_up_stops_at_max_rate(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext, tmp_path: Path
) -> None:
    calls = _fake_edge(monkeypatch)
    monkeypatch.setattr(tts_edge, "audio_duration", lambda path, cbr_kbps: 10.0)
    edge = registry.create("tts", "edge", {"max_rate": 30})
    result = edge.synthesize(
        "x", voice="v", language="en", output=tmp_path / "a.mp3", max_duration=2.0, ctx=ctx
    )
    assert [c["rate"] for c in calls] == ["+0%", "+30%"]
    assert result.duration == 10.0  # reported as it is; the engine decides what to do


def test_edge_retries_network_errors(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext, tmp_path: Path
) -> None:
    calls = _fake_edge(monkeypatch, fail_first=2)
    monkeypatch.setattr(tts_edge, "audio_duration", lambda path, cbr_kbps: 1.0)
    registry.create("tts", "edge").synthesize(
        "x", voice="v", language="en", output=tmp_path / "a.mp3", ctx=ctx
    )
    assert len(calls) == 3


def test_edge_rejects_empty_text(monkeypatch: pytest.MonkeyPatch, ctx: RunContext, tmp_path: Path) -> None:
    _fake_edge(monkeypatch)
    with pytest.raises(EraseDubError, match="nothing to say"):
        registry.create("tts", "edge").synthesize(
            "  ", voice="v", language="en", output=tmp_path / "a.mp3", ctx=ctx
        )


def test_audio_duration_falls_back_to_the_bit_rate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_audio, "find_ffprobe", lambda: None)
    path = tmp_path / "a.mp3"
    path.write_bytes(b"\0" * 6000)
    assert _audio.audio_duration(path, cbr_kbps=48) == pytest.approx(1.0)


# --- ElevenLabs -------------------------------------------------------------------------------------------


class _Response:
    def __init__(self, status: int, data: Any = None, content: bytes = b"") -> None:
        self.status_code = status
        self._data = data
        self.content = content

    def json(self) -> Any:
        if self._data is None:
            raise ValueError("no json")
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _HTTPStatusError(self)


class _HTTPStatusError(Exception):
    def __init__(self, response: _Response) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


class _TransportError(Exception):
    pass


def _fake_httpx(monkeypatch: pytest.MonkeyPatch, responses: list[_Response]) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    class Client:
        def __init__(self, *, base_url: str, headers: dict[str, str], timeout: float) -> None:
            sent.append({"init": True, "base_url": base_url, "headers": headers})

        def request(self, method: str, url: str, **kwargs: Any) -> _Response:
            sent.append({"method": method, "url": url, **kwargs})
            return responses.pop(0)

        def close(self) -> None:
            pass

    module = types.ModuleType("httpx")
    module.Client = Client  # type: ignore[attr-defined]
    module.HTTPStatusError = _HTTPStatusError  # type: ignore[attr-defined]
    module.TransportError = _TransportError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "httpx", module)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key-not-real")
    return sent


def test_elevenlabs_lists_all_voices_verified_first_across_pages(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext
) -> None:
    page1 = {
        "voices": [
            {"voice_id": "a", "name": "Adam", "labels": {"gender": "male"}, "verified_languages": []},
            {
                "voice_id": "b",
                "name": "Bich",
                "labels": {"gender": "female"},
                "verified_languages": [{"language": "vi", "preview_url": "https://example.invalid/b.mp3"}],
            },
        ],
        "has_more": True,
        "next_page_token": "p2",
    }
    page2 = {"voices": [{"voice_id": "c", "name": "Cy", "labels": {}}], "has_more": False}
    sent = _fake_httpx(monkeypatch, [_Response(200, page1), _Response(200, page2)])
    voices = registry.create("tts", "elevenlabs").voices("vi", ctx=ctx)
    assert [v.id for v in voices] == ["b", "a", "c"]
    assert voices[0].preview_url == "https://example.invalid/b.mp3"
    assert voices[0].gender == "female" and voices[2].gender is None
    assert all(v.language == "vi" for v in voices)
    assert sent[0]["headers"] == {"xi-api-key": "test-key-not-real"}
    assert sent[2]["params"] == {"page_size": 100, "next_page_token": "p2"}


def test_elevenlabs_synthesize_and_speed_up(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext, tmp_path: Path
) -> None:
    sent = _fake_httpx(monkeypatch, [_Response(200, content=b"mp3-1"), _Response(200, content=b"mp3-2")])
    durations = iter([2.4, 2.0])
    monkeypatch.setattr(tts_elevenlabs, "audio_duration", lambda path, cbr_kbps: next(durations))
    tts = registry.create("tts", "elevenlabs", {"stability": 0.4})
    result = tts.synthesize(
        "Xin chào", voice="voice/1", language="vi", output=tmp_path / "x.wav", max_duration=2.0, ctx=ctx
    )
    first, second = sent[1], sent[2]
    assert first["url"] == "/v1/text-to-speech/voice%2F1"
    assert first["params"] == {"output_format": "mp3_44100_128"}
    assert first["json"]["voice_settings"] == {"speed": 1.0, "stability": 0.4}
    assert "language_code" not in first["json"]
    assert second["json"]["voice_settings"]["speed"] == 1.2  # 2.4 s into 2.0 s, capped at the API's 1.2
    assert result.audio == tmp_path / "x.mp3" and result.audio.read_bytes() == b"mp3-2"
    assert result.duration == 2.0


def test_elevenlabs_retries_rate_limits(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext, tmp_path: Path
) -> None:
    sent = _fake_httpx(monkeypatch, [_Response(429), _Response(503), _Response(200, content=b"ok")])
    monkeypatch.setattr(tts_elevenlabs, "audio_duration", lambda path, cbr_kbps: 1.0)
    tts = registry.create("tts", "elevenlabs", {"send_language": True})
    tts.synthesize("hi", voice="v", language="pt-BR", output=tmp_path / "a.mp3", ctx=ctx)
    posts = [s for s in sent if s.get("method") == "POST"]
    assert len(posts) == 3
    assert posts[-1]["json"]["language_code"] == "pt"


def test_elevenlabs_bad_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch, ctx: RunContext) -> None:
    _fake_httpx(monkeypatch, [_Response(401, {"detail": {"message": "invalid key"}})])
    with pytest.raises(EraseDubError, match="ELEVENLABS_API_KEY") as info:
        registry.create("tts", "elevenlabs").voices("en", ctx=ctx)
    assert "test-key-not-real" not in str(info.value)


def test_elevenlabs_gives_up_after_retries(monkeypatch: pytest.MonkeyPatch, ctx: RunContext) -> None:
    _fake_httpx(monkeypatch, [_Response(500)] * 2)
    with pytest.raises(EraseDubError, match="HTTP 500"):
        registry.create("tts", "elevenlabs", {"retries": 2}).voices("en", ctx=ctx)
