# CLI reference

The commands and options of EraseDub v0.1. Run `erasedub <command> --help` for the options your installed version
has.

```text
erasedub prepare VIDEO --to LANGS   transcribe + detect text + translate -> editable scripts
erasedub render  VIDEO              erase text + voice + subtitles -> final video
erasedub run     VIDEO --to LANGS   prepare + render in one go
erasedub webui                      browser UI
erasedub doctor                     check this machine
erasedub plugins                    list providers and whether each can run here
erasedub version                    print the version
```

Every option that has a config equivalent overrides `erasedub.toml` (see [configuration.md](configuration.md)).

## Global options

| Option | Meaning |
|---|---|
| `--config FILE`, `-c FILE` | Config file (default: `./erasedub.toml` if present) |
| `--version` | Print the version and exit |
| `--help` | Show help |

`--config` works both before and after the command; if given in both places, the one after the command wins.
`plugins` and `version` accept it too but read no config:

```bash
erasedub -c my.toml run video.mp4 --to vi
erasedub run video.mp4 --to vi -c my.toml
```

## `erasedub prepare`

```text
erasedub prepare VIDEO --to vi[,en,...] [--from auto] [--gpu local|modal] [--no-erase]
                       [--workdir DIR] [--config FILE] [--dry-run]
```

Extracts the audio, transcribes it, finds on-screen text (unless erasing is off), and translates. For each
target language it writes `script.<lang>.json` and `script.<lang>.srt` to `<workdir>/<video-stem>/`, plus a shared
`regions.json` with the text found on screen — see [script-format.md](script-format.md). Edit the SRT files before
rendering if you want.

| Option | Default | Meaning |
|---|---|---|
| `--to LANGS`, `-t` | `general.target_languages` (`vi`) | Target language(s), comma-separated: `--to vi,en` |
| `--from LANG`, `-f` | `auto` | Language spoken in the video; `auto` = detect |
| `--gpu local\|modal` | `erase.gpu` (`local`) | Where erasing will run. Giving it **requires** erasing for this run (see below) |
| `--no-erase` | off | Erasing will not run, so text detection is skipped (`erase.enabled = "off"`) |
| `--workdir DIR` | `work` (relative to the current directory) | Work directory |
| `--config FILE`, `-c` | `./erasedub.toml` if present | Config file |
| `--dry-run` | off | Check and print the plan, then exit without touching media (see below) |

Text detection (OCR) runs on the CPU, so `prepare` detects text on every machine, with or without an NVIDIA GPU,
unless erasing is off (`--no-erase`, `erase.enabled = "off"` or `erase.provider = "none"`).

With `erase.enabled = "on"` (or an explicit `--gpu`, below), `prepare` already stops with exit code 3 when erasing
cannot run here (for example no NVIDIA GPU with `erase.gpu = "local"`), before transcribing, so you do not wait for a
render that would fail.

**`--gpu` on the command line means erasing is wanted.** On `prepare`, `render` and `run`, an explicit
`--gpu <backend>` sets `erase.enabled = "on"` for that run, even if the config says `"off"`. If that backend cannot
run here (no NVIDIA GPU for `local`, the `modal` extra or Modal token missing...), the command stops with exit
code 3 instead of skipping erasing, with a message like "GPU backend 'modal' was picked with --gpu, which asks for
erasing, but GPU backend 'modal' is not ready: … or leave out --gpu to erase only when it can run". `--no-erase`
wins over `--gpu`. `--gpu` together with `erase.provider = "none"` in the config (no eraser named) is exit code 2:
set `erase.provider` to an eraser, or leave out `--gpu`. Setting `erase.gpu` only in `erasedub.toml`
keeps `erase.enabled` as configured (normally `auto`: erase if possible, otherwise a notice). Use the same `--gpu` /
`--no-erase` for `prepare` and `render`.

## `erasedub render`

```text
erasedub render VIDEO [--to vi[,en,...]] [--gpu local|modal] [--no-erase] [--no-voice] [--no-subs]
                      [--music FILE] [--out DIR] [--workdir DIR] [--config FILE] [--dry-run]
```

Reads the scripts from the work directory, erases burned-in text while generating the new voice, draws
subtitles, mixes the audio and writes `<video-stem>.<lang>.mp4` for each target language.

