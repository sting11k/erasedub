"""LLM translators: bring your own API key, or run a local model with Ollama.

All of them share one prompt pipeline; only the client call differs. Lines are sent in batches with a few
earlier lines as context. The model is asked for a JSON array with exactly one translation per line; an
answer with the wrong number of items is asked for once more, then the run stops with an error. Keys are
read only from environment variables.
"""

from __future__ import annotations

import json
import os
import re
from abc import abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from pydantic import Field

from erasedub.context import RunContext
from erasedub.errors import EraseDubError
from erasedub.models import TranslationStyle
from erasedub.providers._retry import with_retries
from erasedub.providers.base import PLUGIN_API_VERSION, ProviderOptions, Translator
from erasedub.script import ScriptLine

_STYLE_RULES: dict[str, str] = {
    "faithful": (
        "Translate faithfully: keep the meaning, tone and every piece of information of each line. "
        "Do not add, drop or explain anything."
    ),
    "natural": (
        "Translate naturally, the way a native speaker would say it in a dubbed video. Rewrite freely for "
        "fluency and drop filler words, but keep the meaning of each line."
    ),
}
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class LlmOptions(ProviderOptions):
    model: str = Field(min_length=1)
    #: Sampling temperature; ``None`` uses the model's default (some models accept no other value).
    temperature: float | None = Field(default=None, ge=0, le=2)
    #: Lines per request.
    batch_lines: int = Field(default=40, ge=1, le=200)
    #: Earlier lines (with their translations) sent along as context.
    context_lines: int = Field(default=3, ge=0, le=20)
    #: Upper limit for the answer's length, in tokens (used by the APIs that require one).
    max_tokens: int = Field(default=8192, ge=256)
    #: Seconds to wait for one answer.
    timeout: float = Field(default=180, gt=0)
    #: Tries per request on rate limits, timeouts and server errors.
    retries: int = Field(default=3, ge=1, le=10)


class OpenAIOptions(LlmOptions):
    model: str = Field(default="gpt-5-mini", min_length=1)
    #: Another OpenAI-compatible server (DeepSeek, OpenRouter, a local vLLM, ...). Its key still comes
    #: from ``OPENAI_API_KEY``.
    base_url: str | None = None
    #: Ask for a JSON answer (``response_format``); turn off for servers that do not support it.
    json_mode: bool = True


class GeminiOptions(LlmOptions):
    model: str = Field(default="gemini-2.5-flash", min_length=1)


class ClaudeOptions(LlmOptions):
    model: str = Field(default="claude-sonnet-5", min_length=1)


class OllamaOptions(LlmOptions):
    model: str = Field(default="qwen3:8b", min_length=1)
    #: Ollama server; ``None`` uses ``OLLAMA_HOST`` or ``http://localhost:11434``.
    host: str | None = None


