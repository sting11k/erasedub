"""Google and LLM translators, with the libraries and the network replaced by fakes."""

from __future__ import annotations

import json
import sys
import threading
import types
from pathlib import Path
from typing import Any, ClassVar

import deep_translator
import pytest
from deep_translator.exceptions import TooManyRequests

from erasedub import registry
from erasedub.context import RunContext
from erasedub.errors import CancelledError, EraseDubError
from erasedub.providers import _retry, translate_google, translate_llm
from erasedub.script import ScriptLine


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_retry, "_sleep", lambda seconds, ctx: None)


def _lines(*texts: str) -> list[ScriptLine]:
    return [ScriptLine(start=i, end=i + 1.5, text=t) for i, t in enumerate(texts)]


# --- Google -----------------------------------------------------------------------------------------------


class _FakeGoogle:
    instances: ClassVar[list[_FakeGoogle]] = []

    def __init__(self, source: str, target: str) -> None:
        self.source, self.target = source, target
        self.requests: list[str] = []
        self.fail = 0
        self.merge_lines = False
        _FakeGoogle.instances.append(self)

    def translate(self, text: str) -> str:
        self.requests.append(text)
        if self.fail:
            self.fail -= 1
            raise TooManyRequests()
        rows = [f"<{row}>" for row in text.split("\n")]
        return " ".join(rows) if self.merge_lines else "\n".join(rows)


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> type[_FakeGoogle]:
    _FakeGoogle.instances = []
    monkeypatch.setattr(deep_translator, "GoogleTranslator", _FakeGoogle)
    return _FakeGoogle


def test_google_translates_in_one_batch_and_keeps_line_count(
    google: type[_FakeGoogle], ctx: RunContext
) -> None:
    lines = _lines("你好", "", "两行\n字幕", "再见")
    out = registry.create("translator", "google").translate(lines, target="vi", source="zh", ctx=ctx)
    assert out == ["<你好>", "", "<两行 字幕>", "<再见>"]
    client = google.instances[0]
    assert (client.source, client.target) == ("zh-CN", "vi")
    assert client.requests == ["你好\n两行 字幕\n再见"]


