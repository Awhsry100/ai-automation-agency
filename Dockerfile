FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

# ✅ Create non-root user (stable UID/GID for Docker Desktop / Windows mounts)
RUN groupadd -g 10001 appuser \
 && useradd -m -u 10001 -g 10001 -s /bin/bash appuser

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# ✅ Ensure data dir exists + permissions
RUN mkdir -p /app/data \
 && chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

# ✅ Gunicorn (thread worker is good for Flask + IO)
CMD ["gunicorn", "-w", "1", "-k", "eventlet", "-b", "0.0.0.0:5000", "app:app"]

