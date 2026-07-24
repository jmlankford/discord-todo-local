# Discord Todo — Local

Self-hosted version of the Discord task reminder system. Runs entirely in a single Docker container on your home server (Unraid or any Docker host). Replaces Google Cloud Functions, Cloud Scheduler, Cloud Tasks, and Google Cloud Storage with a local Flask app + APScheduler + a JSON file on disk.

Also consolidates the Bloom & Rose Deli Twilio stack (call routing, voicemail, SMS alerts) into the same container.

> Based on [discord-todo](https://github.com/jmlankford/discord-todo).

---

## What It Does

- **6:30 PM ET every day** — APScheduler fires, picks a random 0–120 minute delay, then texts Josh, JB, and Zach their numbered task lists from their Discord threads
- **Text back a number** — marks the task complete, deletes it from Discord, logs it in the `-completed` thread, sends an updated list
- **Inbound calls** — texts you the caller's info, bridges the call to your real number via a Twilio conference
- **Voicemail** — records a message and texts you the link
- **SMS utilities** — `/sms/send` and `/sms/alert` endpoints for one-off and broadcast messages

---

## Prerequisites

- Docker (Unraid, Docker Desktop, or any Docker host)
- A domain or subdomain with HTTPS pointing to your server (e.g. `switchboard.thebloomandrose.com`)
- A reverse proxy handling SSL termination (Nginx Proxy Manager, Traefik, Caddy, etc.)
- A Twilio account with an active phone number
- A Discord bot with **Manage Messages** permission in your server

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/jmlankford/discord-todo-local.git
cd discord-todo-local
```

### 2. Fill in docker-compose.yml

Open `docker-compose.yml` and replace every `YOUR_..._HERE` placeholder with your real values. See the **Credentials Reference** section below for where to find each one.

### 3. Set up your appdata directory

```bash
mkdir -p /mnt/user/appdata/taskbot/data
```

Adjust the path in `docker-compose.yml` if your Unraid appdata lives elsewhere.

### 4. Start the container

```bash
docker compose up -d
```

Check logs:
```bash
docker logs -f taskbot
```

Health check:
```bash
curl https://switchboard.thebloomandrose.com/health
```

---

## Twilio Setup

### Step 1 — Get your credentials

1. Log in to [console.twilio.com](https://console.twilio.com)
2. From the dashboard, copy:
   - **Account SID** → `TWILIO_SID` in docker-compose.yml
   - **Auth Token** (click the eye icon) → `TWILIO_AUTH`
3. Go to **Account → API keys & tokens → Create API key**
   - Type: Standard
   - Copy the **SID** → `TWILIO_API_KEY`
   - Copy the **Secret** (shown once) → `TWILIO_API_SECRET`
4. Go to **Phone Numbers → Manage → Active Numbers**
   - Copy your number in E.164 format (e.g. `+17165550000`) → `TWILIO_FROM`

### Step 2 — Configure webhooks

For each webhook below, go to **Phone Numbers → Manage → Active Numbers → [your number]**:

#### Inbound SMS
- **"A message comes in"** → `https://switchboard.thebloomandrose.com/sms`
- Method: **HTTP POST**

#### Inbound Calls
- **"A call comes in"** → `https://switchboard.thebloomandrose.com/voice`
- Method: **HTTP POST**

#### Voicemail (optional — only if you want callers to leave voicemail instead of being bridged)
Update the call flow in Twilio Studio or point unanswered calls to:
- `https://switchboard.thebloomandrose.com/voice/voicemail`

Click **Save** after each change.

### Step 3 — Test it

**Test task send (immediate, no delay):**
```bash
curl -X POST https://switchboard.thebloomandrose.com/send?user=Josh
```

**Test inbound SMS reply (simulate Josh texting back "1"):**
```bash
curl -X POST https://switchboard.thebloomandrose.com/reply \
  -d "From=$PHONE_JOSH&Body=1"
```

**Send a one-off SMS:**
```bash
curl -X POST https://switchboard.thebloomandrose.com/sms/send \
  -d "to=+12125550000&body=Hello from TaskBot" \
  "?key=YOUR_SMS_SECRET"
```

---

## Reverse Proxy Setup (Nginx Proxy Manager)

If you're using Nginx Proxy Manager on Unraid:

1. **Proxy Hosts → Add Proxy Host**
2. Domain: `switchboard.thebloomandrose.com`
3. Forward Hostname/IP: your Unraid IP (e.g. `192.168.1.100`)
4. Forward Port: `8080`
5. Enable **"Block Common Exploits"**
6. **SSL tab** → Request a new SSL certificate → Enable **Force SSL**

Make sure your router forwards port 443 to your Unraid box, and that your DNS (Cloudflare or registrar) has an A record for `switchboard` pointing to your public IP.

The steps above cover the **`taskbot` API** service only.

### Web UI (`taskbot-web`)

A second container in the same stack serves a mobile-friendly page for viewing, adding,
and deleting tasks. Discord threads stay the source of truth — it reuses
`server/taskbot.get_tasks_for_user` verbatim, so its numbering matches the daily SMS
digest exactly rather than reimplementing the ordering.

It publishes **no host port at all**. NPM forwards to it by container name
(`taskbot-web:8080`) over the shared `nginx_default` network, so nothing new listens on
the Unraid interface and no host port can conflict.

Proxy host, Cloudflare tunnel ingress (including the required `originServerName`), the
Discord **Manage Messages** precheck, and the full verification checklist are in
**[`web/DEPLOY.md`](web/DEPLOY.md)**. Public hostname: `https://tasks.thebloomandrose.com`.

Run its self-tests with `python web/test_app.py` (26 checks, no live Discord/Twilio needed).

---

## Credentials Reference

| docker-compose.yml key | Where to find it |
|---|---|
| `DISCORD_TOKEN` | Discord Developer Portal → Your App → Bot → Token |
| `GUILD_ID` | Discord → right-click server name → Copy Server ID |
| `CHANNEL_ID` | Discord → right-click channel → Copy Channel ID |
| `TWILIO_SID` | Twilio Console → Dashboard |
| `TWILIO_AUTH` | Twilio Console → Dashboard (click eye icon) |
| `TWILIO_FROM` | Twilio Console → Phone Numbers → Active Numbers |
| `TWILIO_API_KEY` | Twilio Console → API keys & tokens → Create API key |
| `TWILIO_API_SECRET` | Shown once when you create the API key — save it immediately |
| `FORWARD_NUMBER` | The real phone number inbound calls should forward to |
| `PHONE_JOSH/JB/ZACH` | Personal phone numbers in E.164 format (+1XXXXXXXXXX) |
| `SMS_SECRET` | Any string you choose — used to protect /sms/send and /sms/alert |
| `BASE_URL` | Your public HTTPS subdomain, e.g. `https://switchboard.thebloomandrose.com` |

---

## File Structure

```
discord-todo-local/
├── app/                        ← Windows/Mac desktop widget (unchanged)
├── server/                     ← Docker container source
│   ├── main.py                 ← Flask app + APScheduler startup
│   ├── taskbot.py              ← Daily send + SMS reply logic
│   ├── voice.py                ← Call routing, voicemail, SMS utilities
│   ├── discord_api.py          ← Discord REST API helpers
│   ├── state.py                ← JSON state file management
│   ├── config.py               ← All config from environment variables
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example            ← Template — do not commit filled-in version
├── web/                        ← Web UI container source (taskbot-web)
│   ├── app.py                  ← Flask routes: list, add, delete
│   ├── auth.py                 ← Shared-password login, lockout, session cookie
│   ├── templates/              ← index.html, login.html
│   ├── static/style.css
│   ├── check_permissions.py    ← Reports whether delete will work (Manage Messages)
│   ├── test_app.py             ← 26 self-tests — `python web/test_app.py`
│   ├── DEPLOY.md               ← Proxy host, tunnel ingress, verification
│   ├── requirements.txt
│   └── Dockerfile
├── docker-compose.yml          ← Fill in your credentials here
├── .gitignore
└── README.md
```

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/send` | Send task lists now. Add `?user=Josh` to test one person. |
| POST | `/trigger` | Pick random delay then send (like the old Cloud Scheduler flow) |
| POST | `/reply` | Twilio inbound SMS — task number replies |
| POST | `/sms` | Twilio inbound SMS — all other messages (Wave MFA, default reply) |
| POST | `/sms/send` | Send a single SMS via HTTP |
| POST | `/sms/alert` | Broadcast SMS to all configured recipients |
| POST | `/voice` | Twilio inbound call — bridges to FORWARD_NUMBER |
| POST | `/voice/bridge` | TwiML for the forwarded call leg |
| POST | `/voice/voicemail` | Voicemail greeting + record prompt |
| POST | `/voice/voicemail-handler` | Processes completed voicemail recording |
| GET | `/health` | Container health check |

---

## Migrating from the Cloud Version

If you were previously running the Google Cloud Function version:

1. Export your state: download `gs://YOUR-BUCKET/task_state.json` from GCS
2. Copy it to `/mnt/user/appdata/taskbot/data/task_state.json` on your server
3. Fill in `docker-compose.yml` with your existing credentials
4. Start the container
5. Update your Twilio webhook URLs to point to `https://switchboard.thebloomandrose.com/...`
6. Decommission the Cloud Function, Cloud Scheduler job, and Cloud Tasks queue
