# ─── backend — Dockerfile ──────────────────────────────────────────────────────
# API pública: único punto que el frontend conoce. Multi-stage, sin root.

FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --upgrade pip setuptools wheel hatchling && \
    pip install --no-cache-dir --prefix=/install .


FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="CLOUD BANK Backend"
LABEL org.opencontainers.image.version="2.0"
LABEL org.opencontainers.image.description="API REST: dominio, casos de uso, persistencia, seguridad"

RUN groupadd --gid 1000 cloudbank && \
    useradd --uid 1000 --gid cloudbank --shell /bin/bash --create-home cloudbank

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=cloudbank:cloudbank src/ ./src/
COPY --chown=cloudbank:cloudbank config/ ./config/
COPY --chown=cloudbank:cloudbank .env.example ./.env.example

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    PATH="/usr/local/bin:$PATH"

EXPOSE 8000
EXPOSE 9090

USER cloudbank

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/v1/admin/health/live')"

CMD ["python", "-m", "uvicorn", "src.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--no-server-header"]
