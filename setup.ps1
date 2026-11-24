# One-command Windows bootstrap: creates .venv, installs dependencies, and runs `cli.py setup`.
$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating Python virtual environment (.venv)..."
    python -m venv .venv
}

Write-Host "Installing dependencies into .venv..."
& .venv\Scripts\pip.exe install -r requirements.txt

Write-Host ""
& .venv\Scripts\python.exe cli.py setup @args