| Option | Default | Meaning |
|---|---|---|
| `--to LANGS`, `-t` | `general.target_languages` (`vi`) | Target language(s) to render, comma-separated; each needs its `script.<lang>.json` from `prepare` |
| `--gpu local\|modal` | `erase.gpu` (`local`) | Where erasing runs; giving it requires erasing (exit 3 if that backend cannot run). `modal` uses **your** Modal account ([gpu-rental.md](gpu-rental.md)) |
| `--no-erase` | off | Keep the original picture (`erase.enabled = "off"`) |
| `--no-voice` | off | No new voice track (`tts.enabled = false`) |
| `--no-subs` | off | No subtitles (`subtitles.enabled = false`) |
| `--music FILE` | none | Background music (`audio.music`) |
| `--out DIR`, `-o` | next to the input video (`general.output_dir`) | Output folder |
| `--workdir`, `--config`, `--dry-run` | | As for `prepare` |

With `erase.gpu = "local"` (the default, no `--gpu` given) and no NVIDIA GPU, erasing with the default eraser is
skipped with a notice (which suggests the `lama` eraser, below) and everything else runs. The same happens when `regions.json` is missing (for example `prepare` ran with `--no-erase`).
With `erase.enabled = "on"` or an explicit `--gpu` (including `--gpu local`), either case is an error instead: exit
code 3 when the backend cannot run, exit code 2 when `regions.json` is missing. See
[architecture.md](architecture.md#the-gpu-rule).

### Choosing the eraser

There is no command-line option for it; set `erase.provider` in `erasedub.toml`
([configuration.md](configuration.md#erase)). How to choose: [erasers.md](erasers.md).

| `erase.provider` | Needs | Notes |
|---|---|---|
| `sttn` (default) | NVIDIA GPU (local or `--gpu modal`); extra `erase` | |
| `propainter` | NVIDIA GPU; extra `propainter` | Higher quality on hard scenes, slower. **Non-commercial licence** (S-Lab License 1.0): the plan, `plugins`, `doctor` and the web UI show a notice whenever it is selected or listed ([models-and-licenses.md](models-and-licenses.md)) |
| `lama` | no NVIDIA GPU needed: runs on the CPU or the Apple GPU (MPS); extra `lama` | For machines without an NVIDIA GPU, such as Macs. Much slower; it inpaints each frame on its own, so it is **prone to flicker**, and it **flattens repeating patterned backgrounds** |
| `none` | — | Keep the original picture (same as `--no-erase`) |

Neither `propainter` nor `lama` is part of the `full` extra; install the one you need, e.g.
`uv sync --extra full --extra lama` (or `pip install -e ".[full,lama]"`) in the clone. Erasing is never switched to `lama` automatically: without an NVIDIA GPU you
choose it yourself.

## `erasedub run`

```text
erasedub run VIDEO --to vi [prepare and render options]
```

`prepare` followed by `render`, without stopping to edit. Accepts the options of both.

## `--dry-run`

Builds the plan without touching any media and prints, for each step, whether it runs, which provider it uses,
whether it is ready here (`ready` column; if not, the fix is in the note) and why a step is skipped. It also shows the
files each target language reads and writes. It validates the setup:

- an unknown provider name in the config or options, or invalid provider options → exit code 2;
- `render`: a missing or unreadable `script.<lang>.json` / `.srt` (run `prepare` for that language first) → exit
  code 2;
- `render`: a missing `regions.json` when erasing is required (`erase.enabled = "on"` or `--gpu`) → exit code 2.
  Under `auto` a missing `regions.json` only skips erasing, with a notice. A `regions.json` that exists but cannot
  be read → exit code 2 whenever erasing would run;
- a provider needed by an enabled step that cannot run here (missing extra, missing API key...), including the GPU
  backend when erasing is required → exit code 3;
- ffmpeg or ffprobe not found, or ffmpeg without libx264 → exit code 3. An ffmpeg without libass is not an error:
  the subtitles are added as a soft subtitle track (players can turn it on) instead of being burned in, and the
  plan and a notice say so.

When both kinds of problem exist, the exit code is 3; all of them are shown in the output, and the last line adds
how many render inputs are missing or unreadable. Exit code 0 means the run
would start with this setup.

## `erasedub webui`

```text
erasedub webui [--host 127.0.0.1] [--port 7860] [--open] [--config FILE]
```

Starts the Gradio web UI: upload a video, choose options, watch progress, download the result. Needs the `webui`
extra (included in `full`); without it the command exits with code 3. It listens on `127.0.0.1` by
default, so only this computer can reach it. `--port` must be between 1 and 65535 (otherwise exit code 2). Any
`--host` that is not a loopback address (`localhost`, `127.x.x.x`, `::1`) — including `0.0.0.0` and host names —
prints a warning: the web UI has no login, so anyone on that network could run jobs with your API keys. Use it only
on a network you trust.

`--open` also opens the page in your default browser; the default is `--no-open`. The Windows portable bundle's
`EraseDub.exe` starts `erasedub webui --open`.

Inside a container (the Docker image sets `ERASEDUB_IN_CONTAINER=1`), the server must listen on `0.0.0.0` to be
reachable at all, so instead of the warning a non-loopback host prints one line:
`note: Running in a container: publish the port as -p 127.0.0.1:7860:7860 to keep it local.` (with your `--port`).
If the variable is unset, empty, `0`, `false` or `no`, the warning is printed as usual.

The web UI plans with the same rules as the CLI:

- **GPU choice.** **Default** follows `erase.gpu` and `erase.enabled` from the config (normally `auto`: erase if
  possible, otherwise a notice). Picking **This machine** or **Modal** is the same as an explicit `--gpu`: erasing is
  required, and a backend that cannot run is shown as an error. Picking a GPU while Erase is **Off** switches Erase
  to **Auto**; setting Erase back to Off afterwards turns erasing off again (like `--no-erase --gpu`). A picked GPU
  with `erase.provider = "none"` in the config is an error.
- **Render without a script** for a language prepares that language first, like `erasedub run`; the plan shows both
  phases.
- **Errors, not warnings.** An unknown provider name in `erasedub.toml` is shown as an error ("Cannot run with these
  settings: …"), where the CLI exits with code 2.
- **SRT uploads** larger than 10 MB are refused.

## `erasedub doctor`

Checks, without downloading anything or calling any network service:

- Python version (3.11+), platform, console and file-system encoding;
- which config file is used and whether it is valid;
- `ffmpeg` and `ffprobe` (from `ERASEDUB_FFMPEG` / `ERASEDUB_FFPROBE` if set, else `PATH`), and that ffmpeg has
  **libx264** (H.264, required) and **libass** (optional: needed to burn subtitles in; without it they become a soft
  subtitle track);
- `nvidia-smi`: not found (no NVIDIA driver), found but reporting no GPU (a driver problem), or its path;
- NVIDIA GPU (name, memory, driver), and whether the installed PyTorch can use CUDA. If it cannot, it says the
  installed torch "is a CPU-only build but an NVIDIA GPU is present" — common on Windows, see
  [troubleshooting](troubleshooting.md#doctor-says-torch-is-a-cpu-only-build-typical-on-windows);
- installed optional extras (versions only; nothing is imported);
- the configured eraser: its summary, whether it can run here, its notice (licence or known weaknesses), and the
  hint to use `erase.provider = "lama"` when it needs an NVIDIA GPU this machine lacks;
- every registered provider and whether it can run (missing extra, missing environment variable); rows that are not
  ready still show the provider's summary, so ProPainter's licence and LaMa's weaknesses are visible before you
  install them;
- which API-key environment variables are set (never their values).

Each row starts with a mark: `ok` = fine, `!!` = a problem to fix (the text says how), `--` = not present and not
needed on this machine (for example no `nvidia-smi` on a Mac, or torch not installed when you do not erase locally).
A check that crashes is shown as `!!` with the error instead of stopping the command.

It exits with code 1 only when Python is too old. Missing ffmpeg, GPU or providers are reported but still exit 0,
because what you need depends on what you run. Run it first when something does not work, and paste its output into
bug reports.

## `erasedub plugins`

Lists every registered provider by kind, with its one-line summary, the package that registered it (`from`) and
whether it is available here; if not, why and
how to fix it (for example `uv sync --extra asr` or `set environment variable(s) OPENAI_API_KEY`).
Third-party providers appear here once installed (see [plugins.md](plugins.md)). Whether an NVIDIA GPU is present is
reported by the `gpu` providers (`local`), not by each eraser. Rows that are not ready show the provider's summary
and the reason, e.g. `propainter`'s non-commercial licence next to `uv sync --extra propainter`.

## `erasedub version`

Prints the installed version (same as `erasedub --version`).

## Exit codes

| Code | Meaning | Examples |
|---|---|---|
| 0 | Success | |
| 1 | Generic error | unexpected failure; ffmpeg failed; `doctor` on a too-old Python |
| 2 | Bad configuration, options or script | invalid `erasedub.toml` (including a file saved as UTF-16 instead of UTF-8), a key in the config file, unknown provider name, `--gpu` given while `erase.provider = "none"`, missing or unreadable `script.<lang>.json` / `.srt`, missing `regions.json` for `render` when erasing is required (`erase.enabled = "on"` or `--gpu`; under `auto` it is a notice), unreadable `regions.json`, `--port` out of range, wrong command-line usage |
| 3 | Provider unavailable | missing extra, missing API key, a plugin written for another plugin API version, `erase.enabled = "on"` when erasing cannot run here, `--gpu` given but that backend cannot run (e.g. `--gpu local` without an NVIDIA GPU, `--gpu modal` without the extra or token), ffmpeg missing or without libx264 |
| 130 | Cancelled | Ctrl+C |
