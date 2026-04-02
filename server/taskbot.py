"""
TaskBot — Core Logic
Handles reading Discord threads, sending SMS task lists, and processing replies.
Replaces the Cloud Function endpoints /send and /reply.
State is stored in a local JSON file (see state.py) instead of Google Cloud Storage.
"""

import pytz
from datetime import datetime
from twilio.rest import Client

import config
import state
import discord_api as discord

ET = pytz.timezone("America/New_York")


# ── SMS ───────────────────────────────────────────────────────────────────────

def _twilio_client():
    return Client(config.TWILIO_SID, config.TWILIO_AUTH)


def send_sms(to: str, body: str) -> None:
    """Send SMS via Twilio, splitting into chunks if over 1500 chars."""
    client = _twilio_client()

    if len(body) <= 1500:
        client.messages.create(to=to, from_=config.TWILIO_FROM, body=body)
        return

    lines = body.split("\n")
    chunks, current = [], ""
    for line in lines:
        candidate = (current + "\n" + line).strip() if current else line
        if len(candidate) > 1400:
            if current:
                chunks.append(current.strip())
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current.strip())

    total = len(chunks)
    for i, chunk in enumerate(chunks):
        labeled = f"({i+1}/{total})\n{chunk}" if total > 1 else chunk
        client.messages.create(to=to, from_=config.TWILIO_FROM, body=labeled)


# ── Task list helpers ─────────────────────────────────────────────────────────

def get_tasks_for_user(username: str, threads: list):
    """
    Read a user's Discord thread and return deduplicated tasks.
    Returns: (tasks, thread_id, task_message_ids, message_task_count)
    """
    thread = discord.find_thread(threads, username)
    if not thread:
        print(f"  [warn] No thread found for '{username}'")
        return [], None, {}, {}

    thread_id = thread["id"]

    if thread.get("thread_metadata", {}).get("archived", False):
        discord.unarchive_thread(thread_id)

    messages = discord.get_thread_messages(thread_id)

    seen = set()
    tasks = []
    task_message_ids = {}
    message_task_count = {}

    existing_state = state.load()
    blocklist = set(existing_state.get(username, {}).get("completed_tasks_blocklist", []))

    for msg in reversed(messages):
        if msg.get("author", {}).get("bot", False):
            continue
        content = msg.get("content", "").strip()
        if not content:
            continue

        msg_id = msg["id"]
        for line in content.split("\n"):
            line = line.strip()
            if not line or line.startswith("✅") or line.startswith("---"):
                continue
            key = line.lower()
            message_task_count[msg_id] = message_task_count.get(msg_id, 0) + 1

            if key in blocklist:
                continue

            if key not in seen:
                seen.add(key)
                tasks.append(line)
                task_message_ids[line] = [msg_id]
            else:
                canonical = next((t for t in task_message_ids if t.lower() == key), None)
                if canonical and msg_id not in task_message_ids[canonical]:
                    task_message_ids[canonical].append(msg_id)

    return tasks, thread_id, task_message_ids, message_task_count


def format_task_list(username: str, tasks: list) -> str:
    if not tasks:
        return f"Hi {username}! You have no tasks right now — great work! 🎉"
    lines = [f"📋 {username}'s Tasks:"]
    for i, task in enumerate(tasks, 1):
        lines.append(f"{i}. {task}")
    lines.append("\nReply with a number to mark a task complete.")
    return "\n".join(lines)


# ── Send handler ──────────────────────────────────────────────────────────────

def handle_send(only_user: str = None) -> None:
    """
    Read each user's Discord thread, deduplicate tasks, send numbered SMS lists.
    Pass only_user to limit to a single user (useful for testing).
    Called directly by the scheduler — not over HTTP.
    """
    print(f"[send] Starting{f' (only: {only_user})' if only_user else ''}...")
    threads = discord.get_all_threads()
    current_state = state.load()

    for username, user_info in config.USERS.items():
        if only_user and username.lower() != only_user.lower():
            continue

        phone = user_info.get("phone", "")
        if not phone:
            print(f"  [skip] No phone configured for {username}")
            continue

        print(f"[send] Processing {username}...")
        tasks, thread_id, task_message_ids, message_task_count = get_tasks_for_user(
            username, threads
        )

        if thread_id is None:
            print(f"  Skipping {username} — no thread found.")
            continue

        numbered = {str(i + 1): task for i, task in enumerate(tasks)}
        current_state[username] = {
            "tasks": tasks,
            "numbered": numbered,
            "thread_id": thread_id,
            "phone": phone,
            "sent_at": datetime.now(ET).isoformat(),
            "task_message_ids": task_message_ids,
            "message_task_count": message_task_count,
            "completed_tasks_blocklist": [],
        }

        send_sms(phone, format_task_list(username, tasks))
        print(f"  ✓ Sent {len(tasks)} task(s) to {username}")

    state.save(current_state)
    print("[send] Done.")


