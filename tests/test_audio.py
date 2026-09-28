"""Fitting voice clips to their slots, the voice track and the mix graph (synthetic PCM, no media)."""

import wave
from pathlib import Path

import pytest

from erasedub import audio
from erasedub.config import AudioConfig


def test_slots_run_to_the_next_line_or_the_end_of_the_video() -> None:
    got = audio.slots([(0.0, 1.0), (2.0, 3.0), (2.0, 2.5), (6.0, 9.5)], video_end=9.0)
    assert [(s.number, s.start, s.end) for s in got] == [(1, 0, 2), (2, 2, 6), (3, 2, 6), (4, 6, 9.5)]
    assert got[0].length == 2


def test_tempo_never_slows_down_and_is_capped() -> None:
    slot = audio.Slot(1, 0, 2)
    assert audio.tempo_for(1.5, slot) == 1.0
    assert audio.tempo_for(2.4, slot) == pytest.approx(1.2)
    assert audio.tempo_for(10, slot) == audio.MAX_TEMPO


def test_place_never_overlaps_and_names_the_long_lines() -> None:
    clips = [(audio.Slot(1, 0, 2), 4.0), (audio.Slot(2, 2, 4), 1.0), (audio.Slot(3, 10, 12), 1.0)]
    placed = audio.place(clips)
    assert placed[0].tempo == audio.MAX_TEMPO
    assert placed[0].end == pytest.approx(4 / audio.MAX_TEMPO)
    assert placed[1].start == pytest.approx(placed[0].end)  # pushed back, never on top of line 1
    assert placed[2].start == 10
    notice = audio.timing_notice(placed, script_name="script.vi.srt")
    assert notice is not None
    assert notice.startswith("script.vi.srt: the voice of 1 line(s)")
    assert "lines 1 (00:00:00,000)" in notice
    assert audio.timing_notice(placed[2:], script_name="x") is None


def test_atempo_filter() -> None:
    assert audio.atempo_filter(1.0) is None
    assert audio.atempo_filter(1.25) == "atempo=1.2500"


def _pcm(path: Path, seconds: float, value: int = 1) -> Path:
    path.write_bytes(value.to_bytes(2, "little", signed=True) * round(seconds * audio.SAMPLE_RATE))
    return path


def test_write_track_places_each_clip_at_its_start(tmp_path: Path) -> None:
    a = _pcm(tmp_path / "a.pcm", 0.5, 7)
    b = _pcm(tmp_path / "b.pcm", 0.25, 9)
    c = _pcm(tmp_path / "c.pcm", 0.25, 5)
    seconds = audio.write_track([(1.0, a), (1.25, b), (3.0, c)], tmp_path / "voice.wav")
    # b asked for 1.25 s but a lasts until 1.5 s: b follows a instead of overlapping it.
    assert seconds == pytest.approx(3.25)
    with wave.open(str(tmp_path / "voice.wav"), "rb") as track:
        assert (track.getnchannels(), track.getsampwidth(), track.getframerate()) == (1, 2, 48000)
        frames = track.readframes(track.getnframes())

    def sample(t: float) -> int:
        i = round(t * audio.SAMPLE_RATE) * 2
        return int.from_bytes(frames[i : i + 2], "little", signed=True)

    assert [sample(0.5), sample(1.2), sample(1.6), sample(2.0), sample(3.1)] == [0, 7, 9, 0, 5]


def test_mix_graph_band_gates_the_original_under_the_voice() -> None:
    graph = audio.mix_graph(audio.MixInputs(original=1, voice=2), AudioConfig(), duration=10)
    assert graph is not None
    assert "[1:a:0]" in graph and "[2:a:0]" in graph
    assert "acrossover=split='150 6000'" in graph
    # 0.25 (-12 dB) outside the voice band, 28 dB lower inside it (-40 dB).
    assert "[o_low]volume=0.250000" in graph and "[o_high]volume=0.250000" in graph
    assert "[o_band]volume=0.009953" in graph
    assert "amix=inputs=2:normalize=0:duration=longest" in graph
    assert "apad=whole_dur=10.000" in graph
    assert audio.LOUDNORM in graph
    assert graph.endswith("[aout]")


def test_mix_graph_without_voice_keeps_the_original_as_it_is() -> None:
    cfg = AudioConfig(loudnorm=False)
    graph = audio.mix_graph(audio.MixInputs(original=0), cfg, duration=5)
    assert graph is not None
    assert "acrossover" not in graph and "amix" not in graph and "loudnorm" not in graph
    assert audio.mix_graph(audio.MixInputs(), cfg, duration=5) is None


def test_mix_graph_music_fades_out() -> None:
    cfg = AudioConfig(music=Path("m.mp3"), music_volume=0.2)
    graph = audio.mix_graph(audio.MixInputs(voice=1, music=2), cfg, duration=30)
    assert graph is not None
    assert "[2:a:0]" in graph and "volume=0.200000,afade=t=out:st=28.000:d=2" in graph
    assert "amix=inputs=2" in graph
