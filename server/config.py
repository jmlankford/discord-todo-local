"""
TaskBot — Configuration
All values loaded from environment variables.
See .env.example for the full list. Never hardcode secrets here.
"""

import os

# ── Discord ───────────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
GUILD_ID      = os.environ.get("GUILD_ID", "")
CHANNEL_ID    = os.environ.get("CHANNEL_ID", "")

# ── Twilio (core) ─────────────────────────────────────────────────────────────
TWILIO_SID    = os.environ.get("TWILIO_SID", "")
TWILIO_AUTH   = os.environ.get("TWILIO_AUTH", "")
TWILIO_FROM   = os.environ.get("TWILIO_FROM", "")       # Your Twilio phone number

# ── Twilio (API key — used for outbound calls) ────────────────────────────────
TWILIO_API_KEY    = os.environ.get("TWILIO_API_KEY", "")
TWILIO_API_SECRET = os.environ.get("TWILIO_API_SECRET", "")

# ── Bloom & Rose voice routing ────────────────────────────────────────────────
FORWARD_NUMBER = os.environ.get("FORWARD_NUMBER", "")   # Real number calls forward to
SMS_SECRET     = os.environ.get("SMS_SECRET", "")       # Optional key for /sms/send endpoint

# ── Users (TaskBot recipients) ────────────────────────────────────────────────
USERS = {
    "Josh": {"phone": os.environ.get("PHONE_JOSH", "")},
    "JB":   {"phone": os.environ.get("PHONE_JB", "")},
    "Zach": {"phone": os.environ.get("PHONE_ZACH", "")},
}

# Reverse lookup: phone → username (skips any empty entries)
PHONE_TO_USER = {v["phone"]: k for k, v in USERS.items() if v["phone"]}

# All phones that receive SMS alerts (voicemail, call notifications, broadcasts)
ALERT_RECIPIENTS = [v["phone"] for v in USERS.values() if v["phone"]]

# ── Server ────────────────────────────────────────────────────────────────────
PORT     = int(os.environ.get("PORT", "8080"))
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")   # e.g. https://switchboard.thebloomandrose.com

# ── State storage ─────────────────────────────────────────────────────────────
STATE_FILE = os.environ.get("STATE_FILE", "/data/task_state.json")

# ── Wave MFA (writes captured code to file for Playwright to pick up) ─────────
MFA_CODE_FILE = os.environ.get("MFA_CODE_FILE", "/data/wave_mfa_code.txt")
