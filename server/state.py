"""
TaskBot — State Management
Thread-safe JSON file storage. Replaces Google Cloud Storage.
State is persisted to a Docker volume so it survives container restarts.
"""

import json
import os
import threading

import config

_lock = threading.Lock()


def load() -> dict:
    """Load the full state dict from disk. Returns {} if missing or unreadable."""
    try:
        with _lock:
            if os.path.exists(config.STATE_FILE):
                with open(config.STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
    except Exception as e:
        print(f"[state] Could not load state: {e}")
    return {}


def save(state: dict) -> None:
    """Write the full state dict to disk atomically."""
    try:
        with _lock:
            os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
            tmp = config.STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, config.STATE_FILE)  # atomic on POSIX
    except Exception as e:
        print(f"[state] Could not save state: {e}")
