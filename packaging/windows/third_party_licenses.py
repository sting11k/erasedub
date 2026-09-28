"""Write THIRD-PARTY-LICENSES.txt for every package installed in this Python, plus CPython itself.

Run it with the bundle's own interpreter (build.ps1 step 6), so it sees exactly what ships:

    python\\python.exe packaging\\windows\\third_party_licenses.py <output file>

Standard library only: nothing extra is installed into the bundle to build it.
"""

from __future__ import annotations

import re
import sys
import sysconfig
from importlib.metadata import Distribution, distributions
from pathlib import Path

LICENSE_FILE = re.compile(r"^(licen[cs]e|copying|notice|authors)", re.IGNORECASE)
RULE = "=" * 100


def license_of(dist: Distribution) -> str:
    meta = dist.metadata
    if expression := meta.get("License-Expression"):
        return expression
    declared = (meta.get("License") or "").strip()
    if declared and "\n" not in declared and len(declared) <= 100:
        return declared
    classifiers = [
        c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")
    ]
    return ", ".join(classifiers) or "see the licence text below"


def license_texts(dist: Distribution) -> list[tuple[str, str]]:
    texts = []
    for file in dist.files or []:
        parts = file.parts
        in_dist_info = len(parts) >= 2 and parts[0].endswith(".dist-info")
        if in_dist_info and ("licenses" in parts[1:-1] or LICENSE_FILE.match(parts[-1])):
            path = Path(str(file.locate()))
            if path.is_file():
                texts.append((str(file), path.read_text(encoding="utf-8", errors="replace").strip()))
    if not texts:
        # Older metadata puts the whole licence text in the License field.
        declared = (dist.metadata.get("License") or "").strip()
        if "\n" in declared:
            texts.append(("METADATA License field", declared))
    return texts


def cpython_license() -> str:
    candidates = [
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt",
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace").strip()
    raise SystemExit(f"CPython licence not found in {', '.join(map(str, candidates))}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} <output file>")
    dists = {}
    for dist in distributions():
        name = dist.metadata["Name"]
        if name and name.lower() != "erasedub":
            dists.setdefault(name.lower(), dist)

    out = [
        "Third-party software in this EraseDub bundle",
        "",
        "EraseDub itself is Apache-2.0 (LICENSE, NOTICE). ffmpeg is described in ffmpeg\\LICENSE.txt and",
        "ffmpeg\\SOURCE.txt. Below: CPython, then every Python package under python\\Lib\\site-packages,",
        "with the licence it declares and the licence files it ships.",
        "",
        "Packages:",
    ]
    out += [f"  {d.metadata['Name']} {d.version}: {license_of(d)}" for _, d in sorted(dists.items())]
    out += ["", RULE, f"CPython {sys.version.split()[0]}", RULE, "", cpython_license()]
    for _, dist in sorted(dists.items()):
        meta = dist.metadata
        out += ["", RULE, f"{meta['Name']} {dist.version}", f"Licence: {license_of(dist)}"]
        if home := meta.get("Home-page") or next(iter(meta.get_all("Project-URL") or []), None):
            out.append(f"Project: {home.split(', ', 1)[-1]}")
        out.append(RULE)
        texts = license_texts(dist)
        if not texts:
            out += ["", "(no licence file shipped in the package metadata)"]
        for name, text in texts:
            out += ["", f"--- {name} ---", "", text]
    Path(sys.argv[1]).write_text("\n".join(out) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {sys.argv[1]}: {len(dists)} packages\n")


if __name__ == "__main__":
    main()
