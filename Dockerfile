# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
#
# EraseDub container image: Linux amd64 only.
#
#   docker build -t erasedub .                                  # on Apple Silicon: add --platform linux/amd64
#   docker run --rm --gpus all --user "$(id -u):$(id -g)" -v "$PWD:/data" erasedub doctor
#
# Why no arm64: pyannote-audio (pulled in by whisperx) requires torchcodec, which has no linux/aarch64
# wheel, and PyPI torch for aarch64 is CPU-only anyway. The build fails fast on other platforms.
#
# GPU: PyPI torch wheels for Linux x86_64 bundle the CUDA runtime, so no CUDA base image is needed.
# The host only needs the NVIDIA driver + NVIDIA Container Toolkit (see docs/install/docker.md).
#
# Licence note: this image contains a GPL build of FFmpeg (BtbN/FFmpeg-Builds "gpl" variant, needed
# for libx264). Its licence, docs and exact source revisions are in /usr/share/doc/ffmpeg.
# EraseDub itself is Apache-2.0.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------------------------------
# ffmpeg: download the pinned static BtbN build and verify its sha256 before extracting.
# Python's stdlib does the download/verify/extract, so this stage needs no apt packages.
# ---------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS ffmpeg
ARG TARGETARCH
ARG FFMPEG_RELEASE=autobuild-2026-08-31-13-27
# Commit of BtbN/FFmpeg-Builds that the release tag points to (build scripts + library versions).
ARG FFMPEG_BUILDS_COMMIT=8267213e26c1031621e6e1210fe3aa4867214f6a
# Upstream FFmpeg commit the binaries were built from ("g1a748fe2cd" in the archive name).
ARG FFMPEG_COMMIT=1a748fe2cd43e3ead22fafb1b5b7d77f153898a8
ARG FFMPEG_ARCHIVE=ffmpeg-n8.1.2-50-g1a748fe2cd-linux64-gpl-8.1
ARG FFMPEG_SHA256=c733b4b2951e5957e15505f788b2c65a7a41b6da4b289e295852cc38079b4d2b
RUN python3 <<'EOF'
import hashlib
import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

arch = os.environ.get("TARGETARCH", "amd64")
if arch != "amd64":
    sys.exit(
        f"EraseDub images are linux/amd64 only (got TARGETARCH={arch!r}). pyannote-audio needs "
        "torchcodec, which has no linux/aarch64 wheel. On Apple Silicon build with "
        "--platform linux/amd64 (emulated, slow, no GPU)."
    )
name = os.environ["FFMPEG_ARCHIVE"]
expected = os.environ["FFMPEG_SHA256"]
release = os.environ["FFMPEG_RELEASE"]
url = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{release}/{name}.tar.xz"
archive = Path("/tmp/ffmpeg.tar.xz")
print(f"downloading {url}", flush=True)
with urllib.request.urlopen(url, timeout=120) as resp, archive.open("wb") as out:
    shutil.copyfileobj(resp, out)

digest = hashlib.sha256()
with archive.open("rb") as fh:
    for chunk in iter(lambda: fh.read(1 << 20), b""):
        digest.update(chunk)
if digest.hexdigest() != expected:
    sys.exit(f"sha256 mismatch for {url}: got {digest.hexdigest()}, expected {expected}")

with tarfile.open(archive) as tar:
    tar.extractall("/tmp", filter="data")
src = Path("/tmp") / name
out = Path("/opt/ffmpeg")
(out / "bin").mkdir(parents=True)
for tool in ("ffmpeg", "ffprobe"):
    shutil.copy2(src / "bin" / tool, out / "bin" / tool)
doc = out / "doc"
shutil.copytree(src / "doc", doc)
shutil.copy2(src / "LICENSE.txt", doc / "LICENSE.txt")
(doc / "SOURCE.txt").write_text(
    "ffmpeg and ffprobe in this image are the static 'gpl' build from BtbN/FFmpeg-Builds,\n"
    "licensed under the GNU GPL v3 (see LICENSE.txt).\n\n"
    f"  Archive:        {url}\n"
    f"  sha256:         {expected}\n"
    f"  FFmpeg source:  https://github.com/FFmpeg/FFmpeg/commit/{os.environ['FFMPEG_COMMIT']}\n"
    "                  (official repository: https://git.ffmpeg.org/ffmpeg.git)\n"
    "  Build scripts and bundled library versions (x264, libass, ...):\n"
    "                  https://github.com/BtbN/FFmpeg-Builds/tree/"
    f"{os.environ['FFMPEG_BUILDS_COMMIT']}\n\n"
    "EraseDub runs ffmpeg as a separate program; EraseDub itself is Apache-2.0.\n",
    encoding="utf-8",
)
archive.unlink()
shutil.rmtree(src)
EOF

