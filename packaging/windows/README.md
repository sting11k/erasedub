# Windows bundle (maintainers)

How the portable Windows bundle is built (`build.ps1`, run by the release workflow). User-facing instructions live in [docs/install/windows.md](../../docs/install/windows.md).

## Decision

Ship a **portable folder**: relocatable CPython + the locked dependencies installed by uv + a small
`EraseDub.exe` launcher. Do **not** freeze the application (torch, gradio, whisperx) with
PyInstaller. Only the launcher, which uses nothing but the standard library, is frozen.

```
EraseDub-<version>-win64-cu128\
  EraseDub.exe      launcher: no arguments = WebUI, otherwise passes arguments to the CLI
  EraseDub.cmd      same launcher as a batch file (fallback if antivirus blocks the .exe)
  python\           python-build-standalone CPython 3.12 with all packages in Lib\site-packages
  ffmpeg\bin\       ffmpeg.exe, ffprobe.exe (BtbN static GPL build) + LICENSE.txt, SOURCE.txt
  models\           empty; models download here on first use (HF_HOME, TORCH_HOME, NLTK_DATA)
  LICENSE, NOTICE
  THIRD-PARTY-LICENSES.txt   CPython and every Python package: declared licence + licence files
```

## Why (evidence, checked 2026-09-26)

How the comparable projects ship on Windows:

| Project | Windows download | How it is built |
|---|---|---|
| video-subtitle-remover (Apache-2.0) | `vsr-v1.1.1-windows-nvidia-cuda-12.8.7z.001…003`, about 4.6 GB, one build per CUDA version | [QPT](https://github.com/QPT-Family/QPT) (PyPI `qpt`): embedded CPython + site-packages folder, built in GitHub Actions (`.github/workflows/build-windows-cuda-12.8.yml`), split 7z volumes attached to the GitHub release |
| pyvideotrans (GPL-3.0) | `sp.exe` in a 2.7 GB 7z, hosted on Baidu/Hugging Face (GitHub release has no assets) | PyInstaller (its `docs/faq.md`; `.github/workflows/main.yml` uses a PyInstaller action). Its `docs/faq.md` says antivirus tools flag it because it is PyInstaller-packed and unsigned. Issue #907 shows `import torch` failing inside the frozen app (`BackendType has no attribute XCCL`). GPU use needs a separate CUDA 12.8 + cuDNN 9.11 install. |
| VideoLingo (Apache-2.0) | No Windows binary | Users install uv, then `setup_env.py` picks the PyTorch CUDA build from `nvidia-smi`. `OneKeyStart.bat` launches it. |

This supports the hypothesis:

- **Freezing torch with PyInstaller is fragile.** PyInstaller has to find every DLL, data file and
  lazy import in torch, whisperx and gradio. When it misses one, the error only shows at runtime on
  the user's machine (pyvideotrans #907). Every dependency bump needs a new frozen build and more
  hook fixes. Antivirus false positives affect the whole app.
- **A portable interpreter + normal `site-packages` is what the most comparable project uses.**
  VSR is a GPU inpainting app like ours. Packages are installed exactly as `pip`/`uv` would install
  them, so the bundle behaves like a normal install that CI and developers already test.
- **Size is set by CUDA torch, not by the packaging method.** The `torch-2.8.0+cu128` Windows wheel
  alone is 3.4 GB (3,461,384,651 bytes). PyInstaller would not make it smaller.
- **Reproducible and verifiable.** Every file comes from a pinned URL with a pinned sha256, or from
  `uv.lock` hashes.

Why python-build-standalone rather than the python.org "embeddable package": the embeddable zip has no
`pip`/`venv`/`tkinter`, uses a `._pth` file that turns off `site` unless you patch it, and needs the
matching MSVC runtime. python-build-standalone's `install_only` builds are a complete, relocatable
CPython. uv uses them for `uv python install`, and they come from the official
`astral-sh/python-build-standalone` releases with published checksums.

## The one non-PyPI source: PyTorch CUDA wheels

On Windows, PyPI's `torch` wheels are **CPU-only**. (On Linux x86_64 they bundle CUDA, so Docker uses
PyPI.) The bundle therefore installs `torch`, `torchaudio` and `torchvision` from the official
PyTorch index `https://download.pytorch.org/whl/cu128/`, pinned with sha256 in
[`torch-cu128.txt`](torch-cu128.txt). Versions must match what `uv.lock` resolves; whisperx currently
pins `torch~=2.8.0`. cu128 was chosen because it supports current GPUs (including RTX 50-series)
and needs a recent NVIDIA driver (570 or newer recommended). Users do **not** need to install the CUDA Toolkit,
because the wheels include the CUDA runtime.

