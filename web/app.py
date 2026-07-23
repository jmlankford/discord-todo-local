"""
TaskBot — Web UI
================
A tiny Flask app for viewing and editing the per-user Discord task lists that
the daily SMS digest reads. Discord stays the single source of truth:
  - list   = read the user's thread (reuses taskbot.get_tasks_for_user, so the
             numbering is byte-for-byte the same algorithm the digest uses)
  - add    = post a message to that user's thread
  - delete = delete that Discord message

Runs as a separate container in the existing taskbot stack. It imports the exact
same server modules (discord_api, taskbot, config, state) that are packaged into
the image — one source, no fork, no drift.
"""

import os

from flask import (
    Flask, flash, redirect, render_template, request, session, url_for
)
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import discord_api as discord
import taskbot
import auth

# ── App + security config ─────────────────────────────────────────────────────
app = Flask(__name__)

# SESSION_SECRET signs the session cookie. If unset we generate an ephemeral one
# (sessions won't survive a restart) rather than run with a predictable key.
app.secret_key = os.environ.get("SESSION_SECRET") or os.urandom(32).hex()

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=64 * 1024,  # nobody needs to POST more than this
)

# Behind Cloudflare Tunnel -> NPM -> container. Trust one proxy hop for scheme
# and client IP so rate limiting sees the real remote address.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

USER_NAMES = list(config.USERS.keys())  # ["Josh", "JB", "Zach"]


# ── Auth gate for everything except login/health/static ───────────────────────
@app.before_request
def require_login():
    open_endpoints = {"login", "health", "static"}
    if request.endpoint in open_endpoints:
        return None
    if not auth.is_authed():
        return redirect(url_for("login"))
    return None


# ── Login / logout ────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if auth.is_authed():
        return redirect(url_for("index"))

    if request.method == "POST":
        locked = auth.lockout_remaining()
        if locked > 0:
            return render_template(
                "login.html",
                error=f"Too many attempts. Try again in {locked // 60 + 1} min.",
            ), 429

        if auth.check_password(request.form.get("password", "")):
            auth.record_success()
            session.clear()
            session["authed"] = True
            return redirect(url_for("index"))

        locked = auth.record_failure()
        msg = "Incorrect password."
        if locked:
            msg = f"Too many attempts. Locked for {locked // 60} min."
        return render_template("login.html", error=msg), 401

    return render_template("login.html", error=None)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Main page ─────────────────────────────────────────────────────────────────
def _load_all_lists():
    """
    Return {username: {"tasks": [...], "thread_id": id, "error": str|None}}.
    Numbering is implicit in list order (1-based) and matches the digest because
    it reuses taskbot.get_tasks_for_user.
    """
    threads = discord.get_all_threads()
    out = {}
    for name in USER_NAMES:
        try:
            tasks, thread_id, _msg_ids, _counts = taskbot.get_tasks_for_user(name, threads)
            out[name] = {"tasks": tasks, "thread_id": thread_id, "error": None}
        except Exception as e:
            out[name] = {"tasks": [], "thread_id": None, "error": str(e)}
    return out


@app.route("/")
def index():
    try:
        lists = _load_all_lists()
    except Exception as e:
        # Most likely a Discord auth/permission failure reading threads.
        return render_template("index.html", lists=None, load_error=str(e),
                               users=USER_NAMES), 502
    return render_template("index.html", lists=lists, load_error=None,
                           users=USER_NAMES)


# ── Add a task ────────────────────────────────────────────────────────────────
@app.route("/add", methods=["POST"])
def add_task():
    user = request.form.get("user", "")
    text = (request.form.get("text", "") or "").strip()

    if user not in config.USERS:
        flash("Unknown user.", "error")
        return redirect(url_for("index"))
    if not text:
        flash("Task text is empty.", "error")
        return redirect(url_for("index"))

    threads = discord.get_all_threads()
    thread = discord.find_thread(threads, user)
    if not thread:
        flash(f"No Discord thread found for {user}.", "error")
        return redirect(url_for("index"))

    try:
        discord.send_message(thread["id"], text)
        flash(f"Added to {user}: “{text}”", "ok")
    except Exception as e:
        flash(f"Could not add task: {e}", "error")
    return redirect(url_for("index"))


# ── Delete a task ─────────────────────────────────────────────────────────────
@app.route("/delete", methods=["POST"])
def delete_task():
    user = request.form.get("user", "")
    text = request.form.get("text", "")

    if user not in config.USERS:
        flash("Unknown user.", "error")
        return redirect(url_for("index"))

    # Re-read live so we act on the current message IDs, not a stale render.
    threads = discord.get_all_threads()
    try:
        _tasks, _tid, task_message_ids, message_task_count = taskbot.get_tasks_for_user(user, threads)
    except Exception as e:
        flash(f"Could not read {user}'s thread: {e}", "error")
        return redirect(url_for("index"))

    # Match by exact text (case-insensitive) — robust to renumbering.
    canonical = next((t for t in task_message_ids if t.lower() == text.lower()), None)
    if canonical is None:
        flash("That task is no longer in the list (already removed?).", "error")
        return redirect(url_for("index"))

    msg_ids = task_message_ids[canonical]

    # If any message holding this task also holds OTHER tasks, deleting it would
    # take siblings with it. We do NOT hide the task instead — we refuse and say
    # why (honest, not a silent degrade).
    shared = [m for m in msg_ids if message_task_count.get(m, 1) > 1]
    if shared:
        flash(
            f"“{canonical}” shares a Discord message with other tasks, so it "
            f"can't be deleted individually here. Complete it via SMS or edit "
            f"that message in Discord.",
            "error",
        )
        return redirect(url_for("index"))

    # Real deletes. Surface the actual Discord status — never fake success.
    channel_id = thread_id_for(user, threads)
    failures = []
    for m in msg_ids:
        status = discord.delete_message_checked(channel_id, m)
        if status not in (200, 204):
            failures.append(status)

    if failures:
        if 403 in failures:
            flash(
                "Delete failed: the bot is not allowed to delete this message "
                "(missing Manage Messages permission in Discord).",
                "error",
            )
        else:
            flash(f"Delete failed (Discord returned {failures}).", "error")
    else:
        flash(f"Deleted “{canonical}” from {user}.", "ok")
    return redirect(url_for("index"))


def thread_id_for(user, threads):
    t = discord.find_thread(threads, user)
    return t["id"] if t else None


# ── Health check (no auth) ────────────────────────────────────────────────────
@app.route("/health")
def health():
    return {"status": "ok"}, 200


if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", "8080"))
    serve(app, host="0.0.0.0", port=port)
