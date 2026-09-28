"""Model downloads (pinned URL + sha256 or size, atomic, cached) and safe checkpoint loading. No network."""

from __future__ import annotations

import hashlib
import io
import os
import pickle
from collections import OrderedDict
from pathlib import Path

import pytest

from erasedub.context import RunContext
from erasedub.errors import CancelledError, EraseDubError
from erasedub.providers import _fetch, _weights

DATA = b"model weights" * 1000
SHA = hashlib.sha256(DATA).hexdigest()


class FakeOpener:
    """Serves fixed bytes for any URL and records the URLs asked for."""

    def __init__(self, data: bytes = DATA) -> None:
        self.data = data
        self.urls: list[str] = []

    def __call__(self, url: str) -> io.BytesIO:
        self.urls.append(url)
        return io.BytesIO(self.data)


def _ctx(tmp_path: Path, **kwargs: object) -> RunContext:
    return RunContext(tmp_dir=tmp_path / "tmp", cache_dir=tmp_path / "cache", **kwargs)  # type: ignore[arg-type]


def test_remote_needs_a_hash_or_a_size() -> None:
    with pytest.raises(ValueError, match="sha256 or at least a size"):
        _fetch.Remote("a.pth", "https://example.org/a.pth")


@pytest.mark.parametrize("name", ["", "..", "a/b.pth", "a\\b.pth"])
def test_remote_name_is_a_plain_file_name(name: str) -> None:
    with pytest.raises(ValueError, match="plain file name"):
        _fetch.Remote(name, "https://example.org/x", size=1)


def test_fetch_downloads_checks_and_caches(tmp_path: Path) -> None:
    remote = _fetch.Remote("m.pth", "https://example.org/m.pth", sha256=SHA.upper())
    opener = FakeOpener()
    progress: list[float] = []
    ctx = _ctx(tmp_path, on_progress=lambda f, m: progress.append(f))
    path = _fetch.fetch(remote, tmp_path / "models", ctx=ctx, opener=opener)
    assert path.read_bytes() == DATA
    assert not (tmp_path / "models" / "m.pth.part").exists()
    assert progress == []  # no size known and no Content-Length: no fraction to report
    again = _fetch.fetch(remote, tmp_path / "models", ctx=ctx, opener=opener)
    assert again == path
    assert opener.urls == ["https://example.org/m.pth"]  # the second call used the cache


def test_fetch_reports_progress_when_the_size_is_known(tmp_path: Path) -> None:
    remote = _fetch.Remote("m.pth", "https://example.org/m.pth", sha256=SHA, size=len(DATA))
    progress: list[float] = []
    _fetch.fetch(
        remote, tmp_path, ctx=_ctx(tmp_path, on_progress=lambda f, m: progress.append(f)), opener=FakeOpener()
    )
    assert progress[-1] == 1.0


def test_fetch_rejects_a_wrong_hash_and_keeps_nothing(tmp_path: Path) -> None:
    remote = _fetch.Remote("m.pth", "https://example.org/m.pth", sha256="0" * 64)
    with pytest.raises(EraseDubError, match="does not match its pinned sha256"):
        _fetch.fetch(remote, tmp_path, ctx=_ctx(tmp_path), opener=FakeOpener())
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_fetch_rejects_a_wrong_size(tmp_path: Path) -> None:
    remote = _fetch.Remote("m.pth", "https://example.org/m.pth", size=len(DATA) + 1)
    with pytest.raises(EraseDubError, match="expected"):
        _fetch.fetch(remote, tmp_path, ctx=_ctx(tmp_path), opener=FakeOpener())
    assert list(tmp_path.iterdir()) == []


def test_size_pinned_file_stores_its_hash_on_first_download(tmp_path: Path) -> None:
    remote = _fetch.Remote("m.pth", "https://example.org/m.pth", size=len(DATA))
    path = _fetch.fetch(remote, tmp_path, ctx=_ctx(tmp_path), opener=FakeOpener())
    assert (tmp_path / "m.pth.sha256").read_text().strip() == SHA
    path.write_bytes(DATA[:-1] + b"!")  # the cached file changes afterwards
    with pytest.raises(EraseDubError, match="does not match its pinned sha256"):
        _fetch.fetch(remote, tmp_path, ctx=_ctx(tmp_path), opener=FakeOpener())


