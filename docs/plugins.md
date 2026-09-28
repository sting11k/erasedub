# Writing a provider plugin

Every step of EraseDub is done by a *provider*. You can publish your own provider — a different eraser, translator,
voice, OCR engine, subtitle layout or GPU backend — as a normal Python package. Once it is installed next to
EraseDub, users select it by name in `erasedub.toml`. No change to EraseDub is needed.

> The plugin API is **version 1**. While EraseDub is `0.x`, details may still change between minor releases. When
> they do, the plugin API version is bumped and EraseDub refuses plugins written for another version with a clear message
> (exit code 3), instead of failing in the middle of a run.

## Import only from `erasedub.plugin`

Everything a plugin needs — base classes, data models, errors, `RunContext`, `ProviderOptions`,
`PLUGIN_API_VERSION` — is re-exported from **`erasedub.plugin`**. That module is the only supported import path;
names imported from anywhere else in `erasedub` may move between releases.

## How providers are found

EraseDub looks up Python [entry points](https://packaging.python.org/en/latest/specifications/entry-points/) in one
group per provider kind:

| Kind | Entry-point group | Base class | Selected by |
|---|---|---|---|
| eraser | `erasedub.eraser` | `TextEraser` | `erase.provider` |
| asr | `erasedub.asr` | `Transcriber` | `asr.provider` |
| ocr | `erasedub.ocr` | `TextDetector` | `ocr.provider` |
| translator | `erasedub.translator` | `Translator` | `translate.provider` |
| tts | `erasedub.tts` | `SpeechSynthesizer` | `tts.provider` |
| layout | `erasedub.layout` | `SubtitleLayout` | `subtitles.layout` |
| gpu | `erasedub.gpu` | `GpuBackend` | `erase.gpu` / `--gpu` |

The entry-point **name** is the provider name users type; the **value** points to your class. The class must be a
subclass of the base class for that group and declare `api_version = 1`, or loading fails with a clear error. If two
packages register the same name in the same group, the built-in provider wins, so a plugin can never silently replace
one; between two plugins, the first one found wins. Both cases log a warning. Pick a distinctive name (for example
`deepl`, not `google`).

`erasedub plugins` lists every registered provider and whether it can run on this machine. A plugin that fails to
import is reported there as "failed to load" instead of breaking the command.

## Minimal example: a translator

Package layout:

```text
erasedub-shout/
├── pyproject.toml
└── src/erasedub_shout/__init__.py
```

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "erasedub-shout"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["erasedub>=0.1,<0.2"]

[project.entry-points."erasedub.translator"]
shout = "erasedub_shout:ShoutTranslator"
```

`src/erasedub_shout/__init__.py`:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from erasedub.plugin import (
    ProviderOptions,
    RunContext,
    ScriptLine,
    TranslationStyle,
    Translator,
)


class ShoutTranslator(Translator):
    """Toy translator: upper-cases every line. Replace the body with a real API call."""

    name: ClassVar[str] = "shout"
    api_version: ClassVar[int] = 1
    summary: ClassVar[str] = "upper-cases text (example plugin)"

    class Options(ProviderOptions):
        exclamation: bool = False

    options: Options  # tells type checkers that self.options is this Options model

    def translate(
        self,
        lines: Sequence[ScriptLine],
        *,
        target: str,
        source: str | None = None,
        style: TranslationStyle = "faithful",
        glossary: Mapping[str, str] | None = None,
        ctx: RunContext,
    ) -> list[str]:
        suffix = "!" if self.options.exclamation else ""
        out: list[str] = []
        for i, line in enumerate(lines):
            ctx.raise_if_cancelled()
            out.append(line.text.upper() + suffix)
            ctx.progress((i + 1) / len(lines))
        return out
```

Install EraseDub from source at the release your plugin targets, then your plugin, in the same environment:

```bash
git clone --branch v0.1.0 https://github.com/EraseDub/erasedub
pip install -e ./erasedub
pip install -e ./erasedub-shout
erasedub plugins          # shout is listed under translator
```

```toml
# erasedub.toml
[translate]
provider = "shout"

[translate.options]
exclamation = true
```

## The `Provider` contract

All base classes derive from `Provider`. Set these class attributes:

| Attribute | Meaning |
|---|---|
| `name` | Provider name (should match the entry-point name) |
| `api_version` | The plugin API the class was written for: write the number literally, `1`. Do not use `PLUGIN_API_VERSION`: it follows the installed EraseDub, so an incompatible plugin would not be detected |
| `summary` | One line shown by `erasedub plugins` |
| `requires_modules` | Python modules that must be importable, e.g. `("deepl",)` |
| `extra` | If the modules come from an EraseDub extra, its name; used in the install hint. Plugins usually leave `None` and make the modules normal dependencies. |
| `required_env` | Environment variables that must be set, e.g. `("DEEPL_API_KEY",)` |
| `notice` | Erasers only, optional (default `""`): a sentence shown with every plan that uses this eraser — in the CLI plan, the web UI and `doctor`. Use it for licence limits and known weaknesses (the built-in `propainter` states its non-commercial licence, `lama` its flicker) |
| `requires_gpu` | `True` if it needs an NVIDIA GPU on the machine that runs it (the built-in `lama` eraser sets `False`: it runs without an NVIDIA GPU). The planner uses it; `check()` does not test for a GPU (the `gpu` backend reports that). |
| `supports_mps` | Erasers only, optional (default `False`): `True` if the eraser can run on the Apple GPU (MPS). Without an NVIDIA GPU, the engine then passes `ctx.device = "mps"` when PyTorch reports MPS as available on macOS 14 or newer (the built-in `lama` eraser sets `True`) |
| `Options` | A nested `ProviderOptions` model for the provider's settings (default: no options) |

**Options.** The provider's table in the config (`[translate.options]`, `[tts.options]`...) is validated against
`Options` when the provider is created and is available as `self.options`. Unknown option names and bad values are
configuration errors (exit code 2), so users learn about typos before a long run starts.

**Calling conventions.**

- Methods are synchronous and block until done. The engine may call them from a worker thread (the web UI does),
  creates one instance per run, and never calls the same instance from two threads at once. A provider built on
  asyncio runs its own event loop inside the call (for example `asyncio.run()`).
- Every method takes a keyword argument `ctx: RunContext`: report progress with `ctx.progress(fraction, message)`,
  call `ctx.raise_if_cancelled()` between chunks of long work, and use `ctx.device` (`"cpu"`, `"cuda"`,
  `"cuda:N"` or `"mps"`, chosen by the engine; `"mps"` only for erasers with `supports_mps`), `ctx.tmp_dir` (deleted after the run), `ctx.cache_dir` (persistent, for model
  weights) and `ctx.logger`.
- Lifecycle: `open(ctx)` is called once before the first method call and `close()` after the last one, also when the
  run fails or is cancelled (and even if `open` raised). Load models in `open`, free them — GPU memory! — in
  `close`. Both do nothing by default.

Methods to implement per kind (see the docstrings in `erasedub.plugin` for the full contract):

| Base class | Method(s) |
|---|---|
| `TextEraser` | `erase(video, regions, output, *, ctx) -> Path` — keep resolution, frame count, frame rate and timestamps; never modify `video` |
| `Transcriber` | `transcribe(audio, *, language=None, ctx) -> Transcript` |
| `TextDetector` | `detect(video, *, languages=(), ctx) -> list[TextRegion]` |
| `Translator` | `translate(lines, *, target, source=None, style="faithful", glossary=None, ctx) -> list[str]` — `lines` are `ScriptLine`s (text plus timing and speaker as context); return exactly `len(lines)` strings, same order, never merge or split |
| `SpeechSynthesizer` | `voices(language, *, ctx) -> list[Voice]` and `synthesize(text, *, voice, language, output, max_duration=None, ctx) -> SynthResult` |
| `SubtitleLayout` | `layout(script, *, video, regions=(), ctx) -> list[SubtitleEvent]` |
| `GpuBackend` | `run_eraser(spec, video, regions, output, *, ctx) -> Path`, plus class attribute `remote` |

**GPU backends** receive an `EraserSpec` (eraser name, JSON options, plugin API version), not an eraser object, so a
remote backend can send it to another machine and build the same eraser there. A local backend builds it with
`erasedub.plugin.create_eraser(spec)` and calls `open` / `erase` / `close`. Set `remote = True` if the eraser runs
elsewhere: the planner then does not require the eraser's packages or an NVIDIA GPU on this machine, and your
`check()` decides.

The data types (`Transcript`, `TextRegion`, `Voice`, `SynthResult`, `SubtitleEvent`, `VideoInfo`, `ScriptLine`...)
are all in `erasedub.plugin`. Times are seconds from the start of the video; boxes are pixels of the source video.

## Rules

1. **`check()` must be cheap and side-effect free.** It runs for every provider on every `erasedub doctor`,
   `erasedub plugins` and `--dry-run`. No downloads, no network calls, no GPU allocation, no model loading. The
   default implementation checks `requires_modules` and `required_env`; override it only to add similar local
   checks, and call `super().check()` first. Return `Availability(False, "<what is missing and how to fix it>")`
   rather than raising.
2. **Import heavy libraries lazily**, inside methods (or `open`) — never at module top level. EraseDub imports every
   registered provider class to list it; a top-level `import torch` would make `erasedub --help` slow and break it
   for users without your dependencies.
3. **Keys only from environment variables.** Declare them in `required_env` and read them with `os.environ` when
   you need them. Never accept keys through `Options`: the config loader rejects option names like `api_key`,
   `token`, `secret` or `password` and values that look like well-known keys, because config files get shared.
   Never log or print key values.
4. **Download models only when the step runs** (in `open`), into `ctx.cache_dir`, from the location the model's
   authors publish, and tell the user what is being downloaded and its licence. If weights are non-commercial, say
   so in `summary` and in your README, and make them opt-in.
5. **Raise EraseDub errors** from `erasedub.plugin` so the CLI exits with the right code: `ConfigError` (2) for bad
   input, `ProviderUnavailableError` (3) when something needed is missing at run time. Cancellation raises
   `CancelledError` (130) through `ctx.raise_if_cancelled()`.
6. **Respect `max_duration`** in TTS providers when you can (speed up or shorten), so speech fits the time slot.

## Testing your plugin

- Unit-test the provider class directly; you do not need entry points for that. Build a `RunContext` with
  `RunContext(tmp_dir=tmp_path, cache_dir=tmp_path / "cache")`.
- Test registration with `erasedub plugins` after `pip install -e .`.
- Keep network and GPU tests behind markers so they can be skipped in CI.
