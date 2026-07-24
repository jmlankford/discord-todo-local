"""
Self-tests that need no live Discord/Twilio/LAN. Run: python web/test_app.py

Covers:
  - Auth: wrong password rejected, right password admits, session persists,
    rate-limit lockout after 5 failures.
  - Numbering parity: the web list order is byte-for-byte the same as the daily
    SMS digest text (taskbot.format_task_list), including dedup + blocklist.
"""

import os
import re
import sys

# Make server/ and web/ importable regardless of CWD.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "server"))
sys.path.insert(0, _HERE)

# Env must be set BEFORE importing app/auth (they read it at import time).
os.environ["APP_PASSWORD"] = "correct horse battery"
os.environ["SESSION_SECRET"] = "test-secret-not-real"
os.environ["GUILD_ID"] = "G1"
os.environ["CHANNEL_ID"] = "C1"

import discord_api            # noqa: E402
import state                  # noqa: E402
import taskbot                # noqa: E402
import auth                   # noqa: E402
import app as webapp          # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        # Must raise, not just tally. Without this, a failing check still lets its
        # enclosing test function return normally, so `pytest test_app.py` reports
        # every function green no matter what failed. The __main__ runner below
        # still reports the full tally because it catches this per function.
        raise AssertionError(name)


# ── Synthetic Discord data ────────────────────────────────────────────────────
def _msg(mid, content, bot=False):
    return {"id": mid, "content": content, "author": {"bot": bot}}


# messages come newest-first from Discord (get_tasks_for_user reverses them)
THREADS = {
    "Josh": [  # newest-first
        _msg("103", "Fix sink"),
        _msg("102", "call bank"),          # dup-case check vs "Call bank"
        _msg("101", "Call bank"),
        _msg("100", "Buy milk"),
    ],
    "JB": [_msg("200", "Ship invoice")],
    "Zach": [],
}
THREAD_META = {
    "Josh": {"id": "TJosh", "name": "Josh", "parent_id": "C1"},
    "JB":   {"id": "TJB",   "name": "JB",   "parent_id": "C1"},
    "Zach": {"id": "TZach", "name": "Zach", "parent_id": "C1"},
}


def install_stubs(blocklist=None):
    discord_api.get_all_threads = lambda: list(THREAD_META.values())

    def _find(threads, name, channel_id=None):
        return THREAD_META.get(name)
    discord_api.find_thread = _find

    def _msgs(thread_id):
        for name, meta in THREAD_META.items():
            if meta["id"] == thread_id:
                return THREADS[name]
        return []
    discord_api.get_thread_messages = _msgs

    st = {"Josh": {"completed_tasks_blocklist": blocklist or []}}
    state.load = lambda: st


# ── Auth tests ────────────────────────────────────────────────────────────────
def test_auth():
    print("\n[auth]")
    webapp.app.config["TESTING"] = True
    auth._attempts.clear()
    c = webapp.app.test_client()

    # unauthenticated index redirects to login
    r = c.get("/")
    check("index redirects to /login when not authed", r.status_code == 302 and "/login" in r.headers["Location"])

    # wrong password rejected, no session
    r = c.post("/login", data={"password": "nope"})
    check("wrong password -> 401", r.status_code == 401)
    r = c.get("/")
    check("still unauthenticated after wrong password", r.status_code == 302)

    # correct password admits + session persists across reloads
    r = c.post("/login", data={"password": "correct horse battery"})
    check("correct password -> redirect to index", r.status_code == 302 and r.headers["Location"].endswith("/"))
    r = c.get("/")
    check("session persists across reload (index loads)", r.status_code in (200, 502))  # 502 only if discord stub absent

    # cookie flags
    set_cookie = "; ".join(v for k, v in r.headers if k == "Set-Cookie") + "".join(
        h for h in [str(x) for x in []]
    )
    # Inspect the login response cookie flags
    r2 = webapp.app.test_client().post("/login", data={"password": "correct horse battery"})
    cookie_hdr = r2.headers.get("Set-Cookie", "")
    check("cookie HttpOnly", "HttpOnly" in cookie_hdr)
    check("cookie Secure", "Secure" in cookie_hdr)
    check("cookie SameSite=Lax", "SameSite=Lax" in cookie_hdr)


def test_rate_limit():
    print("\n[rate limit]")
    auth._attempts.clear()
    c = webapp.app.test_client()
    codes = []
    for _ in range(5):
        codes.append(c.post("/login", data={"password": "x"}).status_code)
    r = c.post("/login", data={"password": "x"})
    check("locks out after 5 failures (429)", r.status_code == 429)
    # even the CORRECT password is refused while locked
    r = c.post("/login", data={"password": "correct horse battery"})
    check("correct password refused during lockout", r.status_code == 429)


# ── Numbering parity ──────────────────────────────────────────────────────────
def _rendered_tasks(html, user):
    """Extract task-text spans for a user's <section>, in document order."""
    # isolate the user's card
    m = re.search(rf"<h2>{user}</h2>(.*?)</section>", html, re.S)
    if not m:
        return []
    return re.findall(r'<span class="task-text">(.*?)</span>', m.group(1), re.S)