def test_google_falls_back_to_line_by_line_when_lines_merge(
    google: type[_FakeGoogle], ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = google.__init__

    def merging(self: _FakeGoogle, source: str, target: str) -> None:
        original(self, source, target)
        self.merge_lines = True

    monkeypatch.setattr(google, "__init__", merging)
    out = registry.create("translator", "google").translate(_lines("a", "b"), target="en", ctx=ctx)
    assert out == ["<a>", "<b>"]
    assert google.instances[0].requests == ["a\nb", "a", "b"]
    assert google.instances[0].source == "auto"


def test_google_batches_respect_the_line_limit(google: type[_FakeGoogle], ctx: RunContext) -> None:
    lines = _lines(*[f"l{i}" for i in range(5)])
    out = registry.create("translator", "google", {"batch_lines": 2}).translate(lines, target="en", ctx=ctx)
    assert out == [f"<l{i}>" for i in range(5)]
    assert google.instances[0].requests == ["l0\nl1", "l2\nl3", "l4"]


def test_google_applies_the_glossary(google: type[_FakeGoogle], ctx: RunContext) -> None:
    out = registry.create("translator", "google").translate(
        _lines("I love EraseDub and erasedub", "keep [3] as is EraseDub"),
        target="vi",
        glossary={"EraseDub": "EraseDub-VN", " ": "ignored"},
        ctx=ctx,
    )
    assert out[0] == "<I love EraseDub-VN and EraseDub-VN>"
    assert out[1] == "<keep [3] as is EraseDub>"  # a line with its own [n] is left alone
    assert google.instances[0].requests[0].startswith("I love [0] and [0]")


def test_google_retries_rate_limits_then_fails_clearly(google: type[_FakeGoogle], ctx: RunContext) -> None:
    translator = registry.create("translator", "google", {"retries": 3})
    original = google.translate

    def flaky(self: _FakeGoogle, text: str) -> str:
        self.fail = 2 if not self.requests else self.fail
        return original(self, text)

    google.translate = flaky  # type: ignore[method-assign]
    try:
        assert translator.translate(_lines("x"), target="en", ctx=ctx) == ["<x>"]
        assert len(google.instances[0].requests) == 3

        def always(self: _FakeGoogle, text: str) -> str:
            raise TooManyRequests()

        google.translate = always  # type: ignore[method-assign]
        with pytest.raises(EraseDubError, match="Google Translate failed"):
            translator.translate(_lines("x"), target="en", ctx=ctx)
    finally:
        google.translate = original  # type: ignore[method-assign]


def test_google_keeps_lines_it_returns_empty(google: type[_FakeGoogle], ctx: RunContext) -> None:
    original = google.translate

    def echo_none(self: _FakeGoogle, text: str) -> str | None:
        # deep-translator returns None when the input has no letters or digits.
        return original(self, text) if any(c.isalnum() for c in text) else None

    google.translate = echo_none  # type: ignore[method-assign,assignment]
    try:
        translator = registry.create("translator", "google")
        assert translator.translate(_lines("…", "!!"), target="vi", ctx=ctx) == ["…", "!!"]  # one batch
        assert translator.translate(_lines("♪♪"), target="vi", ctx=ctx) == ["♪♪"]  # one line
    finally:
        google.translate = original  # type: ignore[method-assign]


def test_google_gives_up_on_a_stuck_request(google: type[_FakeGoogle], ctx: RunContext) -> None:
    release = threading.Event()
    original = google.translate

    def stuck(self: _FakeGoogle, text: str) -> str:
        self.requests.append(text)
        release.wait()
        return text

    google.translate = stuck  # type: ignore[method-assign]
    try:
        translator = registry.create("translator", "google", {"timeout": 0.05, "retries": 2})
        with pytest.raises(EraseDubError, match=r"Google Translate failed \(TimeoutError\)"):
            translator.translate(_lines("x"), target="en", ctx=ctx)
        assert len(google.instances[0].requests) == 2  # a timeout is retried
    finally:
        release.set()
        google.translate = original  # type: ignore[method-assign]


def test_google_cancel_does_not_wait_for_a_stuck_request(google: type[_FakeGoogle], tmp_path: Path) -> None:
    started, release = threading.Event(), threading.Event()
    original = google.translate

    def stuck(self: _FakeGoogle, text: str) -> str:
        started.set()
        release.wait()
        return text

    google.translate = stuck  # type: ignore[method-assign]
    ctx = RunContext(tmp_dir=tmp_path / "tmp", cache_dir=tmp_path / "cache", is_cancelled=started.is_set)
    try:
        translator = registry.create("translator", "google")  # default timeout: 30 s
        with pytest.raises(CancelledError):
            translator.translate(_lines("x"), target="en", ctx=ctx)
    finally:
        release.set()
        google.translate = original  # type: ignore[method-assign]


@pytest.mark.parametrize(
    ("code", "google_code"),
    [
        ("zh", "zh-CN"),
        ("zh-TW", "zh-TW"),
        ("zh-Hant-HK", "zh-TW"),
        ("he", "iw"),
        ("pt-BR", "pt"),
        ("vi", "vi"),
    ],
)
def test_google_language_codes(code: str, google_code: str) -> None:
    assert translate_google.google_code(code) == google_code


# --- LLM prompt and answer parsing ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        '["a", "b"]',
        '{"translations": ["a", "b"]}',
        '```json\n{"translations": ["a", "b"]}\n```',
        'Sure! Here you go: {"translations": ["a", "b"]} Hope it helps.',
        '[{"n": 1, "translation": "a"}, {"n": 2, "text": "b"}]',
    ],
)
def test_parse_translations_accepts_common_shapes(answer: str) -> None:
    assert translate_llm.parse_translations(answer) == ["a", "b"]


@pytest.mark.parametrize("answer", ["", "no json here", '{"other": []}', "[1, 2]"])
def test_parse_translations_rejects_unusable_answers(answer: str) -> None:
    assert translate_llm.parse_translations(answer) is None


