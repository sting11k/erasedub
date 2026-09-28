# 0013 — Docker image and Windows bundle are amd64-only for v0.1

- Status: Accepted
- Date: 2026-09-26

## Context

WhisperX 3.8 pins torch 2.8, which holds `torchcodec` at 0.7.0 — a version with no Linux arm64 wheel — and
PyPI's arm64 torch is CPU-only. An arm64 image could neither install cleanly nor erase on a GPU.

## Decision

Publish `linux/amd64` images and an x64 Windows bundle only. pip installs on other platforms remain possible
for the parts that work there.

## Alternatives considered

- **Multi-arch image with a CPU-only arm64 variant**: installs would still fail on torchcodec; not worth the
  CI time for v0.1.

## Consequences

Apple Silicon users run the Docker image under emulation or use a pip install. With pip, macOS can erase
locally only with the opt-in `lama` eraser (CPU or Apple GPU; ADR 0014); the image does not include it. Revisit when WhisperX allows a torch/torchcodec pair with
arm64 wheels.
