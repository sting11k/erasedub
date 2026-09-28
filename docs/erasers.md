# Choosing an eraser

EraseDub removes burned-in text with one of three erasers. **STTN is the default**; the other two are opt-in: each
has its own extra (none of them is part of `full`), and you select it in `erasedub.toml`:

```toml
[erase]
provider = "lama"      # "sttn" (default) | "propainter" | "lama" | "none"
```

Why exactly these three: [ADR 0014](adr/0014-erasers-sttn-default-others-opt-in.md). Licences in detail:
[models-and-licenses.md](models-and-licenses.md).

Speed and quality notes below describe how each model works.

## At a glance

| | `sttn` (default) | `propainter` | `lama` |
|---|---|---|---|
| Hardware | NVIDIA GPU, local or `--gpu modal` | NVIDIA GPU | **no NVIDIA GPU needed**: CPU, or Apple GPU (MPS) |
| Install | extra `erase` (in `full`) | extra `propainter` | extra `lama` |
| Character | video model; the lighter of the two GPU erasers | flow-guided video model; better on hard, moving backgrounds; slower | image model applied frame by frame; much slower on a CPU; **prone to flicker**, **flattens repeating patterned backgrounds** |
| Licence | code MIT; weights licence not stated separately (**unresolved**) | **non-commercial** (S-Lab License 1.0, code and weights) | code Apache-2.0; weights: no licence stated at the official source (**unclear**) |

## `sttn` — the default

STTN (spatial-temporal transformer) fills the text region using the frames around it. It needs an NVIDIA GPU, on
this machine or on Modal with your own account (`--gpu modal`, see [gpu-rental.md](gpu-rental.md)). Without an
NVIDIA GPU and without `--gpu`, erasing is skipped with a notice and everything else still runs.

## `propainter` — higher quality, non-commercial

```bash
uv sync --extra full --extra propainter      # in the clone; or: pip install -e ".[full,propainter]"
```

ProPainter propagates pixels along optical flow and then fills the rest with a transformer, which helps on camera
motion and busy backgrounds. It needs an NVIDIA GPU and is slower than STTN.

**Its licence (S-Lab License 1.0) allows non-commercial use only.** EraseDub shows a notice whenever it is selected
or listed (the plan, `erasedub plugins`, `erasedub doctor`, the web UI). Its code and weights are never shipped
with EraseDub. At first use, the code is fetched from the official repository at a pinned commit, each file
checked against a pinned sha256; the weights come from its
[v0.1.0 release](https://github.com/sczhou/ProPainter/releases/tag/v0.1.0), which publishes **no checksums**, so
EraseDub pins their exact sizes and records each file's sha256 on first download; a later mismatch is an error.

## `lama` — for machines without an NVIDIA GPU

```bash
uv sync --extra full --extra lama            # in the clone; or: pip install -e ".[full,lama]"
```

LaMa (big-lama) is an **image** inpainting model. EraseDub runs it on the CPU, or on the Apple GPU (MPS) on
Apple Silicon Macs, so it is the one way to erase locally on a Mac or on a PC with AMD or
Intel graphics. EraseDub never switches to it
by itself: without an NVIDIA GPU, the notice suggests it and you choose it.

It uses an NVIDIA GPU when the machine has one, else the Apple GPU when PyTorch can use it (macOS 14 or newer),
else the CPU. To choose, set `device` (`auto`, `cpu`, `cuda` or `mps`) in `[erase.options]`.

Know its limits before you use it:

- **Flicker.** Each frame is filled on its own, without looking at its neighbours, so the filled area can shimmer
  from frame to frame, especially on moving backgrounds.
- **Flat patterns.** Repeating patterned backgrounds (stripes, tiles, fabric, grids) tend to come out as smooth,
  flat patches.
- **Speed.** On a CPU it is much slower than STTN on an NVIDIA GPU; long videos take a long time.

It works best on subtitles over plain or slowly changing backgrounds.

**Where it comes from:** the generator code is vendored into EraseDub from
[advimman/lama](https://github.com/advimman/lama) at a pinned commit (Apache-2.0, licence text included). The
big-lama weights are downloaded on first use from the Hugging Face repository `smartywu/big-lama`, the download
location the upstream README gives, at a pinned revision and checked against a pinned sha256.

**Licences:** the code is Apache-2.0. The authors' own sources for the weights (README, Google Drive folder) state
**no licence**, so the weights' terms are unclear. The Apache-2.0 tag on the `smartywu/big-lama` repository is not
the authors' own statement.

## `none`

Keeps the original picture (same as `--no-erase`). Translation, voice and subtitles still run.
