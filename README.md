<div align="center">

# EraseDub

**Remove burned-in subtitles, translate, and dub a video — in one tool, on your own machine.**

**English** | [简体中文](https://github.com/sting11k/erasedub/blob/main/README.zh-CN.md) | [Tiếng Việt](https://github.com/sting11k/erasedub/blob/main/README.vi.md)

[Documentation](https://github.com/sting11k/erasedub/blob/main/docs/index.md) ·
[Install](https://github.com/sting11k/erasedub/blob/main/docs/index.md#install) ·
[Roadmap](https://github.com/sting11k/erasedub/blob/main/docs/roadmap.md)

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/sting11k/erasedub/blob/main/LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

</div>

<p align="center">
  <img src="https://github.com/sting11k/erasedub/raw/main/docs/assets/demo-before-after.webp" alt="Before and after: burned-in Chinese subtitles removed, Vietnamese subtitles added" width="720">
</p>
<p align="center"><sub>Before / after: burned-in Chinese subtitles removed, Vietnamese subtitles added. Demo made outside EraseDub. <em>Tears of Steel</em> © Blender Foundation | mango.blender.org</sub></p>

EraseDub is an open-source AI video translation, dubbing and video localization tool. It removes hardcoded
(burned-in, hardsub) subtitles with video inpainting, turns the speech into text with Whisper (speech-to-text),
translates the subtitles with Google Translate or an LLM, and dubs the video with text-to-speech (TTS) and new
subtitles.

## How it works

```text
video ──> erase burned-in text ──> transcribe ──> translate ──> dub ──> add subtitles ──> video.<lang>.mp4
```

Run it in one go (`erasedub run`), or in two steps: `prepare` writes an editable subtitle file, you fix the
translation in any subtitle editor, then `render` makes the video.

## Demo

Full before / after comparisons, side by side: the original with burned-in Chinese subtitles on the left, the same
frames with the text erased on the right (no new subtitles, so you can judge the erasing). The demo was made
outside EraseDub. The comparisons are silent; the full videos with voice and subtitles (Vietnamese, English,
Chinese) are attached to the v0.1 release.

<p align="center">
  <img src="https://github.com/sting11k/erasedub/raw/main/docs/assets/compare-horizontal.webp" alt="Full before and after comparison of a Tears of Steel scene: burned-in Chinese subtitles on the left, the same frames with the text erased on the right" width="960">
</p>
<p align="center"><sub>A scene from <em>Tears of Steel</em> (19 s) with Chinese subtitles we burned in. <em>Tears of Steel</em> © Blender Foundation | mango.blender.org</sub></p>

<p align="center">
  <img src="https://github.com/sting11k/erasedub/raw/main/docs/assets/compare-vertical.webp" alt="Full before and after comparison of a vertical product video: real burned-in Chinese subtitles on the left, the same frames with the text erased on the right" width="720">
</p>
<p align="center"><sub>A vertical product video (15 s) with real burned-in Chinese subtitles.</sub></p>

## Why

To translate a video that has subtitles burned into the picture, you need three things: remove the old text,
translate the speech, and add a new voice and subtitles. Open-source tools do parts of this well:

- [pyvideotrans](https://github.com/jianchang512/pyvideotrans) and [VideoLingo](https://github.com/Huanshere/VideoLingo)
  transcribe, translate, and dub, but they do **not** remove burned-in subtitles, so the old text stays on
  screen under your new subtitles.
- [video-subtitle-remover](https://github.com/YaoFANGUK/video-subtitle-remover) removes burned-in subtitles,
  but does **not** translate or dub.

EraseDub does the whole job in one run, locally, with free defaults that need no API keys.

## Features

The tools behind each step are in [Providers](#providers).

- **One tool for the whole job:** erase, transcribe, translate, dub and subtitle in a single run.
- **Free defaults, no API keys:** bring your own key only if you want a paid translator or voice.
- **No GPU? Still useful:** erasing is skipped with a clear notice and everything else runs; or erase on
  [Modal](https://modal.com) with your own account (`--gpu modal`), or opt into the CPU eraser `lama` (slower, prone to
  flicker, flattens repeating patterned backgrounds).
- **Two-step workflow:** `prepare` writes an editable subtitle file per language (`script.vi.srt`); fix it in any
  subtitle editor, then `render`.
- **Standard subtitles**, centred at the bottom of the frame, plus optional background music and loudness normalization.
- **CLI and a simple web UI** (Gradio).
- **Plugins:** every step is a provider that can be replaced by a third-party package
  ([docs/plugins.md](https://github.com/sting11k/erasedub/blob/main/docs/plugins.md)).

## Providers

Each step is a provider you choose in `erasedub.toml`; `erasedub plugins` lists the installed ones.

| Step | Default (free, no key) | Opt-in choices | What it needs |
|---|---|---|---|
| Erase burned-in text | `sttn` — STTN video inpainting | `propainter` — ProPainter, higher quality on hard scenes, **non-commercial** licence · `lama` — LaMa, for machines without an NVIDIA GPU, per-frame: prone to flicker, flattens repeating patterned backgrounds · `none` — keep the picture | `sttn`, `propainter`: NVIDIA GPU. `lama`: CPU or Apple GPU (MPS). Extras `erase`, `propainter`, `lama` |
| Where erasing runs | `local` — this machine's NVIDIA GPU | `modal` — [Modal](https://modal.com) cloud GPU (`--gpu modal`) | `modal`: extra `modal` and your own Modal account, billed to you |
| Detect on-screen text | `rapidocr` — RapidOCR | — | CPU. Extra `ocr` |
| Transcribe | `whisperx` — WhisperX | — | Extra `asr`; faster with an NVIDIA GPU. Opt-in: word timestamps (`align`, off by default for licence reasons) and speaker labels (`diarize`, needs `HF_TOKEN`) |
| Translate | `google` — Google Translate web endpoint | `openai` (or any OpenAI-compatible API) · `gemini` · `claude` · `ollama` — local model, no key | `google`: internet, unofficial. `openai`/`gemini`/`claude`: extra `llm` and your API key. `ollama`: extra `ollama` and a local Ollama server |
| Voice | `edge` — Edge-TTS voices | `elevenlabs` — ElevenLabs | `edge`: internet, unofficial. `elevenlabs`: extra `elevenlabs` and your API key |
| Subtitle layout | `bottom` — bottom centre | — | ffmpeg with libass to burn them in (without it: a soft subtitle track) |

`erasedub[full]` installs `asr`, `ocr`, `erase`, `ollama` and `webui`: everything that needs no key, without the
opt-in erasers. Licences: [Licensing of models and services](#licensing-of-models-and-services).
Which eraser to use: [docs/erasers.md](https://github.com/sting11k/erasedub/blob/main/docs/erasers.md).

## Quick start

**Windows: portable bundle** (from the GitHub release). Extract it and double-click `EraseDub.exe`; no
Python needed, and it ships the CUDA build of PyTorch. See the
[Windows guide](https://github.com/sting11k/erasedub/blob/main/docs/install/windows.md).

**From source** ([uv](https://docs.astral.sh/uv/) or pip, Python 3.11–3.13 and an ffmpeg with libx264, plus libass
to burn subtitles in, see [install guides](https://github.com/sting11k/erasedub/blob/main/docs/index.md#install)):

```bash
git clone https://github.com/sting11k/erasedub
cd erasedub
uv sync --python 3.12 --extra full      # or, in a venv: pip install -e ".[full]"
source .venv/bin/activate               # Windows: .venv\Scripts\activate
erasedub doctor                         # checks Python, ffmpeg, GPU, providers and keys
```

One shot:

```bash
erasedub run video.mp4 --to vi          # -> video.vi.mp4
```

Or in two steps, so you can fix the translation first:

```bash
erasedub prepare video.mp4 --to vi      # transcribe + detect text + translate
                                        # -> work/video/script.vi.srt (+ script.vi.json, regions.json)
# edit work/video/script.vi.srt in Notepad, Subtitle Edit, ... (optional)
erasedub render video.mp4               # erase text (GPU) + voice + subtitles -> video.vi.mp4
```

**Web UI** (included in `full`):

```bash
erasedub webui --open                   # serves http://127.0.0.1:7860 and opens it in your browser
```

**Docker** (Linux amd64): run the `ghcr.io/sting11k/erasedub` image on the folder that holds your video. Commands,
GPU setup and file permissions are in the
[Docker guide](https://github.com/sting11k/erasedub/blob/main/docs/install/docker.md).

See the [CLI reference](https://github.com/sting11k/erasedub/blob/main/docs/cli.md) and
[configuration](https://github.com/sting11k/erasedub/blob/main/docs/configuration.md).

## Hardware

Erasing burned-in text is video inpainting: the default eraser needs an NVIDIA GPU. Everything else runs on a
normal computer.

| Your machine | Erase burned-in text | Transcribe, translate, voice, subtitles |
|---|---|---|
| Linux + NVIDIA GPU | Yes, locally | Yes |
| Windows + NVIDIA GPU | Yes, locally, after installing the **CUDA build of PyTorch** (see below) | Yes |
| No NVIDIA GPU (macOS, AMD or Intel graphics) | Skipped by default, with a notice. Or opt into the `lama` eraser: runs on the CPU or the Apple GPU (MPS); slower, per-frame (can flicker), flattens patterned backgrounds | Yes (transcription is slower on CPU) |
| No NVIDIA GPU + `--gpu modal` | Yes, on [Modal](https://modal.com), billed to **your** Modal account | Yes, locally |
| Rented GPU (vast.ai, RunPod, ...) | Yes | Yes |

<details>
<summary><b>Windows + NVIDIA from source: install the CUDA build of PyTorch</b></summary>

The portable Windows bundle is the recommended way; it ships the right PyTorch. From source, note that the
PyTorch wheels on PyPI are CPU-only on Windows: after `uv sync --python 3.12 --extra full`, replace them with the
CUDA 12.8 build of the exact version WhisperX requires (torch 2.8), from the official PyTorch index:

```powershell
uv pip install --force-reinstall --no-deps "torch==2.8.0" "torchaudio==2.8.0" "torchvision==0.23.0" --index-url https://download.pytorch.org/whl/cu128
erasedub doctor
```

A later `uv sync` puts the CPU build back; run the reinstall again after it.

If this step is missing, `erasedub doctor` says the installed torch "is a CPU-only build but an NVIDIA GPU is
present". See the [Windows guide](https://github.com/sting11k/erasedub/blob/main/docs/install/windows.md).

</details>

The free default translator and voices (Google Translate, Edge-TTS) are online services, so you need internet
access.
Guides: [renting a GPU](https://github.com/sting11k/erasedub/blob/main/docs/gpu-rental.md) ·
[troubleshooting](https://github.com/sting11k/erasedub/blob/main/docs/troubleshooting.md).

## Languages

EraseDub accepts speech in any language that WhisperX recognizes.

| Target language | Status |
|---|---|
| Vietnamese (`vi`), English (`en`), Chinese (`zh`) | **Verified**: translation, voice, and subtitles are checked by hand before each release |
| Any other language supported by the translator and TTS voice you choose | **Supported**: not part of the release checks |

Reports and fixes for other languages are very welcome.

## Notes

- **Erasing with `sttn` or `propainter` needs an NVIDIA GPU**: locally, on a rented machine, or on Modal
  (`--gpu modal`, billed to your account). Without one, erasing is skipped with a notice, or you opt into `lama`.
- **`lama` runs on the CPU or the Apple GPU (MPS).** It works frame by frame, so it is slower, can flicker, and
  flattens repeating patterned backgrounds.
- **The free translator and voices are unofficial services.** Google Translate and Edge-TTS are used through public
  web endpoints that are not developer APIs; they can change or be rate-limited.
- **One voice per target language** (`tts.voice`) reads the whole video. Speaker labels (opt-in, `diarize`) are
  recorded in the script, but do not choose voices.
- **amd64 only** for the Docker image and the Windows bundle.

## Comparison

How EraseDub v0.1 compares with the closest existing projects (EraseDub is inspired by them but does not
reuse their code). Other projects' cells come from their READMEs and docs as of 2026-09-26; ❌ = not offered there;
"?" = not stated. Corrections are welcome.

| | EraseDub v0.1 | [pyvideotrans](https://github.com/jianchang512/pyvideotrans) | [VideoLingo](https://github.com/Huanshere/VideoLingo) | [video-subtitle-remover](https://github.com/YaoFANGUK/video-subtitle-remover) |
|---|---|---|---|---|
| Remove burned-in subtitles | ✅ STTN; opt-in ProPainter, LaMa | ❌ | ❌ | ✅ STTN, LaMa, ProPainter, OpenCV |
| Speech recognition | ✅ WhisperX | ✅ faster-whisper + many APIs | ✅ WhisperX, ElevenLabs API | ❌ |
| Translation | ✅ Google (free), LLMs with your key, Ollama | ✅ LLMs, Google, Microsoft, ... | ✅ OpenAI-compatible LLM | ❌ |
| Dubbing (TTS) | ✅ Edge-TTS (free), ElevenLabs | ✅ Edge-TTS and many more | ✅ Edge-TTS and many more | ❌ |
| Voice cloning | ❌ (roadmap) | ✅ F5-TTS, CosyVoice, GPT-SoVITS | ✅ GPT-SoVITS, CosyVoice2, F5-TTS | ❌ |
| Edit the script before the final video | ✅ `script.<lang>.srt` | ✅ pause and proofread each stage | ? (pause/resume at each step; editing not stated) | — |
| Interface | CLI, web UI (Gradio) | desktop GUI, CLI, web UI (Gradio) | web UI (Streamlit), batch mode (beta) | desktop GUI, CLI |
| Hardware for erasing | NVIDIA GPU, or Modal with your account; CPU or Apple GPU with LaMa | — | — | NVIDIA, DirectML (AMD/Intel), CPU, Apple Silicon |
| Install | from source (uv or pip), Docker (amd64), portable Windows bundle | Windows `.exe`, source, Dockerfile | source, Dockerfile | Windows packages, source, Docker image |
| Licence | Apache-2.0 | GPL-3.0 | Apache-2.0 | Apache-2.0 |
| GitHub stars (2026-09-27) | new | ~19.2k | ~18.5k | ~13.1k |

These projects are excellent at what they do. If you only need one of the steps, use them.

## Licensing of models and services

EraseDub's own code is Apache-2.0. The models and services it can use have their own terms:

- **One optional eraser is non-commercial.** ProPainter (S-Lab License 1.0) must not be used commercially. It is
  opt-in only (extra `propainter`, not in `full`), never shipped with EraseDub, and EraseDub shows its licence
  whenever it is selected. Check the licence of any optional eraser before commercial use.
  WhisperX word alignment is **off by default** for the same reason: some of its per-language alignment models
  (for example the Vietnamese one) are CC BY-NC 4.0.
- Edge-TTS and the free Google Translate endpoint are unofficial uses of online services from Microsoft and
  Google. They can change or be rate-limited at any time. Check their terms for your use.
- The Docker image and Windows bundle ship a GPL build of ffmpeg (needed for libx264), with its licence, the
  exact upstream revision, and the corresponding source published alongside each release.

Details, with sources: [docs/models-and-licenses.md](https://github.com/sting11k/erasedub/blob/main/docs/models-and-licenses.md).
You are responsible for having the rights to the videos you process.

## Documentation

- [Documentation index](https://github.com/sting11k/erasedub/blob/main/docs/index.md)
- Install: [Linux](https://github.com/sting11k/erasedub/blob/main/docs/install/linux.md) ·
  [macOS](https://github.com/sting11k/erasedub/blob/main/docs/install/macos.md) ·
  [Windows](https://github.com/sting11k/erasedub/blob/main/docs/install/windows.md) ·
  [Docker](https://github.com/sting11k/erasedub/blob/main/docs/install/docker.md)
- [CLI reference](https://github.com/sting11k/erasedub/blob/main/docs/cli.md) ·
  [Configuration](https://github.com/sting11k/erasedub/blob/main/docs/configuration.md) ·
  [Script format](https://github.com/sting11k/erasedub/blob/main/docs/script-format.md) ·
  [Choosing an eraser](https://github.com/sting11k/erasedub/blob/main/docs/erasers.md)
- [Architecture](https://github.com/sting11k/erasedub/blob/main/docs/architecture.md) ·
  [Writing a plugin](https://github.com/sting11k/erasedub/blob/main/docs/plugins.md)
- [GPU rental](https://github.com/sting11k/erasedub/blob/main/docs/gpu-rental.md) ·
  [Troubleshooting](https://github.com/sting11k/erasedub/blob/main/docs/troubleshooting.md)
- [Models and licences](https://github.com/sting11k/erasedub/blob/main/docs/models-and-licenses.md) ·
  [Roadmap](https://github.com/sting11k/erasedub/blob/main/docs/roadmap.md)

## Community and support

Everything happens on GitHub:

- **Questions, ideas, showing your results:** [GitHub Discussions](https://github.com/sting11k/erasedub/discussions).
- **Bugs and installation problems:** [GitHub Issues](https://github.com/sting11k/erasedub/issues), using the issue
  forms. [SUPPORT.md](https://github.com/sting11k/erasedub/blob/main/SUPPORT.md) says what to include (start with
  the output of `erasedub doctor`).
- **Security problems:** never in a public issue. Report them privately through GitHub's private vulnerability
  reporting, as described in [SECURITY.md](https://github.com/sting11k/erasedub/blob/main/SECURITY.md).

## Contributing

Contributions are welcome, especially bug reports, installation reports from different machines, and checks of
languages beyond vi/en/zh. Please read [CONTRIBUTING.md](https://github.com/sting11k/erasedub/blob/main/CONTRIBUTING.md)
and the [Code of Conduct](https://github.com/sting11k/erasedub/blob/main/CODE_OF_CONDUCT.md).

## Acknowledgements

EraseDub stands on the work of these projects, which it depends on or downloads at first use:

- Erasing: [STTN](https://github.com/researchmm/STTN), [ProPainter](https://github.com/sczhou/ProPainter),
  [LaMa](https://github.com/advimman/lama).
- Speech and text: [WhisperX](https://github.com/m-bain/whisperX), [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
  [RapidOCR](https://github.com/RapidAI/RapidOCR) with [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) models,
  [deep-translator](https://github.com/nidhaloff/deep-translator), [Ollama](https://github.com/ollama/ollama).
- Voices: [edge-tts](https://github.com/rany2/edge-tts).
- Video and interface: [FFmpeg](https://ffmpeg.org/) (the Docker image and Windows bundle use builds from
  [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)),
  [Gradio](https://github.com/gradio-app/gradio), [Typer](https://github.com/fastapi/typer),
  [Rich](https://github.com/Textualize/rich), [Pydantic](https://github.com/pydantic/pydantic),
  [Modal client](https://github.com/modal-labs/modal-client).

The idea of doing the whole job in one tool is inspired by [pyvideotrans](https://github.com/jianchang512/pyvideotrans),
[VideoLingo](https://github.com/Huanshere/VideoLingo) and
[video-subtitle-remover](https://github.com/YaoFANGUK/video-subtitle-remover); EraseDub does not reuse their code.

## License

[Apache License 2.0](https://github.com/sting11k/erasedub/blob/main/LICENSE). See
[NOTICE](https://github.com/sting11k/erasedub/blob/main/NOTICE) for third-party notices.
