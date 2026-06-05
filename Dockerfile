# ---- Build stage ----
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps for git and compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml setup.cfg ./
COPY devpulse/ ./devpulse/

RUN pip install --upgrade pip \
    && pip install --no-cache-dir ".[toml]"


# ---- Runtime stage ----
FROM python:3.12-slim AS runtime

WORKDIR /app

# Git is needed at runtime for repo analysis
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin/devpulse    /usr/local/bin/devpulse

# Copy app source and web assets
COPY devpulse/ ./devpulse/

# Data directory (mount a volume here in production)
RUN mkdir -p /data

ENV DEVPULSE_DB_PATH=/data/devpulse.db \
    DEVPULSE_EXPORT_DIR=/data/exports \
    DEVPULSE_STATIC_DIR=/app/devpulse/web \
    DEVPULSE_SERVER_HOST=0.0.0.0 \
    DEVPULSE_SERVER_PORT=8765 \
    PYTHONUNBUFFERED=1

EXPOSE 8765

VOLUME ["/data"]

CMD ["python", "-m", "devpulse.server"]