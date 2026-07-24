"""
TaskBot — Flask Application Entry Point
========================================
Replaces the Google Cloud Function (main.py) + Cloud Scheduler + Cloud Tasks.

Endpoints:
  POST /send                  → Send task lists now (optional ?user=Josh)
  POST /trigger               → Pick random delay then send (mirrors old /trigger)
  POST /reply                 → Twilio inbound SMS webhook (task completion)
  POST /sms                   → Twilio inbound SMS webhook (all other SMS)
  POST /sms/send              → Send a one-off SMS via HTTP
  POST /sms/alert             → Broadcast SMS to all recipients
  POST /voice                 → Twilio inbound call webhook
  POST /voice/bridge          → Conference bridge TwiML for forwarded leg
  POST /voice/voicemail       → Voicemail prompt TwiML
  POST /voice/voicemail-handler → Voicemail recording handler
  GET  /health                → Container health check

Scheduling:
  APScheduler fires handle_send() at 6:30 PM ET every day, with a random
  0–120 minute delay — replicating the old Cloud Scheduler + Cloud Tasks flow.
"""

import logging
import random
import threading
from datetime import datetime
from functools import wraps

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, request, jsonify
from twilio.request_validator import RequestValidator

import config
import taskbot
import voice

# Make INFO-level activity visible in `docker logs`. Flask's app.logger otherwise
# sits at WARNING (debug off), which silently swallowed all [scheduler]/[send]
# lines — so a stuck scheduler looked identical to a healthy one. basicConfig sets
# a root StreamHandler so app.logger AND the taskbot module logger both surface.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("apscheduler").setLevel(logging.INFO)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)
ET  = pytz.timezone("America/New_York")


def _run_send_safely(only_user=None):
    """
    Run handle_send in a context where an exception won't vanish. The send runs
    in a detached Thread/Timer, so an unhandled error would otherwise disappear
    with no log and no SMS — exactly how a failure could go unnoticed. Here it is
    logged at ERROR and Josh is alerted.
    """
    try:
        taskbot.handle_send(only_user)
    except Exception:
        app.logger.exception("[send] handle_send crashed")
        try:
            taskbot.alert_josh(
                "⚠️ TaskBot: the daily send CRASHED with an error before "
                "completing. Check the container logs."
            )
        except Exception:
            app.logger.exception("[send] could not send crash alert")


# ── Twilio signature validation ───────────────────────────────────────────────

def validate_twilio(f):
    """Decorator — rejects requests that don't carry a valid Twilio signature."""
    @wraps(f)
    def decorated(*args, **kwargs):
        validator = RequestValidator(config.TWILIO_AUTH)
        signature = request.headers.get("X-Twilio-Signature", "")
        # Use the full public URL so the signature matches what Twilio signed
        url    = f"{config.BASE_URL}{request.path}"
        params = request.form.to_dict()
        if config.TWILIO_AUTH and not validator.validate(url, params, signature):
            app.logger.warning(f"[security] Invalid Twilio signature on {request.path}")
            return "Forbidden", 403
        return f(*args, **kwargs)
    return decorated


# ── TaskBot routes ────────────────────────────────────────────────────────────

@app.route("/send", methods=["POST"])
def route_send():
    """
    Immediately send task lists to all users (or one with ?user=Josh).
    Use this to test without waiting for the scheduled time.
    """
    only_user = request.args.get("user")
    threading.Thread(target=_run_send_safely, args=(only_user,), daemon=True).start()
    return jsonify({"status": "send started", "user": only_user or "all"}), 200


@app.route("/trigger", methods=["POST"])
def route_trigger():
    """
    Pick a random 0–120 minute delay then fire handle_send.
    Mirrors the old Cloud Scheduler → Cloud Tasks flow for manual testing.
    """
    delay = random.randint(0, 7200)
    eta   = datetime.now(ET) + __import__("datetime").timedelta(seconds=delay)
    app.logger.info(
        f"[trigger] Sending in {delay // 60}m {delay % 60}s "
        f"(~{eta.strftime('%I:%M %p ET')})"
    )
    threading.Timer(delay, _run_send_safely).start()
    return jsonify({"status": "scheduled", "delay_seconds": delay}), 200