def test_prompts_carry_style_glossary_timing_and_context() -> None:
    system = translate_llm.build_system_prompt(
        target="vi", source="zh", style="natural", glossary={"不锈钢": "inox"}
    )
    assert "from zh into the language with code vi" in system
    assert "Translate naturally" in system
    assert "- 不锈钢 => inox" in system
    user = json.loads(
        translate_llm.build_user_prompt(
            [ScriptLine(start=0, end=2.345, text="你好", speaker="A")], [("早", "Chào buổi sáng")]
        )
    )
    assert user == {
        "context": [{"text": "早", "translation": "Chào buổi sáng"}],
        "lines": [{"n": 1, "text": "你好", "seconds": 2.35, "speaker": "A"}],
    }


# --- LLM vendors ------------------------------------------------------------------------------------------


class _Scripted:
    """Answers each request with the next scripted reply (a string, or an exception to raise)."""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.requests: list[dict[str, Any]] = []

    def next(self, **request: Any) -> str:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return str(reply)


def _fake_openai(monkeypatch: pytest.MonkeyPatch, script: _Scripted) -> list[dict[str, Any]]:
    clients: list[dict[str, Any]] = []

    class OpenAI:
        def __init__(self, **kwargs: Any) -> None:
            clients.append(kwargs)
            completions = types.SimpleNamespace(create=self._create)
            self.chat = types.SimpleNamespace(completions=completions)

        def _create(self, **kwargs: Any) -> Any:
            text = script.next(**kwargs)
            message = types.SimpleNamespace(content=text)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

        def close(self) -> None:
            clients.append({"closed": True})

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=OpenAI))
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-not-real")
    return clients


def test_openai_translates_in_batches_with_context(monkeypatch: pytest.MonkeyPatch, ctx: RunContext) -> None:
    script = _Scripted(['{"translations": ["A", "B"]}', '{"translations": ["C"]}'])
    clients = _fake_openai(monkeypatch, script)
    translator = registry.create(
        "translator", "openai", {"batch_lines": 2, "context_lines": 1, "base_url": "http://localhost:8000/v1"}
    )
    translator.open(ctx)
    out = translator.translate(_lines("a", "", "b", "c"), target="vi", glossary={"b": "B!"}, ctx=ctx)
    translator.close()
    assert out == ["A", "", "B", "C"]
    assert clients[0]["base_url"] == "http://localhost:8000/v1"
    assert clients[0]["api_key"] == "test-openai-not-real"
    assert clients[-1] == {"closed": True}
    first, second = script.requests
    assert first["model"] == "gpt-5-mini"
    assert first["response_format"] == {"type": "json_object"}
    assert "temperature" not in first
    assert "- b => B!" in first["messages"][0]["content"]
    assert json.loads(second["messages"][1]["content"])["context"] == [{"text": "b", "translation": "B"}]


def test_llm_asks_again_once_on_a_wrong_count(monkeypatch: pytest.MonkeyPatch, ctx: RunContext) -> None:
    script = _Scripted(['{"translations": ["A B"]}', '{"translations": ["A", "B"]}'])
    _fake_openai(monkeypatch, script)
    out = registry.create("translator", "openai").translate(_lines("a", "b"), target="vi", ctx=ctx)
    assert out == ["A", "B"]
    assert "had 1 items, but there are 2 lines" in script.requests[1]["messages"][1]["content"]


def test_llm_stops_after_the_second_wrong_answer(monkeypatch: pytest.MonkeyPatch, ctx: RunContext) -> None:
    _fake_openai(monkeypatch, _Scripted(["not json", '["only one"]']))
    with pytest.raises(EraseDubError, match="did not return one translation per line"):
        registry.create("translator", "openai").translate(_lines("a", "b"), target="vi", ctx=ctx)


class _ApiError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"Error code: {status} - key test-openai-not-real is wrong")
        self.status_code = status


