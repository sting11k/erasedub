# 0003 — Two steps, `prepare` then `render`, with an editable SRT in between

- Status: Accepted
- Date: 2026-09-26

## Context

Automatic transcription and translation are never perfect. One-click tools force users to either accept
mistakes or re-run everything. Erasing and dubbing are the expensive steps (GPU time, paid voices), so
mistakes should be fixed *before* them.

## Decision

- `erasedub prepare` runs the cheap steps (audio extraction, speech recognition, on-screen text detection,
  translation) and writes, per target language, `work/<video>/script.<lang>.json` and
  `work/<video>/script.<lang>.srt`, plus `work/<video>/regions.json` (on-screen text to erase).
- The user may edit `script.<lang>.srt` in any subtitle editor.
- `erasedub render` runs the expensive steps from the script. On render, `script.<lang>.srt` is authoritative
  for timing and text; per-line metadata from `script.<lang>.json` (source text, speaker) is taken from the JSON line that
  overlaps the edited cue by more than half of its duration; failing that, from the same position when the
  number of lines is unchanged; otherwise it is dropped with a warning.
- `erasedub run` = `prepare` + `render` without stopping.

## Alternatives considered

- **One step with an interactive editor only in the web UI**: friendlier for some, but leaves CLI and
  scripting users without a review point and ties editing to one interface.
- **Proprietary project file instead of SRT**: richer, but no existing editor can open it.

## Consequences

- SRT is universal and tool-friendly, but cannot carry metadata; that is why the JSON exists and why the
  matching rule is needed. See `docs/script-format.md`.
- The web UI mirrors the same two steps.
