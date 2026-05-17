# =====================================================================
# SHIELD app image. Multi-stage; runs as non-root.
# =====================================================================
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
 && rm -rf /var/lib/apt/lists/*

# --- builder ---
FROM base AS builder
WORKDIR /build
COPY requirements.txt requirements-dev.txt ./
# Always include dev deps so `pytest`, `ruff`, `pre-commit` are available
# in the running container. The dev compose override is the only intended
# consumer of this image; if a lean prod image is needed later, gate the
# dev install behind an ARG.
RUN pip install --prefix=/install -r requirements.txt \
 && pip install --prefix=/install -r requirements-dev.txt \
 # spaCy model for Presidio NER. en_core_web_sm is ~12 MB. We install
 # the wheel by direct URL (rather than `python -m spacy download`) so
 # it lands in /install alongside the rest of the deps. The 3.7.1 model
 # is the one the spacy==3.7.5 release page validates against.
 && pip install --prefix=/install \
      "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl"

# --- runtime ---
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -r shield --gid=1000 \
 && useradd -r -g shield --uid=1000 -d /app -s /sbin/nologin shield

COPY --from=builder /install /usr/local
WORKDIR /app
COPY --chown=shield:shield . /app
# Pre-create the artifacts dir owned by the shield user so that when
# compose mounts a fresh `artifacts` named volume on top, Docker copies
# this empty-but-correctly-owned directory into the volume — fixing the
# PermissionError shield user otherwise hits writing to a root-owned vol.
RUN mkdir -p /app/artifacts && chown shield:shield /app/artifacts

USER shield
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--access-logfile", "-", "wsgi:app"]