class _LlmTranslator(Translator):
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    extra: ClassVar[str | None] = "llm"

    options: LlmOptions
    _client: Any = None

    def open(self, ctx: RunContext) -> None:
        self._client = self._new_client()

    def close(self) -> None:
        client, self._client = self._client, None
        closer = getattr(client, "close", None)
        if callable(closer):
            closer()

    def _api(self) -> Any:
        if self._client is None:
            self._client = self._new_client()
        return self._client

    @abstractmethod
    def _new_client(self) -> Any:
        """Create the vendor's client (keys from the environment only)."""

    @abstractmethod
    def _complete(self, system: str, user: str) -> str:
        """Send one request and return the model's text answer."""

    def _retryable(self, exc: Exception) -> bool:
        """Rate limits, timeouts, dropped connections and server errors."""
        status = _status(exc)
        if status is not None and (status == 429 or status >= 500):
            return True
        name = type(exc).__name__
        return name in ("APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError") or (
            type(exc).__module__.startswith("httpx") and name.endswith(("Timeout", "Error"))
        )

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
        system = build_system_prompt(target=target, source=source, style=style, glossary=glossary or {})
        todo = [i for i, line in enumerate(lines) if line.text.strip()]
        out = ["" for _ in lines]
        size = self.options.batch_lines
        for done in range(0, len(todo), size):
            ctx.raise_if_cancelled()
            ctx.progress(done / max(1, len(todo)), f"{self.name}: {done}/{len(todo)} lines")
            batch = todo[done : done + size]
            before = todo[max(0, done - self.options.context_lines) : done]
            context = [(lines[i].text, out[i]) for i in before]
            answers = self._translate_batch([lines[i] for i in batch], context, system, ctx)
            for i, text in zip(batch, answers, strict=True):
                out[i] = text
        ctx.progress(1.0, f"{self.name}: done")
        return out

    def _translate_batch(
        self, batch: list[ScriptLine], context: list[tuple[str, str]], system: str, ctx: RunContext
    ) -> list[str]:
        user = build_user_prompt(batch, context)
        problem = ""
        for attempt in (1, 2):
            prompt = (
                user if attempt == 1 else f"{user}\n\n{problem} Answer again with exactly {len(batch)} items."
            )
            answer = self._ask(system, prompt, ctx)
            parsed = parse_translations(answer)
            if parsed is None:
                problem = "Your previous answer was not a JSON array of strings."
            elif len(parsed) != len(batch):
                problem = f"Your previous answer had {len(parsed)} items, but there are {len(batch)} lines."
            else:
                return parsed
            ctx.logger.warning("%s: %s", self.name, problem)
        raise EraseDubError(
            f"{self.name} did not return one translation per line ({problem.lower()}); try again or use "
            "another model (translate.options.model)"
        )

    def _ask(self, system: str, user: str, ctx: RunContext) -> str:
        try:
            return with_retries(
                lambda: self._complete(system, user),
                retryable=self._retryable,
                ctx=ctx,
                what=f"{self.name} request",
                attempts=self.options.retries,
            )
        except EraseDubError:
            raise
        except Exception as exc:
            # Only the error type and HTTP status: some services echo part of the key in their messages.
            status = _status(exc)
            where = f" (HTTP {status})" if status else ""
            raise EraseDubError(
                f"{self.name} request failed: {type(exc).__name__}{where}. {self._hint(status)}".rstrip()
            ) from exc

    def _hint(self, status: int | None) -> str:
        """What to check for a failed request."""
        if status in (401, 403) and self.required_env:
            return f"Check the key in {self.required_env[0]}."
        if status == 404:
            return f"Check the model name (translate.options.model = {self.options.model!r})."
        if status == 429:
            return "The service is rate limiting this key; wait and run again."
        return ""


def _status(exc: Exception) -> int | None:
    """The HTTP status of an SDK error: ``status_code`` (openai, anthropic, ollama) or ``code`` (genai)."""
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value < 600:
            return value
    return None


def build_system_prompt(
    *, target: str, source: str | None, style: TranslationStyle, glossary: Mapping[str, str]
) -> str:
    source_part = f"from {source} " if source else ""
    parts = [
        f"You translate video subtitles that will also be spoken by a dubbing voice. Translate each line "
        f"{source_part}into the language with code {target} (BCP-47).",
        _STYLE_RULES[style],
        "Each line has the number of seconds it is spoken in; keep its translation short enough to be said "
        "in that time at a natural pace.",
        "Translate every line separately, in order: never merge, split, skip or reorder lines. Lines under "
        "'context' were translated already; use them only to understand the situation.",
        'Answer with JSON only: {"translations": ["...", "..."]}, one string per line, exactly as many '
        "strings as there are lines.",
    ]
    terms = [(s, t) for s, t in glossary.items() if s.strip()]
    if terms:
        parts.append("Always translate these terms exactly like this:")
        parts += [f"- {s} => {t}" for s, t in terms]
    return "\n".join(parts)


def build_user_prompt(batch: Sequence[ScriptLine], context: Sequence[tuple[str, str]]) -> str:
    payload: dict[str, Any] = {}
    if context:
        payload["context"] = [{"text": text, "translation": done} for text, done in context]
    items = []
    for n, line in enumerate(batch, start=1):
        item: dict[str, Any] = {"n": n, "text": line.text, "seconds": round(line.duration, 2)}
        if line.speaker:
            item["speaker"] = line.speaker
        items.append(item)
    payload["lines"] = items
    return json.dumps(payload, ensure_ascii=False)


def parse_translations(answer: str) -> list[str] | None:
    """The list of strings in a model's answer: a JSON array, or an object with a ``translations`` array.

    Code fences and text around the JSON are tolerated; ``None`` when there is no usable list.
    """
    text = _FENCE.sub("", answer.strip())
    value: Any = None
    try:
        value = json.loads(text)
    except ValueError:
        for opener, closer in (("{", "}"), ("[", "]")):
            first, last = text.find(opener), text.rfind(closer)
            if 0 <= first < last:
                try:
                    value = json.loads(text[first : last + 1])
                    break
                except ValueError:
                    continue
    if isinstance(value, dict):
        value = value.get("translations")
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("translation", item.get("text"))
        if not isinstance(item, str):
            return None
        out.append(item.strip())
    return out


