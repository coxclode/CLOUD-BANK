"""
Registro de métricas Prometheus — CLOUD BANK

Nomenclatura: cloudbank_<subsystem>_<metric>_<unit>
Etiquetas estándar en todas las métricas: environment, version

Exportadas en /metrics para Prometheus scraping.
Grafana consume estas métricas para los dashboards de operaciones.
"""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Summary,
    make_asgi_app,
)
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

# ── Registry personalizado (evita conflictos con el registry global) ──────────
REGISTRY = CollectorRegistry()

# ── Solicitudes de crédito ────────────────────────────────────────────────────
CREDIT_APPLICATIONS_TOTAL = Counter(
    "cloudbank_credit_applications_total",
    "Total de solicitudes de crédito recibidas",
    labelnames=["channel", "purpose", "status"],
    registry=REGISTRY,
)

CREDIT_DECISIONS_TOTAL = Counter(
    "cloudbank_credit_decisions_total",
    "Total de decisiones crediticias emitidas",
    labelnames=["outcome", "reason", "decided_by"],
    registry=REGISTRY,
)

CREDIT_ESCALATIONS_TOTAL = Counter(
    "cloudbank_credit_escalations_total",
    "Total de escalaciones al comité",
    labelnames=["committee_type", "priority"],
    registry=REGISTRY,
)

# ── Pipeline de evaluación ────────────────────────────────────────────────────
PIPELINE_DURATION_SECONDS = Histogram(
    "cloudbank_pipeline_duration_seconds",
    "Duración del pipeline completo de evaluación crediticia",
    labelnames=["status"],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
    registry=REGISTRY,
)

ACTIVE_PIPELINES = Gauge(
    "cloudbank_active_pipelines",
    "Número de pipelines de evaluación en ejecución",
    registry=REGISTRY,
)

# ── Agentes Deep ──────────────────────────────────────────────────────────────
AGENT_EXECUTION_DURATION_SECONDS = Histogram(
    "cloudbank_agent_execution_duration_seconds",
    "Duración de ejecución de cada agente Deep",
    labelnames=["agent_name", "outcome"],
    buckets=(0.1, 0.5, 1.0, 3.0, 10.0, 30.0),
    registry=REGISTRY,
)

AGENT_SELF_CORRECTIONS_TOTAL = Counter(
    "cloudbank_agent_self_corrections_total",
    "Total de autocorrecciones del loop L7 por agente",
    labelnames=["agent_name"],
    registry=REGISTRY,
)

AGENT_QUALITY_SCORE = Histogram(
    "cloudbank_agent_quality_score",
    "Quality score declarado por cada agente (0.0-1.0)",
    labelnames=["agent_name"],
    buckets=(0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    registry=REGISTRY,
)

# ── Modelos ML / LLM ─────────────────────────────────────────────────────────
LLM_TOKENS_USED = Counter(
    "cloudbank_llm_tokens_total",
    "Total de tokens consumidos por el LLM",
    labelnames=["model", "agent_name", "direction"],  # direction: input|output
    registry=REGISTRY,
)

LLM_CALLS_TOTAL = Counter(
    "cloudbank_llm_calls_total",
    "Total de llamadas al LLM",
    labelnames=["model", "agent_name", "status"],
    registry=REGISTRY,
)

MODEL_DEFAULT_PROBABILITY = Histogram(
    "cloudbank_model_default_probability",
    "Distribución de probabilidades de incumplimiento",
    buckets=(0.05, 0.10, 0.20, 0.30, 0.45, 0.60, 0.70, 0.80, 1.0),
    registry=REGISTRY,
)

FRAUD_SCORE_DISTRIBUTION = Histogram(
    "cloudbank_fraud_score_distribution",
    "Distribución de fraud scores",
    buckets=(0.15, 0.35, 0.65, 0.85, 1.0),
    registry=REGISTRY,
)

# ── Seguridad ─────────────────────────────────────────────────────────────────
SECURITY_VIOLATIONS_TOTAL = Counter(
    "cloudbank_security_violations_total",
    "Total de violaciones de seguridad detectadas",
    labelnames=["violation_type", "severity"],
    registry=REGISTRY,
)

RATE_LIMIT_HITS_TOTAL = Counter(
    "cloudbank_rate_limit_hits_total",
    "Total de requests rechazados por rate limiting",
    labelnames=["identifier_type"],
    registry=REGISTRY,
)

AUTHENTICATION_FAILURES_TOTAL = Counter(
    "cloudbank_authentication_failures_total",
    "Total de fallos de autenticación",
    labelnames=["reason"],
    registry=REGISTRY,
)

# ── API ───────────────────────────────────────────────────────────────────────
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "cloudbank_http_request_duration_seconds",
    "Duración de requests HTTP",
    labelnames=["method", "endpoint", "status_code"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
    registry=REGISTRY,
)

HTTP_REQUESTS_TOTAL = Counter(
    "cloudbank_http_requests_total",
    "Total de requests HTTP",
    labelnames=["method", "endpoint", "status_code"],
    registry=REGISTRY,
)

# ── Infraestructura ───────────────────────────────────────────────────────────
REDIS_OPERATIONS_TOTAL = Counter(
    "cloudbank_redis_operations_total",
    "Total de operaciones Redis",
    labelnames=["operation", "status"],
    registry=REGISTRY,
)

DB_QUERY_DURATION_SECONDS = Histogram(
    "cloudbank_db_query_duration_seconds",
    "Duración de queries a la base de datos",
    labelnames=["operation", "table"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
    registry=REGISTRY,
)

# ── Helpers de registro ───────────────────────────────────────────────────────

def record_credit_decision(outcome: str, reason: str) -> None:
    CREDIT_DECISIONS_TOTAL.labels(
        outcome=outcome, reason=reason, decided_by="pipeline"
    ).inc()


def record_agent_execution(agent: str, outcome: str, duration_s: float, quality: float) -> None:
    AGENT_EXECUTION_DURATION_SECONDS.labels(
        agent_name=agent, outcome=outcome
    ).observe(duration_s)
    AGENT_QUALITY_SCORE.labels(agent_name=agent).observe(quality)


def record_security_violation(violation_type: str, severity: str = "HIGH") -> None:
    SECURITY_VIOLATIONS_TOTAL.labels(
        violation_type=violation_type, severity=severity
    ).inc()


def get_metrics_output() -> bytes:
    return generate_latest(REGISTRY)


def get_metrics_content_type() -> str:
    return CONTENT_TYPE_LATEST
