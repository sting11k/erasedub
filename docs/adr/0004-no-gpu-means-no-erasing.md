# 0004 — Without an NVIDIA GPU, skip erasing by default and run everything else

- Status: Accepted
- Date: 2026-09-26

## Context

Good video inpainting is only practical on an NVIDIA GPU. Many users (laptops, Macs) do not have one but still
want translation, a dubbed voice and subtitles. An image model such as LaMa can erase on a CPU or Apple GPU, but
more slowly and with visible weaknesses (ADR 0014).

## Decision

- `erase.enabled = "auto"` (default): erase when a GPU backend is usable, otherwise skip erasing with a
  clear notice naming what is missing, and still run every other step. `"on"` fails fast when erasing
  cannot run; `"off"` never erases.
- On-screen text detection (OCR) runs on the CPU, so `prepare` runs it unless `erase.enabled = "off"` —
  even without a local GPU — and stores the result in `work/<video>/regions.json` for `render`, which may run
  the erase step elsewhere.
- Erasing is **not** turned on automatically without an NVIDIA GPU. The default eraser (`sttn`) needs one, so
  under `auto` it is skipped, and the notice suggests the options below.
- Users without a local NVIDIA GPU have three documented ways to erase:
  1. rent a GPU machine (vast.ai, RunPod, ...) and run EraseDub there (`docs/gpu-rental.md`);
  2. `--gpu modal`: run only the erase step on [Modal](https://modal.com) with **their own** Modal account;
  3. opt into the **`lama`** eraser (`erase.provider = "lama"`, extra `lama`), which runs on the CPU or the Apple GPU (MPS).
     A user who picks it gets local erasing under `auto`; it is slower and prone to flicker, and it flattens
     repeating patterned backgrounds (ADR 0014).

## Alternatives considered

- **CPU erasing as an explicit opt-in** — chosen, as the `lama` eraser (above). OpenCV `cv2.inpaint` was not
  chosen: it is visibly poor on video.
- **Turn `lama` on automatically when no NVIDIA GPU is found**: every user would get erasing, but by default
  with a slower, flicker-prone result, and "erasing works" would mean two very different things depending on the
  machine. Not chosen for v0.1; kept open for a later decision.
- **Refusing to run without a GPU**: loses every user who only needs translation and dubbing.

## Consequences

- The no-GPU path must be tested in CI (it is the path CI runners take), including the notice that suggests
  `lama`.
- Costs of remote GPUs are always the user's own and are stated in the docs.