class OpenAITranslator(_LlmTranslator):
    """OpenAI chat completions, or any OpenAI-compatible server through the ``base_url`` option."""

    name: ClassVar[str] = "openai"
    summary: ClassVar[str] = "OpenAI-compatible chat API (your key; base_url option for other vendors)"
    requires_modules: ClassVar[tuple[str, ...]] = ("openai",)
    required_env: ClassVar[tuple[str, ...]] = ("OPENAI_API_KEY",)
    Options: ClassVar[type[ProviderOptions]] = OpenAIOptions

    options: OpenAIOptions

    def _new_client(self) -> Any:
        import openai

        return openai.OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=self.options.base_url,
            timeout=self.options.timeout,
            max_retries=0,  # retried here, with cancellation checks
        )

    def _complete(self, system: str, user: str) -> str:
        kwargs: dict[str, Any] = {}
        if self.options.temperature is not None:
            kwargs["temperature"] = self.options.temperature
        if self.options.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._api().chat.completions.create(
            model=self.options.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs,
        )
        return str(response.choices[0].message.content or "")


class GeminiTranslator(_LlmTranslator):
    """Google Gemini through the google-genai SDK."""

    name: ClassVar[str] = "gemini"
    summary: ClassVar[str] = "Google Gemini (your key)"
    requires_modules: ClassVar[tuple[str, ...]] = ("google.genai",)
    required_env: ClassVar[tuple[str, ...]] = ("GEMINI_API_KEY",)
    Options: ClassVar[type[ProviderOptions]] = GeminiOptions

    options: GeminiOptions

    def _new_client(self) -> Any:
        import google.genai as genai
        from google.genai import types

        return genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY"),
            http_options=types.HttpOptions(timeout=int(self.options.timeout * 1000)),
        )

    def _complete(self, system: str, user: str) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=self.options.temperature,
            max_output_tokens=self.options.max_tokens,
        )
        response = self._api().models.generate_content(model=self.options.model, contents=user, config=config)
        return str(response.text or "")


class ClaudeTranslator(_LlmTranslator):
    """Anthropic Claude through the Messages API."""

    name: ClassVar[str] = "claude"
    summary: ClassVar[str] = "Anthropic Claude (your key)"
    requires_modules: ClassVar[tuple[str, ...]] = ("anthropic",)
    required_env: ClassVar[tuple[str, ...]] = ("ANTHROPIC_API_KEY",)
    Options: ClassVar[type[ProviderOptions]] = ClaudeOptions

    options: ClaudeOptions

    def _new_client(self) -> Any:
        import anthropic

        return anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY"), timeout=self.options.timeout, max_retries=0
        )

    def _complete(self, system: str, user: str) -> str:
        kwargs: dict[str, Any] = {}
        if self.options.temperature is not None:
            kwargs["temperature"] = self.options.temperature
        message = self._api().messages.create(
            model=self.options.model,
            max_tokens=self.options.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            **kwargs,
        )
        return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")


class OllamaTranslator(_LlmTranslator):
    """A local model served by Ollama (no key; the model must be pulled first: ``ollama pull <model>``)."""

    name: ClassVar[str] = "ollama"
    summary: ClassVar[str] = "Local LLM through Ollama (no key, runs on your machine)"
    requires_modules: ClassVar[tuple[str, ...]] = ("ollama",)
    extra: ClassVar[str | None] = "ollama"
    Options: ClassVar[type[ProviderOptions]] = OllamaOptions

    options: OllamaOptions

    def _hint(self, status: int | None) -> str:
        if status is None:
            return "Is the Ollama server running (ollama serve) and reachable (OLLAMA_HOST)?"
        if status == 404:
            return f"Pull the model first: ollama pull {self.options.model}"
        return super()._hint(status)

    def _new_client(self) -> Any:
        import ollama

        return ollama.Client(host=self.options.host, timeout=self.options.timeout)

    def _complete(self, system: str, user: str) -> str:
        options: dict[str, Any] = {"num_predict": self.options.max_tokens}
        if self.options.temperature is not None:
            options["temperature"] = self.options.temperature
        response = self._api().chat(
            model=self.options.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            format="json",
            options=options,
        )
        return str(response.message.content or "")
