# Issue triage

Answering issues is real operational work. To keep it sustainable, maintainers batch it into **one triage
session per week** instead of reacting to every notification, and reuse the canned replies below for the most
common installation problems.

## Weekly triage session

Once a week (about an hour), go through, in this order:

1. **Security advisories** (**Security and quality** tab → Advisories): acknowledge new private reports first —
   see the targets in [SECURITY.md](../../SECURITY.md).
2. **New issues** since the last session (search `is:issue is:open created:>=YYYY-MM-DD`):
   - Is it complete? If `erasedub doctor` output, install method or logs are missing, reply with the
     "Missing information" canned reply and label `needs-info`.
   - Is it a question? Convert it to a Discussion (issue sidebar → *Convert to discussion*).
   - Is it a known install problem? Use the matching canned reply below; keep `install` and add `gpu`,
     `windows` or `macos` where it fits.
   - Is it a duplicate? Link the original, label `duplicate`, close.
   - Otherwise check the type label the form applied (`bug` / `enhancement`), fix it if wrong
     (`documentation`, `performance`, ...), and optionally add `good first issue` or `help wanted`.
3. **`needs-info` issues** with no reply for 14 days: close with a short note that they can be reopened.
4. **Open pull requests**: review or leave a note about when a review will happen. First-time contributors
   get an answer in the same week, even if it is only "thanks, will review next week".
5. **Discussions** without an answer: answer, or mark a helpful community reply as the answer.
6. If an install problem came up more than twice this week, improve the docs instead of writing a third reply.

Never ask users to post API keys, and edit out any key that appears in an issue — then tell the user to
revoke it.

## Labels

This table is the **single source** for label names: `.github/release.yml` (release-note categories),
`.github/dependabot.yml` and the issue forms must use exactly these names. Create them once the repository
exists (Issues → Labels). The issue forms apply `bug`, `enhancement` or `install` automatically.

| Label | Colour | Meaning | Release-note category |
|---|---|---|---|
| `bug` | `d73a4a` | something does not work as documented | Fixes |
| `enhancement` | `a2eeef` | new feature or improvement | Features |
| `documentation` | `0075ca` | documentation only | Docs |
| `breaking-change` | `b60205` | breaks the CLI, config, script format or plugin interface | Breaking changes |
| `dependencies` | `0366d6` | dependency updates (also set by Dependabot) | Maintenance |
| `ci` | `ededed` | GitHub Actions and other CI | Maintenance |
| `packaging` | `c5def5` | wheel and sdist build, Docker image, Windows bundle | Maintenance |
| `install` | `f9d0c4` | installation problems: drivers, CUDA, ffmpeg, Docker, Windows bundle | — |
| `gpu` | `5319e7` | NVIDIA GPU, CUDA, VRAM, `--gpu modal` | — |
| `windows` | `1d76db` | Windows-specific | — |
| `macos` | `1d76db` | macOS-specific | — |
| `performance` | `fbca04` | speed or memory use | — |
| `question` | `d876e3` | usage question (normally converted to a Discussion) | — |
| `needs-info` | `fef2c0` | waiting for the reporter | — |
| `duplicate` | `cfd3d7` | already reported elsewhere | — |
| `wontfix` | `ffffff` | out of scope or declined | — |
| `good first issue` | `7057ff` | small, well-described, good for newcomers | — |
| `help wanted` | `008672` | maintainers would welcome a PR | — |
| `skip-changelog` | `e4e669` | leave this PR out of the release notes | excluded |

A PR gets exactly one of the category labels (`breaking-change`, `enhancement`, `bug`, `documentation`,
`dependencies`, `ci`, `packaging`) or `skip-changelog`; otherwise it lands under "Other changes" in the
generated release notes.

With the GitHub CLI, for example: `gh label create "needs-info" --color fef2c0 --description "Waiting for the reporter"`.

## Canned replies

Save these as GitHub saved replies (Settings → Saved replies) so they are one click away. Adapt them to the
report — a canned reply should never be the *only* thing that shows you read the issue.

### Missing information

> Thanks for the report! To look into this we need a bit more information:
>
> - the full output of `erasedub doctor`
> - how you installed EraseDub (pip/uv, Docker, Windows bundle, source) and `erasedub version`
> - the exact command you ran and the full log as text
>
> Please remove any API keys or tokens before pasting. We will close this in 14 days if we do not hear back,
> but you can always reopen it.

### No NVIDIA GPU

