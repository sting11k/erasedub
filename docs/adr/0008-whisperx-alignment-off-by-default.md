# 0008 — WhisperX forced alignment is off by default

- Status: Accepted
- Date: 2026-09-26

## Context

WhisperX improves timestamps with a per-language forced-alignment model. Those models come from different
authors under different licences. In WhisperX 3.8 the defaults for Vietnamese
(`nguyenvulebinh/wav2vec2-base-vi-vlsp2020`) and for French, German, Spanish and Italian (torchaudio
`VOXPOPULI_ASR_BASE_10K_*`) are CC BY-NC 4.0, and a few others state no licence. Plain subtitles and dubbing only need
segment-level timestamps.

## Decision

The WhisperX provider runs without forced alignment unless `[asr.options] align = true` is set.
`docs/models-and-licenses.md` lists the alignment model and licence for each verified language.

## Alternatives considered

- **Align by default and document the licence**: better timestamps, but a default path that is
  non-commercial for some source languages without the user noticing.

## Consequences

Transcripts carry no word timestamps by default (`TranscriptSegment.words` is empty).
