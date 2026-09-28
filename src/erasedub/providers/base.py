"""Provider interfaces.

A *provider* implements one pipeline step (erase text, transcribe, translate, speak, ...). The engine talks
to providers only through these base classes, so a provider can be swapped without touching the engine:
built-in providers ship with EraseDub, third-party packages register more through Python entry points
(see ``docs/plugins.md``). Plugin authors import everything from :mod:`erasedub.plugin`, the only supported
import path.

Rules every provider follows:

* Import heavy dependencies (torch, whisperx, gradio, ...) inside methods, never at module import time,
  so ``erasedub --help`` stays fast and works without optional extras.
* Read secrets only from environment variables named in ``required_env``; never log their values.
* ``check()`` must be cheap and side-effect free: no downloads, no network calls, no GPU allocation.
* Declare ``api_version`` as the literal number of the plugin API the provider was written for (currently
  ``1``). The registry refuses a provider whose number differs from :data:`PLUGIN_API_VERSION`.
* Declare accepted options as a nested ``Options(ProviderOptions)`` model. They are validated when the
  provider is created; unknown names and bad values are configuration errors (exit code 2).

Calling conventions:

* Methods are **synchronous**: they block until the work is done. The engine may call them from a worker
  thread (the web UI does), creates one instance per run and never calls the same instance from two
  threads at once. A provider built on asyncio (edge-tts, httpx's async client) runs its own event loop
  inside the call, for example with ``asyncio.run()``, and must not assume anything about the caller's
  thread. Do not call provider methods from inside a running event loop.
* Every method takes a keyword argument ``ctx`` (:class:`~erasedub.context.RunContext`): report progress
  with ``ctx.progress()``, call ``ctx.raise_if_cancelled()`` between chunks of long work, use
  ``ctx.device``, ``ctx.tmp_dir``, ``ctx.cache_dir`` and ``ctx.logger``.
* Lifecycle: the engine calls ``open(ctx)`` once before the first method call and ``close()`` after the
  last one, also when the run fails or is cancelled. Load models in ``open`` and free them (GPU memory!)
  in ``close``; both do nothing by default.
"""

from __future__ import annotations

import importlib.util
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from erasedub.context import RunContext
from erasedub.errors import ConfigError, describe_validation_error
from erasedub.links import missing_extra
from erasedub.models import (
    FrozenModel,
    SubtitleEvent,
    SynthResult,
    TextRegion,
    Transcript,
    TranslationStyle,
    VideoInfo,
    Voice,
)
from erasedub.script import Script, ScriptLine

#: Version of the provider contract in this module. Bumped on any incompatible change.
PLUGIN_API_VERSION: Final = 1

Kind = Literal["eraser", "asr", "ocr", "translator", "tts", "layout", "gpu"]
KINDS: tuple[Kind, ...] = ("eraser", "asr", "ocr", "translator", "tts", "layout", "gpu")


@dataclass(frozen=True)
class Availability:
    """Result of ``Provider.check()``. ``reason`` explains what is missing and how to fix it."""

    ok: bool
    reason: str = ""

    @classmethod
    def ready(cls) -> Availability:
        return cls(ok=True)


def module_available(name: str) -> bool:
    """True if ``name`` (possibly dotted, e.g. ``google.genai``) is importable.

    Only parent packages are imported; the module itself is not.
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # a missing parent package raises instead of returning None
        return False


# pip distribution names for modules whose import name differs, used in "missing package" hints.
PIP_NAMES: dict[str, str] = {
    "cv2": "opencv-python",
    "deep_translator": "deep-translator",
    "edge_tts": "edge-tts",
    "google.genai": "google-genai",
}


def pip_name(module: str) -> str:
    return PIP_NAMES.get(module, module.split(".", 1)[0].replace("_", "-"))


class ProviderOptions(BaseModel):
    """Base class for a provider's options: immutable, unknown option names rejected."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class NoOptions(ProviderOptions):
    """The provider takes no options."""


class Provider(ABC):
    """Common behaviour of all providers."""

    kind: ClassVar[Kind]
    name: ClassVar[str]
    #: Plugin API the provider was written for; must equal :data:`PLUGIN_API_VERSION` to be loaded.
    api_version: ClassVar[int]
    #: One-line description shown by ``erasedub plugins``.
    summary: ClassVar[str] = ""
    #: Python modules that must be importable, e.g. ``("whisperx",)``.
    requires_modules: ClassVar[tuple[str, ...]] = ()
    #: Extra that installs ``requires_modules``, e.g. ``"asr"`` -> ``uv sync --extra asr``.
    extra: ClassVar[str | None] = None
    #: Environment variables that must be set (API keys). Only their presence is ever checked.
    required_env: ClassVar[tuple[str, ...]] = ()
    #: Needs an NVIDIA GPU on the machine that runs it (the planner skips it when there is none).
    requires_gpu: ClassVar[bool] = False
    #: Options model; see :class:`ProviderOptions`.
    Options: ClassVar[type[ProviderOptions]] = NoOptions

    options: ProviderOptions

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        try:
            self.options = self.Options.model_validate(dict(options or {}))
        except ValidationError as exc:
            raise ConfigError(
                f"options of {self.kind} provider '{self.name}': {describe_validation_error(exc)}"
            ) from exc

    def check(self) -> Availability:
        """Report whether this provider can run here. Must not download or call the network."""
        missing = [m for m in self.requires_modules if not module_available(m)]
        if missing:
            if self.extra:
                hint = missing_extra(self.extra)
            else:
                hint = "pip install " + " ".join(dict.fromkeys(pip_name(m) for m in missing))
            return Availability(False, f"missing Python packages {', '.join(missing)} - {hint}")
        unset = [var for var in self.required_env if not os.environ.get(var)]
        if unset:
            return Availability(False, f"set environment variable(s) {', '.join(unset)}")
        return Availability.ready()

    def open(self, ctx: RunContext) -> None:  # noqa: B027 — optional hook, deliberately not abstract
        """Prepare for work: load models, open clients. Called once before the first method call."""

    def close(self) -> None:  # noqa: B027 — optional hook, deliberately not abstract
        """Release what :meth:`open` acquired (GPU memory, clients).

        Called after the last method call, also on failure or cancellation, and even when :meth:`open`
        raised, so it must cope with a half-opened provider and with being called twice.
        """


