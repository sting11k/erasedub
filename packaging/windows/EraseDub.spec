# PyInstaller spec for the small EraseDub.exe launcher (standard library only).
# Built by build.ps1 with the hash-pinned PyInstaller from build-tools.txt.
# One-file mode: unpacks to %TEMP% on each start (slower, more AV heuristics than one-dir).
# TODO: revisit one-dir (exclude_binaries=True + COLLECT) together with code signing.
# The application itself is NOT frozen; see README.md in this folder for why.
# ruff: noqa

block_cipher = None

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=[],  # codespell:ignore
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # Nothing heavy may leak into the launcher.
    excludes=["tkinter", "unittest", "pydoc", "email", "http", "xml", "sqlite3"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,  # codespell:ignore
    [],
    name="EraseDub",
    console=True,  # progress and errors are printed to the console window
    debug=False,
    strip=False,
    upx=False,  # UPX-packed executables trigger more antivirus false positives
    icon=None,  # TODO: add packaging/windows/erasedub.ico once the logo exists
    version=None,
)
