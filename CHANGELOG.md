# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the version is `0.x`, minor releases may contain breaking changes; they are always listed here.

## [Unreleased]

## [0.1.0] - 2026-09-28

First release.

### Added

- **One command for the whole job:** `erasedub run video.mp4 --to vi` erases burned-in text, transcribes the speech,
  translates it, dubs it with a new voice and adds new subtitles.
- **Two-step workflow:** `erasedub prepare` writes an editable `script.<lang>.srt` (plus `script.<lang>.json` and
  `regions.json`); `erasedub render` makes the video from the edited script.
- **Web UI** (Gradio): `erasedub webui`, extra `webui`.
- **Erasers** ([docs/erasers.md](docs/erasers.md), ADR 0014): `sttn` (default, NVIDIA GPU); `propainter` (opt-in,
  extra `propainter`, NVIDIA GPU, **non-commercial** S-Lab License 1.0); `lama` (opt-in, extra `lama`, CPU or Apple
  GPU; per-frame, may flicker and flatten repeating patterns); `none`. Model code and weights are downloaded at
  first use from their official sources.
- **Where erasing runs:** `local` (this machine's NVIDIA GPU) or `modal` (`--gpu modal`, your own Modal account).
  Without an NVIDIA GPU, erasing is skipped with a notice that suggests `lama`, and everything else runs.
- **Text detection:** RapidOCR (extra `ocr`). **Speech recognition:** WhisperX (extra `asr`); word timestamps
  opt-in (`asr.options.align = true`, off by default for licence reasons); speaker labels opt-in
  (`asr.options.diarize = true`, needs `HF_TOKEN` for the gated pyannote model).
- **Translation:** Google Translate web endpoint (default, no key); OpenAI or any OpenAI-compatible API, Gemini and
  Claude with your key (extra `llm`); Ollama locally (extra `ollama`). LLM translators use the glossary and nearby
  lines as context, and keep each line short enough for its time slot. Google Translate keeps glossary terms fixed.
- **Voices:** Edge-TTS (default, no key); ElevenLabs with your key (extra `elevenlabs`).
- **Subtitles and audio:** bottom-centre subtitle layout (ffmpeg with libass), background music, loudness
  normalization.
- **Commands:** `prepare`, `render`, `run`, `webui`, `doctor` (checks Python, ffmpeg, GPU, PyTorch CUDA support,
  providers and API-key variables), `plugins`, `version`; `--dry-run` shows the steps that would run and why.
- **Configuration** in `erasedub.toml`; API keys only from environment variables.
- **Plugin API version 1:** every step is a provider loaded from entry points; third-party packages can add their
  own ([docs/plugins.md](docs/plugins.md)). Optional `TextEraser.notice`, shown with every plan that uses the eraser.
- **Verified target languages:** Vietnamese, English, Chinese.
- **Install:** from source (`git clone`, then `uv sync --extra full` or `pip install -e ".[full]"`) with extras `asr`, `ocr`, `erase`, `propainter`, `lama`, `llm`, `ollama`,
  `elevenlabs`, `modal`, `webui` and `full`; Docker image `ghcr.io/sting11k/erasedub` (amd64); portable Windows
  bundle (x64 + NVIDIA). The Docker image and the Windows bundle ship a GPL build of ffmpeg.
  EraseDub is not published to PyPI; the release workflow builds the wheel and sdist and attaches them to the
  GitHub Release.
- **Docs:** README in English, Vietnamese and Chinese; install guides for Linux, macOS, Windows and Docker; CLI,
  configuration, script format, erasers, models and licences, GPU rental and troubleshooting.

[Unreleased]: https://github.com/sting11k/erasedub/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sting11k/erasedub/releases/tag/v0.1.0
