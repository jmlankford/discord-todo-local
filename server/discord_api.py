"""
TaskBot — Discord API Helpers
Thin wrappers around Discord REST API v10.
"""

import requests
import config

_HEADERS = {
    "Authorization": f"Bot {config.DISCORD_TOKEN}",
    "Content-Type": "application/json",
}


def _headers():
    """Return headers with the current token (re-evaluated at call time)."""
    return {
        "Authorization": f"Bot {config.DISCORD_TOKEN}",
        "Content-Type": "application/json",
    }


# ── Low-level HTTP ────────────────────────────────────────────────────────────

def discord_get(path: str) -> dict:
    r = requests.get(f"https://discord.com/api/v10{path}", headers=_headers())
    r.raise_for_status()
    return r.json()


def discord_post(path: str, payload: dict) -> dict:
    r = requests.post(
        f"https://discord.com/api/v10{path}",
        headers=_headers(),
        json=payload,
    )
    r.raise_for_status()
    return r.json()


def discord_patch(path: str, payload: dict) -> dict:
    r = requests.patch(
        f"https://discord.com/api/v10{path}",
        headers=_headers(),
        json=payload,
    )
    r.raise_for_status()
    return r.json()


def discord_delete(channel_id: str, message_id: str) -> None:
    """Delete a single message. Requires Manage Messages permission."""
    try:
        r = requests.delete(
            f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
            headers=_headers(),
        )
        if r.status_code not in (200, 204):
            print(f"  [warn] Delete msg {message_id} returned {r.status_code}")
    except Exception as e:
        print(f"  [warn] Could not delete message {message_id}: {e}")


# ── Thread helpers ────────────────────────────────────────────────────────────

def get_all_threads() -> list:
    """Return all active + first page of archived public threads visible to the bot."""
    threads = []

    data = discord_get(f"/guilds/{config.GUILD_ID}/threads/active")
    threads.extend(data.get("threads", []))

    try:
        data = discord_get(
            f"/channels/{config.CHANNEL_ID}/threads/archived/public?limit=100"
        )
        threads.extend(data.get("threads", []))
    except Exception as e:
        print(f"  [warn] Could not fetch archived threads: {e}")

    return threads


def find_thread(threads: list, name: str, channel_id: str = None):
    cid = channel_id or config.CHANNEL_ID
    return next(
        (t for t in threads
         if t["name"].lower() == name.lower() and t["parent_id"] == cid),
        None,
    )


def unarchive_thread(thread_id: str) -> None:
    try:
        discord_patch(f"/channels/{thread_id}", {"archived": False, "locked": False})
    except Exception as e:
        print(f"  [warn] Could not unarchive thread {thread_id}: {e}")


def find_or_create_thread(threads: list, name: str, channel_id: str = None) -> str:
    """Find (and unarchive if needed) or create a thread. Returns thread_id."""
    cid = channel_id or config.CHANNEL_ID
    thread = find_thread(threads, name, cid)

    if thread:
        if thread.get("thread_metadata", {}).get("archived", False):
            print(f"  Unarchiving thread '{name}'...")
            unarchive_thread(thread["id"])
        return thread["id"]

    print(f"  Creating new thread '{name}'...")
    result = discord_post(
        f"/channels/{cid}/threads",
        {
            "name": name,
            "type": 11,             # PUBLIC_THREAD
            "auto_archive_duration": 10080,  # 7 days
        },
    )
    return result["id"]


def get_thread_messages(thread_id: str) -> list:
    """Fetch all messages from a thread (handles pagination). Newest-first."""
    messages = []
    last_id = None
    while True:
        path = f"/channels/{thread_id}/messages?limit=100"
        if last_id:
            path += f"&before={last_id}"
        batch = discord_get(path)
        if not batch:
            break
        messages.extend(batch)
        if len(batch) < 100:
            break
        last_id = batch[-1]["id"]
    return messages


def send_message(thread_id: str, content: str) -> dict:
    return discord_post(f"/channels/{thread_id}/messages", {"content": content})
