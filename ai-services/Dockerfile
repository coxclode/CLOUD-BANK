# ─── ai-services — Dockerfile ─────────────────────────────────────────────────
# Servicio interno: solo backend lo llama. Multi-stage, imagen mínima, sin root.

FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --upgrade pip setuptools wheel hatchling && \
    pip install --no-cache-dir .


FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="CLOUD BANK ai-services"
LABEL org.opencontainers.image.version="1.0"
LABEL org.opencontainers.image.description="Motor de IA: LangGraph + 4 Deep Agents + LLM multi-proveedor"

RUN groupadd --gid 1000 aiservices && \
    useradd --uid 1000 --gid aiservices --shell /bin/bash --create-home aiservices

WORKDIR /app

COPY --from=builder /venv /venv
COPY --chown=aiservices:aiservices src/ ./src/
COPY --chown=aiservices:aiservices .env.example ./.env.example

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/venv/bin:$PATH"

EXPOSE 8100

USER aiservices

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health')"

CMD ["python", "-m", "uvicorn", "src.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8100", \
     "--no-server-header"]
