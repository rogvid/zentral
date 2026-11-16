# syntax=docker/dockerfile:1.7
#
# Zentral — single image for the API/web server, the worker and migrations.
#   docker build -t zentral .
#
# Pin the base images by digest in your own registry for reproducible builds.

# --- 1. web client -----------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- 2. Python dependencies ----------------------------------------------------
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable

# --- 3. runtime ----------------------------------------------------------------
FROM python:3.13-slim AS runtime
LABEL org.opencontainers.image.title="Zentral" \
      org.opencontainers.image.description="Privacy-first, AI-friendly service desk" \
      org.opencontainers.image.licenses="Apache-2.0"

# Unprivileged, fixed uid so that Kubernetes `runAsNonRoot` can verify it.
RUN groupadd --system --gid 10001 zentral \
 && useradd --system --uid 10001 --gid zentral --home-dir /nonexistent --shell /usr/sbin/nologin zentral

COPY --from=build /app/.venv /app/.venv
COPY --from=web /web/dist /app/web

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ZENTRAL_ENV=production \
    ZENTRAL_STATIC_DIR=/app/web

USER 10001:10001
WORKDIR /app
EXPOSE 8000

# The image needs no writable filesystem: run it with a read-only root.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]

ENTRYPOINT ["zentral"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