class TextEraser(Provider):
    """Removes burned-in text from video frames."""

    kind: ClassVar[Kind] = "eraser"
    #: Shown with every plan that uses this eraser (licence limits, known weaknesses); empty for none.
    notice: ClassVar[str] = ""
    #: Can run on an Apple GPU: without CUDA, the engine then erases on ``ctx.device = "mps"`` when PyTorch
    #: reports MPS as available. Erasers that leave it False get ``"cpu"`` there.
    supports_mps: ClassVar[bool] = False

    @abstractmethod
    def erase(self, video: Path, regions: Sequence[TextRegion], output: Path, *, ctx: RunContext) -> Path:
        """Inpaint ``regions`` in ``video`` and return the path of the clean video.

        The result must keep the resolution, frame count, frame rate and timestamps of ``video`` so the new
        audio and subtitles stay in sync; audio is not needed (the engine takes it from the original).
        Write to ``output`` normally; returning another path is allowed (for example ``video`` itself when
        there is nothing to erase). Never modify ``video``. Run on ``ctx.device``.
        """


class Transcriber(Provider):
    """Speech recognition with segment and (if possible) word timestamps."""

    kind: ClassVar[Kind] = "asr"

    @abstractmethod
    def transcribe(self, audio: Path, *, language: str | None = None, ctx: RunContext) -> Transcript:
        """Transcribe ``audio``. ``language=None`` means auto-detect."""


class TextDetector(Provider):
    """Finds on-screen text and when it is visible."""

    kind: ClassVar[Kind] = "ocr"

    @abstractmethod
    def detect(self, video: Path, *, languages: Sequence[str] = (), ctx: RunContext) -> list[TextRegion]:
        """Return text regions found in ``video``; ``languages`` are hints for the OCR model."""


class Translator(Provider):
    """Translates script lines."""

    kind: ClassVar[Kind] = "translator"

    @abstractmethod
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
        """Translate the ``text`` of each line into ``target``, in order.

        Timing, neighbouring lines and ``speaker`` are context (for example, to fit a line into its time
        slot). Must return exactly ``len(lines)`` strings, one per line: never merge or split lines. The
        engine checks this. ``source=None`` means the source language is unknown.
        """


class SpeechSynthesizer(Provider):
    """Text-to-speech."""

    kind: ClassVar[Kind] = "tts"

    @abstractmethod
    def voices(self, language: str, *, ctx: RunContext) -> list[Voice]:
        """Voices available for ``language``."""

    @abstractmethod
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
        """Speak ``text`` into ``output``; try to fit within ``max_duration`` seconds when given."""


class SubtitleLayout(Provider):
    """Decides where and how each subtitle line is drawn."""

    kind: ClassVar[Kind] = "layout"

    @abstractmethod
    def layout(
        self, script: Script, *, video: VideoInfo, regions: Sequence[TextRegion] = (), ctx: RunContext
    ) -> list[SubtitleEvent]:
        """Turn script lines into subtitle events (see :class:`~erasedub.models.SubtitleEvent`)."""


class EraserSpec(FrozenModel):
    """A serializable description of the eraser to run, so a remote backend can build the same one.

    ``options`` must be plain JSON data (they come from the config file).
    """

    name: str = Field(min_length=1)
    options: dict[str, JsonValue] = Field(default_factory=dict)
    api_version: int = PLUGIN_API_VERSION


class GpuBackend(Provider):
    """Where GPU work (erasing) runs: this machine, or a remote GPU paid for by the user."""

    kind: ClassVar[Kind] = "gpu"
    #: True when the eraser runs on another machine. The planner then does not require the eraser's
    #: Python packages or an NVIDIA GPU on this machine; the backend's own ``check()`` decides.
    remote: ClassVar[bool] = False

    @abstractmethod
    def run_eraser(
        self, spec: EraserSpec, video: Path, regions: Sequence[TextRegion], output: Path, *, ctx: RunContext
    ) -> Path:
        """Build the eraser described by ``spec``, run it, and return the clean video's path on this machine.

        A local backend creates the eraser with :func:`erasedub.plugin.create_eraser` and calls its
        ``open``/``erase``/``close``; a remote backend ships ``spec``, ``video`` and ``regions`` to the
        remote machine and builds the eraser there. The output contract is :meth:`TextEraser.erase`'s.
        """


BASE_CLASSES: dict[Kind, type[Provider]] = {
    "eraser": TextEraser,
    "asr": Transcriber,
    "ocr": TextDetector,
    "translator": Translator,
    "tts": SpeechSynthesizer,
    "layout": SubtitleLayout,
    "gpu": GpuBackend,
}