def test_llm_retries_server_errors_and_hides_error_text(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext
) -> None:
    script = _Scripted([_ApiError(503), '["ok"]'])
    _fake_openai(monkeypatch, script)
    assert registry.create("translator", "openai").translate(_lines("a"), target="vi", ctx=ctx) == ["ok"]

    _fake_openai(monkeypatch, _Scripted([_ApiError(401)]))
    with pytest.raises(EraseDubError) as info:
        registry.create("translator", "openai").translate(_lines("a"), target="vi", ctx=ctx)
    assert "HTTP 401" in str(info.value) and "OPENAI_API_KEY" in str(info.value)
    assert "test-openai-not-real" not in str(info.value)


def test_claude_uses_the_messages_api(monkeypatch: pytest.MonkeyPatch, ctx: RunContext) -> None:
    script = _Scripted(['["Xin chào"]'])

    class Anthropic:
        def __init__(self, **kwargs: Any) -> None:
            self.messages = types.SimpleNamespace(create=self._create)

        def _create(self, **kwargs: Any) -> Any:
            blocks = [types.SimpleNamespace(type="text", text=script.next(**kwargs))]
            return types.SimpleNamespace(content=blocks)

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    out = registry.create("translator", "claude", {"temperature": 0.2}).translate(
        _lines("Hello"), target="vi", style="faithful", ctx=ctx
    )
    assert out == ["Xin chào"]
    request = script.requests[0]
    assert request["model"] == "claude-sonnet-5"
    assert request["max_tokens"] == 8192
    assert request["temperature"] == 0.2
    assert "Translate faithfully" in request["system"]


def test_gemini_uses_generate_content(monkeypatch: pytest.MonkeyPatch, ctx: RunContext) -> None:
    script = _Scripted(['{"translations": ["Xin chào"]}'])

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            self.models = types.SimpleNamespace(generate_content=self._generate)

        def _generate(self, **kwargs: Any) -> Any:
            return types.SimpleNamespace(text=script.next(**kwargs))

    genai_types = types.SimpleNamespace(HttpOptions=lambda **kw: kw, GenerateContentConfig=lambda **kw: kw)
    genai = types.SimpleNamespace(Client=Client, types=genai_types)
    monkeypatch.setitem(sys.modules, "google", types.SimpleNamespace(genai=genai))
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai_types)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    out = registry.create("translator", "gemini").translate(_lines("Hello"), target="vi", ctx=ctx)
    assert out == ["Xin chào"]
    request = script.requests[0]
    assert request["model"] == "gemini-2.5-flash"
    assert request["config"]["response_mime_type"] == "application/json"


def test_ollama_uses_chat_with_json_format(monkeypatch: pytest.MonkeyPatch, ctx: RunContext) -> None:
    script = _Scripted(['{"translations": ["Xin chào"]}'])
    hosts: list[Any] = []

    class Client:
        def __init__(self, host: str | None = None, **kwargs: Any) -> None:
            hosts.append(host)

        def chat(self, **kwargs: Any) -> Any:
            return types.SimpleNamespace(message=types.SimpleNamespace(content=script.next(**kwargs)))

    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=Client))
    out = registry.create("translator", "ollama", {"host": "http://gpu-box:11434"}).translate(
        _lines("Hello"), target="vi", ctx=ctx
    )
    assert out == ["Xin chào"]
    assert hosts == ["http://gpu-box:11434"]
    assert script.requests[0]["format"] == "json"
    assert script.requests[0]["model"] == "qwen3:8b"


def test_ollama_connection_error_suggests_starting_the_server(
    monkeypatch: pytest.MonkeyPatch, ctx: RunContext
) -> None:
    class Client:
        def __init__(self, host: str | None = None, **kwargs: Any) -> None:
            pass

        def chat(self, **kwargs: Any) -> Any:
            raise ConnectionRefusedError("refused")

    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=Client))
    with pytest.raises(EraseDubError, match="Is the Ollama server running"):
        registry.create("translator", "ollama").translate(_lines("Hello"), target="vi", ctx=ctx)
