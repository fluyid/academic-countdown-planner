#!/usr/bin/env bash
# One-command macOS bootstrap: creates .venv, installs dependencies, and runs `cli.py setup`.
set -euo pipefail

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating Python virtual environment (.venv)..."
  python3 -m venv .venv
fi

echo "Installing dependencies into .venv..."
.venv/bin/pip install -r requirements.txt

echo ""
.venv/bin/python cli.py setup "$@"
