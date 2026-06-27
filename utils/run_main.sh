#!/bin/bash
# run_main.sh — Auto-restart wrapper for main.py with 30s delay
# chmod +x run_main.sh

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PATH="$PROJECT_DIR/.venv"          # adjust if your venv is elsewhere
PYTHON="$VENV_PATH/bin/py"
MAIN_SCRIPT="$PROJECT_DIR/main.py"

# Ensure venv exists
if [ ! -f "$PYTHON" ]; then
    echo "[ERROR] Python not found at $PYTHON"
    echo "[ERROR] Ensure venv is created: py -m venv .venv"
    exit 1
fi

# Infinite restart loop
while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting main.py..."
    
    # Run the script; capture exit code
    "$PYTHON" "$MAIN_SCRIPT"
    EXIT_CODE=$?
    
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] main.py exited with code $EXIT_CODE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Restarting in 30 seconds..."
    
    sleep 30
done