`build.ps1` removes the PyPI torch entries from `uv export` and adds these three lines. It stops with
an error if the versions in `torch-cu128.txt` differ from the ones in `uv.lock`. Everything else
comes from PyPI with `--require-hashes`.

Build-time packages are hash-pinned too: hatchling (builds the erasedub wheel) and setuptools
(builds `antlr4-python3-runtime`, the one sdist-only dependency) come from
[`packaging/build-constraints.txt`](../build-constraints.txt). PyInstaller comes from
[`build-tools.txt`](build-tools.txt) and is installed into a throwaway venv.

The launcher removes `PYTHONHOME`/`PYTHONPATH` and turns off the user site-packages, so a system-wide
Python install cannot break the bundled one.

## Build

On Windows 10/11 x64 with PowerShell 7, [uv](https://docs.astral.sh/uv/) and 7-Zip installed, and about
25 GB free disk:

```powershell
pwsh packaging\windows\build.ps1            # -> dist\EraseDub-<ver>-win64-cu128.7z.001 ... + SHA256SUMS.txt
pwsh packaging\windows\build.ps1 -SkipArchive
```

CI: `.github/workflows/windows-bundle.yml` runs the same script on `windows-latest`.
Run manually (`workflow_dispatch`), it only uploads a workflow artifact. `release.yml` calls it
(`workflow_call` with `tag` and `upload: true`). It then attests the archive and uploads it to the
draft release that `release.yml` already created. It never creates or publishes a release, and it
refuses to upload to a release that is already published.

The launcher is frozen with PyInstaller from [`EraseDub.spec`](EraseDub.spec) (`launcher.py`, stdlib
only, no UPX).

## Release steps

1. `release.yml` builds the bundle on `windows-latest` with the script above. The build needs about 25 GB of
   free disk; check the free space and the size of the 7z volumes in the workflow log.
2. Test the bundle on a clean Windows + NVIDIA machine, as described in the
   [release checklist](../../docs/dev/release-checklist.md).
3. Publish the **GPL corresponding source** with the release, because the bundle and the Docker image
   distribute GPL ffmpeg: FFmpeg at `1a748fe2cd43e3ead22fafb1b5b7d77f153898a8` and BtbN/FFmpeg-Builds at
   `8267213e26c1031621e6e1210fe3aa4867214f6a`, with the third-party library sources it fetches (x264,
   libass, ...), or a written offer valid for 3 years. See the release checklist and
   [ADR 0010](../../docs/adr/0010-gpl-ffmpeg-in-binary-distributions.md).

## Licences inside the bundle

- `ffmpeg.exe`/`ffprobe.exe` are the BtbN **GPL** build (needed for libx264). Their licence goes in
  `ffmpeg\LICENSE.txt`. `ffmpeg\SOURCE.txt` records the exact FFmpeg and BtbN build-script commits.
  EraseDub calls them as separate programs. See the GPL corresponding-source item above.
- Python packages keep their own licences under `python\Lib\site-packages\*.dist-info`;
  `THIRD-PARTY-LICENSES.txt` collects them, with CPython's licence, in one file
  ([third_party_licenses.py](third_party_licenses.py), run by the bundle's own Python in step 6). edge-tts is
  LGPL-3.0 and is installed unmodified as a normal package, so users can replace it.
- No model weights are bundled. They download on first use from their official hosts under their
  own licences. See [docs/models-and-licenses.md](../../docs/models-and-licenses.md).
