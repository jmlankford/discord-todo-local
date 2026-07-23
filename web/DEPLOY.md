# TaskBot Web UI — Deploy Runbook

Hostname: **tasks.lankamerica.com** · Container: **taskbot-web** · Host port **8091** → container 8080
Stack: existing **taskbot** stack (Portainer 217) · Image: `ghcr.io/jmlankford/discord-todo-local-web:latest`

Everything below runs on **your** side (LAN / Cloudflare / Portainer). The image is
published by the repo's Actions (`Build & Push Web UI to ghcr.io`).

---

## 1. Add the service to the taskbot stack (Portainer 217)

In the existing stack editor, add a second service. Reuse the Discord vars the
`taskbot` service already sets — do **not** paste secret values anywhere new.
Add two web-only secrets in the stack's **Environment variables** section:

- `APP_PASSWORD` — the shared login password
- `SESSION_SECRET` — random, e.g. `openssl rand -hex 32`

```yaml
  taskbot-web:
    image: ghcr.io/jmlankford/discord-todo-local-web:latest
    container_name: taskbot-web
    restart: unless-stopped
    ports:
      - "8091:8080"
    volumes:
      - /mnt/user/appdata/taskbot/data:/data:ro   # read-only; matches digest numbering
    environment:
      DISCORD_TOKEN: ${DISCORD_TOKEN}
      GUILD_ID: ${GUILD_ID}
      CHANNEL_ID: ${CHANNEL_ID}
      APP_PASSWORD: ${APP_PASSWORD}
      SESSION_SECRET: ${SESSION_SECRET}
      PORT: "8080"
      STATE_FILE: "/data/task_state.json"
      TZ: "America/New_York"
```

`${VAR}` inherits from the stack env, so no secret is duplicated. Deploy the stack
(this only **adds** a container; the `taskbot` service is untouched).

> The `:ro` `/data` mount lets the UI read `completed_tasks_blocklist` so its
> numbering matches the digest exactly. It never writes state.

---

## 2. NPM proxy host

- Domain: `tasks.lankamerica.com`
- Scheme: `http` · Forward host: `taskbot-web` (if NPM shares the stack network) or the Unraid IP · Forward port: `8080` (container-name route) or `8091` (host-IP route)
- Block Common Exploits: on · Websockets: not required
- **SSL** → request a new Let's Encrypt cert **via DNS-01** (HTTP-01 is retired here); Force SSL on

## 3. Cloudflare DNS

- Type **CNAME**, name `tasks`, target `f280cb78-3750-4782-a5fd-dd27e20ed870.cfargotunnel.com`, **Proxied** (orange cloud).

## 4. Tunnel ingress (homelab tunnel)

Add a rule **above** the catch-all. `originServerName` is mandatory — without it
NPM's TLS handshake 502s (this bit us before):

```yaml
  - hostname: tasks.lankamerica.com
    service: https://192.168.1.165:35443
    originRequest:
      noTLSVerify: true
      originServerName: tasks.lankamerica.com
```

---

## 5. Discord permission check (do this early — may block delete)

Deleting a message posted by **another user** needs **Manage Messages** on the
channel (the bot can always delete its own). Run, inside the container:

```bash
docker exec taskbot-web python check_permissions.py
```

It reports human-vs-bot authorship of existing tasks and whether Manage Messages
is held. If it prints `Manage Messages: NO`, delete of human-posted tasks will
fail (403) and the UI says so plainly — it does **not** hide the task. Granting
Manage Messages is a Discord change only Josh can make.

---

## 6. Verify (checklist)

- [ ] `https://tasks.lankamerica.com` loads over HTTPS through the tunnel
- [ ] Wrong password rejected; correct admits; session survives a reload
- [ ] All three lists show and match the SMS digest content **and order**
- [ ] Add a task for each user → lands in the correct Discord thread
- [ ] Delete a task → disappears from Discord
- [ ] Digest still works: `curl -X POST http://192.168.1.165:8090/send?user=Josh` from the LAN, then `docker logs taskbot`
- [ ] `docker ps` shows **both** `taskbot` and `taskbot-web` up; taskbot unaffected
