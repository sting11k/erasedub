# Configuration

EraseDub works with no configuration at all. To change defaults, create an `erasedub.toml` file.

## Where settings come from

Later sources override earlier ones:

1. **Built-in defaults** (listed below).
2. **`erasedub.toml`**: the file given with `--config FILE` / `-c FILE` (before or after the command:
   `erasedub -c my.toml run ...` or `erasedub run ... -c my.toml`), otherwise `./erasedub.toml` in the current
   directory if it exists.
3. **Command-line options** (`--to`, `--gpu`, `--no-voice`...).

API keys and tokens are **not** settings: they come only from environment variables (see
[API keys](#api-keys-and-tokens)).

An invalid file, an unknown section or key, or a value of the wrong type stops the command with exit code 2 and a
message that names the problem. The file must be UTF-8 (a UTF-8 byte-order mark is accepted); a file saved as UTF-16
("Unicode" in Windows Notepad) is refused with exit code 2 — save it again as UTF-8.

Relative paths in the file (`workdir`, `output_dir`, `music`) and on the command line are relative to the **current
directory**, not to the folder of the config file.

## Example

A commented example with every section is kept in the repository as `examples/erasedub.toml`.

```toml
[general]
target_languages = ["vi", "en"]

[translate]
provider = "gemini"            # needs GEMINI_API_KEY in the environment
style = "natural"
glossary = { "小米" = "Xiaomi" }

[tts]
voice = "vi-VN-HoaiMyNeural"

[audio]
original = "mute"
music = "music/background.mp3"
```

## All keys

### `[general]`

| Key | Default | Meaning | CLI |
|---|---|---|---|
| `workdir` | `"work"` | Where `prepare` writes scripts and intermediate files, relative to the current directory (one sub-folder per video) | `--workdir` |
| `output_dir` | unset | Where finished videos are written; unset = next to the input video | `--out` |
| `source_language` | unset | Language spoken in the video; unset or `"auto"` = detect | `--from` |
| `target_languages` | `["vi"]` | One or more target languages, e.g. `["vi", "en"]`; at least one | `--to vi,en` |

Language codes are BCP-47 tags. Case and separators are normalized (`"zh_tw"` becomes `zh-TW`, `"EN-us"` becomes
`en-US`); the region or script is kept, because it matters to translators and voices (`zh-TW` is Traditional
Chinese). The support tier is decided by the base language: `zh-TW` counts as `zh`.

### `[erase]`

| Key | Default | Meaning | CLI |
|---|---|---|---|
| `enabled` | `"auto"` | `auto`: erase if the chosen eraser can run here, otherwise skip with a notice · `on`: fail if it cannot erase · `off`: never erase | `--no-erase` = `off`; an explicit `--gpu` = `on` |
| `provider` | `"sttn"` | Eraser: `"sttn"` \| `"propainter"` \| `"lama"` \| `"none"` (or a plugin). `"none"` keeps the original picture (then `--gpu` is an error, exit code 2). See below | |
| `gpu` | `"local"` | Where erasing runs: `local` or `modal` (or a plugin backend). Set here, it keeps `enabled` as is; given as `--gpu` on the command line, it also makes erasing required for that run | `--gpu` |
| `options` | `{}` | Provider-specific options | |

The erasers — hardware, speed and quality, licence and extra for each — are compared in
[erasers.md](erasers.md):

- **`sttn`** (default): NVIDIA GPU, local or `--gpu modal`. Extra `erase` (part of `full`).
- **`propainter`**: NVIDIA GPU; higher quality, slower. Extra `propainter`. **Non-commercial** (S-Lab License 1.0);
  EraseDub shows a licence notice whenever it is selected or listed.
- **`lama`**: **no NVIDIA GPU needed** (CPU or Apple GPU), for Macs and other machines without one. Extra `lama`.
  Much slower, **prone to flicker**, and **flattens repeating patterned backgrounds**. Never picked automatically;
  the no-GPU notice suggests it.

None of the optional erasers is part of `full`; install the extra you need, e.g. `uv sync --extra full --extra lama` (or `pip install -e ".[full,lama]"`).

```toml
[erase]
provider = "lama"
```

### `[asr]`

| Key | Default | Meaning |
|---|---|---|
| `provider` | `"whisperx"` | Speech recognition provider |
| `model` | `"auto"` | Model name; `auto` picks a large model on GPU and a small one on CPU |
| `options` | `{}` | Provider-specific options, see below |

Options of the `whisperx` provider:

| Option | Default | Meaning |
|---|---|---|
| `model` | `"auto"` | Set from `asr.model`: `tiny`, `base`, `small`, `medium`, `large-v3`, ... or a local path; `auto` = `large-v3` on an NVIDIA GPU, `small` on the CPU |
| `compute_type` | `"auto"` | `float16`, `float32`, `int8` or `int8_float16`; `auto` = `float16` on CUDA, `int8` on the CPU |
| `batch_size` | `8` | Segments transcribed at once (1–64); lower it if GPU memory runs out |
| `align` | `false` | Run WhisperX forced alignment for word timestamps. **Off by default**: plain subtitles and dubbing only need segment timestamps, and some per-language alignment models are non-commercial (the defaults for vi, fr, de, es and it are CC BY-NC 4.0). Check the licence for your language in [models-and-licenses.md](models-and-licenses.md#whisperx-alignment-models) before turning it on. |
| `align_model` | unset | Hugging Face id of an alignment model to use instead of WhisperX's default for the language |
| `diarize` | `false` | Label speakers with pyannote (the `speaker` field in `script.<lang>.json`). Needs `HF_TOKEN` from a Hugging Face account that accepted the terms of the gated [pyannote model](models-and-licenses.md) (CC BY 4.0). Without the token, speakers stay empty and a warning is printed. |
| `min_speakers`, `max_speakers` | unset | Hints for the number of speakers when `diarize` is on |

```toml
[asr.options]
align = true       # word timestamps (check the alignment model's licence first)
diarize = true     # speaker labels (needs HF_TOKEN)
```

### `[ocr]`

| Key | Default | Meaning |
|---|---|---|
| `provider` | `"rapidocr"` | On-screen text detection provider (only used when erasing) |
| `options` | `{}` | Provider-specific options, see below |

Options of the `rapidocr` provider:

| Option | Default | Meaning |
|---|---|---|
| `sample_fps` | `1.0` | Frames examined per second of video (up to 10); lower is faster on long videos |
| `min_score` | `0.5` | Lowest recognition score kept (0–1) |
| `model_type` | `"small"` | PP-OCRv6 model size: `tiny` (faster; cannot read Japanese), `small` or `medium` |
| `min_iou`, `min_text_similarity`, `max_gap` | `0.5`, `0.6`, `1` | How boxes in consecutive samples are joined into one region: overlap, text similarity, and missed samples allowed |
| `time_padding` | unset | Seconds added before and after each region; unset = one sample interval |

### `[translate]`

| Key | Default | Meaning |
|---|---|---|
| `provider` | `"google"` | `google` (free, no key), `openai`, `gemini`, `claude`, `ollama`, or a plugin |
| `style` | `"faithful"` | `faithful` keeps the meaning close; `natural` lets LLM providers rewrite for fluency |
| `glossary` | `{}` | Terms to translate a fixed way, `{ "source term" = "translation" }` |
| `options` | `{}` | Provider-specific options, see below |

Options of the LLM translators (`openai`, `gemini`, `claude`, `ollama`):

| Option | Default | Meaning |
|---|---|---|
| `model` | `openai`: `"gpt-5-mini"`, `gemini`: `"gemini-2.5-flash"`, `claude`: `"claude-sonnet-5"`, `ollama`: `"qwen3:8b"` | Model name |
| `base_url` | unset | `openai` only: another OpenAI-compatible server (DeepSeek, OpenRouter, a local vLLM, ...); the key still comes from `OPENAI_API_KEY` |
| `json_mode` | `true` | `openai` only: ask for a JSON answer; turn off for servers that do not support it |
| `host` | unset | `ollama` only: the Ollama server; unset = `OLLAMA_HOST` or `http://localhost:11434` |
| `temperature` | unset | Sampling temperature; unset = the model's default |
| `batch_lines`, `context_lines` | `40`, `3` | Lines per request, and earlier lines sent along as context |
| `max_tokens`, `timeout`, `retries` | `8192`, `180`, `3` | Answer length limit, seconds per answer, tries per request |

The `google` translator takes `batch_lines` (default `40`) and `retries` (default `5`).

```toml
[translate]
provider = "openai"

[translate.options]
model = "deepseek-chat"
base_url = "https://api.deepseek.com"
```

### `[tts]`

| Key | Default | Meaning | CLI |
|---|---|---|---|
| `enabled` | `true` | Generate a new voice track | `--no-voice` = `false` |
| `provider` | `"edge"` | `edge` (free, no key), `elevenlabs`, or a plugin | |
| `voice` | `"auto"` | Voice id; `auto` = the provider's default voice for each target language | |
| `options` | `{}` | Provider-specific options, see below | |

Options of the `edge` voice:

| Option | Default | Meaning |
|---|---|---|
| `max_rate` | `50` | Largest speed-up, in percent, used to fit a line into its time slot (`50` = 1.5× speed) |
| `pitch`, `volume` | `"+0Hz"`, `"+0%"` | Pitch and volume change |
| `retries` | `3` | Tries per request |

Options of the `elevenlabs` voice:

| Option | Default | Meaning |
|---|---|---|
| `model_id` | `"eleven_multilingual_v2"` | ElevenLabs model |
| `max_speed` | `1.2` | Largest speaking speed used to fit a line into its time slot (1.2 is the API maximum) |
| `send_language` | `false` | Send the language code with each request; only some models accept it (turbo/flash v2.5 and newer) |
| `stability`, `similarity_boost`, `style` | unset | Voice settings; unset = the voice's own |
| `timeout`, `retries` | `120`, `4` | Seconds per request, tries per request |

### `[subtitles]`

| Key | Default | Meaning | CLI |
|---|---|---|---|
| `enabled` | `true` | Burn subtitles into the output | `--no-subs` = `false` |
| `layout` | `"bottom"` | Layout provider; `bottom` = standard bottom-centre subtitles | |
| `font` | unset | Font name; unset = a default font that covers the target language | |
| `font_size` | `0` | Font size in pixels; `0` = scale with the shorter side of the video | |
| `options` | `{}` | Layout-specific options, e.g. `margin_ratio = 0.08` for `bottom` | |

### `[audio]`

| Key | Default | Meaning | CLI |
|---|---|---|---|
| `original` | `"keep"` | Original soundtrack under the new voice: `keep` or `mute` | |
| `original_volume` | `0.25` | Volume of the original soundtrack (0–2) | |
| `voice_volume` | `1.0` | Volume of the new voice (0–2) | |
| `music` | unset | Background music file | `--music` |
| `music_volume` | `0.15` | Volume of the music (0–2) | |
| `loudnorm` | `true` | Normalize loudness of the final mix | |

## API keys and tokens

Keys are read **only from environment variables**, never from `erasedub.toml`. This keeps config files safe to share.
The loader refuses the file (exit code 2) and tells you to use an environment variable instead when:

- a key name contains a credential word as a `_`/`-`-separated part — `key`, `api_key`, `token`, `token_id`,
  `secret`, `password`, `auth`, `bearer`, `credentials`, `cookie`, `access_key`, `private_key`... (for example
  `openai_api_key`, `hf-token`, `modal_token_id`); or
- a value looks like a well-known key (OpenAI/Anthropic `sk-...`, Google `AIza...`, Hugging Face `hf_...`, Modal
  tokens, `Bearer ...`), wherever it appears — also inside a URL.

Glossary entries are not checked, and options like `max_tokens` are allowed. The error message never repeats the
value.

| Variable | Used by |
|---|---|
| `OPENAI_API_KEY` | `translate.provider = "openai"` |
| `GEMINI_API_KEY` | `translate.provider = "gemini"` |
| `ANTHROPIC_API_KEY` | `translate.provider = "claude"` |
| `ELEVENLABS_API_KEY` | `tts.provider = "elevenlabs"` |
| `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` | `--gpu modal` (or run `modal token new`, which writes `~/.modal.toml`) |
| `HF_TOKEN` | `asr.options.diarize = true`: speaker labels (the pyannote model is gated on Hugging Face) |

The defaults (Google Translate, Edge-TTS, local erasing) need no key, and neither does Ollama.

Set a variable for one session:

```bash
export GEMINI_API_KEY=...          # Linux / macOS
```

```powershell
$env:GEMINI_API_KEY = "..."        # Windows PowerShell
```

`erasedub doctor` shows which variables are set — never their values.

### Other environment variables

| Variable | Meaning |
|---|---|
| `ERASEDUB_FFMPEG` | Full path to the `ffmpeg` executable to use (it must exist); otherwise `ffmpeg` is taken from `PATH` |
| `ERASEDUB_FFPROBE` | Same for `ffprobe` |
| `ERASEDUB_CACHE_DIR` | Folder for downloaded eraser and OCR models. Default: the user cache folder + `erasedub` (Linux `~/.cache/erasedub` or `$XDG_CACHE_HOME/erasedub`, macOS `~/Library/Caches/erasedub`, Windows `%LOCALAPPDATA%\erasedub\cache`) |
| `ERASEDUB_IN_CONTAINER` | Set to `1` by the Docker image. `erasedub webui` on a non-loopback host then prints a "publish the port as `-p 127.0.0.1:<port>:<port>`" note instead of the no-login warning. Unset, empty, `0`, `false` or `no` = not in a container |

The ffmpeg variables are useful when several ffmpeg builds are installed and the first one on `PATH` lacks libass or libx264.
`erasedub doctor` shows which executables it uses.

## Writing paths on Windows

In TOML, backslashes inside `"double quotes"` are escape characters. Use forward slashes or single quotes:

```toml
music = "C:/Users/me/Music/bg.mp3"
# or, equivalently:
# music = 'C:\Users\me\Music\bg.mp3'
```
