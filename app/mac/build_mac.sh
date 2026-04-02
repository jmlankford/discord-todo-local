#!/bin/bash
# Discord To-Do — Mac build script (creates a standalone .app)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo " Discord To-Do Widget — Mac Builder"
echo "============================================"
echo ""

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found."
    echo "Install from https://www.python.org or: brew install python"
    exit 1
fi

if ! python3 -c "import tkinter" &>/dev/null; then
    echo "ERROR: tkinter not available."
    echo "Fix: brew install python-tk  or use python.org installer"
    exit 1
fi

echo "[1/3] Installing dependencies..."
python3 -m pip install --quiet requests apscheduler pytz pyinstaller

echo "[2/3] Building .app with PyInstaller..."
python3 -m PyInstaller \
    --onefile \
    --windowed \
    --name "DiscordTodo" \
    --hidden-import=apscheduler.schedulers.background \
    --hidden-import=apscheduler.triggers.cron \
    --hidden-import=apscheduler.executors.pool \
    --hidden-import=apscheduler.jobstores.memory \
    --hidden-import=pytz \
    discord_todo.py

echo ""
echo "[3/3] Copying config..."
if [ -f config.json ]; then
    cp config.json dist/config.json
    echo "    config.json copied to dist/"
else
    echo "    No config.json — setup screen will appear on first run."
fi

echo ""
echo "============================================"
echo " BUILD COMPLETE"
echo "============================================"
echo ""
echo " Executable: dist/DiscordTodo"
echo " Keep config.json in the same folder as the exe."
echo ""
