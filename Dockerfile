# syntax=docker/dockerfile:1
#
# بورس‌نگار — application image (Gunicorn + Flask)
#
# ---------------------------------------------------------------------------
# IRAN / BLOCKED-REGISTRY NOTE
#
# Docker Hub refuses connections from Iranian IP ranges, so `FROM python:...`
# will not resolve on the target VPS. Every base image reference here is
# prefixed with ${REGISTRY}, which is EMPTY by default (normal Docker Hub) and
# is meant to be set to a domestic mirror at build time:
#
#     docker compose build --build-arg REGISTRY=<mirror>/
#
# The trailing slash is part of the value. Set REGISTRY in deploy/.env and
# compose passes it through. See the README for the candidate mirrors — confirm
# one is reachable from your VPS before the first build, since these come and go.
#
# PyPI is usually reachable from Iran but slow; PIP_INDEX_URL swaps in a
# domestic mirror the same way. No AWS / GCP / Azure / Heroku / Vercel / Fly /
# Render service is used anywhere in this stack.
# ---------------------------------------------------------------------------
ARG REGISTRY=

FROM ${REGISTRY}python:3.12-slim-bookworm

# UTF-8 everywhere: the entire application is Persian, and a C-locale default
# turns every log line and every subprocess write into mojibake or a
# UnicodeEncodeError. PYTHONUNBUFFERED keeps `docker compose logs` live.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUTF8=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Non-root from the start. A fixed uid/gid keeps ownership stable across
# rebuilds and matches the named volume created below, so the state volume does
# not silently become unwritable after an image update.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# --- dependency layer -------------------------------------------------------
# requirements.txt is copied ALONE and installed before any source, so editing
# db.py or a template reuses this layer instead of reinstalling everything.
# psycopg2-binary ships its own libpq, so no build toolchain or libpq-dev is
# needed — which is what keeps this image slim and the build reproducible.
ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=
COPY requirements.txt ./
RUN set -eux; \
    if [ -n "${PIP_INDEX_URL}" ]; then \
        pip install --no-cache-dir \
            --index-url "${PIP_INDEX_URL}" \
            ${PIP_TRUSTED_HOST:+--trusted-host ${PIP_TRUSTED_HOST}} \
            -r requirements.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi; \
    python -c "import flask, psycopg2, redis, gunicorn; print('deps ok')"

# --- application source -----------------------------------------------------
# Owned by root and NOT writable by the `app` user: a compromised worker must
# not be able to rewrite its own code. Everything the app writes at runtime goes
# to APP_STATE_DIR instead (see market.py), which is the one writable path.
COPY --chown=root:root . /app

ENV APP_STATE_DIR=/var/lib/boursenegar
RUN mkdir -p "${APP_STATE_DIR}" \
 && chown app:app "${APP_STATE_DIR}" \
 && chmod 750 "${APP_STATE_DIR}" \
 && rm -rf /app/.tools /app/backups /app/legacy /app/__pycache__

USER app

EXPOSE 8000

# Liveness only — /healthz deliberately touches neither PostgreSQL nor Redis, so
# a database hiccup cannot turn into a restart loop. compose declares the same
# check; this one makes the image correct when run standalone.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

# `app:app` = the Flask object named `app` in app.py. Worker counts, timeouts
# and logging all live in gunicorn.conf.py, which explains its own numbers.
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
