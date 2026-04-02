FROM python:3.11-slim

# Don't write .pyc files, don't buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (layer-cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Data directory — will be overridden by the volume mount in docker-compose
RUN mkdir -p /data

EXPOSE 8080

CMD ["python", "main.py"]
