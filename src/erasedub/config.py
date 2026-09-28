"""Configuration: defaults < ``erasedub.toml`` < command-line options.

API keys and tokens are never read from the config file — only from environment variables — so a config
file can be shared or committed safely. :func:`load` rejects files that look like they contain secrets.

Command-line options are merged into the file's data and validated together with it, so file values and
option values go through exactly the same checks. Relative paths (``general.workdir``, ``audio.music``, ...)
are relative to the current directory, not to the config file.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from erasedub import languages
from erasedub.errors import ConfigError
from erasedub.links import doc
from erasedub.models import TranslationStyle

CONFIG_NAME = "erasedub.toml"

# Key names that hold credentials: the last ``_``/``-`` separated part of the name is a credential word,
# optionally followed by ``_id``/``_ids`` (``api_key``, ``openai_api_keys``, ``modal_token_id``,
# ``client_secret``). Names that only mention one earlier (``api_key_env``, ``auth_url``, ``secret_name``)
# are fine, and so is a plural ``..._tokens`` (a count, as in ``max_input_tokens``). ``tokens`` alone is not.
_SECRET_NAME = re.compile(
    r"(^|[_-])("
    r"api[_-]?keys?|keys?|token|secrets?|passw(or)?ds?|pwd|auth|authorization|auth[_-]?headers?|bearer"
    r"|credentials?|cookies?|access[_-]?keys?|private[_-]?keys?|client[_-]?secrets?"
    r")([_-]?ids?)?$"
    r"|^tokens$",
    re.IGNORECASE,
)
# Option names that match the pattern above but are not credentials.
ALLOWED_NAMES: frozenset[str] = frozenset(
    {
        "max_tokens",
        "max_new_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "token_limit",
        "speaker_key",
        "sort_key",
        "voice_key",
        "cache_key",
    }
)
# Values that look like well-known credentials, wherever they appear in a string (a URL, a header, ...).
# Lengths keep ordinary values apart: the Edge voice "sk-SK-ViktoriaNeural" is not an OpenAI key.
_SECRET_VALUE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"sk-[A-Za-z0-9_-]{20,}"  # OpenAI, Anthropic (sk-ant-...), many OpenAI-compatible vendors
    r"|AIza[A-Za-z0-9_-]{30,}"  # Google API keys (Gemini)
    r"|hf_[A-Za-z0-9]{20,}"  # Hugging Face
    r"|a[ks]-[A-Za-z0-9]{20,}"  # Modal token id / secret
    r"|Bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r")"
)
# Free-form user data where any word is a legitimate key: glossary terms like "token" or "key".
_NOT_SCANNED: frozenset[tuple[str, ...]] = frozenset({("translate", "glossary")})


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class GeneralConfig(_Section):
    workdir: Path = Path("work")
    output_dir: Path | None = None
    source_language: str | None = None
    target_languages: list[str] = Field(default_factory=lambda: ["vi"])

    @field_validator("target_languages")
    @classmethod
    def _targets(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one target language is required")
        # De-duplicated in order: ["vi", "VI"] runs Vietnamese once.
        return list(dict.fromkeys(languages.normalize(v) for v in value))

    @field_validator("source_language")
    @classmethod
    def _source(cls, value: str | None) -> str | None:
        if value is None or value.strip().lower() in ("", "auto"):
            return None
        return languages.normalize(value)


class EraseConfig(_Section):
    #: ``auto`` erases when a GPU backend is usable and otherwise skips with a notice.
    enabled: Literal["auto", "on", "off"] = "auto"
    provider: str = Field(default="sttn", min_length=1)
    gpu: str = Field(default="local", min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class AsrConfig(_Section):
    provider: str = Field(default="whisperx", min_length=1)
    #: ``auto`` picks a large model on GPU and a small one on CPU.
    model: str = "auto"
    options: dict[str, Any] = Field(default_factory=dict)


class OcrConfig(_Section):
    provider: str = Field(default="rapidocr", min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class TranslateConfig(_Section):
    provider: str = Field(default="google", min_length=1)
    #: ``faithful`` keeps meaning close; ``natural`` lets LLM providers rewrite for fluency.
    style: TranslationStyle = "faithful"
    glossary: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class TtsConfig(_Section):
    enabled: bool = True
    provider: str = Field(default="edge", min_length=1)
    #: ``auto`` picks the provider's default voice for each target language.
    voice: str = "auto"
    options: dict[str, Any] = Field(default_factory=dict)


class SubtitleConfig(_Section):
    enabled: bool = True
    layout: str = Field(default="bottom", min_length=1)
    font: str | None = None
    font_size: int = Field(default=0, ge=0)  # 0 = scale with the shorter side of the video
    options: dict[str, Any] = Field(default_factory=dict)


class AudioConfig(_Section):
    #: What to do with the original soundtrack under the new voice.
    original: Literal["keep", "mute"] = "keep"
    original_volume: float = Field(default=0.25, ge=0, le=2)
    voice_volume: float = Field(default=1.0, ge=0, le=2)
    music: Path | None = None
    music_volume: float = Field(default=0.15, ge=0, le=2)
    loudnorm: bool = True


class Config(_Section):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    erase: EraseConfig = Field(default_factory=EraseConfig)
    asr: AsrConfig = Field(default_factory=AsrConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    translate: TranslateConfig = Field(default_factory=TranslateConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)
    subtitles: SubtitleConfig = Field(default_factory=SubtitleConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)


def _find_secrets(data: object, path: tuple[str, ...] = ()) -> list[str]:
    """Dotted paths of keys named like credentials and of values that look like one (never the values)."""
    found: list[str] = []
    if path in _NOT_SCANNED:
        return found
    dotted = ".".join(path)
    if isinstance(data, Mapping):
        for key, value in data.items():
            name = str(key)
            if name.lower() not in ALLOWED_NAMES and _SECRET_NAME.search(name):
                found.append(".".join((*path, name)))
            else:
                found += _find_secrets(value, (*path, name))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            found += _find_secrets(item, (*path[:-1], f"{path[-1]}[{i}]") if path else (f"[{i}]",))
    elif isinstance(data, str) and _SECRET_VALUE.search(data):
        found.append(dotted)
    return found


def env_flag(name: str) -> bool:
    """Whether the environment variable ``name`` is switched on: set to anything but empty, 0, false or no."""
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no")


def _merge(data: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """``data`` with each dotted ``section.field`` of ``overrides`` set; ``data`` itself is not changed."""
    merged = dict(data)
    for dotted, value in overrides.items():
        *sections, field = dotted.split(".")
        target = merged
        for name in sections:
            inner = target.get(name)
            target[name] = dict(inner) if isinstance(inner, Mapping) else {}
            target = target[name]
        target[field] = value
    return merged


def _describe(exc: ValidationError, source: str, labels: Mapping[str, str]) -> str:
    """Like :func:`erasedub.errors.describe_validation_error`, naming the option (``--to``) behind a value."""
    parts = []
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        label = next((lbl for key, lbl in labels.items() if loc == key or loc.startswith(key + ".")), None)
        parts.append(f"{label}: {err['msg']}" if label else f"{source}: {loc}: {err['msg']}")
    return "; ".join(parts)


def from_mapping(
    data: Mapping[str, Any],
    *,
    source: str = "<config>",
    overrides: Mapping[str, Any] | None = None,
    labels: Mapping[str, str] | None = None,
) -> Config:
    """Validate ``data`` (a parsed config file) with ``overrides`` applied on top.

    ``overrides`` maps dotted field names (``"general.target_languages"``) to values from the command line;
    ``labels`` maps the same names to the option that set them (``"--to"``), for error messages. Only
    ``data`` is scanned for secrets: option values are never written to a file.
    """
    secrets = _find_secrets(data)
    if secrets:
        raise ConfigError(
            f"{source}: {', '.join(secrets)} look like secrets (an API key, token or password). Put them "
            f"in environment variables instead ({doc('configuration.md')}); the config file must never hold "
            "them. If an option is only named like a secret, rename it (avoid key, token, secret, auth, "
            "password in option names)."
        )
    overrides = overrides or {}
    try:
        return Config.model_validate(_merge(data, overrides))
    except ValidationError as exc:
        described = {key: labels.get(key, f"option {key}") for key in overrides} if labels else {}
        raise ConfigError(_describe(exc, source, described)) from exc


def _read(path: Path) -> str:
    """The text of a config file; every way reading it can fail is a :class:`ConfigError`."""
    if path.is_dir():  # Windows raises PermissionError for a folder, so check first
        raise ConfigError(f"{path} is a folder; --config needs a file such as {path / CONFIG_NAME}")
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc.strerror or exc}") from exc
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ConfigError(
            f'{path} is saved as UTF-16 ("Unicode" in Notepad). Save it as UTF-8; TOML files must be UTF-8.'
        )
    try:
        return raw.decode("utf-8-sig")  # tolerate the byte order mark some Windows editors write
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"{path} is not valid UTF-8 text (bad byte at offset {exc.start}). Save it as UTF-8."
        ) from exc


def load(
    path: Path | None = None,
    *,
    cwd: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
    labels: Mapping[str, str] | None = None,
) -> Config:
    """Load ``path``, or ``./erasedub.toml`` if it exists, or the defaults; then apply ``overrides``.

    See :func:`from_mapping` for ``overrides`` and ``labels``.
    """
    if path is None:
        candidate = (cwd or Path.cwd()) / CONFIG_NAME
        if not candidate.is_file():
            return from_mapping({}, source="defaults", overrides=overrides, labels=labels)
        path = candidate
    try:
        data = tomllib.loads(_read(path))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    return from_mapping(data, source=str(path), overrides=overrides, labels=labels)
