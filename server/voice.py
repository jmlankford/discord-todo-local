"""
TaskBot — Voice & SMS Utility Handlers
Replaces bridge.php, voice_bridge.php, voicemail.php, voicemail_handler.php,
send_alert.php, and send_sms.php from the original PHP stack.

All inbound Twilio webhook routes are defined here and registered in main.py.
"""

import os
import re
import time
import threading

from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Dial, Conference
from twilio.twiml.messaging_response import MessagingResponse

import config

WAVE_MFA_PATTERN = re.compile(
    r"(?:Wave|verification|code|confirm)[:\s]+(\d{6})", re.IGNORECASE
)


def _client():
    """Return a Twilio REST client."""
    return Client(config.TWILIO_SID, config.TWILIO_AUTH)


# ── Inbound call handler (/voice) ─────────────────────────────────────────────

def handle_inbound_call(form: dict) -> str:
    """
    Called by Twilio when someone calls the Bloom & Rose number.
    1. Texts ALERT_RECIPIENTS with the caller's info.
    2. Dials FORWARD_NUMBER into the BloomAndRoseBridge conference.
    3. Returns TwiML that puts the inbound caller into the same conference.
    """
    from_number = form.get("From", "Unknown")
    from_city   = form.get("FromCity", "")
    from_state  = form.get("FromState", "")

    location = ""
    if from_city and from_state:
        location = f" ({from_city}, {from_state})"
    elif from_state:
        location = f" ({from_state})"

    alert_body = f"📞 Incoming call to Bloom & Rose from {from_number}{location}"

    # Text alert recipients — fire in background so TwiML responds fast
    def _send_alerts():
        client = _client()
        for recipient in config.ALERT_RECIPIENTS:
            try:
                client.messages.create(
                    to=recipient,
                    from_=config.TWILIO_FROM,
                    body=alert_body,
                )
            except Exception as e:
                print(f"  [warn] Alert to {recipient} failed: {e}")

    threading.Thread(target=_send_alerts, daemon=True).start()

    # Dial FORWARD_NUMBER into the conference (outbound leg)
    bridge_url = f"{config.BASE_URL}/voice/bridge"

    def _dial_forward():
        try:
            _client().calls.create(
                to=config.FORWARD_NUMBER,
                from_=config.TWILIO_FROM,
                url=bridge_url,
            )
        except Exception as e:
            print(f"  [warn] Could not dial forward number: {e}")

    threading.Thread(target=_dial_forward, daemon=True).start()

    # TwiML for the inbound caller — join the same conference with hold music
    resp = VoiceResponse()
    resp.say("Connecting your call to Bloom and Rose.", voice="alice")
    dial = Dial()
    dial.conference(
        "BloomAndRoseBridge",
        start_conference_on_enter=True,
        end_conference_on_exit=True,
        wait_url="https://twimlets.com/holdmusic?Bucket=com.twilio.music.classical",
    )
    resp.append(dial)
    return str(resp)


# ── Conference bridge TwiML (/voice/bridge) ───────────────────────────────────

def handle_bridge(form: dict) -> str:
    """
    TwiML endpoint for the outbound (forwarded) call leg.
    Connects the forwarded party into the BloomAndRoseBridge conference.
    """
    resp = VoiceResponse()
    dial = Dial()
    dial.conference(
        "BloomAndRoseBridge",
        start_conference_on_enter=True,
        end_conference_on_exit=True,
    )
    resp.append(dial)
    return str(resp)


# ── Voicemail prompt (/voice/voicemail) ───────────────────────────────────────

def handle_voicemail(form: dict) -> str:
    """
    TwiML endpoint that plays a voicemail prompt and starts recording.
    Recording posts to /voice/voicemail-handler when complete.
    """
    handler_url = f"{config.BASE_URL}/voice/voicemail-handler"
    resp = VoiceResponse()
    resp.say("Please leave a message after the tone.", voice="alice")
    resp.record(
        max_length=120,
        action=handler_url,
        transcribe=False,
        play_beep=True,
    )
    return str(resp)


# ── Voicemail recording handler (/voice/voicemail-handler) ────────────────────

