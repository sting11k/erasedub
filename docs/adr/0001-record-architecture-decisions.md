# 0001 — Record architecture decisions

- Status: Accepted
- Date: 2026-09-26

## Context

EraseDub glues together many third-party tools (video inpainting, speech recognition, translation,
text-to-speech, ffmpeg). Many choices look arbitrary from the outside — why this model, why this file
format, why a dependency is optional — and will be questioned by new contributors.

## Decision

Keep architecture decision records in `docs/adr/`, one Markdown file per decision, numbered. Status is one of
`Proposed` (open question, needs a maintainer decision), `Accepted`, or `Superseded by NNNN`. Before v0.1 is
published, records may be edited in place; after that, accepted records are only superseded by new ones.

## Consequences

Pull requests that change a recorded decision must add a new ADR. Reviewers can point to ADRs instead of
re-arguing settled questions.