def test_size_pinned_redownload_must_match_the_stored_hash(tmp_path: Path) -> None:
    remote = _fetch.Remote("m.pth", "https://example.org/m.pth", size=len(DATA))
    _fetch.fetch(remote, tmp_path, ctx=_ctx(tmp_path), opener=FakeOpener()).unlink()
    other = DATA[:-1] + b"!"  # same size, different content upstream
    with pytest.raises(EraseDubError, match="does not match its pinned sha256"):
        _fetch.fetch(remote, tmp_path, ctx=_ctx(tmp_path), opener=FakeOpener(other))
    assert not (tmp_path / "m.pth").exists()


def test_a_file_copied_in_by_hand_is_checked_by_size_and_pinned(tmp_path: Path) -> None:
    remote = _fetch.Remote("m.pth", "https://example.org/m.pth", size=len(DATA))
    (tmp_path / "m.pth").write_bytes(DATA)
    opener = FakeOpener()
    _fetch.fetch(remote, tmp_path, ctx=_ctx(tmp_path), opener=opener)
    assert opener.urls == []
    assert (tmp_path / "m.pth.sha256").read_text().strip() == SHA
    (tmp_path / "m.pth.sha256").unlink()
    (tmp_path / "m.pth").write_bytes(b"short")
    with pytest.raises(EraseDubError, match="bytes, expected"):
        _fetch.fetch(remote, tmp_path, ctx=_ctx(tmp_path), opener=opener)


def test_cancelling_a_download_leaves_no_partial_file(tmp_path: Path) -> None:
    remote = _fetch.Remote("m.pth", "https://example.org/m.pth", sha256=SHA)
    with pytest.raises(CancelledError):
        _fetch.fetch(remote, tmp_path, ctx=_ctx(tmp_path, is_cancelled=lambda: True), opener=FakeOpener())
    assert list(tmp_path.iterdir()) == []


def test_network_errors_become_erasedub_errors(tmp_path: Path) -> None:
    def broken(url: str) -> io.BytesIO:
        raise OSError("connection reset")

    remote = _fetch.Remote("m.pth", "https://example.org/m.pth", sha256=SHA)
    with pytest.raises(EraseDubError, match=r"could not download m\.pth .*connection reset"):
        _fetch.fetch(remote, tmp_path, ctx=_ctx(tmp_path), opener=broken)


def test_default_opener_refuses_plain_http() -> None:
    with pytest.raises(EraseDubError, match="non-HTTPS"):
        _fetch._urlopen("http://example.org/m.pth")


def test_fetch_all_keeps_the_order(tmp_path: Path) -> None:
    remotes = [_fetch.Remote(n, f"https://example.org/{n}", sha256=SHA) for n in ("b.pth", "a.pth")]
    paths = _fetch.fetch_all(remotes, tmp_path, ctx=_ctx(tmp_path), opener=FakeOpener())
    assert [p.name for p in paths] == ["b.pth", "a.pth"]


# --- checkpoint loading ------------------------------------------------------------------------------------


class _Exploit:
    def __init__(self, target: Path) -> None:
        self.target = target

    def __reduce__(self) -> tuple[object, tuple[str]]:
        return (os.remove, (str(self.target),))


def test_restricted_unpickler_never_calls_foreign_globals(tmp_path: Path) -> None:
    canary = tmp_path / "canary"
    canary.write_text("still here")
    payload = pickle.dumps({"weights": OrderedDict(a=1), "config": _Exploit(canary)})
    loaded = _weights.RESTRICTED_PICKLE.load(io.BytesIO(payload))
    assert canary.exists()
    assert loaded["weights"] == OrderedDict(a=1)
    assert isinstance(loaded["config"], _weights._Placeholder)


def test_state_dict_picks_key_and_prefix_and_strips_data_parallel() -> None:
    checkpoint = {
        "state_dict": {"generator.module.conv.weight": 1, "generator.bn.bias": 2, "discriminator.x": 3}
    }
    assert _weights.state_dict(checkpoint, key="state_dict", prefix="generator.") == {
        "conv.weight": 1,
        "bn.bias": 2,
    }
    assert _weights.state_dict({"module.fc": 5}) == {"fc": 5}


def test_state_dict_errors() -> None:
    with pytest.raises(EraseDubError, match=r"no weights under 'generator\.'"):
        _weights.state_dict({"x": 1}, prefix="generator.")
    with pytest.raises(EraseDubError, match="does not contain a state dict"):
        _weights.state_dict({"netG": [1, 2]}, key="netG")
