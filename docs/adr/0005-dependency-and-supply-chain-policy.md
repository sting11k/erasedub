# 0005 — Dependency and supply-chain policy

- Status: Accepted
- Date: 2026-09-26

## Context

A video tool that downloads models and runs third-party code is an attractive supply-chain target, and its
users often paste commands from READMEs.

## Decision

- Python packages come from pypi.org, with **one named exception**: CUDA builds of PyTorch for Windows come
  from the official PyTorch index (download.pytorch.org), pinned by version and sha256 and matching the torch
  version that `uv.lock` resolves for WhisperX (see `packaging/windows/`). Reason: PyPI's Windows PyTorch
  wheels are CPU-only.
- `uv.lock` is committed and CI checks it is up to date.
- The core install is light: CLI plus the no-key defaults (see ADR 0011). Everything heavy or key-based is an
  extra (`asr`, `ocr`, `erase`, `llm`, `ollama`, `elevenlabs`, `modal`, `webui`); `full` = everything that
  needs no API key.
- Model weights are downloaded at runtime (or at Docker build time) from the publisher's official location,
  pinned to a revision and checked against a hash where the publisher offers one.
- GitHub Actions and pre-commit hooks are pinned to full commit SHAs; Dependabot proposes updates.
- Native binaries (ffmpeg) are downloaded from their official release with a pinned checksum, never
  committed (see ADR 0010).
- Never `curl | bash` in docs or scripts.

## Alternatives considered

- **PyPI only, and tell Windows users to fix torch themselves**: the most common failure in comparable tools.
- **A third-party mirror of CUDA wheels on PyPI**: no official one exists.

## Consequences

- `erasedub doctor` flags a CPU-only torch next to an NVIDIA GPU.
- Updating torch means updating the Windows pin file together with `uv.lock`.
