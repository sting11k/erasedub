from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, ClassVar

from pydantic import Field

from erasedub import languages
from erasedub.context import RunContext
from erasedub.errors import ConfigError, EraseDubError
from erasedub.models import TranslationStyle
from erasedub.providers._retry import with_retries
from erasedub.providers.base import PLUGIN_API_VERSION, ProviderOptions, Translator
from erasedub.script import ScriptLine

#: Google's codes where they differ from BCP-47 (Chinese needs a script; a few legacy codes).
_GOOGLE_CODES: dict[str, str] = {
    "zh": "zh-CN",
    "zh-Hans": "zh-CN",
    "zh-CN": "zh-CN",
    "zh-SG": "zh-CN",
    "zh-Hant": "zh-TW",
    "zh-TW": "zh-TW",
    "zh-HK": "zh-TW",
    "zh-MO": "zh-TW",
    "he": "iw",
    "jv": "jw",
    "fil": "tl",
    "nb": "no",
}
#: Stays in one request: the web endpoint accepts up to 5000 characters.
_MAX_REQUEST_CHARS = 4500


class GoogleOptions(ProviderOptions):
    #: Lines sent per request, joined by line breaks (fewer requests, less rate limiting).
    batch_lines: int = Field(default=40, ge=1, le=200)
    #: Tries per request; waits 1, 2, 4, ... seconds between them.
    retries: int = Field(default=5, ge=1, le=10)
    #: Seconds to wait for one request before giving up on it (and retrying).
    timeout: float = Field(default=30, gt=0, le=300)


class GoogleFreeTranslator(Translator):
    """Google Translate through the free web endpoint (via deep-translator). No key, best effort.

    Lines are sent in batches joined by line breaks; a batch whose answer does not split back into the same
    number of lines is translated again line by line, so the result always has one string per line.
    Glossary terms are swapped for placeholders before translating and replaced by the wanted translation
    afterwards. ``style`` does not apply to machine translation. A line Google gives back empty (only
    punctuation or symbols) is kept as it is.
    """

    name: ClassVar[str] = "google"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = "Google Translate web (free, unofficial, may be rate-limited)"
    requires_modules: ClassVar[tuple[str, ...]] = ("deep_translator",)
    Options: ClassVar[type[ProviderOptions]] = GoogleOptions

    options: GoogleOptions

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
        from deep_translator import GoogleTranslator
        from deep_translator.exceptions import LanguageNotSupportedException

        try:
            client = GoogleTranslator(
                source=google_code(source) if source else "auto", target=google_code(target)
            )
        except LanguageNotSupportedException as exc:
            raise ConfigError(f"Google Translate does not support {source or ''} -> {target}") from exc

        protect = _Glossary(glossary or {})
        hidden = [protect.hide(" ".join(line.text.split())) for line in lines]
        texts = [text for text, _ in hidden]
        out: list[str] = []
        for start, batch in _batches(texts, self.options.batch_lines):
            ctx.raise_if_cancelled()
            ctx.progress(start / max(1, len(texts)), f"Google Translate {start}/{len(texts)}")
            out += self._translate_batch(client, batch, ctx)
        ctx.progress(1.0, "Google Translate done")
        return [protect.restore(text) if used else text for text, (_, used) in zip(out, hidden, strict=True)]

    def _translate_batch(self, client: Any, batch: list[str], ctx: RunContext) -> list[str]:
        todo = [i for i, text in enumerate(batch) if text.strip()]
        result = ["" for _ in batch]
        if not todo:
            return result
        joined = "\n".join(batch[i] for i in todo)
        if len(todo) > 1:
            parts = self._call(client.translate, joined, ctx).split("\n")
            if len(parts) == len(todo):
                for i, part in zip(todo, parts, strict=True):
                    result[i] = part.strip()
                return result
            ctx.logger.debug("Google Translate merged or split lines; translating this batch line by line")
        for i in todo:
            result[i] = self._call(client.translate, batch[i], ctx).strip()
        return result

    def _call(self, translate: Callable[[str], Any], text: str, ctx: RunContext) -> str:
        try:
            value = with_retries(
                lambda: _with_timeout(lambda: translate(text), self.options.timeout, ctx),
                retryable=_retryable,
                ctx=ctx,
                what="Google Translate",
                attempts=self.options.retries,
            )
        except Exception as exc:
            if _retryable(exc) or type(exc).__module__.startswith("deep_translator"):
                raise EraseDubError(
                    f"Google Translate failed ({type(exc).__name__}); try again later or choose another "
                    "translator (translate.provider)"
                ) from exc
            raise
        return value if isinstance(value, str) else text


