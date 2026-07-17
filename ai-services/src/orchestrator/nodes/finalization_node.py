"""
Nodos de finalización del pipeline: auditoría, escalación, error handler.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from src.orchestrator.state.evaluation_state import (
    EvaluationState, NodeName, PipelineStatus,
)

logger = structlog.get_logger(__name__)


class AuditFinalizationNode:
    """Cierra el pipeline, calcula métricas totales y marca como completado."""

    async def execute(self, state: EvaluationState) -> dict[str, Any]:
        durations = state.get("node_durations") or {}
        total_ms  = sum(durations.values())
        approval  = state.get("agent_results", {}).get("approval")
        outcome   = approval.payload.get("decision", "UNKNOWN") if approval else "UNKNOWN"

        logger.info(
            "audit_finalization.completed",
            pipeline_id=state.get("pipeline_id"),
            outcome=outcome,
            total_ms=round(total_ms, 2),
            agent_count=len(state.get("agent_results", {})),
        )
        return {
            "current_node":    NodeName.AUDIT,
            "pipeline_status": PipelineStatus.COMPLETED,
        }


class HumanEscalationNode:
    """Encola la solicitud en el sistema de revisión humana."""

    async def execute(self, state: EvaluationState) -> dict[str, Any]:
        approval = state.get("agent_results", {}).get("approval")
        priority = "MEDIUM"
        if approval:
            priority = approval.payload.get("escalation_priority", "MEDIUM")

        logger.info(
            "human_escalation.enqueued",
            pipeline_id=state.get("pipeline_id"),
            application_id=state.get("application_id"),
            priority=priority,
        )
        return {
            "current_node":    NodeName.HUMAN_ESCALATION,
            "pipeline_status": PipelineStatus.ESCALATED,
        }


class ErrorHandlerNode:
    """Registra el error terminal y marca el pipeline como fallido."""

    async def execute(self, state: EvaluationState) -> dict[str, Any]:
        errors = state.get("errors", [])
        last   = errors[-1] if errors else None

        logger.error(
            "error_handler.terminal",
            pipeline_id=state.get("pipeline_id"),
            application_id=state.get("application_id"),
            total_errors=len(errors),
            last_error=last.message if last else "unknown",
        )
        return {
            "current_node":    NodeName.ERROR_HANDLER,
            "pipeline_status": PipelineStatus.FAILED,
        }
