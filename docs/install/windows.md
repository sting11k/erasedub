# Install on Windows

There are two ways to install on Windows 10/11 (64-bit):

1. **Portable bundle:** download, extract, double-click `EraseDub.exe`. No Python needed.
2. **pip / uv:** for people who already use Python.

## Prerequisites for GPU text erasing

Text erasing needs an **NVIDIA GPU**. Without one, EraseDub skips erasing and prints a notice. It
still transcribes, translates, dubs and adds subtitles.

- Install a recent NVIDIA driver from [nvidia.com/drivers](https://www.nvidia.com/drivers) (570 or newer
  recommended). Check it with `nvidia-smi` in a terminal.
- You do **not** need the CUDA Toolkit or cuDNN. The bundle uses PyTorch builds that include the CUDA runtime.

## Option 1: portable bundle

1. Download every `EraseDub-<version>-win64-cu128.7z.*` part and `SHA256SUMS.txt` from the GitHub
   release. The bundle is several GB, mostly PyTorch with CUDA, so it is split into parts under 2 GB.
2. Optional: check the downloads in PowerShell with `Get-FileHash <file>` and compare against `SHA256SUMS.txt`.
3. Extract the parts with [7-Zip](https://www.7-zip.org/): open the `.7z.001` file. Extract to a short
   path such as `C:\EraseDub`. Do not run it from inside the archive.
4. Double-click `EraseDub.exe`. The WebUI opens at `http://127.0.0.1:7860`. For the command line, open
   a terminal in that folder and run, for example, `EraseDub.exe run clip.mp4 --to vi`.
5. Models download on first use. Speech-recognition models go to the `models` folder inside the bundle (unless
   `HF_HOME`/`TORCH_HOME` are set); eraser and OCR weights go to `%LOCALAPPDATA%\erasedub\cache` (set
   `ERASEDUB_CACHE_DIR` to move them).

If your antivirus blocks `EraseDub.exe` (an unsigned launcher can trigger false positives), use
`EraseDub.cmd` instead. It does the same thing.

Maintainers: see [packaging/windows/README.md](../../packaging/windows/README.md) for how the bundle is
built and why.

## Option 2: from source (uv or pip)

The portable bundle is the recommended Windows path. Use this option only if you already work with Python.
You need Git, Python 3.11, 3.12 or 3.13 (the speech-recognition stack does not support 3.14 yet), and ffmpeg on
`PATH` built with libx264, and with libass to burn subtitles in (for example the "gpl" builds from
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases)).

On Windows, the PyTorch wheels on PyPI are **CPU-only**. For GPU text erasing, replace them with the CUDA
build from the official PyTorch index. Use exactly the versions EraseDub's dependencies pin (whisperx
currently needs torch 2.8): torch 2.8.0, torchaudio 2.8.0, torchvision 0.23.0, CUDA 12.8 build.

**With [uv](https://docs.astral.sh/uv/)** (recommended):

```powershell
git clone https://github.com/sting11k/erasedub
cd erasedub
uv sync --python 3.12 --extra full
uv pip install --force-reinstall --no-deps torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
.venv\Scripts\activate
erasedub doctor
```

Call `erasedub` from the activated `.venv` (or use `uv run --no-sync erasedub`). A plain `uv run` or `uv sync`
puts the CPU torch from the lock file back; run the `uv pip install` line again after it.

**With pip:**

```powershell
git clone https://github.com/sting11k/erasedub
cd erasedub
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[full]"
pip install --force-reinstall --no-deps torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
erasedub doctor
```

The optional erasers are separate extras, not part of `full` (see [erasers.md](../erasers.md)). Add the one you
want before the CUDA torch reinstall, so that the reinstall comes last: `uv sync --extra full --extra propainter`, or
`pip install -e ".[full,propainter]"`.

## Licences

EraseDub is Apache-2.0. The portable bundle also contains:

- **FFmpeg**: the static **GPL** build from BtbN/FFmpeg-Builds (needed for libx264). Its licence and a
  pointer to the exact source revisions are in `ffmpeg\LICENSE.txt` and `ffmpeg\SOURCE.txt`.
- **Python packages**: each keeps its own licence in `python\Lib\site-packages\*.dist-info`.
  edge-tts is LGPL-3.0.
- **PyTorch**: from the official PyTorch index (BSD-style licence, includes NVIDIA CUDA runtime libraries
  under NVIDIA's licence).

Model weights are not bundled. They download on first use under their own licences, and some forbid
commercial use. See [models and licences](../models-and-licenses.md).
