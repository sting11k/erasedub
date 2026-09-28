from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from erasedub import languages
from erasedub.context import RunContext
from erasedub.errors import EraseDubError
from erasedub.models import SynthResult, Voice
from erasedub.providers._audio import audio_duration, mp3_path
from erasedub.providers._retry import with_retries
from erasedub.providers.base import PLUGIN_API_VERSION, ProviderOptions, SpeechSynthesizer

#: edge-tts always returns "audio-24khz-48kbitrate-mono-mp3".
EDGE_KBPS = 48
_GENDERS: dict[str, Literal["female", "male"]] = {"Female": "female", "Male": "male"}


class EdgeOptions(ProviderOptions):
    #: Largest speed-up used to fit a line into ``max_duration``, in percent (``+50%`` = 1.5x speed).
    max_rate: int = Field(default=50, ge=0, le=100)
    #: Pitch change, e.g. ``"+0Hz"`` or ``"-5Hz"``.
    pitch: str = Field(default="+0Hz", pattern=r"^[+-]\d+Hz$")
    #: Volume change, e.g. ``"+0%"``.
    volume: str = Field(default="+0%", pattern=r"^[+-]\d+%$")
    #: Tries per request when the service fails or drops the connection.
    retries: int = Field(default=3, ge=1, le=10)


class EdgeTTS(SpeechSynthesizer):
    """Microsoft Edge online voices via the edge-tts package (LGPL-3.0). Free, no key, needs internet.

    Writes MP3 (``output`` with an ``.mp3`` suffix). With ``max_duration``, a line that comes out too long is
    spoken again faster, up to ``max_rate`` percent.
    """

    name: ClassVar[str] = "edge"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = "Edge-TTS online voices (free, unofficial, may break)"
    requires_modules: ClassVar[tuple[str, ...]] = ("edge_tts",)
    Options: ClassVar[type[ProviderOptions]] = EdgeOptions

    options: EdgeOptions

    def voices(self, language: str, *, ctx: RunContext) -> list[Voice]:
        """Voices whose locale matches ``language``; exact region matches (``zh-TW``) come first."""
        import edge_tts

        found = with_retries(
            lambda: asyncio.run(edge_tts.list_voices()),
            retryable=_retryable,
            ctx=ctx,
            what="Edge-TTS voice list",
            attempts=self.options.retries,
        )
        wanted = languages.normalize(language)
        base = languages.base(wanted)
        exact: list[Voice] = []
        same_base: list[Voice] = []
        for item in found:
            try:
                code = languages.normalize(item["Locale"])
            except ValueError:
                continue
            if languages.base(code) != base:
                continue
            voice = Voice(
                id=item["ShortName"],
                language=code,
                name=item.get("FriendlyName") or item["ShortName"],
                gender=_GENDERS.get(item.get("Gender", "")),
            )
            (exact if code == wanted or code.startswith(wanted + "-") else same_base).append(voice)
        return exact + same_base

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
            raise EraseDubError("Edge-TTS: nothing to say (empty line)")
        audio = mp3_path(output)
        audio.parent.mkdir(parents=True, exist_ok=True)
        rate = 0
        duration = self._speak(text, voice, rate, audio, ctx)
        # The speaking rate scales duration almost linearly; two corrections are enough in practice.
        for _ in range(2):
            if max_duration is None or duration <= max_duration or rate >= self.options.max_rate:
                break
            # speed (1 + rate/100) must grow by duration/max_duration; +2 points of margin
            needed = math.ceil((100 + rate) * duration / max_duration - 100) + 2
            rate = min(self.options.max_rate, max(rate + 1, needed))
            ctx.logger.debug(
                "Edge-TTS line is %.2f s for a %.2f s slot; speaking at +%d%%", duration, max_duration, rate
            )
            duration = self._speak(text, voice, rate, audio, ctx)
        return SynthResult(audio=audio, duration=duration)

    def _speak(self, text: str, voice: str, rate: int, audio: Path, ctx: RunContext) -> float:
        import edge_tts

        def run() -> None:
            communicate = edge_tts.Communicate(
                text, voice, rate=f"{rate:+d}%", volume=self.options.volume, pitch=self.options.pitch
            )
            asyncio.run(communicate.save(str(audio)))

        with_retries(run, retryable=_retryable, ctx=ctx, what="Edge-TTS", attempts=self.options.retries)
        if not audio.exists() or audio.stat().st_size == 0:
            raise EraseDubError(f"Edge-TTS returned no audio for voice {voice}")
        return audio_duration(audio, cbr_kbps=EDGE_KBPS)


def _retryable(exc: Exception) -> bool:
    """Network trouble and the service's own hiccups; bad arguments (unknown voice) are not retried."""
    if isinstance(exc, ValueError | TypeError):
        return False
    module = type(exc).__module__
    return module.startswith(("edge_tts", "aiohttp", "asyncio")) or isinstance(exc, OSError | TimeoutError)
