# 0011 — Free web services are the no-key defaults for translation and voice

- Status: Accepted
- Date: 2026-09-26

## Context

A first run should work without accounts or keys. The only zero-setup options with acceptable quality are
Google Translate's free web endpoint (through `deep-translator`) and Microsoft Edge's online read-aloud voices
(through `edge-tts`, LGPL-3.0). Neither is an official API.

## Decision

- Use them as defaults, labelled "free, unofficial" in `erasedub plugins` and explained in
  `docs/models-and-licenses.md`; their terms of use are the user's responsibility.
- Keep them behind the `Translator` / `SpeechSynthesizer` interfaces, with key-based (OpenAI, Gemini, Claude,
  ElevenLabs) and local (Ollama; a local TTS is on the roadmap) alternatives one config line away.

## Alternatives considered

- **Require an API key from the start**: reliable, but most first-time users would stop at setup.
- **Local-only defaults**: needs a GPU and large downloads for acceptable quality.

## Consequences

`deep-translator` has had no release since June 2023; if it breaks, it is replaced behind the interface
without touching the engine.