# ---------------------------------------------------------------------------------------------
# builder: install the locked dependency set + EraseDub into /opt/venv with uv.
# `uv sync` cannot hash-check build backends, so this uses `uv export` + `uv pip install
# --require-hashes` with hash-pinned build constraints (packaging/build-constraints.txt).
# ---------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /src
# Dependencies first (cached layer as long as the lock file does not change).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=packaging/build-constraints.txt,target=build-constraints.txt \
    uv venv --python /usr/local/bin/python3 /opt/venv \
    && uv export --locked --no-dev --extra full --no-emit-project --no-header \
        --format requirements-txt --output-file /tmp/requirements.txt \
    && uv pip install --python /opt/venv/bin/python --require-hashes \
        --build-constraints build-constraints.txt -r /tmp/requirements.txt
COPY . /src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --build-constraints packaging/build-constraints.txt --out-dir /tmp/wheel \
    && uv pip install --python /opt/venv/bin/python --no-deps /tmp/wheel/erasedub-*.whl

# ---------------------------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="EraseDub" \
      org.opencontainers.image.description="Remove burned-in subtitles, translate and dub videos locally." \
      org.opencontainers.image.source="https://github.com/EraseDub/erasedub" \
      org.opencontainers.image.url="https://github.com/EraseDub/erasedub" \
      org.opencontainers.image.documentation="https://github.com/EraseDub/erasedub/blob/main/docs/install/docker.md" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="The EraseDub Authors"

# Fonts for burned-in subtitles: Noto covers Latin incl. Vietnamese; Noto CJK covers Chinese/Japanese/Korean.
# fontconfig provides /etc/fonts, which the static ffmpeg/libass build reads.
# libgl1, libglib2.0-0 and libxcb1: shared libraries that the opencv-python wheel (cv2) loads.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends fontconfig fonts-noto-core fonts-noto-cjk \
       libgl1 libglib2.0-0 libxcb1 \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

COPY --from=ffmpeg /opt/ffmpeg/bin/ffmpeg /opt/ffmpeg/bin/ffprobe /usr/local/bin/
COPY --from=ffmpeg /opt/ffmpeg/doc /usr/share/doc/ffmpeg
COPY --from=builder /opt/venv /opt/venv

# Default user uid 10001. HOME and the model cache exist in the image so that a new named volume
# mounted on /home/erasedub/.cache inherits them. They are world-writable (sticky bit) so that
# `docker run --user "$(id -u):$(id -g)"` also works. /data is where the user's folder is mounted.
RUN useradd --create-home --home-dir /home/erasedub --uid 10001 --user-group erasedub \
    && mkdir -p /home/erasedub/.cache /data \
    && chown -R 10001:10001 /home/erasedub /data \
    && chmod 1777 /home/erasedub /home/erasedub/.cache /data

ENV PATH=/opt/venv/bin:$PATH \
    HOME=/home/erasedub \
    USER=erasedub \
    LOGNAME=erasedub \
    ERASEDUB_IN_CONTAINER=1 \
    XDG_CACHE_HOME=/home/erasedub/.cache \
    HF_HOME=/home/erasedub/.cache/huggingface \
    TORCH_HOME=/home/erasedub/.cache/torch \
    NLTK_DATA=/home/erasedub/.cache/nltk_data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,video

USER 10001:10001
# Mount your folder here; EraseDub writes work/<video-stem>/ and outputs relative to it.
WORKDIR /data

# Models are not baked into the image: they download on first use into /home/erasedub/.cache
# (mount a volume there to keep them).

# Gradio WebUI (`erasedub webui --host 0.0.0.0`).
EXPOSE 7860
HEALTHCHECK NONE
ENTRYPOINT ["erasedub"]
CMD ["--help"]
