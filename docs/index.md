# EraseDub documentation

Documentation for EraseDub v0.1.

## Using EraseDub

| Page | What it covers |
|---|---|
| [CLI reference](cli.md) | Every command, option and exit code |
| [Configuration](configuration.md) | `erasedub.toml` keys, defaults, environment variables for API keys |
| [Choosing an eraser](erasers.md) | The three erasers — STTN (default), ProPainter, LaMa: hardware, speed and quality, licence |
| [Script format](script-format.md) | `script.<lang>.json`, `script.<lang>.srt`, `regions.json`, and how your edits are applied |
| [GPU rental](gpu-rental.md) | Erasing without your own NVIDIA GPU: vast.ai, RunPod, `--gpu modal` |
| [Troubleshooting](troubleshooting.md) | GPU/CUDA, VRAM, ffmpeg, rate limits, Windows paths |
| [Models and licences](models-and-licenses.md) | Every model and service, its licence, and whether commercial use is allowed |

## Install

| Platform | Guide |
|---|---|
| Linux | [install/linux.md](install/linux.md) |
| macOS | [install/macos.md](install/macos.md) (local erasing only with the opt-in `lama` eraser) |
| Windows | [install/windows.md](install/windows.md) (portable bundle recommended) |
| Docker | [install/docker.md](install/docker.md) (Linux amd64) |

## How it works and extending it

| Page | What it covers |
|---|---|
| [Architecture](architecture.md) | Engine, providers, the `prepare` / `render` pipeline, data flow |
| [Writing a plugin](plugins.md) | Ship your own eraser, translator, voice... as a separate package |
| [Architecture decisions](adr/README.md) | Why things are the way they are (ADRs) |
| [Roadmap](roadmap.md) | Milestones to v0.1 and ideas after it |

## Project and maintainers

| Page | What it covers |
|---|---|
| [Demo set](demo-assets.md) | The demo clips, their outputs, and the size rules for the README animations |
| [Contributing](../CONTRIBUTING.md) | How to set up a dev environment and send changes |
| [CI](dev/ci.md) · [Branching and releases](dev/branching-and-releases.md) · [Issue triage](dev/issue-triage.md) | Maintainer processes |
| [Release checklist](dev/release-checklist.md) | What must be true before each release |
