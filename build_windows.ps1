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

$pyiArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", "VehicleDatasetManager",
    $mode,
    "--paths", $root,
    "--collect-all", "PySide6",
    "--collect-submodules", "cv2",
    "--hidden-import", "vehicle_dataset_manager",
    "run.py"
)

Write-Host "==> Running PyInstaller ($mode)" -ForegroundColor Cyan
& $py @pyiArgs

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
if ($OneFile) {
    Write-Host "  dist\VehicleDatasetManager\VehicleDatasetManager.exe"
} else {
    Write-Host "  dist\VehicleDatasetManager\VehicleDatasetManager.exe"
}
Write-Host "Place model files in: dist\VehicleDatasetManager\models\"