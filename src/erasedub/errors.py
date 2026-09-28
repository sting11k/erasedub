"""Exception hierarchy. Each error maps to a stable CLI exit code."""

from __future__ import annotations

from pydantic import ValidationError


class EraseDubError(Exception):
    """Base class for all expected, user-facing errors."""

    exit_code = 1


class ConfigError(EraseDubError):
    """The configuration file or command-line options are invalid."""

    exit_code = 2


class ScriptFormatError(EraseDubError):
    """A script file (SRT/JSON) cannot be parsed."""

    exit_code = 2


class ProviderNotFoundError(EraseDubError):
    """No provider with the requested name is registered."""

    exit_code = 2


class ProviderUnavailableError(EraseDubError):
    """A provider exists but its dependencies, hardware or keys are missing."""

    exit_code = 3


class CancelledError(EraseDubError):
    """The user cancelled the run (Ctrl+C, or Cancel in the web UI).

    Providers raise it through ``RunContext.raise_if_cancelled()``. Exit code 130 is the shell convention
    for a run stopped by Ctrl+C. Not related to ``asyncio.CancelledError``.
    """

    exit_code = 130


def describe_validation_error(exc: ValidationError) -> str:
    """Summarize a pydantic error as ``"loc: message; ..."`` without the offending input values.

    pydantic's own ``str(exc)`` repeats each input value; for a config file that may be an API key pasted
    into the wrong field, so it must never reach the terminal (and from there, a GitHub issue).
    """
    parts = []
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)
