import pytest

from erasedub import hardware, links, media
from erasedub.errors import ProviderUnavailableError
from erasedub.hardware import FfmpegInfo


def test_doc_links_are_absolute() -> None:
    assert links.doc("gpu-rental.md") == "https://github.com/sting11k/erasedub/blob/main/docs/gpu-rental.md"
    assert links.doc("/install/windows.md") == links.DOCS_URL + "install/windows.md"


def test_user_messages_use_absolute_links(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware, "detect_ffmpeg", lambda: FfmpegInfo(None, None, False, False, False))
    pattern = r"https://github\.com/sting11k/erasedub/.*/docs/troubleshooting\.md"
    with pytest.raises(ProviderUnavailableError, match=pattern):
        media.find_tools()


def test_install_hints_are_for_a_source_checkout() -> None:
    # EraseDub is installed from its git folder, not from PyPI: no hint may say `pip install "erasedub[...]"`.
    assert links.missing_extra("ocr") == (
        "missing extra 'ocr': run `uv sync --extra ocr` (or `pip install -e \".[ocr]\"`) "
        "in the erasedub folder"
    )
