# Build context is the REPO ROOT (see .github workflow) so we can package the
# exact server modules the digest uses. One source of truth, no fork.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Deps first (layer cache)
COPY web/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Reuse the real server modules — copied verbatim from ./server, never edited here.
COPY server/discord_api.py server/config.py server/taskbot.py server/state.py ./

# Web-specific code
COPY web/app.py web/auth.py web/check_permissions.py ./
COPY web/templates ./templates
COPY web/static ./static

EXPOSE 8080

CMD ["python", "app.py"]
