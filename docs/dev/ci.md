# CI and release automation

Everything lives in `.github/`. The workflows are written to be safe by default:

- Every third-party action is pinned to a full commit SHA, with the tag in a comment.
  Dependabot bumps both the SHA and the comment.
- Top-level `permissions: {}`. Each job asks for only what it needs.
- Write scopes appear in only two places: the release jobs, and CodeQL's result upload
  (`security-events: write`).
- `actions/checkout` always runs with `persist-credentials: false`.
- No `pull_request_target`. Workflows triggered by fork PRs get a read-only token and no secrets.
- Every release job only runs in the upstream repository (`github.repository == 'sting11k/erasedub'`),
  so a fork that pushes a `v*` tag publishes nothing.
- Release jobs never use a shared cache, and they pin uv by checksum and the build backend by version.

## Workflows

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | push to `main`, PRs, manual | Jobs: `pre-commit` (all hooks, including gitleaks, zizmor and actionlint), `mypy` (strict), `uv.lock up to date` (`uv lock --check`), the `test` matrix, `test (webui extra)` (installs the `webui` extra, runs `pytest -m webui` with `ERASEDUB_REQUIRE_WEBUI=1` so a missing Gradio fails instead of skipping, then mypy again), and `build`. `build` makes the sdist and wheel, runs `twine check --strict`, runs the wheel's CLI and uploads `dist/`. |
| `release.yml` | tag `v*` | The only tag-triggered workflow. It creates and publishes the GitHub Release; see [Making a release](#making-a-release). |
| `windows-bundle.yml` | manual, or called by `release.yml` | Builds the portable Windows bundle. A manual run only produces a workflow artifact. When `release.yml` calls it with `upload: true`, it attests the bundle and uploads it to the draft release; it never creates a release itself. A called workflow only gets the permissions its caller grants, so the calling job in `release.yml` grants exactly `contents: write`, `id-token: write` and `attestations: write`. Keep the two files in sync if either changes. |
| `docker.yml` | PRs touching `Dockerfile`, `.dockerignore`, `pyproject.toml`, `uv.lock`, `packaging/build-constraints.txt`, `src/**`, `compose.yaml`; manual | Builds the `runtime` target of `./Dockerfile` (linux/amd64) and never pushes. It first frees runner disk space, because the image contains PyTorch. |
| `links.yml` | push to `main`, PRs, weekly, manual | `links (offline)` (push, PRs, manual) checks relative links, `#anchors` and links to this repository's own files in README.md, README.vi.md, README.zh-CN.md and `docs/`. `links (external)` (weekly, manual) also fetches every external URL; it never runs on PRs, so a flaky website cannot block a merge. See [Checking links](#checking-links). |
| `codeql.yml` | push/PR to `main`, weekly | CodeQL `security-extended` for `python` and `actions` (the workflows themselves). |
| `dependency-review.yml` | PRs | Fails if a PR adds a dependency with a known vulnerability of moderate severity or higher. |
| `pr-title.yml` | PRs | The PR title must follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`), because squash-merge uses it as the title of the commit on `main`. |

Test matrix: Ubuntu, Windows and macOS × Python 3.11, 3.12 and 3.13, plus Python 3.14 on Ubuntu
as a non-blocking job (`continue-on-error`). Some optional extras (e.g. whisperx) do not support 3.14 yet,
but the core install should. CI installs only the core dependencies and the `dev` group
(`uv sync --locked`). Heavy extras (torch, whisperx, gradio) are never installed in CI. Tests that need
them, a GPU or the network must use the `gpu` / `network` / `slow` markers, which pytest deselects by default.

Other config:

- `.github/dependabot.yml`: weekly, grouped updates for `uv`, `github-actions` and `docker`, with a 7-day cooldown.
- `.github/release.yml`: release-note categories, chosen by PR label (see [Labels](#labels)).
- `.github/CODEOWNERS`: the maintainer, `@sting11k` (see below).

The uv version is pinned in each workflow (`UV_VERSION`), because a newer uv can rewrite `uv.lock`.
Bump it in all workflows at once, in its own PR. In `release.yml`, also update `UV_CHECKSUM`: the
sha256 of `uv-x86_64-unknown-linux-gnu.tar.gz` for that version, shown on its GitHub release page.

## Checking workflows locally

```bash
uv run pre-commit run --all-files                 # includes zizmor and actionlint on .github/workflows
uvx zizmor@1.30.1 --persona=pedantic .github/     # stricter audit
uvx check-jsonschema@0.38.2 --builtin-schema vendor.github-workflows .github/workflows/*.yml
uvx check-jsonschema@0.38.2 --builtin-schema vendor.dependabot .github/dependabot.yml
```

[actionlint](https://github.com/rhysd/actionlint) runs as a pre-commit hook. Like gitleaks, pre-commit builds it
from the pinned source with a Go toolchain it bootstraps itself. actionlint also runs
[shellcheck](https://github.com/koalaman/shellcheck) on every `run:` script, **but only if `shellcheck` is on your
`PATH`**; without it those checks are silently skipped. CI always has shellcheck (it is preinstalled on
`ubuntu-latest`). To get the same result locally, install shellcheck from your package manager or the official
release (`shellcheck-v0.11.0.<os>.<arch>.tar.gz` at
[github.com/koalaman/shellcheck/releases](https://github.com/koalaman/shellcheck/releases/tag/v0.11.0); check the
download against the sha256 digest shown next to the asset). If the Go bootstrap fails, use the official actionlint
release binary with the `actionlint-system` hook id, the same way CONTRIBUTING.md describes for `gitleaks-system`.

## Checking links

[lychee](https://github.com/lycheeverse/lychee) checks README.md, README.vi.md, README.zh-CN.md and `docs/`, using
`lychee.toml`. Links to this repository's own files (`https://github.com/sting11k/erasedub/blob|tree|raw/main/...`,
which the READMEs use) are mapped to the local checkout with `--remap`. So they are
checked offline, anchors included, against the files of the commit being checked. Links inside HTML comments and
code blocks are not checked.

`lychee.toml` excludes `ghcr.io/sting11k/erasedub`: the image page exists once the first release tag has pushed the image.
Delete that line after the first release ([release-checklist.md](release-checklist.md)). Do not link to line numbers of repository files
(`.../blob/main/docs/cli.md#L10`): GitHub accepts them, but the offline check cannot verify them and reports them
as broken.

lychee has no pre-commit hook we can use: its `lychee` hook installs cargo-binstall with `curl | bash`, and
`lychee-docker` pulls an image by tag. Install the official release binary instead, pinned and verified:

| OS | Asset of [lychee-v0.24.2](https://github.com/lycheeverse/lychee/releases/tag/lychee-v0.24.2) | sha256 |
|---|---|---|
| Linux x86_64 | `lychee-x86_64-unknown-linux-gnu.tar.gz` | `1f4e0ef7f6554a6ed33dd7ac144fb2e1bbed98598e7af973042fc5cd43951c9a` |
| Linux arm64 | `lychee-aarch64-unknown-linux-gnu.tar.gz` | `91a7bd65685da41b90ccb9bc867a3d649a7818042dae04ff405e55a25bddee4c` |
| macOS Apple silicon | `lychee-aarch64-apple-darwin.tar.gz` | `c9d3740ea2d891854d37116c9fba840f37b6e7c89d330e7db84ac333631c4977` |
| macOS Intel | `lychee-x86_64-apple-darwin.tar.gz` | `887503a9cff667d322b8d0892b40bf49976eb9507af8483220a3706cdad55978` |
| Windows x64 | `lychee-x86_64-pc-windows-msvc.zip` | `32975d1493ee1a975d6bb41e4fb56fe419cb442ded628bb772ba2e614acfacad` |

Set `asset` and `sha` to your row of the table (the example is macOS Apple silicon), then run the block in a
scratch directory. `shasum -c` must print `OK`; on Linux without `shasum`, use `sha256sum -c -` instead. The last
line installs into `~/.local/bin`, which must be on your `PATH` (any other directory on your `PATH` works too).

```bash
asset=lychee-aarch64-apple-darwin.tar.gz
sha=c9d3740ea2d891854d37116c9fba840f37b6e7c89d330e7db84ac333631c4977
curl --proto '=https' -sSfLO "https://github.com/lycheeverse/lychee/releases/download/lychee-v0.24.2/$asset"
echo "$sha  $asset" | shasum -a 256 -c -
mkdir -p ~/.local/bin
tar -xzf "$asset" && mv "${asset%.tar.gz}/lychee" ~/.local/bin/
```

On Windows, download the zip, compare `(Get-FileHash lychee-x86_64-pc-windows-msvc.zip).Hash` with the table and
unzip `lychee.exe` onto your `PATH`.

Then run it from the repository root, in bash or zsh on macOS or Linux (on Windows, use WSL).
`${PWD// /%20}` URL-encodes spaces in the checkout path, which `--remap` cannot take literally.

The first command is the same as the required `links (offline)` check: relative links, anchors and this
repository's own files.

```bash
lychee --config lychee.toml --offline \
  --remap "^https://github\.com/sting11k/erasedub/(?:blob|tree|raw)/main/ file://${PWD// /%20}/" \
  README.md README.vi.md README.zh-CN.md docs
```

The second is the same as the weekly `links (external)` check: it also fetches external URLs. Optionally
`export GITHUB_TOKEN=...` (any read-only token) first, to avoid github.com rate limits.

```bash
lychee --config lychee.toml \
  --remap "^https://github\.com/sting11k/erasedub/(?:blob|tree|raw)/main/ file://${PWD// /%20}/" \
  README.md README.vi.md README.zh-CN.md docs
```

When you bump lychee, update `LYCHEE_VERSION` and `LYCHEE_SHA256` in `links.yml` and this table together.

The gitleaks hook must run locally too, because CI only sees a secret after it has been pushed.
pre-commit bootstraps Go for it automatically. If that fails, CONTRIBUTING.md explains how to use
the official gitleaks release binary with the `gitleaks-system` hook id instead.

## Merge policy

The merge rules (squash only, 1 code-owner approval, conversations resolved, branch up to date, review bypass on
own PRs only) are set out in [branching-and-releases.md](branching-and-releases.md#merging). There is no merge
queue, so no workflow listens to `merge_group`.

## Labels

These labels are canonical. Triage, Dependabot and the release notes all use the same names:

`bug`, `enhancement`, `documentation`, `breaking-change`, `dependencies`, `ci`, `packaging`, `install`,
`gpu`, `windows`, `macos`, `performance`, `question`, `needs-info`, `duplicate`, `wontfix`,
`good first issue`, `help wanted`, `skip-changelog`.

Release-note categories:

| Category | Labels |
|---|---|
| Breaking changes | `breaking-change` |
| Features | `enhancement` |
| Fixes | `bug` |
| Docs | `documentation` |
| Maintenance | `dependencies`, `ci`, `packaging` |
| Other changes | any other label |

`skip-changelog` keeps a PR out of the notes.

## Repository settings to turn on

None of this can live in the repo. The repository owner (`sting11k`) sets it in the GitHub web UI or with `gh api`.

1. **General**:
   - Pull Requests: allow **squash merging** only (untick merge commits and rebase merging), with the default
     commit message **Pull request title**. Otherwise a one-commit PR lands with its commit's own message instead
     of the PR title that `semantic PR title` checked;
   - enable Issues and **Discussions**;
   - create the [labels](#labels);
   - **About** (gear icon on the repository page):
     - description (304 of 350 characters): *"Open-source AI video translation and dubbing that also removes
       hardcoded (burned-in) subtitles: hardsub removal, speech-to-text (Whisper), subtitle translation (Google
       Translate or LLM), TTS dubbing and new subtitles, locally in one tool. 去除硬字幕 · 视频翻译 · AI 配音 |
       Xoá phụ đề cứng, dịch và lồng tiếng video."*;
     - website: the `docs/index.md` link until a docs site exists;
     - topics (GitHub allows 20): `video-translation`, `ai-video-translation`, `translate-video`,
       `video-dubbing`, `ai-dubbing`, `dubbing`, `automatic-dubbing`, `video-localization`, `localization`,
       `subtitle-translation`, `subtitles`, `subtitle-removal`, `hardsub`, `video-inpainting`,
       `speech-to-text`, `text-to-speech`, `tts`, `asr`, `whisper`, `gradio`. Most are the topics of the
       projects in the README comparison (checked 2026-09-27), kept only where EraseDub does the thing: not
       `voice-cloning`, `youtube` or `demucs` until those features exist;
     - untick Packages and Deployments while they are empty;
   - **Social preview** (Settings → General → Social preview): upload `docs/assets/social-preview.png`.
2. **Two rulesets for the default branch** (Settings → Rules → Rulesets → New branch ruleset), both with
   enforcement *Active* and target *Default branch*. GitHub applies both; the stricter rule wins. Why two:
   [branching-and-releases.md](branching-and-releases.md#merging).
   - **`main protection`**, with an **empty bypass list** (nobody skips these rules):
     - **Require a pull request before merging** with required approvals **0**, **require conversation
       resolution before merging**, allowed merge methods **Squash** only;
     - **Require status checks to pass**, with **require branches to be up to date before merging**, source
       *GitHub Actions* for each check. Required checks:
       - `pre-commit`, `mypy`, `uv.lock up to date`, `build sdist + wheel`;
       - `test (ubuntu-latest, py3.11)`, `test (ubuntu-latest, py3.12)`, `test (ubuntu-latest, py3.13)`, and
         the same three for `windows-latest` and `macos-latest`;
       - `test (webui extra)`;
       - `links (offline)`.
       - Do not require `test (ubuntu-latest, py3.14)`: it is non-blocking. Do not require `links (external)`
         either: it only runs weekly and on demand, never on PRs.
       - `semantic PR title`, `dependency review`, `analyze (python)` and `analyze (actions)` (CodeQL) run on
         every PR too and may be added to the list.
     - **Block force pushes**, **restrict deletions**, **require linear history**.
   - **`main review`**:
     - **Require a pull request before merging** with required approvals **1**, **dismiss stale pull request
       approvals when new commits are pushed** and **require review from Code Owners**;
     - **bypass list**: the *Repository admin* role, mode **For pull requests only**. It lets a maintainer
       merge their own PR without an approval; everything in `main protection` still applies. Using it only
       on one's own PRs is a maintainer rule that GitHub cannot enforce.
3. **Tag ruleset**: only maintainers may create, update or delete `v*` tags.
4. **Environments** (Settings → Environments):
   - `ghcr`: required reviewer = the maintainer; deployment tags limited to `v*`.
     Leave "prevent self-review" off while there is a single maintainer.
5. **Actions settings**:
   - workflow permissions = *Read repository contents*;
   - do not allow Actions to create or approve PRs;
   - require approval for workflows from outside contributors.
6. **GHCR**: after the first image push, open the `erasedub` package (your profile → Packages) and do three things:
   - set visibility to **Public**;
   - link it to the repository;
   - give the repository's Actions *write* access.
7. **Security** (Settings → "Security and quality" section → **Advanced Security**):
   - enable Dependency graph, Dependabot alerts and security updates, secret scanning and push protection;
   - enable CodeQL (it uses `codeql.yml`, so pick "advanced", not "default" setup);
   - enable **Private vulnerability reporting**.
8. **CODEOWNERS**: lists the maintainers by username (`@sting11k`); a new maintainer needs write access and a
   line in `.github/CODEOWNERS`. `*` covers the whole repository, so every PR needs a maintainer's approval.
9. **Moderation**: the repository is personal, so GitHub's "Report to repository admins" is not available
    (it exists only for repositories owned by an organization). [CODE_OF_CONDUCT.md](../../CODE_OF_CONDUCT.md)
    therefore sends reports to GitHub. Settings → Moderation options → **Interaction limits** can restrict
    comments temporarily during an incident.
10. **Notifications**: every maintainer watches the repository with **All Activity** or **Custom → Security
    alerts**, otherwise private vulnerability reports send them no notification.

## Making a release

The steps are in [the release process](branching-and-releases.md#release-process); the checks to tick before
tagging are in [release-checklist.md](release-checklist.md). This section only shows what `release.yml` runs once
the tag is pushed:

```text
tag == version -> test (ubuntu, py3.12) -> build dists + attest -> draft release (dists attached)
    -> ghcr (approve)                               --+
    -> windows-bundle (attests, uploads to draft)   --+-> publish (undraft) the release
```

EraseDub is not published to PyPI. The wheel and sdist are attached to the GitHub Release only; users install from
source, the Docker image or the Windows bundle.

`ghcr` pushes `X.Y.Z`, `X.Y` and `latest`. Pre-releases such as `v0.1.0rc1` get only their exact image tag, and
their GitHub Release is marked as a pre-release.

Check a downloaded dist or Windows bundle with `gh attestation verify <file> --repo sting11k/erasedub`
(for example `erasedub-0.1.0-py3-none-any.whl`).
