# Script format

`erasedub prepare` writes the translated script for each target language into the video's work directory
(by default `work/<video-stem>/`, relative to the current directory). `erasedub render` reads them back. You can
edit the scripts between the two commands.

For `erasedub prepare clip.mp4 --to vi,en`:

```text
work/clip/
├── regions.json        on-screen text found by OCR (shared by all languages)
├── script.vi.json      engine data, Vietnamese
├── script.vi.srt       what you edit, Vietnamese
├── script.en.json
└── script.en.srt
```

| File | For | Contents |
|---|---|---|
| `script.<lang>.srt` | **you** | The translated lines with their timing. Edit it in any text or subtitle editor. |
| `script.<lang>.json` | the engine | Everything EraseDub knows about each line: timing, translated text, source text, speaker. |
| `regions.json` | the engine | The video it was measured on and every on-screen text region found (box + time span). `render` erases these, so it does not need to run OCR again — also on another machine with `--gpu modal`. You can inspect it; editing it is possible but not needed. |

`<lang>` is the normalized language tag: `vi`, `en`, `zh`, `zh-TW`, `pt-BR`...

## `script.<lang>.srt`

A standard [SubRip](https://en.wikipedia.org/wiki/SubRip) file, written as UTF-8:

```srt
1
00:00:01,200 --> 00:00:03,900
Xin chào các bạn.

2
00:00:04,100 --> 00:00:07,500
Hôm nay mình thử món này.
```

Edit it with Notepad, VS Code, [Subtitle Edit](https://github.com/SubtitleEdit/subtitleedit), Aegisub... You can change
the text, change the times, merge, split, add or delete lines.

The reader is lenient:

- UTF-8 with or without a byte order mark, and UTF-16 with a byte order mark, are fine;
- Windows (CRLF) or old Mac (CR) line endings are fine;
- index numbers can be missing or wrong, and a missing blank line between cues is harmless;
- `.` instead of `,` before the milliseconds is accepted (`00:00:01.200`), and text after the times (position hints
  some editors add) is ignored;
- a line's text can span several rows;
- cues out of time order are sorted, with a warning; overlapping cues are kept, with a warning (their voices will
  overlap);
- a zero-length cue is dropped, with a warning.

It is strict where guessing would be wrong: a bad timing line, a cue that ends before it starts, text before the
first cue, or a file that is not UTF-8/UTF-16 stops `render` with exit code 2. The message names the file and the
line number, e.g. `script.vi.srt:123: ...`.

**Leave a line's text empty** to keep its timing but show no subtitle for it.

## `script.<lang>.json`

Written by `prepare`; you normally do not edit it.

```json
{
  "version": 1,
  "source_language": "zh",
  "target_language": "vi",
  "lines": [
    {
      "start": 1.2,
      "end": 3.9,
      "text": "Xin chào các bạn.",
      "source": "大家好。",
      "speaker": null
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `version` | Format version, currently `1` |
| `source_language` | Detected or given source language tag, or `null` |
| `target_language` | Target language tag, e.g. `vi` (matches `<lang>` in the file name) |
| `lines[].start`, `lines[].end` | Seconds from the start of the video, rounded to milliseconds; `end` must be greater than `start` |
| `lines[].text` | Translated text |
| `lines[].source` | Original text, or `null` |
| `lines[].speaker` | Speaker label, or `null` |

Unknown fields are rejected, so a typo in a hand-edited file fails loudly (exit code 2).

## What `render` uses

For each target language:

1. `script.<lang>.json` must exist; otherwise `render` tells you to run `prepare --to <lang>` first.
2. If `script.<lang>.srt` exists, **it is authoritative for timing and text**, because that is the file you edit.
3. **Matching edited lines to the original** (to keep the source text and speaker label): each SRT cue takes the
   JSON line whose timing overlaps it the most, if that overlap covers more than half of the cue. A cue without such
   a match takes the JSON line at the same position — but only when both files still have the same number of lines.
   Cues that match nothing keep their text and timing, lose the source text and speaker label, and `render` prints
   a warning saying how many.
4. If `script.<lang>.srt` has been deleted, `script.<lang>.json` is used as is.

`render` never writes back to your SRT files.

Tip: if you only fix wording, keep the timing and the number of lines, so every line keeps its speaker label.
