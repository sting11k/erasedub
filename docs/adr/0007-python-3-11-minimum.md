# 0007 — Python 3.11 is the minimum

- Status: Accepted
- Date: 2026-09-26

## Context

Python 3.10 reaches end of life in October 2026, before EraseDub's first release. Current releases of
dependencies (for example onnxruntime) already require 3.11. WhisperX does not support 3.14 yet.

## Decision

Support Python 3.11–3.13 fully; run the core test suite on 3.14 as a non-blocking CI job. The ASR provider
reports clearly when it cannot run on the current Python version. Docker images use 3.12.

## Alternatives considered

- **Keep 3.10 until its end of life**: one more month of support at the cost of a second onnxruntime in the
  lock and a `tomli` fallback.

## Consequences

Standard-library `tomllib` is always available; no `tomli` fallback.
