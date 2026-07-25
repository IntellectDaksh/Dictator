#requires -version 5.1
<#
Piped straight from GitHub: irm https://raw.githubusercontent.com/IntellectDaksh/Dictator/main/scripts/bootstrap.ps1 | iex
Clones the repo (if not already sitting in it) then hands off to install.ps1.
#>
$ErrorActionPreference = "Stop"

function Test-InRepo {
    (Test-Path ".\main.py") -and (Test-Path ".\scripts\install.ps1")
}

if (Test-InRepo) {
    $root = Get-Location
} else {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $winget) {
            Write-Host "Git not found and winget isn't available - install Git from https://git-scm.com/downloads, then re-run." -ForegroundColor Red
            exit 1
        }
        Write-Host "Installing Git via winget..." -ForegroundColor Yellow
        winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    }
    $dest = Join-Path $HOME "Dictator"
    if (-not (Test-Path $dest)) {
        Write-Host "Cloning Dictator to $dest ..." -ForegroundColor Cyan
        git clone https://github.com/IntellectDaksh/Dictator.git $dest
    }
    Set-Location $dest
    $root = Get-Location
}

& "$root\scripts\install.ps1"
