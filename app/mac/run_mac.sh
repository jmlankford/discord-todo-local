#!/bin/bash
# Discord To-Do — Mac launcher (runs from source, no build needed)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo " Discord To-Do Widget — Mac Launcher"
echo "============================================"
echo ""

# Check for Python 3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found."
    echo "Install it from https://www.python.org or via Homebrew: brew install python"
    exit 1
fi

# Check for tkinter (common issue on Mac)
if ! python3 -c "import tkinter" &>/dev/null; then
    echo "ERROR: tkinter is not available in your Python installation."
    echo ""
    echo "Fix options:"
    echo "  • Homebrew Python:  brew install python-tk"
    echo "  • Or install Python from https://www.python.org (includes tkinter)"
    exit 1
fi

# Create virtual environment if it doesn't exist
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "[1/3] Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
else
    echo "[1/3] Virtual environment already exists, skipping."
fi

# Install dependencies into venv
echo "[2/3] Installing dependencies..."
"$VENV_DIR/bin/pip" install --quiet requests apscheduler pytz

echo "[3/3] Launching Discord To-Do..."
echo ""
"$VENV_DIR/bin/python" discord_todo.py
