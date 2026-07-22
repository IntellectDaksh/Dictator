#requires -version 5.1
<#
One-command setup: creates the venv, installs deps, makes sure Ollama has a
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

# 4. Ollama + cleanup model ---------------------------------------------------
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Write-Host ""
    Write-Host "Ollama not found." -ForegroundColor Yellow
    Write-Host "Install it from https://ollama.com/download to get transcript cleanup (filler-word removal, grammar fixes)."
    Write-Host "Dictator still works without it - it just types your raw transcript instead."
} else {
    $tags = $null
    try {
        $tags = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 5
    } catch {
        Write-Host "Starting Ollama..."
        Start-Process "ollama" "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 3
        try { $tags = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 5 } catch { $tags = $null }
    }

    $preferred = @("qwen3:14b", "qwen2.5:7b-instruct", "llama3.1:8b")
    $have = @()
    if ($tags -and $tags.models) { $have = $tags.models | ForEach-Object { $_.name } }

    $found = $false
    foreach ($p in $preferred) {
        $base = $p.Split(":")[0]
        if ($have | Where-Object { $_ -like "$base*" }) { $found = $true; break }
    }

    if (-not $found) {
        Write-Host ""
        Write-Host "No cleanup model installed yet." -ForegroundColor Cyan
        Write-Host "Suggested: qwen2.5:7b-instruct (~4.7 GB, one-time download, good quality/speed balance)."
        Write-Host "Pulling it now - Ctrl+C to skip, Dictator still works without it."
        ollama pull qwen2.5:7b-instruct
    } else {
        Write-Host "Cleanup model already installed."
    }
}

# 5. Launch -------------------------------------------------------------------
Write-Host ""
Write-Host "Setup done. Launching Dictator..." -ForegroundColor Green
Start-Process "$root\Dictator.bat"
