# build_windows.ps1
# Build VehicleDatasetManager.exe with PyInstaller.
#
# Usage:
#   .\build_windows.ps1                 # onedir build (recommended)
#   .\build_windows.ps1 -OneFile        # single-exe build
#   .\build_windows.ps1 -Clean          # remove build/ and dist/ first
#
# Large model files are NOT bundled; place them in  models/  next to the exe.

param(
    [switch]$OneFile,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$env:YOLO_CONFIG_DIR = Join-Path $root ".tmp\ultralytics-build"
$env:YOLO_AUTOINSTALL = "false"
New-Item -ItemType Directory -Force $env:YOLO_CONFIG_DIR | Out-Null

# The Codex desktop runtime can add its bundled Poppler directory to PATH.
# Its icuuc.dll has the same filename as the Windows ICU shim but exports a
# different ABI, so PyInstaller must not resolve Qt6Core against that folder.
$env:PATH = (($env:PATH -split ";") | Where-Object {
    $_ -notmatch "[\\/]codex-primary-runtime[\\/]"
}) -join ";"

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Warning ".venv not found. Creating it..."
    python -m venv .venv
    $py = Join-Path $root ".venv\Scripts\python.exe"
}

Write-Host "==> Ensuring dependencies" -ForegroundColor Cyan
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt
& $py -m pip install pyinstaller

if ($Clean) {
    Write-Host "==> Cleaning build/ and dist/" -ForegroundColor Cyan
    Remove-Item -Recurse -Force .\build, .\dist -ErrorAction SilentlyContinue
}

$mode = if ($OneFile) { "--onefile" } else { "--onedir" }

$pyiArgs = @("-m", "PyInstaller", "--noconfirm")
if ($Clean) {
    $pyiArgs += "--clean"
}
$pyiArgs += @(
    "--windowed",
    "--name", "VehicleDatasetManager",
    $mode,
    "--paths", $root,
    "--hidden-import", "vehicle_dataset_manager",
    "--collect-all", "pip",
    "run.py"
)

Write-Host "==> Running PyInstaller ($mode)" -ForegroundColor Cyan
& $py @pyiArgs

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

# A cached analysis from before PATH sanitization may still contain the
# incompatible Poppler ICU files. Qt uses the Windows system ICU instead.
if (-not $OneFile) {
    $internalDir = Join-Path $root "dist\VehicleDatasetManager\_internal"
    foreach ($strayIcu in @("icuuc.dll", "icudt78.dll")) {
        Remove-Item -LiteralPath (Join-Path $internalDir $strayIcu) `
            -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
if ($OneFile) {
    Write-Host "  dist\VehicleDatasetManager.exe"
} else {
    Write-Host "  dist\VehicleDatasetManager\VehicleDatasetManager.exe"
}
Write-Host "Place model files in: dist\VehicleDatasetManager\models\"
