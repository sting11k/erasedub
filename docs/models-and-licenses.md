# Models, services and licences

EraseDub's own code is licensed under [Apache-2.0](../LICENSE). It uses third-party models, libraries and online
services, each with its own terms. This page lists them so you can decide what you may use, especially for
**commercial** work.

> [!CAUTION]
> **Some optional models forbid commercial use.**
> **ProPainter** (S-Lab License 1.0, code and weights) is **non-commercial**. In v0.1 it is an optional
> eraser: it is **never installed or selected by default** (extra `propainter`, not part of `full`), never shipped in
> the repository, the wheel, the Docker image or the Windows bundle, and EraseDub shows its licence whenever it is
> selected or listed. The **F5-TTS weights** (CC BY-NC 4.0) are non-commercial too; F5-TTS is not part of v0.1.
> The licence of the **LaMa weights** is **unclear**: its official sources state none (details in the table
> below).
> If you use a non-commercial or unclear component, making sure your use is allowed is your responsibility.
>
> **WhisperX word alignment is off by default for the same reason.** Several of its per-language alignment models
> are non-commercial — including the defaults for **Vietnamese**, French, German, Spanish and Italian (CC BY-NC 4.0).
> EraseDub only needs segment timestamps, so it does not run alignment unless you set `[asr.options] align = true`
> (see [WhisperX alignment models](#whisperx-alignment-models) and [ADR 0008](adr/0008-whisperx-alignment-off-by-default.md)).

Licences were checked on 2026-09-26 against each project's repository or model card. Licences change — check the
source link before relying on this table, and please open an issue if something here is out of date.
This is not legal advice.

## Used by v0.1

| Component | Role | Code licence | Weights licence | Commercial use OK? | Bundled or downloaded | Source |
|---|---|---|---|---|---|---|
| STTN | default eraser (`sttn`) | MIT | ? — not stated separately from the code | Code yes; weights: **unresolved** | downloaded on first use (the authors publish them on Google Drive); not pre-fetched into the Docker image | [researchmm/STTN](https://github.com/researchmm/STTN) |
| **ProPainter** | optional eraser (`propainter`): higher quality on hard scenes, slower | **S-Lab License 1.0 — non-commercial** | **S-Lab License 1.0 — non-commercial** | **No** | opt-in only: install the `propainter` extra and set `erase.provider = "propainter"`. Fetched at first use: the code from the official repository at a pinned commit, each file checked against a pinned sha256; the weights (three files) from its release [v0.1.0](https://github.com/sczhou/ProPainter/releases/tag/v0.1.0), which publishes no checksums, so EraseDub pins their exact sizes and records each file's sha256 on first download; a later mismatch is an error. Never bundled | [sczhou/ProPainter](https://github.com/sczhou/ProPainter) |
| LaMa (big-lama) | optional eraser (`lama`) for machines without an NVIDIA GPU: CPU, or Apple GPU (MPS); per-frame, so prone to flicker, and flattens repeating patterned backgrounds | Apache-2.0 | **unclear** — the authors state no licence for the weights (README, Google Drive folder); the Apache-2.0 tag on the Hugging Face repository `smartywu/big-lama` is not the authors' own statement | Code yes; weights **unclear** | opt-in only: extra `lama`, `erase.provider = "lama"`. Code vendored (Apache-2.0) from advimman/lama at a pinned commit; weights downloaded on first use from Hugging Face `smartywu/big-lama`, the location the upstream README gives, at a pinned revision with a pinned sha256 | [advimman/lama](https://github.com/advimman/lama) |
| WhisperX | speech recognition (`whisperx`) | BSD-2-Clause | transcription: faster-whisper models (next row); alignment: **off by default**, see [below](#whisperx-alignment-models) | Yes (with alignment off) | pip dependency (extra `asr`) | [m-bain/whisperX](https://github.com/m-bain/whisperX) |
| faster-whisper | ASR engine used by WhisperX | MIT | large-v3 conversion: MIT | Yes | downloaded on first use (Hugging Face) | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper), [model](https://huggingface.co/Systran/faster-whisper-large-v3) |
| pyannote speaker diarization | optional speaker labels (`asr.options.diarize = true`) | MIT | [speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) (WhisperX's current default): **CC BY 4.0**, **gated** (accept terms on Hugging Face, needs `HF_TOKEN`) | Yes, with attribution | opt-in only: downloaded on first use when `diarize` is on and `HF_TOKEN` is set | [pyannote/pyannote-audio](https://github.com/pyannote/pyannote-audio) |
| RapidOCR (PP-OCR models) | on-screen text detection (`rapidocr`) | Apache-2.0 | Apache-2.0 (PaddleOCR models) | Yes | pip dependency (extra `ocr`); RapidOCR downloads its models from **ModelScope** (`modelscope.cn/models/RapidAI/...`) on first use | [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR), [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) |
| deep-translator → Google Translate | default translator (`google`) | Apache-2.0 / MIT | — | Library yes; **the service is Google's free web endpoint, used unofficially** — see below | core dependency | [nidhaloff/deep-translator](https://github.com/nidhaloff/deep-translator) |
| edge-tts → Microsoft Edge read-aloud | default voices (`edge`) | **LGPL-3.0** (one file MIT) | — | Library yes (LGPL, see below); **the service is Microsoft's, used unofficially** | core dependency; included in the Docker image and Windows bundle | [rany2/edge-tts](https://github.com/rany2/edge-tts) |
| Gradio | web UI | Apache-2.0 | — | Yes | pip dependency (extra `webui`) | [gradio-app/gradio](https://github.com/gradio-app/gradio) |
| Modal client | `--gpu modal` | Apache-2.0 | — | Yes; usage billed to your Modal account | pip dependency (extra `modal`) | [modal-labs/modal-client](https://github.com/modal-labs/modal-client) |
| FFmpeg (BtbN `gpl` builds) | decoding, mixing, subtitles, H.264 | build scripts MIT; **binaries GPL** (libx264 is GPL) | — | Yes, under GPL terms (corresponding source must be provided, see below) | **bundled** in the Docker image and Windows bundle; otherwise you install it | [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) |

## WhisperX alignment models

WhisperX can run *forced alignment* after transcription to get a timestamp for every word. It picks a wav2vec2
model per **source** language. EraseDub does **not** need word timestamps for plain subtitles and dubbing, so
alignment is **off by default** (`[asr.options] align = false`). If you turn it on, check the model for your
language first. Defaults of WhisperX 3.8.6 (checked 2026-09-26 on Hugging Face and in torchaudio's documentation):

| Source language | Default alignment model | Licence | Commercial use OK? |
|---|---|---|---|
| Vietnamese (`vi`) | [nguyenvulebinh/wav2vec2-base-vi-vlsp2020](https://huggingface.co/nguyenvulebinh/wav2vec2-base-vi-vlsp2020) | **CC BY-NC 4.0** | **No** |
| French, German, Spanish, Italian (`fr`, `de`, `es`, `it`) | torchaudio `VOXPOPULI_ASR_BASE_10K_*` | **CC BY-NC 4.0** | **No** |
| English (`en`) | torchaudio `WAV2VEC2_ASR_BASE_960H` | MIT | Yes |
| Chinese, Japanese, Korean, Indonesian, Russian, Portuguese, Arabic and most others | e.g. [jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn](https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn) | Apache-2.0 | Yes |
| Turkish (`tr`) | mpoyraz/wav2vec2-xls-r-300m-cv7-turkish | CC BY 4.0 | Yes, with attribution |
| Swedish (`sv`) | KBLab/wav2vec2-large-voxrex-swedish | CC0 1.0 | Yes |
| Danish, Hebrew, Hindi, Croatian, Galician (`da`, `he`, `hi`, `hr`, `gl`) | see WhisperX's `alignment.py` | "other" or not stated on the model card | **Unknown** — check before use |

The full list is `DEFAULT_ALIGN_MODELS_TORCH` / `DEFAULT_ALIGN_MODELS_HF` in
[whisperx/alignment.py](https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/alignment.py). It can change with
WhisperX versions.

Paid services you can choose instead (OpenAI, Gemini, Claude, ElevenLabs) are used with **your** API key under
**your** agreement with that provider.

## Possible later additions (not in v0.1)

Erasers are not on this list: v0.1 has exactly three, STTN, ProPainter and LaMa, and no others are on the roadmap
([ADR 0014](adr/0014-erasers-sttn-default-others-opt-in.md)).

| Component | Role | Code licence | Weights licence | Commercial use OK? | Would be | Source |
|---|---|---|---|---|---|---|
| **F5-TTS** | local voice cloning | MIT | **CC BY-NC 4.0 — non-commercial** | **No** (weights) | opt-in only, downloaded on request | [SWivid/F5-TTS](https://github.com/SWivid/F5-TTS), [model](https://huggingface.co/SWivid/F5-TTS) |
| CosyVoice | local voices | Apache-2.0 | Apache-2.0 | Yes | opt-in (GPU) | [QwenAudio/CosyVoice](https://github.com/QwenAudio/CosyVoice) (formerly FunAudioLLM/CosyVoice) |

## Notes

**Online services used unofficially.** The free defaults — Google Translate via deep-translator and Microsoft's
read-aloud voices via edge-tts — call public endpoints that are not offered as developer APIs. They have no service
guarantee, can be rate-limited or changed without notice, and their terms may restrict your use. For commercial or
high-volume work, prefer an official API with your own key, or a local model.

**edge-tts is LGPL-3.0.** EraseDub installs it as a separate, unmodified package and only imports it, which the LGPL
permits in an Apache-2.0 project. The Docker image and the Windows bundle redistribute it: they keep it as an
ordinary, replaceable Python package (not frozen into an executable) and include its licence text.

**GPL ffmpeg in the Docker image and Windows bundle.** Those distributions include a GPL build of ffmpeg from
BtbN/FFmpeg-Builds, because libx264 is GPL. EraseDub calls ffmpeg as a separate program; it does not link to it.
Distributing GPL binaries means providing their **complete corresponding source**, not just a link to the build.
Each distribution therefore ships ffmpeg's licence texts, the exact upstream FFmpeg revision and the BtbN
build-script commit, and each release publishes the corresponding source (FFmpeg and the GPL/LGPL libraries in that
build, such as x264) as release assets for as long as the binaries are offered. BtbN's own builds expire, so they
cannot serve as the source offer. An LGPL build with openh264 was considered and not chosen
([ADR 0010](adr/0010-gpl-ffmpeg-in-binary-distributions.md)). An install from source does not include ffmpeg
at all.

**Model downloads.** Models are downloaded the first time a step needs them, from the location their authors
publish or point to (GitHub releases, Hugging Face, ModelScope, Google Drive...). The eraser files that EraseDub
downloads itself are pinned to a commit or release and verified by checksum (or, where the source publishes none, by
size and a hash recorded on first download). No model weights are
stored in this repository, the wheel or the release assets. The Docker image
downloads models on first use like any other install. An image with pre-fetched models would redistribute them, so
only models whose licence allows it could be included. ProPainter's non-commercial code and weights are never included in any distribution.

**Your content.** Licences above cover the software. You are responsible for having the rights to the videos,
music and voices you process and publish.
