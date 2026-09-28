from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import quote

from pydantic import Field

from erasedub import languages
from erasedub.context import RunContext
from erasedub.errors import EraseDubError
from erasedub.models import SynthResult, Voice
from erasedub.providers._audio import audio_duration, mp3_path
from erasedub.providers._retry import with_retries
from erasedub.providers.base import PLUGIN_API_VERSION, ProviderOptions, SpeechSynthesizer

API_URL = "https://api.elevenlabs.io"
#: The output format requested from the API: MP3, 44.1 kHz, 128 kbit/s constant bit rate.
OUTPUT_FORMAT = "mp3_44100_128"
OUTPUT_KBPS = 128
#: ElevenLabs accepts a speaking speed between 0.7 and 1.2 (1.0 = normal).
MAX_SPEED = 1.2
_GENDERS: dict[str, Literal["female", "male", "neutral"]] = {
    "female": "female",
    "male": "male",
    "neutral": "neutral",
    "non-binary": "neutral",
}


class ElevenLabsOptions(ProviderOptions):
    model_id: str = Field(default="eleven_multilingual_v2", min_length=1)
    #: Voice settings sent with every request; ``None`` keeps the voice's own setting.
    stability: float | None = Field(default=None, ge=0, le=1)
    similarity_boost: float | None = Field(default=None, ge=0, le=1)
    style: float | None = Field(default=None, ge=0, le=1)
    #: Largest speaking speed used to fit a line into ``max_duration`` (the API allows up to 1.2).
    max_speed: float = Field(default=MAX_SPEED, ge=1.0, le=MAX_SPEED)
    #: Send ``language_code`` with each request. Only some models accept it (turbo/flash v2.5 and newer).
    send_language: bool = False
    timeout: float = Field(default=120, gt=0)
    retries: int = Field(default=4, ge=1, le=10)


class ElevenLabsTTS(SpeechSynthesizer):
    """ElevenLabs voices with your own API key (``ELEVENLABS_API_KEY``), over the public REST API.

    Writes MP3 (``output`` with an ``.mp3`` suffix). Multilingual models speak every supported language
    with any voice, so :meth:`voices` lists all voices of the account, the ones verified for the language
    first. With ``max_duration``, a line that comes out too long is spoken again faster, up to
    ``max_speed``.
    """

    name: ClassVar[str] = "elevenlabs"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = "ElevenLabs voices (your key)"
    requires_modules: ClassVar[tuple[str, ...]] = ("httpx",)
    extra: ClassVar[str | None] = "elevenlabs"
    required_env: ClassVar[tuple[str, ...]] = ("ELEVENLABS_API_KEY",)
    Options: ClassVar[type[ProviderOptions]] = ElevenLabsOptions

    options: ElevenLabsOptions
    _client: Any = None

    def open(self, ctx: RunContext) -> None:
        self._client = self._new_client()

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.close()

    def _new_client(self) -> Any:
        import httpx

        key = os.environ.get("ELEVENLABS_API_KEY", "")
        if not key:
            raise EraseDubError("set environment variable ELEVENLABS_API_KEY")
        return httpx.Client(base_url=API_URL, headers={"xi-api-key": key}, timeout=self.options.timeout)

    def _http(self) -> Any:
        if self._client is None:
            self._client = self._new_client()
        return self._client

    def _request(self, method: str, url: str, ctx: RunContext, **kwargs: Any) -> Any:
        def send() -> Any:
            response = self._http().request(method, url, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()
            return response

        import httpx

        try:
            response = with_retries(
                send, retryable=_retryable, ctx=ctx, what="ElevenLabs request", attempts=self.options.retries
            )
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise EraseDubError(f"ElevenLabs request failed: HTTP {code} {_detail(exc.response)}") from exc
        except httpx.TransportError as exc:
            raise EraseDubError(f"cannot reach ElevenLabs ({type(exc).__name__})") from exc
        if response.status_code == 401:
            raise EraseDubError("ElevenLabs rejected the API key (HTTP 401): check ELEVENLABS_API_KEY")
        if response.status_code >= 400:
            raise EraseDubError(f"ElevenLabs request failed: HTTP {response.status_code} {_detail(response)}")
        return response

    def voices(self, language: str, *, ctx: RunContext) -> list[Voice]:
        base = languages.base(language)
        verified: list[Voice] = []
        others: list[Voice] = []
        params: dict[str, Any] = {"page_size": 100}
        while True:
            data = self._request("GET", "/v2/voices", ctx, params=params).json()
            for item in data.get("voices", []):
                labels = item.get("labels") or {}
                langs = [v for v in item.get("verified_languages") or [] if _base_or_none(v.get("language"))]
                match = next((v for v in langs if _base_or_none(v.get("language")) == base), None)
                voice = Voice(
                    id=str(item["voice_id"]),
                    language=languages.normalize(language),
                    name=str(item.get("name") or item["voice_id"]),
                    gender=_GENDERS.get(str(labels.get("gender", "")).lower()),
                    preview_url=(match or {}).get("preview_url") or item.get("preview_url"),
                )
                (verified if match else others).append(voice)
            token = data.get("next_page_token")
            if not data.get("has_more") or not token:
                break
            params = {"page_size": 100, "next_page_token": token}
        return verified + others

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
        if not text.strip():
            raise EraseDubError("ElevenLabs: nothing to say (empty line)")
        audio = mp3_path(output)
        audio.parent.mkdir(parents=True, exist_ok=True)
        speed = 1.0
        duration = self._speak(text, voice, language, speed, audio, ctx)
        if max_duration is not None and duration > max_duration and self.options.max_speed > 1.0:
            speed = min(self.options.max_speed, round(duration / max_duration + 0.02, 2))
            ctx.logger.debug(
                "ElevenLabs line is %.2f s for a %.2f s slot; speaking at %.2fx",
                duration,
                max_duration,
                speed,
            )
            duration = self._speak(text, voice, language, speed, audio, ctx)
        return SynthResult(audio=audio, duration=duration)

    def _speak(
        self, text: str, voice: str, language: str, speed: float, audio: Path, ctx: RunContext
    ) -> float:
        settings: dict[str, float] = {"speed": speed}
        for field in ("stability", "similarity_boost", "style"):
            value = getattr(self.options, field)
            if value is not None:
                settings[field] = value
        body: dict[str, Any] = {"text": text, "model_id": self.options.model_id, "voice_settings": settings}
        if self.options.send_language:
            body["language_code"] = languages.base(language)
        response = self._request(
            "POST",
            f"/v1/text-to-speech/{quote(voice, safe='')}",
            ctx,
            params={"output_format": OUTPUT_FORMAT},
            json=body,
            headers={"Accept": "audio/mpeg"},
        )
        if not response.content:
            raise EraseDubError(f"ElevenLabs returned no audio for voice {voice}")
        audio.write_bytes(response.content)
        return audio_duration(audio, cbr_kbps=OUTPUT_KBPS)


def _base_or_none(code: object) -> str | None:
    if not isinstance(code, str):
        return None
    try:
        return languages.base(code)
    except ValueError:
        return None


def _retryable(exc: Exception) -> bool:
    """Timeouts, dropped connections, rate limits (429) and server errors (5xx)."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        status: int = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, httpx.TransportError)


def _detail(response: Any) -> str:
    """The API's error message, without echoing request data."""
    try:
        detail = response.json().get("detail")
    except ValueError:
        return ""
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("status")
    return f"- {detail}" if isinstance(detail, str) else ""
