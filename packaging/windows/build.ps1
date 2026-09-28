#Requires -Version 7.0
<#
.SYNOPSIS
    Build the portable EraseDub bundle for Windows x64 + NVIDIA.

.DESCRIPTION
    Produces dist\EraseDub-<version>-win64-cu128\ and a split 7z archive (GitHub release assets are
    limited to 2 GiB each). Layout:

        EraseDub.exe / EraseDub.cmd   launcher (runs python\python.exe -m erasedub ...)
        python\                       python-build-standalone CPython 3.12 + all dependencies
        ffmpeg\bin\                   BtbN static GPL ffmpeg.exe / ffprobe.exe (+ ffmpeg\LICENSE.txt)
        models\                       empty; models are downloaded here on first use

    Every download comes from an official source and is checked against a pinned sha256:
      - CPython: github.com/astral-sh/python-build-standalone
      - ffmpeg:  github.com/BtbN/FFmpeg-Builds
      - Python packages: PyPI via the hashes in uv.lock (uv export)
      - build backends (hatchling, setuptools): PyPI, hash-pinned in packaging\build-constraints.txt
      - PyInstaller (launcher only): PyPI, hash-pinned in build-tools.txt
      - CUDA PyTorch: download.pytorch.org (torch-cu128.txt), the only non-PyPI source, because
        PyPI torch wheels are CPU-only on Windows.

    Needs: Windows 10/11 x64, PowerShell 7+, uv on PATH, 7-Zip (7z.exe) for the archive step,
    ~25 GB free disk.

.EXAMPLE
    pwsh packaging\windows\build.ps1
    pwsh packaging\windows\build.ps1 -SkipArchive
#>
[CmdletBinding()]
param(
    [string]$OutDir = "dist",
    [string]$WorkDir = "build\windows",
    [switch]$SkipArchive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is very slow with the progress bar

# --- Pins --------------------------------------------------------------------------------------
# Bump these together and take the sha256 values from the release pages.
$PythonVersion = "3.12.14"
$PbsRelease = "20260924"
$PythonAsset = "cpython-$PythonVersion+$PbsRelease-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
$PythonSha256 = "c5bf8edfe858c1df9891be498b5bbc8761d383df5b9790658b088fea4870433a"

$FfmpegRelease = "autobuild-2026-08-31-13-27"
$FfmpegName = "ffmpeg-n8.1.2-50-g1a748fe2cd-win64-gpl-8.1"
$FfmpegSha256 = "273abb45f3f9f76c303e35ff39f5bb6c23c163ae65f6244a32b7d4a7f6cf0616"

# Commits needed to point at the exact GPL corresponding source (see ffmpeg\SOURCE.txt).
$FfmpegCommit = "1a748fe2cd43e3ead22fafb1b5b7d77f153898a8"        # upstream FFmpeg
$FfmpegBuildsCommit = "8267213e26c1031621e6e1210fe3aa4867214f6a"  # BtbN/FFmpeg-Builds at $FfmpegRelease

$CudaFlavour = "cu128"
$TorchPackages = @("torch", "torchaudio", "torchvision")  # replaced by torch-$CudaFlavour.txt
# -----------------------------------------------------------------------------------------------

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot
$BuildConstraints = Join-Path $RepoRoot "packaging\build-constraints.txt"

function Get-VerifiedDownload {
    param([string]$Url, [string]$Sha256, [string]$Destination)
    if (-not (Test-Path $Destination)) {
        Write-Host "Downloading $Url"
        Invoke-WebRequest -Uri $Url -OutFile "$Destination.part" -UseBasicParsing
        Move-Item "$Destination.part" $Destination
    }
    $actual = (Get-FileHash -Algorithm SHA256 $Destination).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256.ToLowerInvariant()) {
        Remove-Item $Destination
        throw "sha256 mismatch for ${Url}: got $actual, expected $Sha256"
    }
}

foreach ($tool in @("uv")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool not found on PATH" }
}

$initPy = Get-Content (Join-Path $RepoRoot "src\erasedub\__init__.py") -Raw
if ($initPy -notmatch '__version__\s*=\s*"([^"]+)"') { throw "cannot read __version__" }
$Version = $Matches[1]
$BundleName = "EraseDub-$Version-win64-$CudaFlavour"
$Downloads = Join-Path $WorkDir "downloads"
$Bundle = Join-Path $OutDir $BundleName
New-Item -ItemType Directory -Force $Downloads, $OutDir | Out-Null
if (Test-Path $Bundle) { Remove-Item -Recurse -Force $Bundle }
New-Item -ItemType Directory -Force $Bundle | Out-Null

