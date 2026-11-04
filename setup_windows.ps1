$ErrorActionPreference = "Stop"
Write-Host "=== Karaoke Maker setup ===" -ForegroundColor Cyan

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing FFmpeg..." -ForegroundColor Yellow
        winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
        Write-Host "If FFmpeg was just installed, close this terminal, reopen it, and run the script again." -ForegroundColor Yellow
    } else {
        throw "FFmpeg is missing and winget was not found."
    }
}

$usePyLauncher = $false
try {
    & py -3.11 --version | Out-Null
    $usePyLauncher = $true
} catch {}

if (-not $usePyLauncher) {
    try {
        $ver = & python --version 2>&1
        if ($ver -notmatch "Python 3\.(10|11|12|13)") {
            throw "Unsupported Python version."
        }
    } catch {
        throw "Install Python 3.11, then run this script again."
    }
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    if ($usePyLauncher) {
        & py -3.11 -m venv .venv
    } else {
        & python -m venv .venv
    }
}

$python = Join-Path $PWD ".venv\Scripts\python.exe"
& $python -m pip install --upgrade pip setuptools wheel
& $python -m pip install -r requirements.txt

Write-Host ""
Write-Host "Setup complete. Run .\run_windows.bat" -ForegroundColor Green