def _with_timeout(call: Callable[[], Any], timeout: float, ctx: RunContext) -> Any:
    """Run ``call`` on a daemon thread; give up after ``timeout`` seconds, or at once on cancel.

    deep-translator sends its requests without a timeout, so a stuck connection would hang the run. An
    abandoned thread ends when its request does, or with the process.
    """
    done = threading.Event()
    outcome: list[Any] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            outcome.append(call())
        except BaseException as exc:
            failure.append(exc)
        finally:
            done.set()

    threading.Thread(target=run, name="google-translate", daemon=True).start()
    deadline = time.monotonic() + timeout
    while not done.wait(min(0.5, max(0.0, deadline - time.monotonic()))):
        ctx.raise_if_cancelled()
        if time.monotonic() >= deadline:
            raise TimeoutError(f"no answer within {timeout:g} s")
    if failure:
        raise failure[0]
    return outcome[0]


def google_code(code: str) -> str:
    """Google Translate's code for a BCP-47 tag, e.g. ``zh`` -> ``zh-CN``, ``pt-BR`` -> ``pt``."""
    tag = languages.normalize(code)
    if tag in _GOOGLE_CODES:
        return _GOOGLE_CODES[tag]
    base = languages.base(tag)
    if base == "zh":
        return "zh-TW" if "-Hant" in tag else "zh-CN"
    return _GOOGLE_CODES.get(base, base)


def _batches(texts: list[str], size: int) -> list[tuple[int, list[str]]]:
    """Consecutive batches of at most ``size`` lines and about ``_MAX_REQUEST_CHARS`` characters."""
    out: list[tuple[int, list[str]]] = []
    start, chars = 0, 0
    current: list[str] = []
    for i, text in enumerate(texts):
        if current and (len(current) >= size or chars + len(text) + 1 > _MAX_REQUEST_CHARS):
            out.append((start, current))
            start, current, chars = i, [], 0
        current.append(text)
        chars += len(text) + 1
    if current:
        out.append((start, current))
    return out


def _retryable(exc: Exception) -> bool:
    """Rate limits, failed or timed-out requests and connection trouble; bad languages are not retried."""
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__
    if name in ("TooManyRequests", "RequestError", "TranslationNotFound"):
        return True
    return type(exc).__module__.startswith(("requests", "urllib3")) or isinstance(exc, ConnectionError)


class _Glossary:
    """Protects glossary terms from machine translation with numbered placeholders."""

    def __init__(self, terms: Mapping[str, str]) -> None:
        # Longest first, so "New York City" wins over "New York".
        self._terms = sorted(((s, t) for s, t in terms.items() if s.strip()), key=lambda st: -len(st[0]))
        self._pattern = (
            re.compile("|".join(re.escape(s) for s, _ in self._terms), re.IGNORECASE) if self._terms else None
        )
        self._index = {s.casefold(): i for i, (s, _) in enumerate(self._terms)}

    def hide(self, text: str) -> tuple[str, bool]:
        """``text`` with each glossary term replaced by ``[n]``, and whether anything was replaced.

        A line that already contains something like ``[3]`` is left alone, so restoring cannot mix them up.
        """
        if self._pattern is None or _PLACEHOLDER.search(text):
            return text, False
        hidden = self._pattern.sub(lambda m: f"[{self._number(m.group(0))}]", text)
        return hidden, hidden != text

    def restore(self, text: str) -> str:
        return _PLACEHOLDER.sub(lambda m: self._target(int(m.group(1)), m.group(0)), text)

    def _number(self, found: str) -> int:
        index = self._index.get(found.casefold())
        if index is None:  # case folding that changes length (German sharp s); match term by term
            index = next(
                i for i, (s, _) in enumerate(self._terms) if re.fullmatch(re.escape(s), found, re.IGNORECASE)
            )
        return index

    def _target(self, index: int, fallback: str) -> str:
        return self._terms[index][1] if index < len(self._terms) else fallback


_PLACEHOLDER = re.compile(r"\[\s*(\d+)\s*\]")
