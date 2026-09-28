"""The run context handed to every provider call.

One frozen object carries everything a provider may need from the engine besides its inputs: where to report
progress, how to notice cancellation, which device to use, where to put temporary and cached files, and a
logger. New fields can be added here later without changing any provider method signature.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from erasedub.errors import CancelledError, ConfigError

_DEVICE = re.compile(r"^(cpu|mps|cuda(:\d+)?)$")


def validate_device(device: str) -> str:
    """Return ``device`` as ``"cpu"``, ``"cuda"``, ``"cuda:N"`` or ``"mps"``; else raise ``ConfigError``."""
    value = device.strip().lower()
    if not _DEVICE.match(value):
        raise ConfigError("device must be 'cpu', 'cuda', 'cuda:N' (for example 'cuda:1') or 'mps'")
    return value


def _ignore_progress(fraction: float, message: str) -> None:
    return None


def _never_cancelled() -> bool:
    return False


@dataclass(frozen=True, kw_only=True)
class RunContext:
    """What the engine gives a provider for one run. Created by the engine; providers only read it.

    ``device``
        ``"cpu"``, ``"cuda"``, ``"cuda:N"`` or ``"mps"`` (Apple GPU), resolved by the engine from the
        hardware check and the configuration. Providers use it as given and never pick a device themselves.
        ``"mps"`` is only given to erasers that declare
        :attr:`~erasedub.providers.base.TextEraser.supports_mps`.
    ``tmp_dir``
        Scratch directory for this run; the engine deletes it afterwards.
    ``cache_dir``
        Persistent directory for downloaded model weights and similar; shared between runs.
    ``logger``
        Logger for diagnostics. Never log secrets (API keys, tokens) or signed URLs.
    """

    tmp_dir: Path
    cache_dir: Path
    device: str = "cpu"
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("erasedub"))
    #: Receives ``(fraction, message)``; may be called from a worker thread.
    on_progress: Callable[[float, str], None] = _ignore_progress
    #: Returns True once the user asked to cancel; must be cheap and thread-safe.
    is_cancelled: Callable[[], bool] = _never_cancelled

    def __post_init__(self) -> None:
        object.__setattr__(self, "device", validate_device(self.device))

    def progress(self, fraction: float, message: str = "") -> None:
        """Report progress of the current step, ``0.0``-``1.0`` (clamped), with a short human message."""
        value = min(1.0, max(0.0, fraction)) if math.isfinite(fraction) else 0.0
        self.on_progress(value, message)

    def cancelled(self) -> bool:
        return self.is_cancelled()

    def raise_if_cancelled(self) -> None:
        """Raise ``CancelledError`` if the user cancelled. Call it between chunks of long work."""
        if self.is_cancelled():
            raise CancelledError("cancelled by the user")
