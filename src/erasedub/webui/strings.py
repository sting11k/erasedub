"""User-facing text of the web UI, kept in one place so it can be translated.

Add a language by adding a dict with the same keys (``"vi"``, ``"zh"``, ...) to :data:`STRINGS`; missing
keys fall back to English. Strings with ``{name}`` placeholders are filled with :meth:`str.format`, and a
translation must use the same placeholders (a test checks this).

Messages that come from the engine (plan step notes, notices, config errors) are in English.
"""

from __future__ import annotations

EN: dict[str, str] = {
    # Header
    "title": "EraseDub",
    "tagline": "Remove burned-in subtitles, translate and dub a video — on your own machine.",
    "version": "v{version}",
    # Input column
    "video": "Video",
    "targets": "Target languages",
    "targets_info": "Vietnamese, English and Chinese are checked by hand before each release.",
    "verified": "checked each release",
    "unverified": "other",
    "source": "Spoken language",
    "source_auto": "Detect automatically",
    "options": "Options",
    "erase": "Erase burned-in text",
    "erase_auto": "Auto (if a GPU is available)",
    "erase_on": "On",
    "erase_off": "Off",
    "erase_info": "With a GPU picked below (not Default), erasing is required: Auto then acts like On.",
    "gpu": "GPU",
    "gpu_default": "Default ({name})",
    "gpu_info": "Picking a GPU asks for erasing: if it cannot run, you get an error, not a skipped step.",
    "gpu_local": "This machine",
    "gpu_modal": "Modal (your own account)",
    "translator": "Translator",
    "tts": "Voice provider",
    "voice": "Voice",
    "voice_info": "'auto' picks the provider's default voice for each language.",
    "provider_not_ready": "{name} (not ready here)",
    "subtitles": "Burn subtitles",
    "original_audio": "Original audio",
    "original_keep": "Keep (quieter, under the new voice)",
    "original_mute": "Mute",
    "music": "Background music (optional)",
    # Steps
    "step1": "Step 1 · Script",
    "prepare": "1. Prepare script",
    "script_language": "Script language",
    "script": "Script (edit the text or timing before rendering)",
    "col_start": "Start",
    "col_end": "End",
    "col_text": "Text",
    "srt_upload": "Upload SRT",
    "srt_export": "Export SRT",
    "srt_file": "Script file",
    "step2": "Step 2 · Video",
    "render": "2. Render",
    "cancel": "Cancel",
    "status": "Status",
    "status_idle": "Upload a video, pick the target languages, then press **1. Prepare script**.",
    "result": "Result",
    "download": "Download",
    # Status messages
    "need_video": "Upload a video first.",
    "need_target": "Pick at least one target language.",
    "plan_prepare": "Prepare plan",
    "plan_render": "Render plan",
    "plan_targets": "Target languages: {targets}",
    "step_on": "runs",
    "step_off": "skipped",
    "unverified_notice": (
        "{names}: outside the languages checked for each release (Vietnamese, English, Chinese)."
    ),
    "provider_notice": "{step}: '{name}' is not ready here — {reason}",
    "script_ready": "Script ({lang}): {count} line(s) will be used.",
    "script_missing": (
        "No script for {lang} yet: Render will run Prepare for it first, like `erasedub run`."
    ),
    "script_saved": "Script ({lang}): the saved script in the work folder will be used.",
    "music_missing": "The background music file from the config was not found and is ignored: {name}",
    "cannot_start": "**Not started:** fix the problems above first.",
    "prepare_done": "**Scripts ready** ({langs}). Check or edit them in the table, then press **2. Render**.",
    "render_done": "**Done:** {count} video(s) written.",
    "file_script": "Script:",
    "file_video": "Video:",
    "run_failed": "**Failed:** {message}",
    "cancelled": "**Cancelled.** Files already finished are kept; running Render again reuses them.",
    "cancelling": "Cancelling: the current step stops as soon as it can.",
    "nothing_running": "Nothing is running.",
    "error": "Cannot run with these settings: {message}",
    # Names the GPU picker inside planner messages ("... was picked with the GPU option, ...").
    "gpu_option": "the GPU option",
    # How to take a GPU pick back, inside the same messages ("... or choose Default under GPU to ...").
    "gpu_undo": "choose Default under GPU",
    "regions_missing": (
        "No on-screen text found for this video yet (regions.json). Run Prepare first (it finds the "
        "on-screen text), or turn erasing off."
    ),
    "srt_error": "Script problem: {message}",
    "srt_row": "row {row}: {message}",
    "srt_loaded": "Loaded {count} line(s) from the SRT file into the {lang} script.",
    "srt_exported": "Exported {count} line(s) to {name}.",
    "srt_empty": "The script is empty — nothing to export.",
    "srt_too_big": "This file is too big for a script (the limit is {limit} MB).",
    # Pipeline step names (keys are the engine's step names)
    "step.extract-audio": "Extract audio",
    "step.transcribe": "Transcribe speech",
    "step.detect-text": "Detect on-screen text",
    "step.translate": "Translate",
    "step.erase": "Erase burned-in text",
    "step.speak": "Voice-over",
    "step.subtitles": "Subtitles",
    "step.mix-and-mux": "Mix audio and write the video",
    # Language names (keys are base language codes, see erasedub.languages.LISTED_LANGUAGES)
    "lang.vi": "Vietnamese",
    "lang.en": "English",
    "lang.zh": "Chinese",
    "lang.ja": "Japanese",
    "lang.ko": "Korean",
    "lang.th": "Thai",
    "lang.id": "Indonesian",
    "lang.es": "Spanish",
    "lang.fr": "French",
    "lang.de": "German",
    "lang.pt": "Portuguese",
    "lang.ru": "Russian",
}

STRINGS: dict[str, dict[str, str]] = {"en": EN}

DEFAULT_LOCALE = "en"


def t(key: str, locale: str = DEFAULT_LOCALE, **values: object) -> str:
    """Look up ``key`` for ``locale`` (falling back to English) and fill in ``values``."""
    text = STRINGS.get(locale, EN).get(key, EN[key])
    return text.format(**values) if values else text


def has(key: str) -> bool:
    return key in EN
