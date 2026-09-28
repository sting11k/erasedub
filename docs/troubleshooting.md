# Troubleshooting

Start with:

```bash
erasedub doctor
```

It checks Python, ffmpeg, the GPU, PyTorch's CUDA support, every provider and which API-key variables are set.
Include its output (it never prints key values) when you open an issue.

The exit code also tells you what kind of problem it is: 2 = configuration/script, 3 = a provider is missing
something (see [cli.md](cli.md#exit-codes)).

## GPU, CUDA and drivers

### "Burned-in text will NOT be erased: no NVIDIA GPU found"

EraseDub did not find a GPU through `nvidia-smi`, so it skipped erasing and ran the rest (with
`erase.enabled = "on"` or an explicit `--gpu local` it stops with exit code 3 instead). Check:

- `nvidia-smi` runs and lists your GPU. If the command is missing or fails, install or repair the NVIDIA driver
  (Linux: your distribution's driver package, then reboot; Windows: the driver from nvidia.com or GeForce/NVIDIA App).
- In Docker: the container was started with `--gpus all` and the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  is installed on the host ([install/docker.md](install/docker.md)).
- In WSL2: install the Windows NVIDIA driver only; do not install a Linux driver inside WSL.
- AMD, Intel and Apple GPUs cannot run the GPU erasers. Use `--gpu modal` or rent a GPU
  ([gpu-rental.md](gpu-rental.md)), or opt into the `lama` eraser (`erase.provider = "lama"`, extra `lama`), which
  runs on the CPU or the Apple GPU (MPS) — much slower, prone to flicker, and it flattens repeating patterned backgrounds
  ([erasers.md](erasers.md)).

### Doctor says torch "is a CPU-only build" (typical on Windows)

`erasedub doctor` prints that the installed torch "is a CPU-only build but an NVIDIA GPU is present":
`nvidia-smi` sees your GPU, but the installed PyTorch was built without CUDA, so erasing cannot use the GPU.
**On Windows, the PyTorch wheels on PyPI are CPU-only**, so installing EraseDub from source (`uv sync` or `pip install -e`) installs the CPU build.

The easiest fix on Windows is the portable bundle ([install/windows.md](install/windows.md)), which ships the right
PyTorch. From source, replace torch — **after** installing EraseDub, in the same environment — with the CUDA 12.8 build
of the exact version WhisperX requires. Do not install whatever version pytorch.org shows by default: WhisperX pins
torch 2.8 (`pip show whisperx` lists its requirements), and a newer torch breaks it.

```powershell
uv pip install --force-reinstall --no-deps "torch==2.8.0" "torchaudio==2.8.0" "torchvision==0.23.0" --index-url https://download.pytorch.org/whl/cu128
erasedub doctor
```

(With a pip venv, use `pip install` instead of `uv pip install`.) `doctor` must no longer print the message. If you
install EraseDub again later (for example after `git pull`, or any `uv sync`), repeat this step, because the install
puts the CPU build back.

On Linux the PyPI wheels already include CUDA; if you see this there, something installed a CPU-only build
explicitly (for example a `+cpu` wheel) — reinstall `torch` from PyPI.

### "CUDA driver version is insufficient" or similar

Your NVIDIA driver is older than the CUDA version your PyTorch was built for (12.8 for the command above). Update
the driver; `nvidia-smi` shows the highest CUDA version it supports in its header.

### Out of GPU memory (`CUDA out of memory`)

- Close other programs using the GPU (games, browsers with hardware acceleration, other AI tools). `nvidia-smi`
  shows what is using memory.
- Erasing works on video frames; higher resolutions need more memory. Try a lower-resolution copy first.
- Speech recognition also uses the GPU: set a smaller model, e.g. `[asr] model = "small"`.

## ffmpeg

### ffmpeg not found

Install it and make sure both `ffmpeg` and `ffprobe` are on your `PATH` (open a new terminal after changing `PATH`),
or point `ERASEDUB_FFMPEG` / `ERASEDUB_FFPROBE` at the executables
([configuration.md](configuration.md#other-environment-variables)).
See the install guide for your system: [Linux](install/linux.md) · [macOS](install/macos.md) ·
[Windows](install/windows.md).

### "ffmpeg without libass" / subtitles are a separate track, not burned in

EraseDub burns subtitles into the picture with ffmpeg's `ass` filter, which needs **libass**, and encodes with
**libx264**. libx264 is required. libass is optional: without it EraseDub still writes the video, but adds the
subtitles as a soft subtitle track that players can turn on (a notice says so), instead of drawing them. Check
your build:

```bash
ffmpeg -hide_banner -buildconf | grep -E "libass|libx264"
```

To burn subtitles in, both must appear. If not:

- Linux / Windows: use a static build from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases),
  **`gpl` variant** (the `lgpl` builds do not include libx264). Debian and Ubuntu's own `ffmpeg` package also works.
  The Windows bundle and Docker image already include a suitable ffmpeg.
- macOS: Homebrew's plain `ffmpeg` has no libass. Install the official `ffmpeg-full` formula and put it first on your
  `PATH` — see [install/macos.md](install/macos.md#1-python-and-ffmpeg).
- Several ffmpeg copies installed? `which ffmpeg` (Linux/macOS) or `where ffmpeg` (Windows) shows which one runs
  first. To pick one explicitly, set `ERASEDUB_FFMPEG` and `ERASEDUB_FFPROBE` to their full paths
  ([configuration.md](configuration.md#other-environment-variables)); `erasedub doctor` shows which one it uses.

## Free online services

### Google Translate: errors, empty results, "too many requests"

The default translator uses Google's free web endpoint through
[deep-translator](https://github.com/nidhaloff/deep-translator). It is unofficial, has no service guarantee, and is
rate-limited: long videos or many videos in a row can get temporarily blocked (usually for minutes to hours).

- Wait and run `erasedub render` / `prepare` again.
- Switch translator: `[translate] provider = "gemini"`, `"openai"`, `"claude"` (your key) or `"ollama"` (local,
  no limits).

### Edge-TTS: errors, 403, timeouts

Edge-TTS uses Microsoft's online read-aloud service unofficially through the
[edge-tts](https://github.com/rany2/edge-tts) package. Microsoft can throttle or change it at any time.

- Retry later; check your internet connection and any proxy/firewall.
- Upgrade the package: `pip install --upgrade edge-tts` (fixes often land there first).
- Use another voice provider: `[tts] provider = "elevenlabs"` (your key), or a local TTS plugin.

## Windows

### Paths with spaces or non-Latin characters

- Quote paths with spaces: `erasedub run "D:\My Videos\clip 01.mp4" --to vi`.
- If a tool in the chain fails on a path with non-Latin characters (Chinese, Vietnamese...), move the video to a
  simple path such as `D:\videos\clip01.mp4` and report the failure as a bug.
- Very long paths can exceed Windows' 260-character limit. Keep the work directory short (`--workdir D:\ed`) or
  enable long paths in Windows.

### Paths in `erasedub.toml`

In TOML, `\` inside double quotes is an escape character, so `"C:\new"` is broken. Use forward slashes
(`"C:/Users/me/music.mp3"`) or single quotes (`'C:\Users\me\music.mp3'`).

### `erasedub` is not recognized

The Python `Scripts` folder is not on `PATH`, or the virtual environment is not activated. Activate it
(`.venv\Scripts\activate`) or run `python -m erasedub`.

More: [install/windows.md](install/windows.md).

## Still stuck?

Search the [issues](https://github.com/sting11k/erasedub/issues). If nothing matches, open a new one with the
`erasedub doctor` output, the full command, and the error message.
