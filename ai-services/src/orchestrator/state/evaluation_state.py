"""
Estado tipado del pipeline LangGraph.

El estado es el único canal de comunicación entre nodos.
Cada nodo recibe el estado completo y devuelve una actualización parcial.
LangGraph fusiona la actualización en el estado global.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from src.contracts.agent_result import AgentResult


class PipelineStatus(str, Enum):
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    ESCALATED = "ESCALATED"


class NodeName(str, Enum):
    VALIDATE_INPUT   = "validate_input"
    FRAUD_ANALYSIS   = "fraud_analysis"
    CREDIT_HISTORY   = "credit_history"
    ACTUARIAL        = "actuarial_analysis"
    APPROVAL         = "approval_decision"
    AUDIT            = "audit_finalize"
    ERROR_HANDLER    = "error_handler"
    HUMAN_ESCALATION = "human_escalation"


class PipelineError(BaseModel):
    node: str
    error_type: str
    message: str
    is_fatal: bool = False
    timestamp: float = 0.0


class EvaluationState(TypedDict):
    """
    Estado global del pipeline de evaluación crediticia.

    Reglas LangGraph:
    - Los nodos solo pueden AGREGAR elementos a listas (no reemplazarlas sin reducer).
    - Los campos escalares son reemplazados por el nodo que los actualiza.
    - El campo 'errors' usa el reducer 'add' para acumular.
    """

    # ── Identificadores ───────────────────────────────────────────────────────
    pipeline_id:    str
    application_id: str
    correlation_id: Optional[str]

    # ── Datos de entrada ─────────────────────────────────────────────────────
    application_data: dict[str, Any]

    # ── Control de flujo ─────────────────────────────────────────────────────
    pipeline_status: PipelineStatus
    current_node:    NodeName
    is_fatal_error:  bool

    # ── Resultados de agentes ─────────────────────────────────────────────────
    agent_results: dict[str, AgentResult]

    # ── Métricas extraídas (usadas por routing y política) ───────────────────
    fraud_score:          float
    aml_clear:            bool
    post_credit_dti:      float
    default_probability:  float
    suggested_rate:       float
    fraud_flags:          list[str]

    # ── Auditoría ────────────────────────────────────────────────────────────
    errors: list[PipelineError]

    # ── Timing ───────────────────────────────────────────────────────────────
    started_at:       float
    node_durations:   dict[str, float]

    # ── Mensajes LLM (LangGraph nativo, con reducer add_messages) ───────────
    # messages: Annotated[list, add_messages]
