# Branching and releases

EraseDub uses **trunk-based development**: `main` is always releasable, and all work lands through short-lived
branches and pull requests.

## Branches

- **`main`** is protected by a ruleset (the merge rules are under [Merging](#merging)): changes only
  through pull requests; no direct pushes, no force-pushes, no deletion.
- **Topic branches** are named `feat/…`, `fix/…`, `docs/…`, `ci/…`, `build/…` or `chore/…` (see
  [CONTRIBUTING.md](../../CONTRIBUTING.md)). They live for days, not weeks. Rebase on `main` rather than
  merging `main` into them.
- There are no long-lived `develop` or `release/*` branches. If an old release ever needs a patch, a
  `release/X.Y` branch is cut from its tag at that moment and fixes are cherry-picked from `main`.

## Merging

A pull request is merged only when all of the following hold. Two rulesets on `main` enforce them (settings in
[ci.md](ci.md#repository-settings-to-turn-on-once-the-erasedub-org-exists)):

- **required CI checks are green** (the list is in [ci.md](ci.md#repository-settings-to-turn-on-once-the-erasedub-org-exists));
- **1 approving review from a code owner** (`.github/CODEOWNERS` assigns every path to the maintainers);
  approvals are **dismissed when new commits are pushed** (updating the branch from `main` can dismiss them
  too), so the reviewed version is the merged version;
- **all review conversations are resolved**;
- **the branch is up to date with `main`**;
- **squash merge only.** Rebase merges and merge commits are disabled. The squash commit message is set to
  "Pull request title", so the PR title becomes the commit on `main`; it must be a
  [Conventional Commit](https://www.conventionalcommits.org/en/v1.0.0/) (`feat(cli): add --dry-run to render`),
  and `.github/workflows/pr-title.yml` checks it. GitHub's default would use the commit's own message for a
  one-commit PR, bypassing that check.

**Bypassing the review — own PRs only.** GitHub never lets the author of a PR approve it, so while there is a
single maintainer their own PRs could never be merged. The rules are therefore split over two rulesets:

- **main protection** — pull request required, required checks green with the branch up to date,
  conversations resolved, squash only, no force pushes or deletions. It has **no bypass list**: these rules
  hold for everyone, maintainers included, and nobody can push to `main` directly.
- **main review** — 1 approval, review from Code Owners, stale approvals dismissed. Repository admins are on
  its bypass list in "for pull requests only" mode, so a maintainer can merge a PR without an approval.

A maintainer uses that bypass **only on their own PRs**, never on someone else's: contributors' PRs always get
a real approving review. GitHub cannot enforce this limit — it does not know whose PR it is — so it is a rule
the maintainers keep themselves. CI, conversation resolution and the up-to-date branch are enforced by GitHub
for everyone.

Delete the branch after merging.

## Versioning

Versions follow [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html) with
[PEP 440](https://peps.python.org/pep-0440/) spelling in Python metadata. Git tags carry a `v` prefix.

| Stage | Python version (`__version__`) | Git tag |
|---|---|---|
| development on `main` | `0.2.0.dev0` | none |
| release candidate | `0.2.0rc1` | `v0.2.0rc1` |
| release | `0.2.0` | `v0.2.0` |
| bug-fix release | `0.2.1` | `v0.2.1` |

The public API covered by SemVer is: the CLI (commands, flags, exit codes), the `erasedub.toml` config format,
the work-directory script files produced by `prepare` (`work/<stem>/script.<lang>.json`,
`script.<lang>.srt` per target language and the shared `regions.json`), and the provider plugin interfaces in
[docs/plugins.md](../plugins.md). Internal modules are not.

### Rules while in 0.x

- `0.MINOR.0` may contain breaking changes. Every breaking change is listed in the changelog with a
  **Breaking:** prefix and a migration note.
- `0.MINOR.PATCH` releases contain only bug fixes and security fixes — never breaking changes.
- Deprecate before removing where it is cheap: keep the old flag or config key working with a warning for at
  least one minor release.
- `1.0.0` is released once the CLI, config and plugin interfaces have been stable for a few minor releases.

## Release process

This section owns the steps. The checks to tick before tagging are in [release-checklist.md](release-checklist.md);
what `release.yml` runs after the tag is shown in [ci.md](ci.md#making-a-release).

1. Make sure `main` is green in CI and the milestone's issues are closed or moved.
2. On a branch `chore/release-X.Y.Z`:
   - set `__version__ = "X.Y.Z"` in `src/erasedub/__init__.py`;
   - in `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add a fresh empty
     `## [Unreleased]` above it, and update the compare links at the bottom;
   - update `version` (and add `date-released`) in `CITATION.cff`;
   - check that docs mention no unreleased flags as available.
3. Open a PR titled `chore: release X.Y.Z`, tick every item of [release-checklist.md](release-checklist.md) in
   its description, wait for green checks and squash-merge it.
4. Tag the merge commit on `main` with a signed tag and push it (maintainers only; a tag ruleset restricts
   `v*` tags to maintainers). **The tag must equal `__version__`** (`vX.Y.Z` ↔ `X.Y.Z`), otherwise
   `release.yml` fails before publishing anything.

   ```bash
   git switch main && git pull --ff-only
   git tag -s vX.Y.Z -m "EraseDub X.Y.Z"
   git push origin vX.Y.Z
   ```

5. The tag triggers **`release.yml`**, the only workflow that runs on `v*` tags and the only one that creates or
   publishes the GitHub Release. It tests, builds and attests the dists, creates a **draft** release with notes
   **generated automatically from PR labels** (see the label table in [issue-triage.md](issue-triage.md)),
   pushes the image to
   `ghcr.io/erasedub/erasedub`, attaches the portable Windows bundle with `SHA256SUMS.txt`, and publishes
   (undrafts) the release only when all of that succeeded. Approve the `ghcr` deployment in the Actions tab when
   asked. EraseDub is not published to PyPI. `docker.yml` and `windows-bundle.yml` never publish on their own.

   If a job fails or a deployment is rejected, the release stays a draft and nothing after that job runs:
   - **Failed before `ghcr` pushed, or `ghcr` was rejected:** nothing was published. Fix the cause, delete the
     draft release and the tag, then tag again. This is the only case in which a tag is reused.
   - **`ghcr` pushed but a later job failed:** re-run the failed jobs from the Actions tab; if that cannot work,
     release a new patch version.
6. Edit the published release body: add a link to the matching `CHANGELOG.md` section (the changelog is still
   maintained by hand and is the authoritative list of changes), the demo assets, and notes on hardware and
   language support.
7. Verify: a fresh `git clone` at the tag, `uv sync --extra full`, `erasedub doctor`, `docker run` the new image,
   and download the Windows bundle from the release page.
8. Open a PR that sets `__version__` on `main` to the next development version (`X.(Y+1).0.dev0`).

If a published release is broken, do not delete or reuse the tag: mark the GitHub Release as broken in its
notes, fix on `main`, and release a new patch version.
