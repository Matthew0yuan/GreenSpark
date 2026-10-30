# The app in one container (deploy/README.md). Build from the repository root:
#   docker build -f deploy/Dockerfile -t greenspark-app:demo .
# Only what the app reads is copied in (deploy/Dockerfile.dockerignore); the live data live in a volume at /app/var.

# --- the frontend, built once ------------------------------------------------------------------------------------------
FROM node:22.21.0-bookworm-slim AS web
WORKDIR /web
COPY app/frontend/package.json app/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY app/frontend/ ./
RUN npm run build

# --- the app -----------------------------------------------------------------------------------------------------------
FROM python:3.13.5-slim-bookworm
# libgomp1: LightGBM's OpenMP (the morning rebuild's price forecast)
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra app --no-install-project && rm /bin/uv

COPY vpp/ vpp/
COPY scripts/ scripts/
COPY reports/ reports/
COPY app/__init__.py app/
COPY app/backend/ app/backend/
COPY app/engine/ app/engine/
COPY app/live/ app/live/
COPY analysis/2026-10-28/app_hurdle50/ analysis/2026-10-28/app_hurdle50/
COPY analysis/2026-10-28/app_factory_h50/ analysis/2026-10-28/app_factory_h50/
COPY analysis/2026-10-28/spikev3/model/ analysis/2026-10-28/spikev3/model/
COPY data/raw/aemo_rtms/rtms_openapi.json data/raw/aemo_rtms/
COPY --from=web /web/dist/ app/frontend/dist/

# not root; the code is read-only to it, only /app/var (the volume) and /tmp are writable
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin gs && mkdir -p /app/var && chown gs:gs /app/var
USER 10001
ENV PATH=/app/.venv/bin:$PATH HOME=/tmp MPLCONFIGDIR=/tmp/matplotlib PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 OPENBLAS_NUM_THREADS=1
EXPOSE 8000
CMD ["python", "-m", "app.backend", "--host", "0.0.0.0", "--port", "8000"]
