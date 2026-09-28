from pathlib import Path

import pytest

from erasedub import config
from erasedub.errors import ConfigError


def test_defaults_without_file(tmp_path: Path) -> None:
    cfg = config.load(cwd=tmp_path)
    assert cfg.general.target_languages == ["vi"]
    assert cfg.translate.provider == "google"
    assert cfg.tts.provider == "edge"
    assert cfg.erase.provider == "sttn"


def test_loads_file_from_cwd_and_normalizes_languages(tmp_path: Path) -> None:
    (tmp_path / config.CONFIG_NAME).write_text(
        '[general]\ntarget_languages = ["EN-us", "zh_CN"]\nsource_language = "auto"\n'
        '[translate]\nprovider = "ollama"\n[translate.options]\nmax_tokens = 512\n',
        encoding="utf-8",
    )
    cfg = config.load(cwd=tmp_path)
    assert cfg.general.target_languages == ["en-US", "zh-CN"]
    assert cfg.general.source_language is None
    assert cfg.translate.options == {"max_tokens": 512}


@pytest.mark.parametrize(
    "snippet",
    ['[translate.options]\napi_key = "sk-x"\n', '[tts.options]\nkey = "x"\n', "[erase.options]\ntoken = 1\n"],
)
def test_rejects_secrets_in_file(tmp_path: Path, snippet: str) -> None:
    path = tmp_path / "c.toml"
    path.write_text(snippet, encoding="utf-8")
    with pytest.raises(ConfigError, match="environment"):
        config.load(path)


SECRET_NAMES = [
    "api_key",
    "apikey",
    "API_KEY",
    "api-key",
    "api_keys",
    "openai_api_key",
    "key",
    "keys",
    "token",
    "tokens",
    "token_id",
    "modal_token_id",
    "modal_token_secret",
    "secret",
    "secrets",
    "client_secret",
    "password",
    "passwd",
    "pwd",
    "auth",
    "auth_header",
    "authorization",
    "bearer",
    "credentials",
    "credential",
    "cookie",
    "cookies",
    "access_key",
    "access_key_id",
    "aws_access_key_id",
    "private_key",
    "auth_token",
    "hf_token",
    "access_token",
    "session_cookie",
    "auth_headers",
    "key_id",
    "token_ids",
]
NOT_SECRET_NAMES = [
    "max_tokens",
    "MAX_TOKENS",
    "max_new_tokens",
    "max_output_tokens",
    "max_completion_tokens",
    "token_limit",
    "speaker_key",
    "sort_key",
    "voice_key",
    "cache_key",
    "keyframe_interval",
    "author",
    "monkey",
    "tokenizer",
    "base_url",
    "model",
    # "...which env var or file holds the key", counts and other names that only mention a secret word
    "api_key_env",
    "api_key_file",
    "tokens_per_minute",
    "token_count",
    "max_input_tokens",
    "context_tokens",
    "key_frame_interval",
    "auth_url",
    "secret_name",
]


@pytest.mark.parametrize("name", SECRET_NAMES)
def test_secret_names_are_rejected_without_echoing_the_value(name: str) -> None:
    with pytest.raises(ConfigError, match="look like secrets") as excinfo:
        config.from_mapping({"translate": {"options": {name: "hunter2-value"}}})
    assert f"translate.options.{name}" in str(excinfo.value)
    assert "hunter2" not in str(excinfo.value)


@pytest.mark.parametrize("name", NOT_SECRET_NAMES)
def test_ordinary_option_names_are_accepted(name: str) -> None:
    cfg = config.from_mapping({"translate": {"options": {name: 1}}})
    assert cfg.translate.options == {name: 1}


FAKE_OPENAI = "sk-" + "a1B2" * 12
FAKE_ANTHROPIC = "sk-ant-api03-" + "x9Y8" * 10
FAKE_GOOGLE = "AIza" + "Sy" + "q7W3" * 9
FAKE_HF = "hf_" + "Zq4K" * 8
FAKE_MODAL = "ak-" + "m5N6" * 6


