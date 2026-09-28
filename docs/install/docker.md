# Install with Docker

The official image is `ghcr.io/erasedub/erasedub`. It is **Linux amd64 only**. It contains Python 3.12, EraseDub with the `full` extra (speech
recognition, OCR, text erasing, Ollama translator, WebUI), a static ffmpeg with libass and libx264, and Noto
fonts for Latin (including Vietnamese) and CJK subtitles.

**Why no arm64 in v0.1:** the speech-recognition stack (whisperx → pyannote-audio) requires `torchcodec`,
which publishes no Linux arm64 wheels. PyTorch on PyPI is also CPU-only on Linux arm64, so GPU erasing would
not work there anyway. The build stops with a clear error on arm64. On an Apple Silicon Mac you can build and
run the amd64 image under emulation with `--platform linux/amd64`. That is slow and has no GPU, so it is only
good for trying the CLI.

## Prerequisites

- Docker Engine 24+ with BuildKit (Docker Desktop also works), and Docker Compose v2 if you use `compose.yaml`.
- **For GPU text erasing (optional):**
  - an NVIDIA GPU;
  - a recent NVIDIA driver on the host: 570 or newer, which RTX 50-series cards require;
  - the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
    configured for Docker.

  You do not need the CUDA Toolkit: the PyTorch wheels in the image include the CUDA runtime.

  After installing the toolkit:

  ```bash
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo systemctl restart docker
  docker run --rm --runtime=nvidia --gpus all ubuntu nvidia-smi   # should list your GPU
  ```

Without a GPU, EraseDub skips text erasing and prints a notice. Everything else still runs.
The image installs the `full` extra, so its eraser is the default STTN; the optional erasers (`propainter`,
`lama`) are not included ([erasers.md](../erasers.md)).

## Get the image

```bash
docker pull ghcr.io/erasedub/erasedub:0.1
docker tag ghcr.io/erasedub/erasedub:0.1 erasedub   # the commands below use the short name
```

Each release is tagged `X.Y.Z` and `X.Y`; `latest` is the newest release.

### Or build it yourself

```bash
git clone https://github.com/EraseDub/erasedub.git
cd erasedub
docker build -t erasedub .
# Apple Silicon / other arm64 hosts (emulated, slow, no GPU):
# docker build --platform linux/amd64 -t erasedub .
```

The image is large (several GB), mostly because of PyTorch and its CUDA libraries.

Models are not included in the image. They download on first use (see [Run](#run)) into the cache volume.

## Run

The container's working directory is `/data`. Mount the folder that holds your video there. EraseDub
then writes `work/<video-name>/` and the finished video into that same folder. Run as your own user so
the files belong to you:

```bash
cd ~/videos                      # the folder with clip.mp4
docker run --rm --gpus all --user "$(id -u):$(id -g)" \
  -v "$PWD:/data" -v erasedub-cache:/home/erasedub/.cache \
  erasedub doctor

docker run --rm --gpus all --user "$(id -u):$(id -g)" \
  -v "$PWD:/data" -v erasedub-cache:/home/erasedub/.cache \
  erasedub run clip.mp4 --to vi
```

- Leave out `--gpus all` on machines without an NVIDIA GPU.
- The `erasedub-cache` volume keeps downloaded models between runs. The image sets `HOME`, `HF_HOME`,
  `TORCH_HOME` and `NLTK_DATA` to paths in `/home/erasedub`, which is writable for any user, so this
  also works with `--user`. If you switch between different user ids, give each one its own cache volume.
- Without `--user`, the container runs as uid 10001 and can only write to your folder if that uid is
  allowed to (`sudo chown -R 10001 <folder>`).

### Keys and translators

Optional API keys are passed as environment variables, for example `-e ELEVENLABS_API_KEY` (the value is
taken from your shell). Never put keys in `erasedub.toml`, a Dockerfile or `compose.yaml`.

The image contains the `full` extra only. The OpenAI, Gemini and Claude translators need the `llm` extra,
which is **not** in the image. To add it, change `--extra full` to `--extra full --extra llm` in the
Dockerfile's builder stage and rebuild. The default translator (Google Translate, no key) and Edge-TTS work
out of the box.

To use an [Ollama](https://ollama.com) server running on the host:

```bash
docker run ... --add-host=host.docker.internal:host-gateway \
  -e OLLAMA_HOST=http://host.docker.internal:11434 erasedub ...
```

(Ollama must listen on an address the container can reach, for example `OLLAMA_HOST=0.0.0.0` on the host.)

### WebUI with Docker Compose

[`compose.yaml`](../../compose.yaml) builds the image locally (or uses the official one, see the comment in the
file), then starts the WebUI with GPU access.
It mounts `./data` at `/data`, keeps the model cache in a named volume, and points Ollama at the host:

```bash
mkdir -p data
ERASEDUB_UID=$(id -u) ERASEDUB_GID=$(id -g) docker compose up --build
# open http://127.0.0.1:7860
```

The WebUI has no login. The example binds it to `127.0.0.1` only. Do not expose it to a network
without putting your own authentication in front of it. On a machine without a GPU, delete the
`deploy:` block.

## Renting a GPU

Services such as vast.ai and RunPod run **containers from a registry image**. You cannot `docker build`
inside them.

- **With the official image:** use `ghcr.io/erasedub/erasedub:0.1` as the instance image (or your own build,
  pushed to a registry you control). Set the container command to `webui --host 0.0.0.0` (or run a shell). The default command only prints `--help` and exits, and the
  platform would treat that as a crash.
- **With the provider's template:** rent an instance from the provider's own PyTorch/CUDA template, then install
  EraseDub inside it from source with uv (clone, then `uv sync --python 3.12 --extra full`), as described in
  [Erasing without your own NVIDIA GPU](../gpu-rental.md).
  Many templates ship Python 3.10, which is too old for pip; uv fetches a suitable Python.

You are billed by the provider, not by EraseDub.

## Licences inside the image

EraseDub is Apache-2.0. The image also contains third-party software under its own licences:

- **FFmpeg** is the static **GPL** build from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)
  (the GPL variant is needed for libx264). Inside the image, `/usr/share/doc/ffmpeg` contains:
  - `LICENSE.txt`, the licence;
  - `SOURCE.txt`, which records the exact FFmpeg commit and BtbN build-script commit.

  If you redistribute the image, you redistribute GPL software and must meet the GPL's terms for that
  part, including making the corresponding source available.
- Debian packages (fonts, fontconfig) keep their licences in `/usr/share/doc/<package>`.
- Python packages keep theirs in `/opt/venv/lib/python3.12/site-packages/*.dist-info`.
- Model weights are **not** in the image. They download
  on first use under their own licences, and some of those licences forbid commercial use. See
  [models and licences](../models-and-licenses.md).
