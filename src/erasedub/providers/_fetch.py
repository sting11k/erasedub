"""Download model files and upstream code once, check them, and keep them in the cache.

Every file an eraser fetches is described by a :class:`Remote`: a URL pinned to a fixed commit or release,
and the file's sha256 (or, where the upstream publishes no checksum, its exact size). Files land in
``<cache>/<group>/`` through a ``.part`` file that is renamed into place only after the check passed, so an
interrupted download never leaves a file that looks complete.

Upstreams that publish no checksum (the STTN weights on Google Drive, the ProPainter release assets) are
pinned by size, and the sha256 computed on the first download is stored next to the file
(``<name>.sha256``). Later runs check the file against that stored hash, so a file that changes in the cache
is an error, never a silent re-download.
"""

from __future__ import annotations

import hashlib
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from erasedub.context import RunContext
from erasedub.errors import EraseDubError

USER_AGENT: Final = "erasedub-model-fetch"
_CHUNK: Final = 1 << 20

#: Opens a URL and returns a readable binary response (injected in tests).
Opener = Callable[[str], IO[bytes]]


@dataclass(frozen=True)
class Remote:
    """One pinned file: where it comes from and how to recognise it.

    ``sha256`` is the published (or pinned) hash. When the upstream publishes none, leave it ``None`` and
    set ``size``: the first download is checked by size and its hash is stored for later runs.
    """

    name: str
    url: str
    sha256: str | None = None
    size: int | None = None

    def __post_init__(self) -> None:
        if self.sha256 is None and self.size is None:
            raise ValueError(f"{self.name}: pin a sha256 or at least a size")
        if "/" in self.name or "\\" in self.name or self.name in {"", ".", ".."}:
            raise ValueError(f"{self.name!r}: the name must be a plain file name")


def _urlopen(url: str) -> IO[bytes]:
    if not url.startswith("https://"):
        raise EraseDubError(f"refusing to download over a non-HTTPS URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 — https only
    response: IO[bytes] = urllib.request.urlopen(request, timeout=60)  # noqa: S310 — https only
    return response


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pin_file(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def _expected_hash(remote: Remote, path: Path) -> str | None:
    if remote.sha256:
        return remote.sha256.lower()
    pin = _pin_file(path)
    return pin.read_text(encoding="ascii").strip().lower() if pin.exists() else None


def _check_cached(remote: Remote, path: Path) -> None:
    expected = _expected_hash(remote, path)
    if expected is None:
        # Size-pinned file whose hash was never stored (for example copied in by hand): store it now.
        if remote.size is not None and path.stat().st_size != remote.size:
            raise EraseDubError(
                f"{path} has {path.stat().st_size} bytes, expected {remote.size}. Delete it to download it "
                "again."
            )
        _pin_file(path).write_text(sha256_of(path) + "\n", encoding="ascii")
        return
    actual = sha256_of(path)
    if actual != expected:
        raise EraseDubError(
            f"{path} does not match its pinned sha256 (expected {expected}, got {actual}). The file changed "
            "after it was downloaded; delete it to download it again."
        )


def fetch(remote: Remote, directory: Path, *, ctx: RunContext, opener: Opener = _urlopen) -> Path:
    """Return the path of ``remote`` in ``directory``, downloading and checking it first if needed.

    A cached file is re-checked against its hash on every call. A download is streamed to
    ``<name>.part``, checked (sha256, or size for size-pinned files), then renamed into place.
    Progress goes to ``ctx.progress``; cancellation is checked between chunks.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / remote.name
    if path.exists():
        _check_cached(remote, path)
        return path

    part = path.with_name(path.name + ".part")
    ctx.logger.info("downloading %s from %s", remote.name, remote.url)
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(remote.url) as response, part.open("wb") as out:
            total = remote.size or _content_length(response)
            for chunk in iter(lambda: response.read(_CHUNK), b""):
                ctx.raise_if_cancelled()
                out.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if total:
                    ctx.progress(received / total, f"downloading {remote.name}")
        actual = digest.hexdigest()
        if remote.size is not None and received != remote.size:
            raise EraseDubError(
                f"download of {remote.name} from {remote.url} has {received} bytes, expected {remote.size}; "
                "the partial file was deleted"
            )
        expected = _expected_hash(remote, path)
        if expected is not None and actual != expected:
            raise EraseDubError(
                f"download of {remote.name} from {remote.url} does not match its pinned sha256 "
                f"(expected {expected}, got {actual}); the file was deleted"
            )
        part.replace(path)
        if expected is None:
            _pin_file(path).write_text(actual + "\n", encoding="ascii")
    except OSError as exc:
        raise EraseDubError(f"could not download {remote.name} from {remote.url}: {exc}") from exc
    finally:
        part.unlink(missing_ok=True)
    return path


def fetch_all(
    remotes: Iterable[Remote], directory: Path, *, ctx: RunContext, opener: Opener = _urlopen
) -> list[Path]:
    return [fetch(remote, directory, ctx=ctx, opener=opener) for remote in remotes]


def _content_length(response: IO[bytes]) -> int | None:
    headers = getattr(response, "headers", None)
    value = headers.get("Content-Length") if headers is not None else None
    try:
        return int(value) if value else None
    except ValueError:
        return None
