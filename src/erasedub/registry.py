"""Discovers providers through Python entry points.

Each provider kind has its own entry-point group, ``erasedub.<kind>`` (for example ``erasedub.tts``).
EraseDub registers its built-in providers in its own ``pyproject.toml``; any installed package can add
more the same way, without changes to EraseDub itself.

When two installed distributions register the same name, the built-in one wins and a warning is logged, so a
plugin can never silently replace a built-in provider. Between two third-party plugins the first one found
wins, also with a warning.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Literal, overload

from erasedub import __version__
from erasedub.errors import ProviderNotFoundError, ProviderUnavailableError
from erasedub.providers.base import (
    BASE_CLASSES,
    KINDS,
    PLUGIN_API_VERSION,
    Availability,
    EraserSpec,
    GpuBackend,
    Kind,
    Provider,
    SpeechSynthesizer,
    SubtitleLayout,
    TextDetector,
    TextEraser,
    Transcriber,
    Translator,
)

GROUP_PREFIX = "erasedub."
BUILTIN_DISTRIBUTION = "erasedub"

log = logging.getLogger(__name__)
_warned: set[str] = set()


def group(kind: Kind) -> str:
    return GROUP_PREFIX + kind


def _warn_once(message: str) -> None:
    if message not in _warned:
        _warned.add(message)
        log.warning(message)


def distribution(ep: EntryPoint) -> str | None:
    """Name of the installed distribution (pip package) that registered ``ep``, if known."""
    dist = getattr(ep, "dist", None)
    name = getattr(dist, "name", None)
    return name if isinstance(name, str) else None


def _is_builtin(ep: EntryPoint) -> bool:
    name = distribution(ep)
    return name is not None and name.lower().replace("_", "-") == BUILTIN_DISTRIBUTION


def _raw_entry_points(kind: Kind) -> Iterable[EntryPoint]:
    return entry_points(group=group(kind))


def _entry_points(kind: Kind) -> dict[str, EntryPoint]:
    chosen: dict[str, EntryPoint] = {}
    for ep in _raw_entry_points(kind):
        kept = chosen.get(ep.name)
        if kept is None:
            chosen[ep.name] = ep
            continue
        if distribution(kept) == distribution(ep):
            continue  # the same distribution seen twice on sys.path
        ignored = ep
        if _is_builtin(ep) and not _is_builtin(kept):
            kept, ignored = ep, kept
            chosen[ep.name] = ep
        _warn_once(
            f"{group(kind)}:{ep.name} is registered by both '{distribution(kept)}' and "
            f"'{distribution(ignored)}'; using the one from '{distribution(kept)}'."
        )
    return chosen


def names(kind: Kind) -> list[str]:
    """Registered provider names for ``kind``, sorted."""
    return sorted(_entry_points(kind))


def load_class(kind: Kind, name: str) -> type[Provider]:
    """Import and return the provider class registered as ``name`` for ``kind``.

    Raises :class:`ProviderNotFoundError` for an unknown name or an entry point that is not a provider of
    this kind, and :class:`ProviderUnavailableError` when the plugin fails to import or targets another
    plugin API version.
    """
    eps = _entry_points(kind)
    if name not in eps:
        known = ", ".join(sorted(eps)) or "none"
        raise ProviderNotFoundError(f"no {kind} provider named {name!r} (installed: {known})")
    ep = eps[name]
    label = f"{group(kind)}:{name}" + (f" (from '{distribution(ep)}')" if distribution(ep) else "")
    try:
        cls = ep.load()
    except Exception as exc:  # a broken plugin: report it as unavailable, with the cause
        raise ProviderUnavailableError(f"{label} failed to import: {type(exc).__name__}: {exc}") from exc
    base = BASE_CLASSES[kind]
    if not (isinstance(cls, type) and issubclass(cls, base)):
        raise ProviderNotFoundError(f"entry point {label} does not point to a {base.__name__} subclass")
    _check_api_version(cls, label)
    if getattr(cls, "name", None) != name:
        _warn_once(f"{label}: class {cls.__name__} says its name is {cls.name!r}; it is used as {name!r}.")
    return cls


def _check_api_version(cls: type[Provider], label: str) -> None:
    version = getattr(cls, "api_version", None)
    if version != PLUGIN_API_VERSION:
        written_for = f"plugin API {version}" if isinstance(version, int) else "no plugin API version"
        raise ProviderUnavailableError(
            f"{label} was written for {written_for}, but EraseDub {__version__} uses plugin API "
            f"{PLUGIN_API_VERSION}. Install a version of the plugin made for this EraseDub."
        )


@overload
def create(kind: Literal["eraser"], name: str, options: Mapping[str, Any] | None = None) -> TextEraser: ...
@overload
def create(kind: Literal["asr"], name: str, options: Mapping[str, Any] | None = None) -> Transcriber: ...
@overload
def create(kind: Literal["ocr"], name: str, options: Mapping[str, Any] | None = None) -> TextDetector: ...
@overload
def create(
    kind: Literal["translator"], name: str, options: Mapping[str, Any] | None = None
) -> Translator: ...
@overload
def create(
    kind: Literal["tts"], name: str, options: Mapping[str, Any] | None = None
) -> SpeechSynthesizer: ...
@overload
def create(
    kind: Literal["layout"], name: str, options: Mapping[str, Any] | None = None
) -> SubtitleLayout: ...
@overload
def create(kind: Literal["gpu"], name: str, options: Mapping[str, Any] | None = None) -> GpuBackend: ...
@overload
def create(kind: Kind, name: str, options: Mapping[str, Any] | None = None) -> Provider: ...
def create(kind: Kind, name: str, options: Mapping[str, Any] | None = None) -> Provider:
    """Instantiate the provider ``name`` of ``kind`` with provider-specific ``options``.

    The return type follows ``kind`` (``create("tts", ...)`` is a :class:`SpeechSynthesizer`).
    Invalid options raise :class:`~erasedub.errors.ConfigError`.
    """
    return load_class(kind, name)(options)


def create_eraser(spec: EraserSpec) -> TextEraser:
    """Create the eraser described by ``spec`` on this machine (used by local GPU backends)."""
    if spec.api_version != PLUGIN_API_VERSION:
        raise ProviderUnavailableError(
            f"eraser '{spec.name}' was requested with plugin API {spec.api_version}, but EraseDub "
            f"{__version__} uses plugin API {PLUGIN_API_VERSION}; run the same EraseDub on both sides."
        )
    return create("eraser", spec.name, spec.options)


def availability(kind: Kind, name: str) -> Availability:
    """Create the provider and run its ``check()``. The default availability checker of the planner.

    Unknown names raise :class:`ProviderNotFoundError`; anything else that goes wrong (import error, bad
    options, a ``check()`` that raises) is reported as unavailable, never raised.
    """
    try:
        cls = load_class(kind, name)
    except ProviderUnavailableError as exc:
        return Availability(False, str(exc))
    try:
        return cls().check()
    except Exception as exc:  # a broken third-party check() must not break planning or `doctor`
        return Availability(False, f"check failed: {type(exc).__name__}: {exc}")


@dataclass(frozen=True)
class ProviderStatus:
    kind: Kind
    name: str
    summary: str
    availability: Availability
    #: Installed distribution (pip package) that registered the provider; "erasedub" for built-ins.
    distribution: str | None = None


def status_all() -> list[ProviderStatus]:
    """Status of every registered provider. Never raises: broken plugins are reported in the rows."""
    rows: list[ProviderStatus] = []
    for kind in KINDS:
        for name, ep in sorted(_entry_points(kind).items()):
            dist = distribution(ep)
            try:
                provider = create(kind, name)
            except Exception as exc:  # a broken third-party plugin must not break `erasedub doctor`
                reason = f"failed to load: {exc}"
                rows.append(ProviderStatus(kind, name, "", Availability(False, reason), dist))
                continue
            try:
                status = provider.check()
            except Exception as exc:
                status = Availability(False, f"check failed: {type(exc).__name__}: {exc}")
            rows.append(ProviderStatus(kind, name, provider.summary, status, dist))
    return rows
