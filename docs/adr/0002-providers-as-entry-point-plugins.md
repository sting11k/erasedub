# 0002 — Every pipeline step is a provider plugin discovered through entry points

- Status: Accepted
- Date: 2026-09-26

## Context

Each step has several reasonable implementations with very different trade-offs: free vs. paid key,
local GPU vs. CPU vs. cloud, permissive vs. non-commercial licences. Users want to swap them; contributors
want to add new ones; heavy dependencies (PyTorch, WhisperX, Gradio) must not be required for everything.

## Decision

- The engine defines one abstract base class per step in `erasedub.providers.base`: `TextEraser`,
  `Transcriber`, `TextDetector`, `Translator`, `SpeechSynthesizer`, `SubtitleLayout`, `GpuBackend`.
- Implementations are registered as Python entry points in the group `erasedub.<kind>`
  (`erasedub.eraser`, `erasedub.asr`, `erasedub.ocr`, `erasedub.translator`, `erasedub.tts`,
  `erasedub.layout`, `erasedub.gpu`). Built-in providers use the same mechanism as third-party packages.
- Providers import heavy dependencies lazily, take secrets from environment variables (or the service's own
  credential file, e.g. Modal's `~/.modal.toml`) and never from `erasedub.toml` (ADR 0012), and implement a
  cheap, offline `check()` that explains what is missing.
- Provider options are a typed model read from the step's config section, e.g. `[asr.options]` or
  `[tts.options]`.
- Plugins import only from `erasedub.plugin` and declare the `api_version` they were written for; the
  registry refuses a plugin whose `api_version` is missing or differs.
- The engine (audio extraction, mixing, muxing, planning) is shared by every combination of providers.

## Alternatives considered

- **A hard-coded `if provider == ...` switch**: simplest, but every new provider needs a change to the
  core and pulls its dependencies into the core package.
- **pluggy hooks**: more flexible than needed; entry points plus base classes are enough and familiar.

## Consequences

- A provider can be published as a separate package (`pip install erasedub-foo`) without changes here.
- The base classes are a public API: changing a signature is a breaking change and needs a deprecation
  period once v0.1 is released.
