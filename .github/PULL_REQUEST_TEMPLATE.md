<!--
Thanks for contributing! Please read CONTRIBUTING.md first.
The PR title must be a Conventional Commit, e.g. "feat(tts): add voice selection to the WebUI".
-->

## What and why

<!-- What does this change, and why? Link the issue: "Fixes #123". -->

## How was it tested?

<!-- Commands you ran, platforms, GPU or not. Screenshots for WebUI changes. -->

## Checklist

- [ ] The PR is small and focused on one change.
- [ ] Tests added or updated; `uv run pytest` passes.
- [ ] `uv run pre-commit run --all-files` and `uv run mypy` pass.
- [ ] Docs and CLI help updated for user-visible changes.
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]` (or this is CI/internal only).
- [ ] No media, model weights, API keys, signed URLs or personal data are committed.
- [ ] New models/libraries/services are opt-in and their licenses are documented in `docs/models-and-licenses.md`.
