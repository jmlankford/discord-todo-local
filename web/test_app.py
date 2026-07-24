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
import config                 # noqa: E402
import app as webapp          # noqa: E402

# The server prints status lines containing ✓/✅ etc. On a Windows console
# (cp1252) those crash the run; the app itself runs in a UTF-8 Linux container.
# Make the harness encoding-safe so it passes on any platform.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

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


def test_client_ip_source():
    """
    The lockout key must be the real client, not the proxy.

    Live chain is client -> CF edge -> cloudflared -> NPM -> app. Verified
    against NPM: the backend receives
        CF-Connecting-IP: <client>
        X-Forwarded-For:  <client>, 172.20.0.1     (NPM appends its own peer)
        X-Real-IP:        172.20.0.1               (the proxy — unusable)
    so keying on remote_addr / the RIGHTMOST XFF entry buckets every external
    visitor under 172.20.0.1: the lockout protects nothing and 5 failures from
    anyone lock out everyone.
    """
    print("\n[client ip: real client, not the proxy]")
    with webapp.app.test_request_context(
        "/login",
        headers={"CF-Connecting-IP": "203.0.113.7",
                 "X-Forwarded-For": "198.51.100.9, 172.20.0.1"},
        environ_base={"REMOTE_ADDR": "172.20.0.1"},
    ):
        check("CF-Connecting-IP wins over XFF and remote_addr",
              auth.client_ip() == "203.0.113.7")

    with webapp.app.test_request_context(
        "/login",
        headers={"X-Forwarded-For": "198.51.100.9, 172.20.0.1"},
        environ_base={"REMOTE_ADDR": "172.20.0.1"},
    ):
        got = auth.client_ip()
        check("falls back to LEFTMOST XFF entry (the client)", got == "198.51.100.9")
        check("never returns the appended proxy hop", got != "172.20.0.1")

    with webapp.app.test_request_context(
        "/login", environ_base={"REMOTE_ADDR": "192.168.1.50"}
    ):
        check("falls back to remote_addr with no proxy headers",
              auth.client_ip() == "192.168.1.50")

    # Two different real clients behind the SAME proxy must not share a bucket.
    auth._attempts.clear()
    for _ in range(auth.MAX_FAILS):
        with webapp.app.test_request_context(
            "/login", headers={"CF-Connecting-IP": "203.0.113.7"},
            environ_base={"REMOTE_ADDR": "172.20.0.1"},
        ):
            auth.record_failure()
    with webapp.app.test_request_context(
        "/login", headers={"CF-Connecting-IP": "203.0.113.7"},
        environ_base={"REMOTE_ADDR": "172.20.0.1"},
    ):
        check("attacker's IP is locked out", auth.lockout_remaining() > 0)
    with webapp.app.test_request_context(
        "/login", headers={"CF-Connecting-IP": "198.51.100.22"},
        environ_base={"REMOTE_ADDR": "172.20.0.1"},
    ):
        check("a DIFFERENT client behind the same proxy is NOT locked out",
              auth.lockout_remaining() == 0)
    auth._attempts.clear()


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
    # Posted as the bot with the web-task sentinel prepended (so the reader will
    # surface it); the marker is stripped for display elsewhere.
    check("add posts the text with the web-task sentinel",
          sent.get("content") == taskbot.WEB_TASK_PREFIX + "Water plants")

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