def test_numbering_parity():
    print("\n[numbering parity vs SMS digest]")
    install_stubs()
    auth._attempts.clear()
    c = webapp.app.test_client()
    c.post("/login", data={"password": "correct horse battery"})
    html = c.get("/").get_data(as_text=True)

    threads = discord_api.get_all_threads()
    for user in ("Josh", "JB", "Zach"):
        # What the DAILY DIGEST would send (the real formatter + real reader):
        tasks, _tid, _ids, _cnt = taskbot.get_tasks_for_user(user, threads)
        digest = taskbot.format_task_list(user, tasks)
        digest_lines = [ln.split(". ", 1)[1] for ln in digest.splitlines()
                        if re.match(r"^\d+\. ", ln)]
        # What the WEB UI renders:
        web_lines = [re.sub(r"\s+", " ", t).strip() for t in _rendered_tasks(html, user)]
        check(f"{user}: web order == digest order  {web_lines}", web_lines == digest_lines)

    # dedup sanity: "Call bank"/"call bank" collapses to one
    tasks, *_ = taskbot.get_tasks_for_user("Josh", threads)
    check("case-insensitive dedup (Call bank once)",
          sum(1 for t in tasks if t.lower() == "call bank") == 1)


def test_numbering_parity_with_blocklist():
    print("\n[numbering parity with a blocklisted task]")
    # NB: get_tasks_for_user compares line.lower() to blocklist entries, so the
    # stored entry must be lowercase to match (this mirrors the digest exactly —
    # the web UI reuses the same function, so whatever the digest filters, the
    # web filters identically).
    install_stubs(blocklist=["buy milk"])   # simulate a completed multi-task item
    c = webapp.app.test_client()
    auth._attempts.clear()
    c.post("/login", data={"password": "correct horse battery"})
    html = c.get("/").get_data(as_text=True)
    threads = discord_api.get_all_threads()
    tasks, *_ = taskbot.get_tasks_for_user("Josh", threads)
    digest_lines = list(tasks)
    web_lines = [re.sub(r"\s+", " ", t).strip() for t in _rendered_tasks(html, "Josh")]
    check("web order == digest order with blocklist applied", web_lines == digest_lines)
    check("blocklisted 'Buy milk' filtered from BOTH web and digest",
          "Buy milk" not in web_lines and "Buy milk" not in digest_lines)


def _authed_client():
    auth._attempts.clear()
    c = webapp.app.test_client()
    c.post("/login", data={"password": "correct horse battery"})
    return c


def test_add():
    print("\n[add]")
    install_stubs()
    sent = {}
    discord_api.send_message = lambda tid, content: sent.update(tid=tid, content=content) or {"id": "new"}
    c = _authed_client()
    r = c.post("/add", data={"user": "Josh", "text": "Water plants"}, follow_redirects=False)
    check("add redirects back", r.status_code == 302)
    check("add posts to Josh's thread", sent.get("tid") == "TJosh")
    check("add posts the exact text", sent.get("content") == "Water plants")

    sent.clear()
    r = c.post("/add", data={"user": "Nobody", "text": "x"})
    check("add to unknown user does NOT post", "tid" not in sent)


def test_delete_single():
    print("\n[delete: single-task message]")
    install_stubs()
    deleted = []
    discord_api.delete_message_checked = lambda cid, mid: deleted.append((cid, mid)) or 204
    c = _authed_client()
    r = c.post("/delete", data={"user": "Josh", "text": "Fix sink"})
    check("delete redirects back", r.status_code == 302)
    check("delete calls Discord delete for the right message", deleted == [("TJosh", "103")])


def test_delete_permission_denied():
    print("\n[delete: 403 must surface, not hide]")
    install_stubs()
    discord_api.delete_message_checked = lambda cid, mid: 403
    c = _authed_client()
    r = c.post("/delete", data={"user": "Josh", "text": "Fix sink"}, follow_redirects=True)
    body = r.get_data(as_text=True)
    check("403 surfaces a Manage Messages error to the user",
          "Manage Messages" in body)
    # Fix sink must still be present (not hidden away as if deleted)
    check("task still shown after failed delete (no silent hide)",
          "Fix sink" in body)


def test_delete_multitask_refused():
    print("\n[delete: multi-task message refused, not sibling-nuked]")
    # One message holding TWO tasks.
    THREADS["JB"] = [_msg("300", "Ship invoice\nEmail Zach")]
    install_stubs()
    called = []
    discord_api.delete_message_checked = lambda cid, mid: called.append(mid) or 204
    c = _authed_client()
    r = c.post("/delete", data={"user": "JB", "text": "Ship invoice"}, follow_redirects=True)
    body = r.get_data(as_text=True)
    check("multi-task delete does NOT delete the shared message", called == [])
    check("user told why it was refused", "shares a Discord message" in body)
    THREADS["JB"] = [_msg("200", "Ship invoice")]  # restore


if __name__ == "__main__":
    install_stubs()
    # check() raises on failure so pytest can't report a false green. Catch it per
    # function here so one failure doesn't abort the rest of the run — we still want
    # the full tally. Anything other than AssertionError is a real bug in the test
    # setup, so let it propagate.
    for _t in (
        test_auth,
        test_rate_limit,
        test_numbering_parity,
        test_numbering_parity_with_blocklist,
        test_add,
        test_delete_single,
        test_delete_permission_denied,
        test_delete_multitask_refused,
    ):
        try:
            _t()
        except AssertionError:
            print(f"  ...{_t.__name__} aborted at first failed check")
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