> Erasing burned-in text needs an NVIDIA GPU with CUDA. Without one, EraseDub skips the erase step and says so,
> but everything else (speech recognition, translation, voices, subtitles, muxing) still runs — you get a
> translated and dubbed video with the original on-screen text still visible.
>
> Options if you need erasing:
>
> - run only the erase step on [Modal](https://modal.com) with your own account: add the `modal` extra (`uv sync --extra full --extra modal`),
>   run `modal setup` once, then `erasedub render VIDEO --gpu modal` (Modal bills your account);
> - rent a GPU machine (for example on vast.ai or RunPod) and run EraseDub there — see
>   [docs/gpu-rental.md](https://github.com/sting11k/erasedub/blob/main/docs/gpu-rental.md);
> - erase on this machine with the opt-in `lama` eraser: add the `lama` extra (`uv sync --extra full --extra lama`), then set
>   `[erase] provider = "lama"` in `erasedub.toml`. It runs on the CPU or the Apple GPU (MPS); it is much slower, prone to
>   flicker, and flattens repeating patterned backgrounds
>   ([docs/erasers.md](https://github.com/sting11k/erasedub/blob/main/docs/erasers.md));
> - or render without erasing on purpose: `erasedub render VIDEO --no-erase`.
>
> Apple Silicon and AMD GPUs cannot run the GPU erasers (STTN, ProPainter).

### CUDA not available / driver mismatch

> EraseDub cannot use your NVIDIA GPU. First find out which PyTorch you have:
>
> ```bash
> python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
> ```
>
> 1. **`torch.version.cuda` is `None`** — you have the **CPU-only** PyTorch. This is the most common cause on
>    Windows: `pip install torch` from PyPI installs a CPU-only build there. Reinstall the CUDA build of the exact
>    version WhisperX requires (a newer torch breaks it), in the same environment:
>
>    ```powershell
>    uv pip install --force-reinstall --no-deps torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
>    ```
>
>    (with a pip venv, use `pip install` instead of `uv pip install`). Details are in
>    [docs/troubleshooting.md](https://github.com/sting11k/erasedub/blob/main/docs/troubleshooting.md#doctor-says-torch-is-a-cpu-only-build-typical-on-windows).
>    Or use the Windows bundle, which ships a CUDA build.
> 2. **`CUDA driver version is insufficient`**, or `is_available()` is `False` although `torch.version.cuda` shows
>    a version — your driver is too old for that CUDA version. Run `nvidia-smi`: the "CUDA Version" in the top
>    right is the *newest* CUDA your driver supports. If it is lower than `torch.version.cuda`, update the NVIDIA
>    driver (recommended), or reinstall the same torch 2.8.0 / torchaudio 2.8.0 / torchvision 0.23.0 from an
>    older CUDA index (for example `https://download.pytorch.org/whl/cu126`). For example, the cu128 build needs
>    driver R570 or newer.
> 3. **`no kernel image is available for execution on the device`** — this is not a driver problem: the
>    PyTorch build does not contain code for your GPU's generation (compute capability). Your GPU is either
>    too old for that build or newer than it. Install a PyTorch version/CUDA variant that supports your GPU
>    (check the pytorch.org release notes); updating the driver does not help.
>
> You do not need to install the CUDA Toolkit separately: on Linux the PyPI wheels, and on every OS the builds
> from the pytorch.org CUDA indexes, ship the CUDA libraries they need. In Docker, also make sure the NVIDIA
> Container Toolkit is installed and you pass `--gpus all`.

### ffmpeg without libass

> Your `ffmpeg` was built without **libass**, which is needed to burn subtitles into the video. EraseDub still
> writes the video, but adds the subtitles as a soft subtitle track (turn it on in your player) and prints a notice
> saying so; `erasedub doctor` shows `libass=NO` on the ffmpeg row. Check which filters your ffmpeg has:
>
> - Linux/macOS: `ffmpeg -hide_banner -filters | grep -E " ass "`
> - Windows: `ffmpeg -hide_banner -filters | findstr /C:" ass "`
>
> If nothing is printed, libass is missing. To get burned-in subtitles:
>
> - Linux: install your distribution's `ffmpeg` package (Debian and Ubuntu builds include libass), or
>   use the Docker image, which ships a suitable ffmpeg.
> - Windows: use the ffmpeg included in the Windows bundle, or a full static build that lists `--enable-libass`
>   in `ffmpeg -version`.
> - macOS: Homebrew's plain `ffmpeg` formula is built **without** libass. Install `brew install ffmpeg-full`
>   instead; it is keg-only (not linked into your `PATH`), so put it first yourself, for example
>   `export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"` in `~/.zshrc`, then open a new terminal.
>
> Make sure the right `ffmpeg` comes first on your `PATH`: `which ffmpeg` on Linux/macOS, `where.exe ffmpeg`
> (or `Get-Command ffmpeg`) in PowerShell. To leave subtitles out entirely, render with `--no-subs`.

### Windows long paths

> This error (`FileNotFoundError` or `[WinError 206]`/`[WinError 3]` on a very long path, often during
> `pip install` or when writing into the work directory) is caused by Windows' 260-character path limit.
>
> - Move your project and videos to a short path such as `C:\ed\`, or pass `--workdir C:\ed\work`.
> - Or enable long paths (needs administrator rights, then sign out and in again): in an elevated PowerShell run
>   `New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force`,
>   or enable the Group Policy *Computer Configuration → Administrative Templates → System → Filesystem →
>   Enable Win32 long paths*.

### Edge-TTS or Google Translate rate limits

> The default voice (Edge-TTS) and translator (Google Translate free) use free online endpoints that are not
> official APIs. They are rate-limited and can change or break without notice — errors such as HTTP 429,
> `403`, `NoAudioReceived` or empty translations usually mean you hit a limit or the service changed.
>
> - Wait a few minutes and retry, and avoid processing many videos in parallel.
> - Update EraseDub and the underlying packages: `git pull` in the clone, then `uv sync --extra full` (or
>   `pip install -U -e ".[full]" edge-tts deep-translator`).
> - For heavier use, choose another provider in the `[translate]` and `[tts]` sections of `erasedub.toml`:
>   translators `gemini`, `openai`, `claude` (your own key) or `ollama` (local), voice `elevenlabs` (your own
>   key). Run `erasedub plugins` to see what is available. Keys are read only from environment variables
>   such as `GEMINI_API_KEY`, never from the config file.

### Out of GPU memory (VRAM)

> `CUDA out of memory` means the erase step needs more GPU memory than is free.
>
> - Close other programs that use the GPU (games, browsers with hardware acceleration, other AI tools) and
>   check free memory with `nvidia-smi`.
> - Try a shorter or lower-resolution clip first to confirm the setup works.
> - If your GPU has little memory, run the erase step on a bigger GPU with `--gpu modal` or on a rented GPU,
>   or skip it with `--no-erase`.
>
> Please include your GPU model, `nvidia-smi` output and the video resolution so we can improve the defaults.
