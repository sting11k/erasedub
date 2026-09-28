# Install on Linux

Supported: x86-64 Ubuntu 22.04/24.04 and Debian 12, with or without an NVIDIA GPU. Other distributions
should work if they meet the requirements.

**Ubuntu 22.04 and Debian 11 ship Python 3.10**, which is too old. Use the `uv` route in step 3: uv downloads a
suitable Python for EraseDub without touching the system one (do not add third-party Python PPAs).

## Requirements

| What | Why | Check |
|---|---|---|
| Python **3.11–3.13** | EraseDub needs 3.11+. WhisperX (speech recognition, extra `asr`) does not support Python 3.14 yet. uv can provide it (step 3). | `python3 --version` |
| ffmpeg with **libx264**, plus ffprobe; **libass** recommended | H.264 output; libass burns subtitles in (without it they become a soft subtitle track) | `erasedub doctor` |
| NVIDIA GPU + driver (optional) | only for erasing burned-in text | `nvidia-smi` |
| Internet | free default translator and voices; first-time model downloads | |

Without an NVIDIA GPU everything except erasing still works: EraseDub skips that step and says so. To erase without
your own GPU, see [gpu-rental.md](../gpu-rental.md), or opt into the `lama` eraser, which runs on the CPU (much
slower, prone to flicker, and flattens repeating patterned backgrounds; see [erasers.md](../erasers.md)).

## 1. ffmpeg

Debian and Ubuntu packages are built with libass and libx264:

```bash
sudo apt update
sudo apt install ffmpeg
```

On other distributions, check with `ffmpeg -hide_banner -buildconf | grep -E "libass|libx264"` — libx264 must
appear, and libass too if you want subtitles burned in (without it they become a soft subtitle track).
If not, use a static build from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases) (the `gpl`
variant; `lgpl` builds lack libx264) and put its `bin/` folder on your `PATH`.

## 2. NVIDIA driver (only for erasing)

Install the proprietary NVIDIA driver from your distribution (Ubuntu: `sudo ubuntu-drivers install`) and reboot.
`nvidia-smi` must list your GPU.

You do **not** need to install the CUDA Toolkit: the PyTorch wheels on PyPI for Linux include the CUDA libraries
they need. You only need a driver recent enough for them; if PyTorch reports that the driver is too old, update it.

> **Windows is different:** PyTorch wheels on PyPI are CPU-only on Windows. There, the portable bundle is the
> recommended install; from source you must replace torch with the CUDA 12.8 build of the exact version WhisperX
> requires (torch 2.8). See [install/windows.md](windows.md) and
> [troubleshooting](../troubleshooting.md#doctor-says-torch-is-a-cpu-only-build-typical-on-windows).

## 3. EraseDub

EraseDub is installed from source, into its own environment (`.venv` inside the clone) so its dependencies do not
clash with your system Python.

```bash
sudo apt install git
git clone https://github.com/sting11k/erasedub
cd erasedub
```

**Recommended — with [uv](https://docs.astral.sh/uv/)** (works on every target, including Ubuntu 22.04). Install uv
as described in [its official documentation](https://docs.astral.sh/uv/getting-started/installation/) (for example
`sudo apt install pipx && pipx install uv`), then, in the clone:

```bash
uv sync --python 3.12 --extra full
source .venv/bin/activate
```

uv downloads Python 3.12 if your system does not have it and installs the exact versions from `uv.lock`. Pinning
`--python 3.12` keeps you on a version WhisperX supports. Activate `.venv` in each new terminal, or prefix commands
with `uv run` (for example `uv run erasedub doctor`).

**Or with pip**, if your system Python is 3.11–3.13 (Ubuntu 24.04, Debian 12), in the clone:

```bash
sudo apt install python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[full]"
```

What the extras add:

| Extra | Adds | Notes |
|---|---|---|
| (none) | CLI, Google Translate (free), Edge-TTS | lightweight, no PyTorch |
| `asr` | WhisperX speech recognition | pulls PyTorch (large download); Python ≤ 3.13 |
| `ocr` | RapidOCR text detection | CPU |
| `erase` | STTN eraser (PyTorch, OpenCV), the default | NVIDIA GPU |
| `propainter` | ProPainter eraser (opt-in) | NVIDIA GPU; **non-commercial** licence; not in `full` |
| `lama` | LaMa eraser (opt-in) | CPU or Apple GPU (MPS), no NVIDIA GPU needed; slower, prone to flicker, flattens repeating patterned backgrounds; not in `full` |
| `ollama` | local LLM translation through [Ollama](https://ollama.com) | Ollama itself is installed separately |
| `webui` | Gradio web UI | |
| `full` | `asr` + `ocr` + `erase` + `ollama` + `webui` | everything that needs no paid key, without the optional erasers |
| `llm` | OpenAI, Gemini, Claude translators | your API key |
| `elevenlabs` | ElevenLabs voices | your API key |
| `modal` | `--gpu modal` | your Modal account |

Combine extras: `uv sync --extra full --extra llm`, or `pip install -e ".[full,llm]"`.
To update later, run `git pull` in the clone and the same install command again. The optional erasers are chosen with
`erase.provider` ([erasers.md](../erasers.md)).

## 4. Check

```bash
erasedub doctor
```

It reports Python, ffmpeg (libass, libx264), your GPU and whether PyTorch can use it, and which providers are
ready. Fix anything it flags; [troubleshooting.md](../troubleshooting.md) covers the common problems.

## Model downloads

Models (WhisperX, OCR, STTN) are downloaded the first time a step needs them, from where their authors publish them,
into your user cache directory. Expect several GB for the full set. See
[models-and-licenses.md](../models-and-licenses.md) for what is downloaded and under which licence.

## Uninstall

Delete the clone folder (the `.venv` is inside it):

```bash
rm -rf erasedub
```

Downloaded models stay in your cache directories, `~/.cache/erasedub` (eraser and OCR models) and
`~/.cache/huggingface` (speech recognition); delete them if you no longer need them.
