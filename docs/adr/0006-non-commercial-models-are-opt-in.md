# 0006 — Models with non-commercial licences are opt-in and never bundled

- Status: Accepted
- Date: 2026-09-26

## Context

Some of the best open models in this space are licensed for non-commercial use only: ProPainter
(S-Lab License 1.0), E2FGVI (CC BY-NC 4.0), F5-TTS weights (CC BY-NC 4.0). EraseDub is Apache-2.0 and
people will use its output commercially.

## Decision

- Defaults avoid non-commercial **licences**: STTN (code MIT; the weights licence is not stated separately —
  see `docs/models-and-licenses.md`), WhisperX (BSD-2-Clause) with faster-whisper large-v3 weights (MIT),
  RapidOCR (Apache-2.0). The no-key translator and voice defaults use online services unofficially; their
  terms are the user's responsibility (ADR 0011). A permissive local voice (CosyVoice, Apache-2.0) is on the roadmap.
- A provider that depends on a non-commercial model may exist only as an explicit opt-in: its own extra, not
  part of `full`, never the default. It downloads the model at runtime from the official source, shows the
  licence clearly whenever it is selected or listed, and is never vendored into the repository or the wheel,
  nor included in the Docker image, the Windows bundle or the default install.
- In v0.1 this applies to one provider: the **`propainter`** eraser (S-Lab License 1.0), extra `propainter`
  (ADR 0014). F5-TTS is not part of v0.1.
- Licences can differ per language inside one tool; see ADR 0008 for WhisperX alignment models.
- `docs/models-and-licenses.md` lists every component with its code and weights licence.

## Alternatives considered

- **Ship ProPainter by default**: better quality on hard scenes, but incompatible with commercial use of the
  output, which most users expect from an Apache-2.0 tool.
- **Leave ProPainter out entirely**: simpler, but users who accept the non-commercial terms would have no
  higher-quality option.

## Consequences

- Some output quality is traded for licence safety by default.
- Reviewers reject pull requests that bundle or silently download non-commercial weights.
