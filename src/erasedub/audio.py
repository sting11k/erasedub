"""The dubbed voice track and the final audio mix.

Each script line's voice clip is fitted to its time slot and placed at the line's start:

* The slot runs from the line's start to the next line's start (or the end of the video), and is never
  shorter than the line itself.
* A clip longer than its slot is sped up with ffmpeg's ``atempo`` (pitch kept), at most :data:`MAX_TEMPO`.
  Clips are never cut, so no word is lost; a clip still too long pushes the next voice back a little instead
  of talking over it, and the engine names those lines so their text can be shortened.
* Clips are never slowed down.

The track is written as one 48 kHz mono WAV: silence, clip, silence, clip... Under it, the original
soundtrack is kept with a *band gate* by default: the voice band (:data:`VOICE_BAND`) of the original is
turned down to :data:`VOICE_BAND_DB` below ``audio.original_volume`` (-40 dB at the default 0.25), and the
rest (bass, cymbals, much of the music and effects) stays at ``original_volume`` (-12 dB at 0.25). The
original speech then disappears under the new voice while the atmosphere stays.
"""

from __future__ import annotations

import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from erasedub.config import AudioConfig
from erasedub.script import format_timestamp

#: Fastest speed-up applied to a voice clip; faster speech gets hard to follow.
MAX_TEMPO = 1.35
#: Sample rate of the voice track and of the final mix.
SAMPLE_RATE = 48_000
#: The band of the original soundtrack that carries speech, in Hz.
VOICE_BAND = (150, 6000)
#: How far the voice band of the original is turned down, relative to ``audio.original_volume``.
VOICE_BAND_DB = -28.0
#: Loudness target of the final mix (``audio.loudnorm``): EBU R128 values common for online video.
LOUDNORM = "loudnorm=I=-16:LRA=11:TP=-1.5"
#: Seconds of fade-out at the end of background music.
MUSIC_FADE = 2.0
#: A voice pushed back by less than this is not worth a warning.
_DRIFT_TOLERANCE = 0.05


@dataclass(frozen=True)
class Slot:
    """Where one voice clip may go: the line's start and the latest time it should end."""

    #: 1-based position of the line in the script, for messages.
    number: int
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


def slots(lines: Sequence[tuple[float, float]], *, video_end: float) -> list[Slot]:
    """The slot of each ``(start, end)`` line, in the given order (which must be sorted by start)."""
    out = []
    for i, (start, end) in enumerate(lines):
        following = [s for s, _ in lines[i + 1 :] if s > start]
        limit = min(following[0], video_end) if following else video_end
        out.append(Slot(number=i + 1, start=start, end=max(end, limit)))
    return out


def tempo_for(duration: float, slot: Slot, *, max_tempo: float = MAX_TEMPO) -> float:
    """The speed-up that makes a clip of ``duration`` seconds fit ``slot``: 1.0 (as is) to ``max_tempo``."""
    if slot.length <= 0 or duration <= slot.length:
        return 1.0
    return min(duration / slot.length, max_tempo)


@dataclass(frozen=True)
class Placement:
    slot: Slot
    #: Seconds the voice starts; later than ``slot.start`` only when the voice before it ran long.
    start: float
    tempo: float
    #: Seconds of voice after the speed-up.
    length: float

    @property
    def end(self) -> float:
        return self.start + self.length


def place(clips: Sequence[tuple[Slot, float]], *, max_tempo: float = MAX_TEMPO) -> list[Placement]:
    """Place ``(slot, clip duration)`` pairs in order: fit each one, and never let two voices overlap."""
    out: list[Placement] = []
    free_from = 0.0
    for slot, duration in clips:
        tempo = tempo_for(duration, slot, max_tempo=max_tempo)
        start = max(slot.start, free_from)
        placed = Placement(slot=slot, start=start, tempo=tempo, length=duration / tempo)
        out.append(placed)
        free_from = placed.end
    return out


def timing_notice(placements: Sequence[Placement], *, script_name: str) -> str | None:
    """One message naming the lines whose voice does not fit even at :data:`MAX_TEMPO`, or None."""
    long = [p for p in placements if p.end > p.slot.end + _DRIFT_TOLERANCE]
    if not long:
        return None
    shown = ", ".join(f"{p.slot.number} ({format_timestamp(p.slot.start)})" for p in long[:8])
    more = f" and {len(long) - 8} more" if len(long) > 8 else ""
    return (
        f"{script_name}: the voice of {len(long)} line(s) is longer than its time slot even at "
        f"{MAX_TEMPO:g}x speed, so the voices after it start a little late: lines {shown}{more}. Shorten "
        "their text in the script and render again for tighter timing."
    )


