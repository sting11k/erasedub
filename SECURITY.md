# Security Policy

## Supported versions

Security fixes are made on `main` and shipped in the next release; only the latest
release receives fixes.

| Version | Supported |
|---|---|
| latest `0.x` release and `main` | yes |
| older releases | no — please upgrade |

## Reporting a vulnerability

**Please do not open a public issue, discussion or pull request for security problems.**

Report privately through GitHub's private vulnerability reporting — the only reporting channel; the project has
no email contact for security reports:

1. Go to the [**Security and quality** tab](https://github.com/sting11k/erasedub/security) of the repository.
2. Click **Report a vulnerability**
   ([direct link](https://github.com/sting11k/erasedub/security/advisories/new)).
3. Describe the problem, the affected version or commit, and steps to reproduce. A minimal proof of concept
   helps. Do not include real API keys or private media.

### What to expect

EraseDub is maintained by volunteers, so these are targets, not guarantees:

- **Acknowledgement** within 7 days.
- **Initial assessment** (confirmed or not, severity) within 14 days.
- **Fix or mitigation** for confirmed issues as soon as practical, aiming for 90 days at most. We will agree
  a disclosure date with you and credit you in the advisory unless you prefer to stay anonymous.

## Scope

In scope — problems in this repository's code, packaging and workflows, for example:

- API keys leaking into logs, output files, error messages, crash reports or the WebUI.
- Keys being read from anywhere other than environment variables (or the provider's own standard credential
  file, such as `~/.modal.toml`). By design, the config loader rejects keys in `erasedub.toml`.
- Model or binary downloads from anything other than the official source, missing integrity checks, or
  unsafe deserialization of downloaded files.
- Path traversal or command injection through file names, subtitles/scripts or config values
  (EraseDub calls `ffmpeg` and other tools as subprocesses).
- The WebUI being reachable from the network by default (it binds to `127.0.0.1` unless you pass `--host`).
- Weaknesses in the GitHub Actions workflows, Docker image or Windows bundle build.

Out of scope:

- Vulnerabilities in third-party dependencies, models or services themselves (PyTorch, ffmpeg, Gradio,
  translation/TTS services, ...). Report those upstream; tell us if EraseDub needs to pin or update something.
- Exposing the WebUI on a public interface with `--host 0.0.0.0` without your own authentication or proxy.
- Issues that need an attacker who already controls your machine or your environment variables.

## Handling your own keys

EraseDub reads keys such as `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`,
`MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` and `HF_TOKEN` from the environment only. Never paste them into issues,
logs or config files. If you exposed a key, revoke it with the provider immediately.