@app.route("/reply", methods=["POST"])
@validate_twilio
def route_reply():
    """
    Twilio webhook — fires when a user texts back a task number.
    Twilio expects a fast response; the actual work runs in a background thread.
    """
    from_number = request.form.get("From", "")
    body        = request.form.get("Body", "")
    threading.Thread(
        target=taskbot.handle_reply,
        args=(from_number, body),
        daemon=True,
    ).start()
    return ('<?xml version="1.0" encoding="UTF-8"?><Response></Response>', 200,
            {"Content-Type": "text/xml"})


# ── SMS routes ────────────────────────────────────────────────────────────────

@app.route("/sms", methods=["POST"])
@validate_twilio
def route_sms():
    """
    Twilio inbound SMS webhook for the Bloom & Rose number.
    Handles: Wave MFA codes, TaskBot task completions, default replies.
    """
    twiml = voice.handle_inbound_sms(request.form, taskbot.handle_reply)
    return (twiml, 200, {"Content-Type": "text/xml"})


@app.route("/sms/send", methods=["POST"])
def route_sms_send():
    """Send a single SMS via HTTP POST. Optional ?key= auth."""
    result, status = voice.handle_send_sms(request.form, request.args)
    return jsonify(result), status


@app.route("/sms/alert", methods=["POST"])
def route_sms_alert():
    """Broadcast an SMS to all alert recipients. Optional ?key= auth."""
    result, status = voice.handle_send_alert(request.form, request.args)
    return jsonify(result), status


# ── Voice / call routing routes ───────────────────────────────────────────────

@app.route("/voice", methods=["POST"])
@validate_twilio
def route_voice():
    """Twilio webhook — inbound call. Routes to conference bridge."""
    twiml = voice.handle_inbound_call(request.form)
    return (twiml, 200, {"Content-Type": "text/xml"})


@app.route("/voice/bridge", methods=["POST"])
@validate_twilio
def route_voice_bridge():
    """TwiML for the forwarded (outbound) call leg of the conference bridge."""
    twiml = voice.handle_bridge(request.form)
    return (twiml, 200, {"Content-Type": "text/xml"})


@app.route("/voice/voicemail", methods=["POST"])
@validate_twilio
def route_voicemail():
    """TwiML voicemail prompt — plays greeting and starts recording."""
    twiml = voice.handle_voicemail(request.form)
    return (twiml, 200, {"Content-Type": "text/xml"})


@app.route("/voice/voicemail-handler", methods=["POST"])
@validate_twilio
def route_voicemail_handler():
    """Called by Twilio when a voicemail recording completes."""
    twiml = voice.handle_voicemail_handler(request.form)
    return (twiml, 200, {"Content-Type": "text/xml"})


# ── Health check ──────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "time_et": datetime.now(ET).strftime("%Y-%m-%d %I:%M %p ET"),
    }), 200


# ── Scheduler setup ───────────────────────────────────────────────────────────

def _scheduled_trigger():
    """
    Fired by APScheduler at 6:30 PM ET daily.
    Picks a random 0–120 minute delay then calls handle_send — replicating
    the original Cloud Scheduler + Cloud Tasks random-delay flow.
    """
    delay = random.randint(0, 7200)
    eta   = datetime.now(ET) + __import__("datetime").timedelta(seconds=delay)
    app.logger.info(
        f"[scheduler] Daily trigger fired. Sending in {delay // 60}m {delay % 60}s "
        f"(~{eta.strftime('%I:%M %p ET')})"
    )
    threading.Timer(delay, _run_send_safely).start()


def start_scheduler():
    scheduler = BackgroundScheduler(timezone=ET)
    scheduler.add_job(
        func=_scheduled_trigger,
        trigger=CronTrigger(hour=18, minute=30, timezone=ET),
        id="daily_task_send",
        replace_existing=True,
    )
    scheduler.start()
    app.logger.info("[scheduler] APScheduler started — daily send at 6:30 PM ET.")
    return scheduler


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    start_scheduler()
    app.run(host="0.0.0.0", port=config.PORT)