def atempo_filter(tempo: float) -> str | None:
    """The ffmpeg audio filter for ``tempo``, or None when the clip is used as it is."""
    return f"atempo={tempo:.4f}" if tempo > 1.0001 else None


def write_track(pieces: Sequence[tuple[float, Path]], output: Path) -> float:
    """Write the voice track: each ``(start seconds, raw PCM file)`` in order, silence in between.

    The PCM files are 16-bit mono at :data:`SAMPLE_RATE` (what the engine converts every clip to). A piece
    that would start before the previous one ended is moved right after it, so voices never overlap.
    Returns the track's length in seconds.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    frame_bytes = 2
    written = 0  # frames
    with wave.open(str(output), "wb") as track:
        track.setnchannels(1)
        track.setsampwidth(frame_bytes)
        track.setframerate(SAMPLE_RATE)
        for start, pcm in pieces:
            gap = round(start * SAMPLE_RATE) - written
            if gap > 0:
                _write_silence(track, gap)
                written += gap
            size = pcm.stat().st_size - pcm.stat().st_size % frame_bytes
            with pcm.open("rb") as source:
                remaining = size
                while remaining:
                    chunk = source.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    track.writeframesraw(chunk)
                    remaining -= len(chunk)
            written += (size - remaining) // frame_bytes
    return written / SAMPLE_RATE


def _write_silence(track: wave.Wave_write, frames: int) -> None:
    block = bytes(2 * SAMPLE_RATE)  # one second
    while frames > 0:
        n = min(frames, SAMPLE_RATE)
        track.writeframesraw(block[: 2 * n])
        frames -= n


# --- The final mix ---------------------------------------------------------------------------------------

_PREP = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"


def _gain(value: float) -> str:
    return f"volume={value:.6f}"


@dataclass(frozen=True)
class MixInputs:
    """ffmpeg input indexes of the audio sources; None when a source is not used."""

    original: int | None = None
    voice: int | None = None
    music: int | None = None


def mix_graph(inputs: MixInputs, audio: AudioConfig, *, duration: float) -> str | None:
    """The ``-filter_complex`` part that mixes the audio into ``[aout]``, or None when there is no audio.

    Without a new voice the original soundtrack is kept at full volume (there is nothing to make room for);
    ``audio.original = "mute"`` drops it in every case.
    """
    parts: list[str] = []
    labels: list[str] = []
    if inputs.original is not None:
        source = f"[{inputs.original}:a:0]{_PREP}"
        if inputs.voice is None:
            parts.append(f"{source}[orig]")
        else:
            rest = audio.original_volume
            band = rest * 10 ** (VOICE_BAND_DB / 20)
            low, high = VOICE_BAND
            parts += [
                f"{source},acrossover=split='{low} {high}'[o_low][o_band][o_high]",
                f"[o_low]{_gain(rest)}[o_l]",
                f"[o_band]{_gain(band)}[o_b]",
                f"[o_high]{_gain(rest)}[o_h]",
                "[o_l][o_b][o_h]amix=inputs=3:normalize=0[orig]",
            ]
        labels.append("[orig]")
    if inputs.voice is not None:
        parts.append(f"[{inputs.voice}:a:0]{_PREP},{_gain(audio.voice_volume)}[voice]")
        labels.append("[voice]")
    if inputs.music is not None:
        music = f"[{inputs.music}:a:0]{_PREP},{_gain(audio.music_volume)}"
        if duration > 2 * MUSIC_FADE:
            music += f",afade=t=out:st={duration - MUSIC_FADE:.3f}:d={MUSIC_FADE:g}"
        parts.append(f"{music}[music]")
        labels.append("[music]")
    if not labels:
        return None
    tail = [f"apad=whole_dur={duration:.3f}"]
    if audio.loudnorm:
        tail += [LOUDNORM, f"aresample={SAMPLE_RATE}"]
    if len(labels) == 1:
        parts.append(f"{labels[0]}{','.join(tail)}[aout]")
    else:
        mix = f"amix=inputs={len(labels)}:normalize=0:duration=longest"
        parts.append(f"{''.join(labels)}{mix},{','.join(tail)}[aout]")
    return ";".join(parts)
