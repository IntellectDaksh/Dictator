#requires -version 5.1
<#
One-command setup: creates the venv, installs deps, tunes models to the hardware, makes sure Ollama has a
cleanup model pulled (suggests + downloads one if none found), then launches
Dictator. Safe to re-run any time — every step is skip-if-already-done.
#>

$ErrorActionPreference = "Stop"
# this script lives in scripts/, the project root is one level up
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host "== Dictator setup ==" -ForegroundColor Cyan

# 1. Git ----------------------------------------------------------------------
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Host "Git not found. Installing via winget..." -ForegroundColor Yellow
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Host "winget not found either. Install Git manually from https://git-scm.com/downloads, then re-run this script." -ForegroundColor Red
        exit 1
    }
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Write-Host "Git install finished but isn't on PATH yet - open a new terminal and re-run this script." -ForegroundColor Red
        exit 1
    }
}

# 2. Python -----------------------------------------------------------------
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "Python not found." -ForegroundColor Red
    Write-Host "Install Python 3.11+ from https://python.org (tick 'Add python.exe to PATH'), then re-run this script."
    exit 1
}

# 3. Virtual env + deps -------------------------------------------------------
$venvPip = "$root\.venv\Scripts\pip.exe"
if ((Test-Path "$root\.venv") -and -not (Test-Path $venvPip)) {
    Write-Host "Existing .venv is incomplete (no pip.exe) - recreating it." -ForegroundColor Yellow
    Remove-Item -Recurse -Force "$root\.venv"
}
if (-not (Test-Path "$root\.venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv "$root\.venv"
}
Write-Host "Installing dependencies (first run downloads CUDA libraries, ~1.3 GB - can take a few minutes)..."
& $venvPip install -q -r "$root\requirements.txt"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Dependency install failed - see errors above." -ForegroundColor Red
    exit 1
}

# 4. Hardware auto-tune + Ollama cleanup model --------------------------------
# hwtune.py reads RAM / GPU / CPU, picks the Whisper + Ollama models that keep
# dictation fast on this machine, writes them into config.json, then starts
# Ollama and pulls the model if it's missing. No prompts.
$ollamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue) -and -not (Test-Path $ollamaExe)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing Ollama (local AI runtime for the cleanup pass)..."
        winget install -e --id Ollama.Ollama --silent --accept-package-agreements --accept-source-agreements | Out-Null
    } else {
        Write-Host "Ollama not found and winget unavailable - install it from https://ollama.com/download" -ForegroundColor Yellow
    }
}
Write-Host ""
Write-Host "Tuning Dictator for this machine..."
& "$root\.venv\Scripts\python.exe" "$root\hwtune.py" --pull
if ($LASTEXITCODE -ne 0) {
    Write-Host "Cleanup model not ready - Dictator still works, Smart mode types the plain transcript until it is." -ForegroundColor Yellow
}

# 5. Launch -------------------------------------------------------------------
Write-Host ""
Write-Host "Setup done. Launching Dictator..." -ForegroundColor Green
Start-Process "$root\Dictator.bat"
