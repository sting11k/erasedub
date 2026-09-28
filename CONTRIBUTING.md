# Contributing to EraseDub

Thanks for your interest in EraseDub! This guide explains how to set up a development environment, how we
name branches and write commits, and what we expect in a pull request.

> Interfaces may change between 0.x releases; breaking changes are always listed in the
> [changelog](CHANGELOG.md).

By participating you agree to follow our [Code of Conduct](CODE_OF_CONDUCT.md). For questions, use
[GitHub Discussions](https://github.com/sting11k/erasedub/discussions) (see [SUPPORT.md](SUPPORT.md)).
Security problems go through private reporting — see [SECURITY.md](SECURITY.md), never a public issue.

## Development setup

You need Python 3.11 or newer, [uv](https://docs.astral.sh/uv/) and Git. An NVIDIA GPU is **not** required
for development.

```bash
git clone https://github.com/sting11k/erasedub.git
cd erasedub
uv sync                          # creates .venv with the package (editable) and the dev tools
uv run pre-commit install        # installs the git pre-commit hook
uv run pytest                    # run the test suite
```

By default `pytest` skips tests marked `slow` (processes real media), `gpu` (needs an NVIDIA GPU) and
`network` (needs internet access). `-m` replaces that default selection: `uv run pytest -m slow` runs **only**
the slow tests, `uv run pytest -m "slow or network"` only those two groups, and `uv run pytest -m ""` runs
everything.

Other checks CI runs:

```bash
uv run ruff check .              # lint
uv run ruff format .             # format (use --check to only verify)
uv run mypy                      # strict type check
uv run pre-commit run --all-files
```

To work on a provider that needs an optional dependency, install its extra, for example `uv sync --extra ocr`.
`uv sync` is exact — it removes extras you do not name — so list every extra you need in one command:
`uv sync --extra ocr --extra llm`. See `pyproject.toml` for the list of extras.

If `uv lock` prints `Updated X vA -> vB` with vB older than vA for a package you did not touch, stop: a new
dependency conflicts with an existing extra, and uv resolved it by downgrading that extra.

The pre-commit hooks run ruff, codespell, gitleaks (secret scanning), zizmor (GitHub Actions security),
actionlint (GitHub Actions workflow syntax) and basic YAML/TOML/JSON checks. Fix what they report; do not bypass
them with `--no-verify`. actionlint runs shellcheck on workflow scripts only if `shellcheck` is installed; CI always
has it, so installing it locally just saves a round trip.

If you change README.md, README.vi.md, README.zh-CN.md or anything in `docs/`, CI checks the links (the
`links (offline)` check). To run the same check locally, install lychee as described in
[docs/dev/ci.md](docs/dev/ci.md#checking-links).

The gitleaks and actionlint hooks are written in Go. pre-commit bootstraps a Go toolchain for them automatically
the first time they run (this needs network access once). If that bootstrap fails on your machine, do **not** skip the hook —
a secret that reaches a pushed commit is already leaked, whatever CI reports later. Instead:

1. Install the official `gitleaks` binary for your OS from
   [github.com/gitleaks/gitleaks/releases](https://github.com/gitleaks/gitleaks/releases) (check it against
   the release's checksums file) and make sure `gitleaks version` works in your shell.
2. Use the upstream `gitleaks-system` hook id (same check, but runs the binary on your `PATH`) through a
   private copy of the config kept inside `.git/`, so the tracked `.pre-commit-config.yaml` stays unchanged:

   ```bash
   cfg="$(git rev-parse --git-dir)/pre-commit-local.yaml"
   sed 's/id: gitleaks$/id: gitleaks-system/' .pre-commit-config.yaml > "$cfg"
   uv run pre-commit install --config "$cfg"
   ```

   Re-run these commands when `.pre-commit-config.yaml` changes. On Windows, run them in Git Bash.

If the actionlint build fails as well, also install the official `actionlint` binary from
[github.com/rhysd/actionlint/releases](https://github.com/rhysd/actionlint/releases) (checked against the release's
checksums file), make sure `actionlint -version` works, and use this instead of the commands in step 2, so both
hooks use the binaries on your `PATH`:

```bash
cfg="$(git rev-parse --git-dir)/pre-commit-local.yaml"
sed -e 's/id: gitleaks$/id: gitleaks-system/' -e 's/id: actionlint$/id: actionlint-system/' \
  .pre-commit-config.yaml > "$cfg"
uv run pre-commit install --config "$cfg"
```

## Branches

Work on a short-lived branch off `main`, named after the kind of change:

| Prefix | Use for |
|---|---|
| `feat/…` | a new feature or provider |
| `fix/…` | a bug fix |
| `docs/…` | documentation only |
| `ci/…` | GitHub Actions and other CI configuration |
| `build/…` | packaging, dependencies, Docker, Windows bundle |
| `chore/…` | maintenance that fits nothing above |

Example: `feat/ollama-translator`, `fix/srt-parse-crlf`. Keep branches short-lived and rebase on `main`
when needed. See [docs/dev/branching-and-releases.md](docs/dev/branching-and-releases.md) for the full policy.

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<optional scope>): <short summary in the imperative>

<optional body: what and why>

<optional footer, e.g. "Fixes #123" or "BREAKING CHANGE: ...">
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert` (the
list `.github/workflows/pr-title.yml` accepts). Mark breaking changes with `!` after the type
(`feat(cli)!: ...`) or a `BREAKING CHANGE:` footer. Pull requests are **squash-merged** (the only merge method
enabled), so the **PR title** must be a Conventional Commit — it becomes the commit message on `main`, and a
CI check validates it.

## Licensing of contributions

EraseDub is licensed under the [Apache License 2.0](LICENSE). Contributions are accepted under the same
license ("inbound = outbound"), as described in section 5 of the license: anything you intentionally submit
is licensed under Apache-2.0 unless you explicitly state otherwise.

- Do not copy code from projects with incompatible licenses (for example GPL or non-commercial licenses)
  into this repository.
- If you add third-party code under a compatible license, keep its copyright notice and mention it in the PR.
- If a new provider depends on a model or library with restrictive terms (non-commercial weights,
  LGPL/GPL binaries, unofficial web endpoints), it must be **opt-in** and documented in
  [docs/models-and-licenses.md](docs/models-and-licenses.md).

## What must never be committed

- **Media**: videos, audio, images of real content. Demo videos live in GitHub release assets, not in Git; the
  only exception is the README's animated WebP previews and comparisons in `docs/assets/` ([docs/demo-assets.md](docs/demo-assets.md)).
  `.gitignore` already blocks common media extensions.
- **Model weights and checkpoints** (`.pth`, `.pt`, `.ckpt`, `.safetensors`, `.onnx`, ...). Providers download
  them at runtime from the model's official source.
- **Keys and credentials**: API keys, tokens, `.env` files, signed URLs. EraseDub reads keys only from
  environment variables; the config loader rejects keys in `erasedub.toml`. Gitleaks runs in pre-commit and CI.
- Personal data, including email addresses in docs or test fixtures.

If you committed a secret by accident, **revoke it first**, then tell a maintainer; rewriting history does not
un-leak a key.

## Pull requests

- **Small and focused.** One logical change per PR; split refactors from behaviour changes.
- **Tests.** New behaviour needs tests; bug fixes need a test that fails without the fix. Tests must not need
  a GPU, network or real media unless they are marked `gpu`, `network` or `slow`.
- **Docs.** Update docs and CLI help when you change user-visible behaviour.
- **Changelog.** Add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md), in the right section
  (Added / Changed / Deprecated / Removed / Fixed / Security). Pure CI or internal refactors can skip this.
- **Green checks.** `uv run pre-commit run --all-files` and `uv run pytest` pass locally.
- Link the issue the PR addresses (`Fixes #123`). For larger changes, open an issue or discussion first so we
  can agree on the approach before you invest time.

A maintainer reviews the PR; expect questions and requested changes — they are part of the process, not a
rejection.

### What a PR needs before it is merged

Nobody pushes to `main` directly; every change, including maintainers' own, goes through a pull request. A PR
is merged only when:

- all required CI checks are green;
- a maintainer (a code owner, see [`.github/CODEOWNERS`](.github/CODEOWNERS)) has approved it — an approval
  given before you push new commits no longer counts, so the latest version is always the one reviewed.
  Updating your branch with new commits from `main` can also dismiss the approval; a maintainer then
  re-approves, so expect a short second look after an update;
- every review conversation is resolved;
- the branch is up to date with `main` (update it with the button on the PR or by rebasing);
- it is merged with **squash merge**, the only method enabled.

The repository enforces these rules; the details for maintainers are in
[docs/dev/branching-and-releases.md](docs/dev/branching-and-releases.md#merging).

## Adding a provider plugin

Erasers, ASR, OCR, translators, TTS voices, layouts and GPU backends are all providers registered through
Python entry points (`erasedub.<kind>` groups). A provider can live in this repository or in a separate
package. The interfaces and a step-by-step example are in [docs/plugins.md](docs/plugins.md).

Checklist for a provider PR:

- Heavy or optional dependencies go into an extra in `pyproject.toml` and are imported lazily.
- The provider reports itself as unavailable (with a clear reason) when its dependency, GPU or key is missing,
  so `erasedub plugins` and `erasedub doctor` can show it.
- Keys come only from environment variables.
- Models are downloaded only from their official source, with the license stated in
  [docs/models-and-licenses.md](docs/models-and-licenses.md).
- Tests run without the heavy dependency (mock it), plus optional marked tests for the real thing.
