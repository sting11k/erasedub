"""Everything a provider (plugin) author needs, in one place.

This module is the only supported import path for plugins: names imported from elsewhere in ``erasedub`` may
move between releases. See ``docs/plugins.md``.

Example::

    from erasedub.plugin import PLUGIN_API_VERSION, ProviderOptions, RunContext, Translator


    class MyTranslator(Translator):
        name = "mine"
        summary = "My translation service"
        api_version = 1  # the plugin API this class was written for

        class Options(ProviderOptions):
            formality: str = "default"

        def translate(self, lines, *, target, source=None, style="faithful", glossary=None, ctx): ...
"""

from __future__ import annotations

from erasedub.context import RunContext, validate_device
from erasedub.errors import (
    CancelledError,
    ConfigError,
    EraseDubError,
    ProviderNotFoundError,
    ProviderUnavailableError,
    ScriptFormatError,
)
from erasedub.models import (
    Box,
    RegionKind,
    SubtitleEvent,
    SynthResult,
    TextRegion,
    TimeSpan,
    Transcript,
    TranscriptSegment,
    TranslationStyle,
    VideoInfo,
    Voice,
    Word,
)
from erasedub.providers.base import (
    PLUGIN_API_VERSION,
    Availability,
    EraserSpec,
    GpuBackend,
    Kind,
    NoOptions,
    Provider,
    ProviderOptions,
    SpeechSynthesizer,
    SubtitleLayout,
    TextDetector,
    TextEraser,
    Transcriber,
    Translator,
    module_available,
)
from erasedub.registry import create_eraser
from erasedub.script import Script, ScriptLine

__all__ = [
    "PLUGIN_API_VERSION",
    "Availability",
    "Box",
    "CancelledError",
    "ConfigError",
    "EraseDubError",
    "EraserSpec",
    "GpuBackend",
    "Kind",
    "NoOptions",
    "Provider",
    "ProviderNotFoundError",
    "ProviderOptions",
    "ProviderUnavailableError",
    "RegionKind",
    "RunContext",
    "Script",
    "ScriptFormatError",
    "ScriptLine",
    "SpeechSynthesizer",
    "SubtitleEvent",
    "SubtitleLayout",
    "SynthResult",
    "TextDetector",
    "TextEraser",
    "TextRegion",
    "TimeSpan",
    "Transcriber",
    "Transcript",
    "TranscriptSegment",
    "TranslationStyle",
    "Translator",
    "VideoInfo",
    "Voice",
    "Word",
    "create_eraser",
    "module_available",
    "validate_device",
]