def handle_voicemail_handler(form: dict) -> str:
    """
    Called by Twilio when a voicemail recording finishes.
    Texts ALERT_RECIPIENTS with the recording link.
    Falls back to re-record prompt if no recording was captured.
    """
    recording_url = form.get("RecordingUrl", "")
    from_number   = form.get("From", "Unknown")
    duration      = form.get("RecordingDuration", "?")

    if not recording_url:
        # No recording captured — prompt again
        return handle_voicemail(form)

    alert_body = (
        f"📬 New voicemail from {from_number} ({duration}s)\n"
        f"{recording_url}.mp3"
    )

    client = _client()
    for recipient in config.ALERT_RECIPIENTS:
        try:
            client.messages.create(
                to=recipient,
                from_=config.TWILIO_FROM,
                body=alert_body,
            )
        except Exception as e:
            print(f"  [warn] Voicemail alert to {recipient} failed: {e}")

    resp = VoiceResponse()
    resp.say("Your message has been recorded. Goodbye!", voice="alice")
    resp.hangup()
    return str(resp)


# ── Inbound SMS handler (/sms) ────────────────────────────────────────────────

def handle_inbound_sms(form: dict, task_reply_fn) -> str:
    """
    Called by Twilio for every inbound SMS to the Bloom & Rose number.
    - Wave MFA codes → captured to file for Playwright
    - TaskBot task numbers → handed off to task_reply_fn
    - Everything else → default deli reply
    Returns TwiML XML string.
    """
    from_number = form.get("From", "")
    body        = form.get("Body", "").strip()

    # ── Wave MFA detection ────────────────────────────────────────────────────
    match = WAVE_MFA_PATTERN.search(body)
    if match:
        mfa_code = match.group(1)
        try:
            os.makedirs(os.path.dirname(config.MFA_CODE_FILE), exist_ok=True)
            with open(config.MFA_CODE_FILE, "w") as f:
                f.write(mfa_code)
            print(f"[sms] Wave MFA code captured: {mfa_code}")
        except Exception as e:
            print(f"[sms] Could not write MFA code: {e}")
        resp = MessagingResponse()
        resp.message(f"Wave MFA code captured: {mfa_code}")
        return str(resp)

    # ── TaskBot reply detection ───────────────────────────────────────────────
    is_task_user  = from_number in config.PHONE_TO_USER
    is_task_reply = bool(re.match(r"^\d+(\s+\d+)*$", body))

    if is_task_user and is_task_reply:
        threading.Thread(
            target=task_reply_fn,
            args=(from_number, body),
            daemon=True,
        ).start()
        # Return empty TwiML — taskbot.handle_reply sends the SMS response itself
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

    # ── Default Bloom & Rose reply ────────────────────────────────────────────
    resp = MessagingResponse()
    resp.message("Thanks for texting Bloom & Rose Deli! We'll get back to you soon.")
    return str(resp)


# ── /sms/send — one-off SMS endpoint ─────────────────────────────────────────

def handle_send_sms(form: dict, args: dict) -> dict:
    """
    HTTP endpoint to send a single SMS message.
    POST params: to, body
    Optional: ?key=SMS_SECRET for auth
    Returns a dict (serialized to JSON by the caller).
    """
    # Optional key auth
    provided_key = args.get("key", "")
    if config.SMS_SECRET and provided_key != config.SMS_SECRET:
        return {"error": "Unauthorized"}, 401

    to   = form.get("to", "").strip()
    body = form.get("body", "").strip()

    if not to or not body:
        return {"error": "Missing 'to' or 'body'"}, 400

    try:
        msg = _client().messages.create(
            to=to,
            from_=config.TWILIO_FROM,
            body=body,
        )
        return {"sid": msg.sid, "status": msg.status}, 200
    except Exception as e:
        return {"error": str(e)}, 500


# ── /sms/alert — broadcast to all alert recipients ───────────────────────────

def handle_send_alert(form: dict, args: dict) -> dict:
    """
    Broadcast an SMS to all ALERT_RECIPIENTS (Josh, JB, Zach).
    POST param: body
    Optional: ?key=SMS_SECRET for auth
    Returns a dict (serialized to JSON by the caller).
    """
    provided_key = args.get("key", "")
    if config.SMS_SECRET and provided_key != config.SMS_SECRET:
        return {"error": "Unauthorized"}, 401

    body = form.get("body", "").strip()
    if not body:
        return {"error": "Missing 'body'"}, 400

    client   = _client()
    results  = []
    errors   = []

    for recipient in config.ALERT_RECIPIENTS:
        try:
            msg = client.messages.create(
                to=recipient,
                from_=config.TWILIO_FROM,
                body=body,
            )
            results.append({"to": recipient, "sid": msg.sid})
            time.sleep(1)  # Rate limit between sends
        except Exception as e:
            errors.append({"to": recipient, "error": str(e)})

    return {"sent": results, "errors": errors}, 200 if not errors else 207
