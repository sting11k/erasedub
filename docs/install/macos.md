# Install on macOS

## What works on a Mac

Macs have no NVIDIA GPU, so the default eraser (STTN) cannot run locally. By default, erasing is skipped with a
notice and everything else works: transcription, translation, new voice, subtitles, music, and the web UI. The
output keeps the original picture, with the new subtitles drawn on top.

To erase text as well, choose one of these:

- **Erase locally with the `lama` eraser** (opt-in, below). It runs on the CPU, or on the Apple GPU (MPS) on Apple Silicon.
  It is **much slower**, and because it fills each frame on its own it is **prone to flicker** and **flattens
  repeating patterned backgrounds**. It works best on subtitles over plain or slowly changing backgrounds.
- Run just the erase step on [Modal](https://modal.com) with your own account: `--gpu modal`. This uses a GPU eraser
  (STTN by default), so the quality is the same as on an NVIDIA machine.
- Rent a Linux machine with an NVIDIA GPU and run EraseDub there.

The last two are explained in [gpu-rental.md](../gpu-rental.md); all erasers are compared in
[erasers.md](../erasers.md).

Apple Silicon and Intel Macs are both supported for the non-erasing steps. Speech recognition runs on the CPU and is
slower than on an NVIDIA GPU; a few minutes of video is fine, hours will take a while.

## Requirements

| What | Why |
|---|---|
| Python **3.11–3.13** | EraseDub needs 3.11+; WhisperX (extra `asr`) does not support Python 3.14 yet |
| ffmpeg with **libx264**, plus ffprobe; **libass** recommended | H.264 output; libass burns subtitles in (without it they become a soft subtitle track) |
| Internet | free default translator and voices; first-time model downloads |

## 1. Python and ffmpeg

With [Homebrew](https://brew.sh):

```bash
brew install python@3.12 ffmpeg-full
```

Use **`ffmpeg-full`**, not `ffmpeg`: Homebrew's plain `ffmpeg` formula is built **without libass**, so it cannot
burn subtitles in (EraseDub then adds them as a soft subtitle track instead). `ffmpeg-full` is the official Homebrew formula that includes libass and libx264. It is *keg-only*
(not linked into your `PATH` automatically), so put it first on your `PATH` — add this line to `~/.zshrc` and open a
new terminal:

```bash
export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"
```

Then check that the right ffmpeg is found and can draw subtitles and encode H.264:

```bash
which ffmpeg        # should point into .../ffmpeg-full/bin
ffmpeg -hide_banner -buildconf | grep -E "libass|libx264"
```

Both names should appear: without libass, subtitles are added as a soft subtitle track instead of being burned
in. `erasedub doctor` checks this too. If you also have the plain `ffmpeg` formula installed,
it can stay; the `PATH` line decides which one runs.

If you would rather not change `PATH`, point EraseDub at `ffmpeg-full` directly instead:

```bash
export ERASEDUB_FFMPEG="$(brew --prefix ffmpeg-full)/bin/ffmpeg"
export ERASEDUB_FFPROBE="$(brew --prefix ffmpeg-full)/bin/ffprobe"
```

## 2. EraseDub

EraseDub is installed from source. On a Mac the `erase` extra (STTN) cannot run locally, so install the rest:

```bash
git clone https://github.com/EraseDub/erasedub
cd erasedub
uv sync --python 3.12 --extra asr --extra ocr --extra ollama --extra webui
source .venv/bin/activate
```

Or, with pip instead of [uv](https://docs.astral.sh/uv/), in the clone:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[asr,ocr,ollama,webui]"
```

The `full` extra also works; it just installs the STTN dependencies you cannot use locally. Add what you need for
erasing:

- to erase locally with LaMa: add the `lama` extra (`--extra lama`, or `".[asr,ocr,ollama,webui,lama]"`), then set
  it in `erasedub.toml`:

  ```toml
  [erase]
  provider = "lama"
  ```

- to erase on Modal: add the `modal` extra (`--extra modal`, or `".[asr,ocr,ollama,webui,modal]"`).

The other extras are listed in [install/linux.md](linux.md#3-erasedub).

## 3. Check

```bash
erasedub doctor
```

It will report that no NVIDIA GPU was found; that is expected. Everything else should be green.

## Run

```bash
erasedub run video.mp4 --to vi                 # erasing skipped (or LaMa, if you chose it), the rest runs
erasedub run video.mp4 --to vi --gpu modal     # erase on Modal with your account
```