def test_web_add_round_trip():
    """
    Full lifecycle of a web-added task against a MUTABLE fake Discord, exercising
    the real code paths end to end:
      web /add  ->  shows in list  ->  shows in SMS digest w/ right number
                ->  completable by SMS reply  ->  and deletable in the UI
    Regression guard for the sentinel bug (web-added bot messages were invisible).
    """
    print("\n[web-add sentinel round-trip]")
    import state as _state

    # ── mutable in-memory Discord backend ─────────────────────────────────────
    store = {"TJosh": [], "TJB": [], "TZach": []}   # oldest-first internally
    seq = {"n": 1000}
    def _threads():
        return list(THREAD_META.values())
    def _find(threads, name, channel_id=None):
        return THREAD_META.get(name)
    def _get_msgs(thread_id):
        return list(reversed(store.get(thread_id, [])))   # Discord returns newest-first
    def _send(thread_id, content):
        seq["n"] += 1
        mid = str(seq["n"])
        store.setdefault(thread_id, []).append(
            {"id": mid, "content": content, "author": {"bot": True}})
        return {"id": mid}
    def _del(cid, mid):
        n0 = len(store.get(cid, []))
        store[cid] = [m for m in store.get(cid, []) if m["id"] != mid]
        return 204 if len(store[cid]) < n0 else 404
    for mod in (discord_api,):
        mod.get_all_threads = _threads
        mod.find_thread = _find
        mod.get_thread_messages = _get_msgs
        mod.send_message = _send
        mod.delete_message_checked = _del
        mod.discord_delete = lambda cid, mid: _del(cid, mid)      # SMS-completion path
        mod.find_or_create_thread = lambda threads, name, channel_id=None: "TJoshDone"

    # in-memory state + captured SMS, so handle_send/handle_reply need no disk/Twilio
    mem = {}
    _state.load = lambda: mem
    _state.save = lambda d: mem.update(d)
    sms = []
    taskbot.send_sms = lambda to, body: sms.append(body)

    # single test user with a phone so the SMS reply flow resolves
    config.USERS = {"Josh": {"phone": "+15550001"}}
    config.PHONE_TO_USER = {"+15550001": "Josh"}
    webapp.USER_NAMES = ["Josh"]

    # seed one HUMAN task so we can prove the web task numbers AFTER it (order)
    store["TJosh"].append({"id": "500", "content": "Wash car", "author": {"bot": False}})

    c = _authed_client()

    # 1) ADD via the web path
    c.post("/add", data={"user": "Josh", "text": "Paint fence"}, follow_redirects=False)
    raw = store["TJosh"][-1]
    check("add posted a BOT message carrying the sentinel",
          raw["author"]["bot"] and raw["content"] == taskbot.WEB_TASK_PREFIX + "Paint fence")

    # 2) shows in the task list (marker stripped), AFTER the human task
    tasks, _tid, tmids, tcount = taskbot.get_tasks_for_user("Josh", _threads())
    check("web task appears in the list, marker stripped", tasks == ["Wash car", "Paint fence"])
    check("its Discord id maps to the bot message", tmids.get("Paint fence") == [raw["id"]])
    check("it counts as a single-task message (SMS-completable, not blocklisted)",
          tcount.get(raw["id"]) == 1)

    # 3) renders on the page with no marker
    html = c.get("/").get_data(as_text=True)
    check("rendered on the page as clean text", "Paint fence" in html and "🔖" not in html)

    # 4) appears in the SMS digest with correct numbering, no marker
    digest = taskbot.format_task_list("Josh", tasks)
    check("digest numbers it correctly (2. Paint fence)", "2. Paint fence" in digest)
    check("digest text carries no marker", "🔖" not in digest)

    # 5) COMPLETABLE BY SMS: send (saves state), then reply with its number
    taskbot.handle_send(only_user="Josh")
    num = next(n for n, t in mem["Josh"]["numbered"].items() if t == "Paint fence")
    taskbot.handle_reply("+15550001", num)
    left, *_ = taskbot.get_tasks_for_user("Josh", _threads())
    check("SMS reply completed the web task (removed from Discord)", "Paint fence" not in left)
    check("the human task is untouched by that completion", "Wash car" in left)

    # 6) DELETABLE IN THE UI: add another, delete it via /delete
    c.post("/add", data={"user": "Josh", "text": "Sweep porch"}, follow_redirects=False)
    check("second web task present before delete",
          "Sweep porch" in taskbot.get_tasks_for_user("Josh", _threads())[0])
    c.post("/delete", data={"user": "Josh", "text": "Sweep porch"}, follow_redirects=False)
    check("UI delete removed the web task from Discord",
          "Sweep porch" not in taskbot.get_tasks_for_user("Josh", _threads())[0])


if __name__ == "__main__":
    install_stubs()
    # check() raises on failure so pytest can't report a false green. Catch it per
    # function here so one failure doesn't abort the rest of the run — we still want
    # the full tally. Anything other than AssertionError is a real bug in the test
    # setup, so let it propagate.
    for _t in (
        test_auth,
        test_rate_limit,
        test_client_ip_source,
        test_numbering_parity,
        test_numbering_parity_with_blocklist,
        test_add,
        test_delete_single,
        test_delete_permission_denied,
        test_delete_multitask_refused,
        test_web_add_round_trip,
    ):
        try:
            _t()
        except AssertionError:
            print(f"  ...{_t.__name__} aborted at first failed check")
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
