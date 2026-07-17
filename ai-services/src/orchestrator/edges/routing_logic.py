"""
Lógica de routing condicional del grafo.

Cada función recibe el estado actual y devuelve el nombre del siguiente nodo.
El routing es determinístico y auditable — no hay aleatoriedad.

Umbrales de routing (diferentes de umbrales de política):
  El routing decide si el pipeline puede continuar o debe cortocircuitarse.
  La política decide si el crédito se aprueba o rechaza.
"""

from __future__ import annotations

from src.orchestrator.state.evaluation_state import EvaluationState, NodeName, PipelineStatus
from src.contracts.agent_result import AgentOutcome

# Umbral de fraude para corto-circuito inmediato (sin pasar por más agentes)
_FRAUD_CIRCUIT_BREAKER = 0.90


def route_after_validation(state: EvaluationState) -> str:
    if state.get("is_fatal_error"):
        return NodeName.ERROR_HANDLER
    if state.get("pipeline_status") == PipelineStatus.FAILED:
        return NodeName.ERROR_HANDLER
    return NodeName.FRAUD_ANALYSIS


def route_after_security(state: EvaluationState) -> str:
    if state.get("is_fatal_error"):
        return NodeName.ERROR_HANDLER
    return NodeName.FRAUD_ANALYSIS


def route_after_fraud(state: EvaluationState) -> str:
    """
    Post-fraude: si el score es extremadamente alto (≥0.90), corto-circuito
    inmediato hacia auditoría con decisión REJECTED. De lo contrario, continúa.
    """
    if state.get("is_fatal_error"):
        return NodeName.ERROR_HANDLER

    fraud_result = state.get("agent_results", {}).get("fraud")
    if fraud_result and fraud_result.outcome == AgentOutcome.FAILED and fraud_result.is_critical_failure:
        return NodeName.ERROR_HANDLER

    fraud_score = state.get("fraud_score", 0.0)
    if fraud_score >= _FRAUD_CIRCUIT_BREAKER:
        # Corto-circuito: ir directo a auditoría con rechazo por fraude crítico
        return NodeName.AUDIT

    return NodeName.CREDIT_HISTORY


def route_after_credit(state: EvaluationState) -> str:
    if state.get("is_fatal_error"):
        return NodeName.ERROR_HANDLER

    credit_result = state.get("agent_results", {}).get("credit")
    if credit_result and credit_result.outcome == AgentOutcome.FAILED and credit_result.is_critical_failure:
        return NodeName.ERROR_HANDLER

    return NodeName.ACTUARIAL


def route_after_actuarial(state: EvaluationState) -> str:
    if state.get("is_fatal_error"):
        return NodeName.ERROR_HANDLER

    actuarial_result = state.get("agent_results", {}).get("actuarial")
    if actuarial_result and actuarial_result.outcome == AgentOutcome.FAILED and actuarial_result.is_critical_failure:
        return NodeName.ERROR_HANDLER

    return NodeName.APPROVAL


def route_after_approval(state: EvaluationState) -> str:
    if state.get("is_fatal_error"):
        return NodeName.ERROR_HANDLER

    approval_result = state.get("agent_results", {}).get("approval")
    if not approval_result:
        return NodeName.ERROR_HANDLER

    decision = approval_result.payload.get("decision", "")
    if decision == "ESCALATED_TO_COMMITTEE":
        return NodeName.HUMAN_ESCALATION

    return NodeName.AUDIT


def route_after_escalation(state: EvaluationState) -> str:
    if state.get("is_fatal_error"):
        return NodeName.ERROR_HANDLER
    return NodeName.AUDIT
