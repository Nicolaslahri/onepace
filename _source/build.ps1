# Builds OnePaceDownloader.exe. Run from this folder:
#
#     pip install -r requirements.txt
#     .\build.ps1
#
# Output lands in dist\OnePaceDownloader.exe; copy it to the repo root to
# replace the shipped binary.

$ErrorActionPreference = "Stop"

# --collect-data certifi is load-bearing. tls_trust.py trusts certifi's
# cacert.pem before the OS certificate store, and without this flag the
# bundle never makes it into the .exe -- the app silently falls back to the
# OS store and users whose Windows predates ISRG Root X2 still can't reach
# pixeldrain. See tls_trust.py for the full story.
#
# The .json files are read from sys._MEIPASS at runtime (see _bundle_dir in
# onepace_downloader.py), so they have to be bundled, not just sat next to
# the .exe.
#
# `python -m PyInstaller` rather than the bare `pyinstaller` shim: pip
# installs the shim into a per-user Scripts dir that often isn't on PATH.
#
# PyInstaller writes its progress log to stderr. Windows PowerShell wraps
# native stderr in ErrorRecords, so under ErrorActionPreference=Stop a plain
# INFO line aborts the build -- and merely running `.\build.ps1 2>&1` is
# enough to trigger it. Drop to Continue around the call and check the real
# exit code instead.
$ErrorActionPreference = "Continue"

python -m PyInstaller `
    --onefile `
    --windowed `
    --clean `
    --noconfirm `
    --name OnePaceDownloader `
    --icon ..\assets\icon.ico `
    --collect-data certifi `
    --add-data "arcs.json;." `
    --add-data "muhn_arcs.json;." `
    --add-data "nyaa_arcs.json;." `
    --add-data "usenet_arcs.json;." `
    --add-data "episode_index.json;." `
    --add-data "..\assets\icon.ico;." `
    --add-data "..\assets\icon.png;." `
    onepace_downloader.py

$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($code -ne 0) { throw "PyInstaller failed with exit code $code" }

$exe = Resolve-Path dist\OnePaceDownloader.exe
Write-Host ""
Write-Host "Built: $exe"
Write-Host "Size:  $([math]::Round((Get-Item $exe).Length / 1MB, 2)) MB"