@pytest.mark.parametrize(
    "value",
    [
        FAKE_OPENAI,
        FAKE_ANTHROPIC,
        FAKE_GOOGLE,
        FAKE_HF,
        FAKE_MODAL,
        "Bearer abcdefgh12345678",
        f"https://llm.example.com/v1?key={FAKE_OPENAI}",
    ],
)
def test_secret_values_are_rejected_wherever_they_are(value: str) -> None:
    with pytest.raises(ConfigError, match=r"translate\.options\.base_url") as excinfo:
        config.from_mapping({"translate": {"options": {"base_url": value}}})
    assert value not in str(excinfo.value)


@pytest.mark.parametrize(
    "value", ["sk-SK-ViktoriaNeural", "vi-VN-HoaiMyNeural", "hf_small", "http://localhost:11434"]
)
def test_ordinary_values_are_accepted(value: str) -> None:
    cfg = config.from_mapping({"tts": {"options": {"voice_name": value}}})
    assert cfg.tts.options["voice_name"] == value


def test_lists_are_walked() -> None:
    with pytest.raises(ConfigError, match=r"translate\.options\.providers\[1\]\.api_key"):
        config.from_mapping({"translate": {"options": {"providers": [{"name": "a"}, {"api_key": "x"}]}}})
    with pytest.raises(ConfigError, match=r"translate\.options\.urls\[0\]"):
        config.from_mapping({"translate": {"options": {"urls": [f"https://x?key={FAKE_OPENAI}"]}}})


def test_only_the_translate_glossary_is_not_scanned() -> None:
    cfg = config.from_mapping({"translate": {"glossary": {"token": "mã thông báo", "API key": FAKE_OPENAI}}})
    assert cfg.translate.glossary["token"] == "mã thông báo"  # noqa: S105 — a glossary term, not a password
    with pytest.raises(ConfigError, match=r"tts\.options\.glossary\.api_key"):
        config.from_mapping({"tts": {"options": {"glossary": {"api_key": "x"}}}})


@pytest.mark.parametrize(
    "data",
    [
        {"tts": {"voice": ["sk-not-a-voice"]}},
        {"general": {"workdir": ["sk-not-a-path"]}},
        {"general": {"target_languages": ["sk-not-a-language"]}},
        {"general": {"source_language": "sk-not-a-language"}},
        {"erase": {"enabled": "sk-not-an-option"}},
    ],
)
def test_validation_errors_do_not_echo_values(data: dict[str, object]) -> None:
    with pytest.raises(ConfigError) as excinfo:
        config.from_mapping(data)
    assert "sk-" not in str(excinfo.value)


def test_languages_keep_region_and_script_and_are_deduplicated() -> None:
    cfg = config.from_mapping(
        {
            "general": {
                "target_languages": ["vi", "VI", "vi-vn", "zh_tw", "zh-hant", "pt-BR"],
                "source_language": "AUTO",
            }
        }
    )
    assert cfg.general.target_languages == ["vi", "vi-VN", "zh-TW", "zh-Hant", "pt-BR"]
    assert cfg.general.source_language is None
    assert config.from_mapping({"general": {"source_language": "Auto"}}).general.source_language is None
    assert config.from_mapping({"general": {"source_language": "ZH-cn"}}).general.source_language == "zh-CN"


@pytest.mark.parametrize(
    "data",
    [
        {"erase": {"provider": ""}},
        {"erase": {"gpu": ""}},
        {"asr": {"provider": ""}},
        {"ocr": {"provider": ""}},
        {"translate": {"provider": ""}},
        {"tts": {"provider": ""}},
        {"subtitles": {"layout": ""}},
    ],
)
def test_provider_names_must_not_be_empty(data: dict[str, object]) -> None:
    with pytest.raises(ConfigError, match="at least 1 character"):
        config.from_mapping(data)


