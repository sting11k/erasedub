# Getting help

EraseDub is maintained by volunteers. Issues are triaged about once a week, so please be
patient and include enough detail for someone to reproduce the problem without a back-and-forth.

## Where to ask

| You want to... | Go to |
|---|---|
| ask a question, get help with usage or configuration, share results | [GitHub Discussions](https://github.com/EraseDub/erasedub/discussions) |
| report a bug (something that used to work or clearly should) | [Bug report](https://github.com/EraseDub/erasedub/issues/new?template=bug_report.yml) |
| report that installation fails | [Installation problem](https://github.com/EraseDub/erasedub/issues/new?template=install_problem.yml) |
| propose a feature or a new provider | [Feature request](https://github.com/EraseDub/erasedub/issues/new?template=feature_request.yml) |
| report a security vulnerability | privately, see [SECURITY.md](SECURITY.md) — never in a public issue |

Before opening an issue, search existing issues and discussions — your problem may already have an answer.

## What to include

1. The output of **`erasedub doctor`**. It checks Python, ffmpeg (with libass and libx264), the NVIDIA GPU
   and driver, which providers are available, and which API keys are *present* (it never prints their values).
2. How you installed EraseDub: pip/uv, Docker, Windows bundle, or from source — and the version
   (`erasedub version`).
3. Your OS, and GPU model if you have one.
4. The exact command you ran (or the WebUI options you chose) and your `erasedub.toml` if you use one.
5. The full log or traceback, as text rather than a screenshot.

**Remove secrets and personal data before posting.** Check logs and config for API keys, tokens and signed
URLs, and replace your user name in file paths with `<user>` (`erasedub doctor` and tracebacks print absolute
paths such as `C:\Users\<user>\...`). If you posted a key by accident, revoke it with the provider right
away — editing the post is not enough.

Please do not attach videos you do not have the rights to share. A few seconds of CC0 or self-made footage
that reproduces the problem is ideal.
