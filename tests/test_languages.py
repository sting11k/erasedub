import pytest

from erasedub import languages


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("vi", "vi"),
        ("VI", "vi"),
        (" en ", "en"),
        ("en_us", "en-US"),
        ("ZH-tw", "zh-TW"),
        ("zh-hant", "zh-Hant"),
        ("zh-Hant-tw", "zh-Hant-TW"),
        ("pt-br", "pt-BR"),
        ("es-419", "es-419"),
        ("fil", "fil"),
        ("de-CH-1996", "de-CH-1996"),
    ],
)
def test_normalize_keeps_region_and_script(code: str, expected: str) -> None:
    assert languages.normalize(code) == expected


@pytest.mark.parametrize(
    "code", ["", "v", "vien", "v1", "zh--TW", "zh-TW!", "tiếng", "en-x-private", "sk-secret-value-123456789"]
)
def test_normalize_rejects_without_echoing(code: str) -> None:
    with pytest.raises(ValueError, match="not a language code") as excinfo:
        languages.normalize(code)
    if len(code) > 2:
        assert code not in str(excinfo.value)


def test_base_and_tiers() -> None:
    assert languages.base("zh-TW") == "zh"
    assert languages.is_verified_target("zh-TW")
    assert languages.is_verified_target("EN-gb")
    assert not languages.is_verified_target("ja")
