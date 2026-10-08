# ==========================================
# Multi-stage Dockerfile for Sentinel Imagery Analysis
# ==========================================

# ------------------------------------------
# Stage 1: Builder
# ------------------------------------------
FROM python:3.13-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libglib2.0-dev \
    python3-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-warn-script-location -r requirements.txt

# ------------------------------------------
# Stage 2: Production Runtime
# ------------------------------------------
FROM python:3.13-slim AS runner

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/home/sentinel/.local/bin:${PATH}" \
    PORT=5000 \
    SENTINEL_DATABASE_PATH="/app/data/sentinel.db" \
    SENTINEL_OUTPUT_ROOT="/app/scans" \
    SENTINEL_CACHE_ROOT="/app/cache"

WORKDIR /app

# Install minimal runtime dependencies for OpenCV, PIL, and healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root application user
RUN groupadd -r sentinel -g 10001 && \
    useradd -r -g sentinel -u 10001 -m -d /home/sentinel -s /bin/bash sentinel

# Copy installed Python packages from builder stage
COPY --from=builder /root/.local /home/sentinel/.local

# Copy application directories and files
COPY sentinel_analysis/ /app/sentinel_analysis/
COPY templates/ /app/templates/
COPY static/ /app/static/
COPY app.py /app/app.py
COPY wsgi.py /app/wsgi.py
COPY gunicorn.conf.py /app/gunicorn.conf.py

# Create persistent storage mountpoints and assign permissions
RUN mkdir -p /app/data /app/scans /app/cache && \
    chown -R sentinel:sentinel /app /home/sentinel

USER sentinel

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5000/healthz || exit 1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
