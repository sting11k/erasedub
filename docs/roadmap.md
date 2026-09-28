# Roadmap

## v0.1 (released)

- `erasedub prepare` / `render` / `run`, `webui`, `doctor`, `plugins`, `version`.
- Erasing, with three erasers ([ADR 0014](adr/0014-erasers-sttn-default-others-opt-in.md),
  [erasers.md](erasers.md)):
  - STTN (default) on a local NVIDIA GPU, or on Modal with the user's own account (`--gpu modal`);
  - ProPainter, opt-in (extra `propainter`), NVIDIA GPU, **non-commercial** licence;
  - LaMa, opt-in (extra `lama`) for machines without an NVIDIA GPU (CPU or Apple GPU): slower, prone to flicker,
    flattens repeating patterned backgrounds.

  No NVIDIA GPU and no `lama` chosen → erasing is skipped with a notice that suggests `lama`; everything else runs.
- Speech recognition: WhisperX (segment timestamps; word alignment opt-in with `asr.options.align = true`, off by
  default for licence reasons; speaker labels opt-in with `asr.options.diarize = true` and `HF_TOKEN`). Text
  detection: RapidOCR.
- Translation: Google Translate (free), OpenAI / Gemini / Claude with your key, Ollama locally. LLM translators use
  the glossary and nearby lines as context, and keep each line short enough for its time slot.
- Voices: Edge-TTS (free), ElevenLabs with your key.
- Standard bottom subtitles, background music, loudness normalization.
- Verified target languages: Vietnamese, English, Chinese.
- Install: from source (uv or pip; wheel and sdist attached to the GitHub release), Docker image
  `ghcr.io/sting11k/erasedub` (amd64), portable Windows bundle.

See the [changelog](../CHANGELOG.md) for the full list.

## After v0.1 (ideas, not promises)

- **Voice cloning and local voices on GPU:** CosyVoice (Apache-2.0). F5-TTS only as an opt-in, since its weights are
  non-commercial.
- **A voice per speaker:** use the speaker labels (opt-in in v0.1) to give each speaker their own voice.
- **Vocal separation:** keep the original music and effects while replacing only the voice.
- **Per-language verification of opt-in word alignment** (`[asr.options] align = true`): test each language and
  record its model licence.
- **Pre-fetched models in the Docker image**, for the models whose licence allows redistribution.
- **arm64** Docker image, once the PyTorch/torchcodec wheels allow it.
- **Demo set rendered by EraseDub** on the same two clips ([demo-assets.md](demo-assets.md)).
- **More verified languages**, as contributors check them.
- **More GPU backends** as plugins.

Want to help with one of these? Open a discussion or issue first so work is not duplicated
([CONTRIBUTING.md](../CONTRIBUTING.md)).
