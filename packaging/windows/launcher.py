"""EraseDub.exe: tiny launcher for the portable Windows bundle.

Frozen with PyInstaller (see EraseDub.spec). Standard library only: the real application
(torch, gradio, ...) lives in the bundled ``python\\`` folder and is never frozen.

    EraseDub.exe                 -> erasedub webui --open (opens the local WebUI)
    EraseDub.exe run clip.mp4    -> erasedub run clip.mp4 (any CLI arguments pass through)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> int:
    root = bundle_root()
    python = root / "python" / "python.exe"
    if not python.is_file():
        sys.stderr.write(
            f"EraseDub: {python} not found. Extract the whole archive and run EraseDub.exe "
            "from the extracted folder, not from inside the zip/7z viewer.\n"
        )
        return 1

    env = os.environ.copy()
    # Isolate the bundled interpreter from any system-wide Python settings.
    for var in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(var, None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PATH"] = os.pathsep.join([str(root / "ffmpeg" / "bin"), env.get("PATH", "")])
    # Keep downloaded models inside the bundle unless the user chose another place.
    models = root / "models"
    env.setdefault("HF_HOME", str(models / "huggingface"))
    env.setdefault("TORCH_HOME", str(models / "torch"))
    env.setdefault("NLTK_DATA", str(models / "nltk_data"))

    args = sys.argv[1:] or ["webui", "--open"]
    # Runs the bundle's own python.exe (absolute path) with the user's own CLI arguments, no shell.
    proc = subprocess.Popen([str(python), "-m", "erasedub", *args], env=env)  # noqa: S603
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:
            continue  # Ctrl+C reaches the child too; wait for it to shut down cleanly


if __name__ == "__main__":
    sys.exit(main())