# ── Reply handler ─────────────────────────────────────────────────────────────

def handle_reply(from_number: str, body: str) -> None:
    """
    Called when a user texts back a number (or space-separated numbers).
    Marks tasks complete in Discord and sends an updated list.
    """
    # Normalize to E.164
    if from_number and not from_number.startswith("+"):
        from_number = "+" + from_number

    print(f"[reply] {from_number} → '{body}'")

    username = config.PHONE_TO_USER.get(from_number)
    if not username:
        print(f"  Unknown number: {from_number}")
        return

    raw_nums = body.strip().split()
    task_nums = [n for n in raw_nums if n.isdigit()]
    if not task_nums:
        send_sms(from_number, "Reply with number(s) to complete tasks (e.g. '3' or '2 5 6').")
        return

    current_state = state.load()
    user_state = current_state.get(username)

    if not user_state:
        send_sms(from_number, "No active task list found. Your list arrives between 6:30–8:30 PM!")
        return

    numbered = user_state.get("numbered", {})

    invalid = [n for n in task_nums if n not in numbered]
    if invalid:
        total = len(user_state.get("tasks", []))
        send_sms(
            from_number,
            f"No task(s) #{', '.join(invalid)}. You have {total} task(s) — reply with 1–{total}.",
        )
        return

    completed_names = [numbered[n] for n in task_nums]

    tasks = [t for t in user_state.get("tasks", []) if t not in completed_names]
    numbered_new = {str(i + 1): t for i, t in enumerate(tasks)}
    current_state[username]["tasks"] = tasks
    current_state[username]["numbered"] = numbered_new

    now_et = datetime.now(ET)
    timestamp = now_et.strftime("%B %d, %Y at %I:%M %p ET")
    threads = discord.get_all_threads()

    active_thread_id = user_state.get("thread_id")
    task_message_ids = user_state.get("task_message_ids", {})
    message_task_count = user_state.get("message_task_count", {})
    blocklist = current_state[username].get("completed_tasks_blocklist", [])

    completed_thread_id = None
    try:
        completed_thread_id = discord.find_or_create_thread(threads, f"{username}-completed")
    except Exception as e:
        print(f"  [warn] Could not find/create completed thread: {e}")

    for task_name in completed_names:
        msg_ids = task_message_ids.get(task_name, [])
        needs_blocklist = False

        if active_thread_id and msg_ids:
            for msg_id in msg_ids:
                task_count = message_task_count.get(msg_id, 1)
                if task_count == 1:
                    print(f"  Deleting message {msg_id} for '{task_name}'")
                    discord.discord_delete(active_thread_id, msg_id)
                else:
                    print(f"  Message {msg_id} has {task_count} tasks — using blocklist")
                    needs_blocklist = True

        if needs_blocklist or not msg_ids:
            key = task_name.lower()
            if key not in [b.lower() for b in blocklist]:
                blocklist.append(task_name)

        if active_thread_id:
            try:
                discord.send_message(
                    active_thread_id,
                    f"✅ **{username}** completed: **{task_name}**",
                )
            except Exception as e:
                print(f"  [warn] Could not post to active thread: {e}")

        if completed_thread_id:
            try:
                discord.send_message(
                    completed_thread_id,
                    f"✅ **{task_name}**\nCompleted by {username} — {timestamp}",
                )
            except Exception as e:
                print(f"  [warn] Could not post to completed thread: {e}")

        print(f"  ✓ {username} completed: '{task_name}'")

    current_state[username]["completed_tasks_blocklist"] = blocklist
    state.save(current_state)
    send_sms(from_number, format_task_list(username, tasks))