# 1. CPython (relocatable python-build-standalone "install_only" build, extracts to .\python).
$pyArchive = Join-Path $Downloads $PythonAsset
Get-VerifiedDownload `
    -Url "https://github.com/astral-sh/python-build-standalone/releases/download/$PbsRelease/$([uri]::EscapeDataString($PythonAsset))" `
    -Sha256 $PythonSha256 -Destination $pyArchive
tar -xzf $pyArchive -C $Bundle
if ($LASTEXITCODE -ne 0) { throw "failed to extract $PythonAsset" }
$BundlePython = Join-Path $Bundle "python\python.exe"

# 2. ffmpeg (static GPL build; keep its licence next to the binaries).
$ffZip = Join-Path $Downloads "$FfmpegName.zip"
Get-VerifiedDownload `
    -Url "https://github.com/BtbN/FFmpeg-Builds/releases/download/$FfmpegRelease/$FfmpegName.zip" `
    -Sha256 $FfmpegSha256 -Destination $ffZip
$ffTmp = Join-Path $WorkDir "ffmpeg"
if (Test-Path $ffTmp) { Remove-Item -Recurse -Force $ffTmp }
Expand-Archive $ffZip -DestinationPath $ffTmp
$ffOut = Join-Path $Bundle "ffmpeg"
New-Item -ItemType Directory -Force (Join-Path $ffOut "bin") | Out-Null
Copy-Item (Join-Path $ffTmp "$FfmpegName\bin\ffmpeg.exe"), (Join-Path $ffTmp "$FfmpegName\bin\ffprobe.exe") (Join-Path $ffOut "bin")
Copy-Item (Join-Path $ffTmp "$FfmpegName\LICENSE.txt") $ffOut
Copy-Item -Recurse (Join-Path $ffTmp "$FfmpegName\doc") (Join-Path $ffOut "doc")
@"
ffmpeg.exe and ffprobe.exe are the static "gpl" build from BtbN/FFmpeg-Builds,
licensed under the GNU GPL v3 (see LICENSE.txt).

  Archive:        https://github.com/BtbN/FFmpeg-Builds/releases/download/$FfmpegRelease/$FfmpegName.zip
  sha256:         $FfmpegSha256
  FFmpeg source:  https://github.com/FFmpeg/FFmpeg/commit/$FfmpegCommit
                  (official repository: https://git.ffmpeg.org/ffmpeg.git)
  Build scripts and bundled library versions (x264, libass, ...):
                  https://github.com/BtbN/FFmpeg-Builds/tree/$FfmpegBuildsCommit

EraseDub runs ffmpeg as a separate program; EraseDub itself is Apache-2.0.
"@ | Set-Content -Encoding utf8 (Join-Path $ffOut "SOURCE.txt")

# 3. Dependencies: the locked set from uv.lock (with hashes), torch swapped for the CUDA build.
$pypiReq = Join-Path $WorkDir "requirements-pypi.txt"
& uv export --locked --no-dev --extra full --format requirements-txt --no-emit-project `
    --no-header --output-file $pypiReq
if ($LASTEXITCODE -ne 0) { throw "uv export failed" }

# Drop the PyPI (CPU) torch entries: a requirement line plus its indented --hash / "# via" lines.
$kept = New-Object System.Collections.Generic.List[string]
$lockedTorch = @{}
$skipping = $false
foreach ($line in Get-Content $pypiReq) {
    if ($line -match '^([A-Za-z0-9_.\-]+)==([^\s;\\]+)') {
        $pkg = $Matches[1].ToLowerInvariant()
        $skipping = $TorchPackages -contains $pkg
        if ($skipping) { $lockedTorch[$pkg] = $Matches[2] }
    } elseif ($line -notmatch '^\s') {
        $skipping = $false
    }
    if (-not $skipping) { $kept.Add($line) }
}
# The CUDA wheels must be the exact versions uv.lock resolved; fail loudly on drift.
$torchReq = Get-Content (Join-Path $PSScriptRoot "torch-$CudaFlavour.txt")
foreach ($pkg in $TorchPackages) {
    $pinLine = $torchReq | Where-Object { $_ -match "^$pkg @ " } | Select-Object -First 1
    if (-not $pinLine -or $pinLine -notmatch "/$pkg-([^%/]+)%2B$CudaFlavour-") {
        throw "torch-$CudaFlavour.txt has no pinned wheel for $pkg"
    }
    $pinned = $Matches[1]
    if (-not $lockedTorch.ContainsKey($pkg)) { throw "$pkg is not in the uv export any more" }
    if ($lockedTorch[$pkg] -ne $pinned) {
        throw "$pkg drift: uv.lock has $($lockedTorch[$pkg]), torch-$CudaFlavour.txt pins $pinned. Update the CUDA pins."
    }
}
$req = Join-Path $WorkDir "requirements-bundle.txt"
@($kept) + @($torchReq) | Set-Content -Encoding utf8 $req

& uv pip install --python $BundlePython --break-system-packages --require-hashes `
    --build-constraints $BuildConstraints --index-url https://pypi.org/simple -r $req
if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }

# 4. EraseDub itself (built from this checkout; its dependencies are already installed above).
$wheelDir = Join-Path $WorkDir "wheel"
if (Test-Path $wheelDir) { Remove-Item -Recurse -Force $wheelDir }
& uv build --wheel --build-constraints $BuildConstraints --out-dir $wheelDir
if ($LASTEXITCODE -ne 0) { throw "uv build failed" }
$wheel = Get-ChildItem (Join-Path $wheelDir "erasedub-*.whl") | Select-Object -First 1
& uv pip install --python $BundlePython --break-system-packages --no-deps $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "erasedub install failed" }

# 5. Launchers: a tiny PyInstaller-frozen EraseDub.exe (stdlib only) + a .cmd fallback.
#    PyInstaller comes from a hash-pinned requirements file, in a throwaway venv.
$toolsVenv = Join-Path $WorkDir "tools-venv"
if (Test-Path $toolsVenv) { Remove-Item -Recurse -Force $toolsVenv }
& uv venv --python 3.12 $toolsVenv
if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
$toolsPython = Join-Path $toolsVenv "Scripts\python.exe"
& uv pip install --python $toolsPython --require-hashes --build-constraints $BuildConstraints `
    --index-url https://pypi.org/simple -r (Join-Path $PSScriptRoot "build-tools.txt")
if ($LASTEXITCODE -ne 0) { throw "installing build tools failed" }
& $toolsPython -m PyInstaller --noconfirm --distpath $Bundle `
    --workpath (Join-Path $WorkDir "pyinstaller") (Join-Path $PSScriptRoot "EraseDub.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Copy-Item (Join-Path $PSScriptRoot "EraseDub.cmd") $Bundle
New-Item -ItemType Directory -Force (Join-Path $Bundle "models") | Out-Null

# 6. Licences and notices.
Copy-Item LICENSE, NOTICE $Bundle
#    THIRD-PARTY-LICENSES.txt: CPython and every package in python\Lib\site-packages, written by the bundle's
#    own interpreter (stdlib only, so nothing extra is installed to build it).
& $BundlePython (Join-Path $PSScriptRoot "third_party_licenses.py") (Join-Path $Bundle "THIRD-PARTY-LICENSES.txt")
if ($LASTEXITCODE -ne 0) { throw "writing THIRD-PARTY-LICENSES.txt failed" }

# 7. Smoke test (no GPU on CI runners: this only proves the bundle imports and starts).
& $BundlePython -m erasedub version
if ($LASTEXITCODE -ne 0) { throw "smoke test failed: erasedub version" }
& $BundlePython -c "import torch; print('torch', torch.__version__, 'cuda build', torch.version.cuda)"
if ($LASTEXITCODE -ne 0) { throw "smoke test failed: import torch" }
& (Join-Path $Bundle "EraseDub.exe") version
if ($LASTEXITCODE -ne 0) { throw "smoke test failed: EraseDub.exe version" }

# 8. Archive: split 7z volumes (< 2 GiB each) + checksums.
if (-not $SkipArchive) {
    if (-not (Get-Command 7z -ErrorAction SilentlyContinue)) { throw "7z not found on PATH" }
    $archive = Join-Path (Resolve-Path $OutDir) "$BundleName.7z"
    Push-Location $OutDir
    try {
        & 7z a -t7z -mx=7 -v1900m $archive $BundleName
        if ($LASTEXITCODE -ne 0) { throw "7z failed" }
    } finally {
        Pop-Location
    }
    Get-ChildItem $OutDir -Filter "$BundleName.7z*" | ForEach-Object {
        "$((Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant())  $($_.Name)"
    } | Set-Content -Encoding ascii (Join-Path $OutDir "SHA256SUMS.txt")
}

Write-Host "Bundle ready: $Bundle"
