"""Audio file helpers shared by the speech synthesizers."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

from erasedub.hardware import find_ffprobe


def ffprobe_duration(path: Path) -> float | None:
    """Duration of a media file in seconds as ffprobe reports it; ``None`` without ffprobe or on failure."""
    ffprobe = find_ffprobe()
    if ffprobe is None:
        return None
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)]
    try:
        done = subprocess.run(cmd, capture_output=True, encoding="utf-8", timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        value = float(done.stdout.strip())
    except ValueError:
        return None
    return value if done.returncode == 0 and math.isfinite(value) and value > 0 else None


def audio_duration(path: Path, *, cbr_kbps: int) -> float:
    """Duration of an MP3 file written at a constant ``cbr_kbps`` bit rate.

    ffprobe is used when available; otherwise the size is divided by the bit rate, which is exact for
    constant-bit-rate streams without tags. Never returns zero.
    """
    probed = ffprobe_duration(path)
    if probed is not None:
        return probed
    return max(0.001, path.stat().st_size * 8 / (cbr_kbps * 1000))


def mp3_path(output: Path) -> Path:
    """``output`` with an ``.mp3`` suffix: the voice providers here always produce MP3."""
    return output if output.suffix.lower() == ".mp3" else output.with_suffix(".mp3")
