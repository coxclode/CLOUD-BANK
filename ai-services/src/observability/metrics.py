"""
CLOUD BANK — Métricas Prometheus
Instrumentación completa del pipeline de evaluación crediticia.
Dashboards: Grafana. Alertas: AlertManager.
"""

from __future__ import annotations

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Summary,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# ─── Registry ─────────────────────────────────────────────────────────────────

REGISTRY = CollectorRegistry(auto_describe=True)

# ─── Contadores ───────────────────────────────────────────────────────────────

APPLICATIONS_TOTAL = Counter(
    "cloudbank_applications_total",
    "Total de solicitudes de crédito recibidas",
    ["channel", "status"],
    registry=REGISTRY,
)

AGENT_EXECUTIONS_TOTAL = Counter(
    "cloudbank_agent_executions_total",
    "Total de ejecuciones por agente",
    ["agent", "status"],
    registry=REGISTRY,
)

AGENT_RETRIES_TOTAL = Counter(
    "cloudbank_agent_retries_total",
    "Total de reintentos por agente",
    ["agent"],
    registry=REGISTRY,
)

SECURITY_VIOLATIONS_TOTAL = Counter(
    "cloudbank_security_violations_total",
    "Total de violaciones de seguridad detectadas",
    ["violation_type"],
    registry=REGISTRY,
)

FRAUD_DETECTIONS_TOTAL = Counter(
    "cloudbank_fraud_detections_total",
    "Total de fraudes detectados por nivel de riesgo",
    ["risk_level"],
    registry=REGISTRY,
)

APPROVALS_TOTAL = Counter(
    "cloudbank_approvals_total",
    "Total de decisiones de aprobación",
    ["decision", "reason"],
    registry=REGISTRY,
)

ESCALATIONS_TOTAL = Counter(
    "cloudbank_escalations_total",
    "Total de escalaciones a comité humano",
    ["committee_type", "priority"],
    registry=REGISTRY,
)

EXTERNAL_SERVICE_CALLS_TOTAL = Counter(
    "cloudbank_external_service_calls_total",
    "Total de llamadas a servicios externos",
    ["service", "status"],
    registry=REGISTRY,
)

LLM_CALLS_TOTAL = Counter(
    "cloudbank_llm_calls_total",
    "Total de llamadas al LLM",
    ["model", "agent", "status"],
    registry=REGISTRY,
)

# ─── Histogramas ──────────────────────────────────────────────────────────────

APPLICATION_DURATION = Histogram(
    "cloudbank_application_duration_seconds",
    "Duración total de evaluación de solicitud",
    ["channel"],
    buckets=[1, 2, 5, 10, 15, 20, 30, 45, 60],
    registry=REGISTRY,
)

AGENT_DURATION = Histogram(
    "cloudbank_agent_duration_seconds",
    "Duración de cada agente",
    ["agent"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 15, 20],
    registry=REGISTRY,
)

LLM_TOKENS_USED = Histogram(
    "cloudbank_llm_tokens_used",
    "Tokens consumidos por llamada LLM",
    ["model", "agent", "type"],
    buckets=[100, 500, 1000, 2000, 4000, 8000],
    registry=REGISTRY,
)

FRAUD_SCORE_DISTRIBUTION = Histogram(
    "cloudbank_fraud_score_distribution",
    "Distribución de scores de fraude",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    registry=REGISTRY,
)

DEFAULT_PROBABILITY_DISTRIBUTION = Histogram(
    "cloudbank_default_probability_distribution",
    "Distribución de probabilidad de impago",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    registry=REGISTRY,
)

CREDIT_SCORE_DISTRIBUTION = Histogram(
    "cloudbank_credit_score_distribution",
    "Distribución de scores crediticios",
    buckets=[300, 400, 500, 550, 600, 650, 700, 750, 800, 850, 1000],
    registry=REGISTRY,
)

# ─── Gauges ───────────────────────────────────────────────────────────────────

ACTIVE_EVALUATIONS = Gauge(
    "cloudbank_active_evaluations",
    "Evaluaciones activas en el pipeline",
    registry=REGISTRY,
)

AGENT_QUEUE_SIZE = Gauge(
    "cloudbank_agent_queue_size",
    "Solicitudes en cola por agente",
    ["agent"],
    registry=REGISTRY,
)

# ─── Funciones de utilidad ────────────────────────────────────────────────────

def record_application(channel: str, status: str) -> None:
    APPLICATIONS_TOTAL.labels(channel=channel, status=status).inc()


def record_agent_execution(agent: str, status: str, duration_s: float) -> None:
    AGENT_EXECUTIONS_TOTAL.labels(agent=agent, status=status).inc()
    AGENT_DURATION.labels(agent=agent).observe(duration_s)


def record_agent_retry(agent: str) -> None:
    AGENT_RETRIES_TOTAL.labels(agent=agent).inc()


def record_security_violation(violation_type: str) -> None:
    SECURITY_VIOLATIONS_TOTAL.labels(violation_type=violation_type).inc()


def record_fraud_detection(risk_level: str, score: float) -> None:
    FRAUD_DETECTIONS_TOTAL.labels(risk_level=risk_level).inc()
    FRAUD_SCORE_DISTRIBUTION.observe(score)


def record_approval(decision: str, reason: str) -> None:
    APPROVALS_TOTAL.labels(decision=decision, reason=reason).inc()


def record_escalation(committee_type: str, priority: str) -> None:
    ESCALATIONS_TOTAL.labels(committee_type=committee_type, priority=priority).inc()


def record_llm_call(model: str, agent: str, status: str, input_tokens: int, output_tokens: int) -> None:
    LLM_CALLS_TOTAL.labels(model=model, agent=agent, status=status).inc()
    LLM_TOKENS_USED.labels(model=model, agent=agent, type="input").observe(input_tokens)
    LLM_TOKENS_USED.labels(model=model, agent=agent, type="output").observe(output_tokens)


def record_credit_score(score: int) -> None:
    CREDIT_SCORE_DISTRIBUTION.observe(score)


def record_default_probability(prob: float) -> None:
    DEFAULT_PROBABILITY_DISTRIBUTION.observe(prob)


def get_metrics_output() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
