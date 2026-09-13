# ClaimIQ Enterprise — backend image
#
# Multi-stage. The build stage compiles wheels; the runtime stage carries only
# what is needed to run, so the shipped image has no compiler toolchain.
#
# Python 3.12: Django 5.2 LTS requires 3.10+, and 3.12 is the current stable
# release with the longest remaining support window. See ADR 0001.

# ---------------------------------------------------------------------------
# Stage 1 — build wheels
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY backend/requirements.txt .

# HARDWARE_PROFILE selects the torch build. The CPU-only wheel is ~2 GB smaller
# than the CUDA one and is the correct default: a deployment without a GPU
# should not carry CUDA libraries it can never use. See ADR 0007.
ARG HARDWARE_PROFILE=cpu
RUN if [ "$HARDWARE_PROFILE" = "cpu" ]; then \
        pip wheel --wheel-dir /wheels \
            --extra-index-url https://download.pytorch.org/whl/cpu \
            torch --index-url https://download.pytorch.org/whl/cpu ; \
    else \
        pip wheel --wheel-dir /wheels torch ; \
    fi
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    # Belt and braces for air-gapped operation: these libraries will not attempt
    # a network fetch even if a model is missing from the local cache.
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libmagic1 \
        # docTR / OpenCV runtime dependencies
        libgl1 \
        libglib2.0-0 \
        # pdfplumber renders via pdfium; poppler covers the fallback paths
        poppler-utils \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root from the start. A process that ingests untrusted PDFs should not be
# running as uid 0.
RUN groupadd --gid 10001 claimiq \
    && useradd --uid 10001 --gid claimiq --create-home --shell /usr/sbin/nologin claimiq

COPY --from=builder /wheels /wheels
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-index --find-links=/wheels -r /tmp/requirements.txt \
    && pip freeze > /opt/requirements.lock.txt \
    && rm -rf /wheels /tmp/requirements.txt

WORKDIR /app
COPY --chown=claimiq:claimiq backend/ /app/

# Writable paths. Everything else in the image stays read-only in practice.
RUN mkdir -p /app/var/media /app/var/static /models \
    && chown -R claimiq:claimiq /app/var /models

USER claimiq

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health/live || exit 1

# Gunicorn with uvicorn workers: ASGI for streaming responses, with gunicorn's
# process supervision. Worker count is set from the environment because the
# right number depends on the host, not on the image.
CMD ["sh", "-c", "gunicorn config.asgi:application \
    --bind 0.0.0.0:8000 \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers ${GUNICORN_WORKERS:-4} \
    --timeout ${GUNICORN_TIMEOUT:-120} \
    --graceful-timeout 30 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --access-logfile - \
    --error-logfile -"]