@pytest.mark.parametrize(
    "data",
    [{"unknown": {}}, {"erase": {"enabled": "maybe"}}, {"general": {"target_languages": []}}],
)
def test_rejects_invalid_values(data: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        config.from_mapping(data)


def test_bad_toml_and_missing_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[general\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        config.load(bad)
    with pytest.raises(ConfigError, match="not found"):
        config.load(tmp_path / "missing.toml")


def test_shipped_example_config_is_valid() -> None:
    example = Path(__file__).resolve().parents[1] / "examples" / "erasedub.toml"
    cfg = config.load(example)
    assert cfg.general.target_languages == ["vi"]


def test_a_folder_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="is a folder"):
        config.load(tmp_path)


def test_utf8_with_byte_order_mark_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "bom.toml"
    path.write_bytes(b"\xef\xbb\xbf" + b'[general]\ntarget_languages = ["en"]\n')
    assert config.load(path).general.target_languages == ["en"]


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-be"])
def test_utf16_asks_for_utf8(tmp_path: Path, encoding: str) -> None:
    path = tmp_path / "unicode.toml"
    text = '[general]\ntarget_languages = ["en"]\n'
    path.write_bytes(text.encode(encoding) if encoding == "utf-16" else b"\xfe\xff" + text.encode(encoding))
    with pytest.raises(ConfigError, match=r"UTF-16.*Save it as UTF-8"):
        config.load(path)


def test_invalid_utf8_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "latin1.toml"
    path.write_bytes(b'[general]\nworkdir = "bad \xe9 byte"\n')
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        config.load(path)


def test_unreadable_file_is_a_config_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "locked.toml"
    path.write_text("", encoding="utf-8")

    def denied(self: Path) -> bytes:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", denied)
    with pytest.raises(ConfigError, match=r"cannot read config file .*Permission denied"):
        config.load(path)


def test_overrides_win_over_the_file_and_leave_other_values(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('[general]\ntarget_languages = ["en"]\nworkdir = "w"\n[tts]\nprovider = "x"\n', "utf-8")
    cfg = config.load(path, overrides={"general.target_languages": ["vi", "VI"], "tts.enabled": False})
    assert cfg.general.target_languages == ["vi"]
    assert cfg.general.workdir == Path("w")
    assert (cfg.tts.enabled, cfg.tts.provider) == (False, "x")


def test_overrides_apply_to_the_defaults_too(tmp_path: Path) -> None:
    cfg = config.load(cwd=tmp_path, overrides={"erase.gpu": "modal"})
    assert cfg.erase.gpu == "modal"


def test_override_errors_name_the_option_and_file_errors_name_the_file(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('[audio]\noriginal = "loud"\n', encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        config.load(
            path,
            overrides={"general.target_languages": ["12"]},
            labels={"general.target_languages": "--to"},
        )
    message = str(info.value)
    assert "--to: Value error, not a language code" in message
    assert f"{path}: audio.original:" in message
    assert f"{path}: general" not in message


def test_relative_paths_stay_relative_to_the_current_directory(tmp_path: Path) -> None:
    folder = tmp_path / "configs"
    folder.mkdir()
    path = folder / "c.toml"
    path.write_text('[general]\nworkdir = "work"\n[audio]\nmusic = "bed.mp3"\n', encoding="utf-8")
    cfg = config.load(path)
    assert (cfg.general.workdir, cfg.audio.music) == (Path("work"), Path("bed.mp3"))


@pytest.mark.parametrize(
    ("value", "on"),
    [(None, False), ("", False), ("0", False), ("false", False), (" No ", False), ("1", True), ("yes", True)],
)
def test_env_flag(monkeypatch: pytest.MonkeyPatch, value: str | None, on: bool) -> None:
    if value is None:
        monkeypatch.delenv("ERASEDUB_TEST_FLAG", raising=False)
    else:
        monkeypatch.setenv("ERASEDUB_TEST_FLAG", value)
    assert config.env_flag("ERASEDUB_TEST_FLAG") is on
