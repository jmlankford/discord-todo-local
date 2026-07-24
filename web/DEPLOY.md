# TaskBot Web UI — Deploy Runbook

Hostname: **tasks.thebloomandrose.com** · Container: **taskbot-web** · **No published host port**
Stack: existing **taskbot** stack (Portainer 217) · Image: `ghcr.io/jmlankford/discord-todo-local-web:latest`

Everything below runs on **your** side (LAN / Cloudflare / Portainer). The image is
published by the repo's Actions (`Build & Push Web UI to ghcr.io`).

## Networking model — no published host port

`taskbot-web` publishes **nothing** on the Unraid host interface. NPM reaches it by
**container name** over the `nginx_default` network:

```
Cloudflare Tunnel → NPM (nginx_default) → taskbot-web:8080
                                              │
                                              └→ taskbot:8080  (taskbot_default)
```

An earlier draft of this runbook mapped host port **8091**. That was wrong — **8091
is not free**, it belongs to `zwave2mqtt-zwavejs2mqtt-1`. Rather than hunt for
another free port on a host where 8080, 8081, 8090, 8091, 35080/35443, 5055, 6380,
8410, 9980 and 9000 are all bound, forwarding by container name removes the
conflict class entirely.

Precedent: NPM already forwards `office.lankamerica.com` to `collabora:9980` by name.
Verified live — `docker exec npm getent hosts nextcloud-app` resolves on NPM's subnet.

`taskbot-web` joins **two** networks:

| Network | Why |
|---|---|
| `default` (`taskbot_default`) | So it can resolve and call `taskbot:8080` |
| `nginx_default` | So NPM can resolve `taskbot-web` |

`nginx_default` is `external: true` — owned by the NPM stack, only joined here.

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
    # no ports: — nothing published on the host
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
    networks:
      - default
      - nginx_default

networks:
  nginx_default:
    external: true
```

`${VAR}` inherits from the stack env, so no secret is duplicated. Deploy the stack
(this only **adds** a container; the `taskbot` service is untouched).

> The `:ro` `/data` mount lets the UI read `completed_tasks_blocklist` so its
> numbering matches the digest exactly. It never writes state.

---

## 2. NPM proxy host

- Domain: `tasks.thebloomandrose.com`
- Scheme: `http` · **Forward host: `taskbot-web`** · **Forward port: `8080`**
- Block Common Exploits: on · Websockets: not required
- **SSL** → request a new Let's Encrypt cert **via DNS-01** (HTTP-01 is retired here); Force SSL on

> Forward Hostname is the **container name**, not an IP and not the Unraid host.
> Forward Port is the port *inside* the container. Do not enter `192.168.1.165` —
> the point of this design is that nothing is published on the host.

## 3. Cloudflare DNS

- Type **CNAME**, name `tasks`, target `f280cb78-3750-4782-a5fd-dd27e20ed870.cfargotunnel.com`, **Proxied** (orange cloud).

## 4. Tunnel ingress (homelab tunnel)

Add a rule **above** the catch-all. `originServerName` is mandatory — without it
NPM's TLS handshake 502s (this bit us before):

```yaml
  - hostname: tasks.thebloomandrose.com
    service: https://192.168.1.165:35443
    originRequest:
      noTLSVerify: true
      originServerName: tasks.thebloomandrose.com
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

**Routing** — each command isolates one hop, so a failure tells you which link broke:

```bash
# a. Which network is NPM actually on? (don't assume it's still nginx_default)
docker inspect npm --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{println}}{{end}}'

# b. Did taskbot-web join that same network? Both names must appear.
docker network inspect nginx_default --format '{{range .Containers}}{{.Name}} {{end}}'

# c. Can NPM resolve it by name? Expect an IP on 172.20.0.0/16.
docker exec npm getent hosts taskbot-web

# d. Can NPM actually reach the app?
docker exec npm curl -sS -o /dev/null -w '%{http_code}\n' http://taskbot-web:8080/

# e. Nothing new listening on the host — the whole point. taskbot-web must show
#    NO 0.0.0.0: mapping. taskbot still shows 0.0.0.0:8090->8080/tcp (unchanged).
docker ps --format '{{.Names}}\t{{.Ports}}' | grep taskbot
```

**Application:**

- [ ] `https://tasks.thebloomandrose.com` loads over HTTPS through the tunnel
- [ ] Wrong password rejected; correct admits; session survives a reload
- [ ] All three lists show and match the SMS digest content **and order**
- [ ] Add a task for each user → lands in the correct Discord thread
- [ ] Delete a task → disappears from Discord
- [ ] Digest still works: `curl -X POST http://192.168.1.165:8090/send?user=Josh` from the LAN, then `docker logs taskbot`
- [ ] `docker ps` shows **both** `taskbot` and `taskbot-web` up; taskbot unaffected
