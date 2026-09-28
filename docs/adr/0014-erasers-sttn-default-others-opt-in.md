# 0014 — Erasers: STTN default; ProPainter and LaMa as opt-ins

- Status: Accepted
- Date: 2026-09-26

## Context

Erasing burned-in text is video inpainting: fill the text region in every frame so that the result stays
consistent over time. Each eraser adds a model with its own licence, weights source, hardware needs and upkeep, and
every one of them has to be tested on the demo set and supported in issues. The project wants one safe default and a
small, fixed set of options for other trade-offs — higher quality, and machines without an NVIDIA GPU — not a menu
of half-maintained backends.

## Decision

v0.1 ships exactly three erasers. Each has its own extra; only STTN's (`erase`) is part of `full`, and only STTN is
the default. Licences of code and weights are listed in `docs/models-and-licenses.md`; `docs/erasers.md` helps users
choose.

| Provider | Model | Runs on | Install | Default? |
|---|---|---|---|---|
| **`sttn`** | [STTN](https://github.com/researchmm/STTN) | NVIDIA GPU (local, or `--gpu modal`) | extra `erase` (in `full`) | **yes** |
| **`propainter`** | [ProPainter](https://github.com/sczhou/ProPainter) | NVIDIA GPU | extra `propainter` | no |
| **`lama`** | [LaMa](https://github.com/advimman/lama) (big-lama) | CPU or Apple MPS, no NVIDIA GPU needed | extra `lama` | no |

- **`propainter`** is non-commercial (S-Lab License 1.0) and follows ADR 0006: its code and weights are never
  vendored into the repository, the wheel, the Docker image or the Windows bundle. They are fetched at first use
  from the official sources: the code from the repository at a pinned commit, each file checked against a
  pinned sha256; the weights from its [v0.1.0 release](https://github.com/sczhou/ProPainter/releases/tag/v0.1.0),
  which publishes no checksums, so EraseDub pins their exact sizes and records each file's sha256 on first
  download; a later mismatch is an error. Whenever it is
  planned or listed (the CLI plan, the web UI, `erasedub plugins`, `erasedub doctor`), the non-commercial licence is
  shown clearly.
- **`lama`** is for machines without an NVIDIA GPU (for example Macs). It does not need a GPU
  (`requires_gpu = False`), so with `erase.provider = "lama"` erasing runs on the CPU or the
  Apple GPU (MPS). Code Apache-2.0; the weights' licence is unclear (the official sources state none). It is never
  turned on automatically (ADR 0004); the no-GPU notice suggests it. Its weaknesses are stated wherever it is
  offered:
  - it is an **image** inpainting model applied **frame by frame**, with no temporal model, so the filled area is
    **prone to flicker**;
  - it **flattens repeating patterned backgrounds** (stripes, tiles, fabric) into smooth areas;
  - on a CPU it is much slower than STTN on a GPU.
- No other eraser is on the roadmap, including as a later addition. Third parties can still publish an eraser as a plugin
  (ADR 0002).

## Alternatives considered

Checked on 2026-09-26 against each project's repository.

- **FGT** ([hitachinsk/FGT](https://github.com/hitachinsk/FGT), flow-guided transformer): MIT, but the code
  targets Python 3.6 and PyTorch 1.10, the weights are published only on Google Drive, and the repository has had
  no commits since February 2024.
- **DiffuEraser** ([lixiaowen-xw/DiffuEraser](https://github.com/lixiaowen-xw/DiffuEraser), Apache-2.0): uses
  ProPainter as its prior model, so its README tells users to comply with ProPainter's non-commercial licence as
  well; it also needs Stable Diffusion 1.5 and other weights. ProPainter itself is already an option.
- **E2FGVI** ([MCG-NKU/E2FGVI](https://github.com/MCG-NKU/E2FGVI)): CC BY-NC 4.0 (non-commercial), and no
  commits since April 2023.
- **EraserDiT** ([JieLiu95/EraserDiT](https://github.com/JieLiu95/EraserDiT), diffusion transformer): its
  requirements conflict with WhisperX's — the original repository needs PyTorch < 2.5 and the maintained fork
  ([MGTVAI/EraserDiT](https://github.com/MGTVAI/EraserDiT)) huggingface-hub 1.x, while WhisperX needs PyTorch ≥ 2.8
  (through pyannote.audio 4) and huggingface-hub < 1.0 — and its weights are about 29 GB.
- **ProPainter as the default**: better on hard scenes, but non-commercial (ADR 0006).
- **LaMa as the automatic eraser when there is no GPU**: see ADR 0004; kept open for a later decision.

## Consequences

- Users who need commercial use check the licences in `docs/models-and-licenses.md` for the eraser they pick;
  STTN's weights licence must be settled before v0.1.
- Users who accept the non-commercial terms can opt into ProPainter for harder scenes.
- Users without an NVIDIA GPU can erase locally with LaMa, slower and with visible weaknesses on moving or patterned
  backgrounds.
- Three erasers must each be tested on the demo set before v0.1.
- Requests for other built-in erasers are answered with this record; plugins remain possible.
