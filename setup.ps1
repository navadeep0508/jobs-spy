# Job Radar - Setup script for Windows
# Run in PowerShell: .\setup.ps1
#
# This script:
#   1. Verifies Python is available
#   2. Installs Python dependencies from backend/requirements.txt
#   3. Copies .env.example to .env (if .env does not already exist)
#   4. Runs `python main.py --list` to verify the pipeline loads
#
# After setup, edit .env to add your NVIDIA_API_KEY, TELEGRAM_BOT_TOKEN and
# TELEGRAM_CHAT_ID, then run .\start.bat or .\run_once.bat.

$ErrorActionPreference = "Stop"
$backendDir = Join-Path $PSScriptRoot "backend"

Write-Host "=== Job Radar Setup ===" -ForegroundColor Cyan
Write-Host ""

# --- 1. Check Python ---------------------------------------------------------
if (!(Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Python is not installed or not on PATH." -ForegroundColor Red
    Write-Host "Install Python from https://www.python.org/ and re-run this script." -ForegroundColor Red
    exit 1
}
Write-Host "Python: $(python --version)" -ForegroundColor Green

# --- 2. Install dependencies -------------------------------------------------
Write-Host ""
Write-Host "Installing dependencies from backend/requirements.txt ..." -ForegroundColor Cyan
python -m pip install -r "$backendDir/requirements.txt"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install dependencies." -ForegroundColor Red
    exit 1
}
Write-Host "Dependencies installed." -ForegroundColor Green

# --- 3. Create .env from .env.example ----------------------------------------
$envExample = Join-Path $PSScriptRoot ".env.example"
$envFile = Join-Path $PSScriptRoot ".env"

Write-Host ""
if (!(Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Host "Created .env from .env.example." -ForegroundColor Green
        Write-Host "Edit .env to add your NVIDIA_API_KEY, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID." -ForegroundColor Yellow
    } else {
        Write-Host "WARNING: .env.example not found - skipping .env creation." -ForegroundColor Yellow
    }
} else {
    Write-Host ".env already exists - leaving it untouched." -ForegroundColor Yellow
}

# --- 4. Verify pipeline loads ------------------------------------------------
Write-Host ""
Write-Host "Verifying pipeline (`python main.py --list`) ..." -ForegroundColor Cyan
python "$backendDir/main.py" --list
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Pipeline verification failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Cyan
Write-Host "Next steps:"
Write-Host "  1. Edit .env and add your NVIDIA_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID"
Write-Host "  2. Run: .\run_once.bat    (single pipeline run, no Telegram)"
Write-Host "     or: .\start.bat        (full pipeline, with Telegram alerts)"
