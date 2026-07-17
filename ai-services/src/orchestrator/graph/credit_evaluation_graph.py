"""
Orquestador LangGraph — Credit Evaluation Graph

Este módulo es el director del pipeline de evaluación crediticia.
Orquesta los 4 Deep Agents en un grafo con estado tipado, checkpointing
para recuperación ante fallos, y routing condicional por resultado.

Flujo principal:
  INPUT → VALIDATE → SECURITY → FRAUD → CREDIT → ACTUARIAL → APPROVAL → AUDIT → END
                                  ↓         ↓          ↓           ↓
                               ERROR    ERROR      ERROR       ESCALATE
                                                              MORE_DOCS

Checkpointing: el estado se persiste en Redis después de cada nodo.
Si el pipeline se interrumpe, puede reanudarse desde el último checkpoint.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import structlog
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from src.orchestrator.state.evaluation_state import (
    EvaluationState,
    PipelineStatus,
    NodeName,
)
from src.orchestrator.nodes.input_validation_node import InputValidationNode
from src.orchestrator.nodes.agent_execution_node import (
    FraudExecutionNode,
    CreditExecutionNode,
    ActuarialExecutionNode,
    ApprovalExecutionNode,
)
from src.orchestrator.nodes.finalization_node import (
    AuditFinalizationNode,
    ErrorHandlerNode,
    HumanEscalationNode,
)
from src.orchestrator.edges.routing_logic import (
    route_after_validation,
    route_after_security,
    route_after_fraud,
    route_after_credit,
    route_after_actuarial,
    route_after_approval,
    route_after_escalation,
)
from src.contracts.agent_result import AgentResult, AgentOutcome

logger = structlog.get_logger(__name__)


class LangGraphOrchestrator:
    """
    Motor de evaluación crediticia de ai-services, implementado sobre LangGraph
    StateGraph. Expuesto vía POST /v1/pipeline/evaluate (src/api/main.py) —
    no conoce entidades de dominio del backend, solo dicts serializables.
    """

    def __init__(
        self,
        fraud_node: FraudExecutionNode,
        credit_node: CreditExecutionNode,
        actuarial_node: ActuarialExecutionNode,
        approval_node: ApprovalExecutionNode,
        validation_node: InputValidationNode,
        audit_node: AuditFinalizationNode,
        error_node: ErrorHandlerNode,
        escalation_node: HumanEscalationNode,
        use_memory_checkpointer: bool = True,
    ) -> None:
        self._fraud      = fraud_node
        self._credit     = credit_node
        self._actuarial  = actuarial_node
        self._approval   = approval_node
        self._validation = validation_node
        self._audit      = audit_node
        self._error      = error_node
        self._escalation = escalation_node
        self._graph      = self._build_graph(use_memory_checkpointer)

    def _build_graph(self, use_memory_checkpointer: bool) -> Any:
        """Construye y compila el StateGraph de LangGraph."""
        builder = StateGraph(EvaluationState)

        # ── Registrar nodos ───────────────────────────────────────────────────
        builder.add_node(NodeName.VALIDATE_INPUT, self._validation.execute)
        builder.add_node(NodeName.FRAUD_ANALYSIS, self._fraud.execute)
        builder.add_node(NodeName.CREDIT_HISTORY, self._credit.execute)
        builder.add_node(NodeName.ACTUARIAL, self._actuarial.execute)
        builder.add_node(NodeName.APPROVAL, self._approval.execute)
        builder.add_node(NodeName.AUDIT, self._audit.execute)
        builder.add_node(NodeName.ERROR_HANDLER, self._error.execute)
        builder.add_node(NodeName.HUMAN_ESCALATION, self._escalation.execute)

        # ── Punto de entrada ─────────────────────────────────────────────────
        builder.set_entry_point(NodeName.VALIDATE_INPUT)

        # ── Edges condicionales ───────────────────────────────────────────────
        builder.add_conditional_edges(
            NodeName.VALIDATE_INPUT,
            route_after_validation,
            {
                NodeName.FRAUD_ANALYSIS: NodeName.FRAUD_ANALYSIS,
                NodeName.ERROR_HANDLER:  NodeName.ERROR_HANDLER,
            },
        )
        builder.add_conditional_edges(
            NodeName.FRAUD_ANALYSIS,
            route_after_fraud,
            {
                NodeName.CREDIT_HISTORY: NodeName.CREDIT_HISTORY,
                NodeName.AUDIT:          NodeName.AUDIT,
                NodeName.ERROR_HANDLER:  NodeName.ERROR_HANDLER,
            },
        )
        builder.add_conditional_edges(
            NodeName.CREDIT_HISTORY,
            route_after_credit,
            {
                NodeName.ACTUARIAL:     NodeName.ACTUARIAL,
                NodeName.AUDIT:         NodeName.AUDIT,
                NodeName.ERROR_HANDLER: NodeName.ERROR_HANDLER,
            },
        )
        builder.add_conditional_edges(
            NodeName.ACTUARIAL,
            route_after_actuarial,
            {
                NodeName.APPROVAL:      NodeName.APPROVAL,
                NodeName.ERROR_HANDLER: NodeName.ERROR_HANDLER,
            },
        )
        builder.add_conditional_edges(
            NodeName.APPROVAL,
            route_after_approval,
            {
                NodeName.AUDIT:             NodeName.AUDIT,
                NodeName.HUMAN_ESCALATION:  NodeName.HUMAN_ESCALATION,
                NodeName.ERROR_HANDLER:     NodeName.ERROR_HANDLER,
            },
        )
        builder.add_conditional_edges(
            NodeName.HUMAN_ESCALATION,
            route_after_escalation,
            {
                NodeName.AUDIT:         NodeName.AUDIT,
                NodeName.ERROR_HANDLER: NodeName.ERROR_HANDLER,
            },
        )

        # ── Edges terminales ──────────────────────────────────────────────────
        builder.add_edge(NodeName.AUDIT, END)
        builder.add_edge(NodeName.ERROR_HANDLER, END)

        checkpointer = MemorySaver() if use_memory_checkpointer else None
        return builder.compile(checkpointer=checkpointer)

    async def run_pipeline(
        self,
        application_data: dict[str, Any],
        application_id: str,
        pipeline_id: str,
    ) -> dict:
        """
        Ejecuta el grafo completo y devuelve los resultados de todos los agentes.
        `application_data` ya viene serializado desde el backend (ver
        AiServicesOrchestratorAdapter._serialize_application).
        """
        start_time = time.monotonic()
        log = logger.bind(pipeline_id=pipeline_id, application_id=application_id)
        log.info("orchestrator.pipeline_starting")

        initial_state: EvaluationState = {
            "pipeline_id":    pipeline_id,
            "application_id": application_id,
            "application_data": application_data,
            "pipeline_status": PipelineStatus.RUNNING,
            "current_node":   NodeName.VALIDATE_INPUT,
            "errors":         [],
            "agent_results":  {},
            "fraud_score":    0.0,
            "aml_clear":      True,
            "post_credit_dti": 0.0,
            "default_probability": 0.5,
            "suggested_rate": 18.0,
            "fraud_flags":    [],
            "started_at":     start_time,
        }

        config = {"configurable": {"thread_id": pipeline_id}}

        try:
            final_state = await self._graph.ainvoke(initial_state, config=config)
            elapsed_ms = (time.monotonic() - start_time) * 1000
            log.info(
                "orchestrator.pipeline_completed",
                status=final_state.get("pipeline_status"),
                elapsed_ms=round(elapsed_ms, 2),
            )
            return self._extract_results(final_state)
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            log.error(
                "orchestrator.pipeline_failed",
                error=str(exc),
                elapsed_ms=round(elapsed_ms, 2),
            )
            return {
                "fraud_result": None, "credit_result": None,
                "actuarial_result": None, "approval_result": None,
                "fraud_score": 0.5, "aml_clear": False,
                "post_credit_dti": 0.0, "default_probability": 0.5,
                "suggested_rate": 18.0, "fraud_flags": [],
                "error": str(exc),
            }

    def _extract_results(self, final_state: dict) -> dict:
        agent_results: dict[str, AgentResult] = final_state.get("agent_results", {})
        return {
            "fraud_result":        agent_results.get("fraud"),
            "credit_result":       agent_results.get("credit"),
            "actuarial_result":    agent_results.get("actuarial"),
            "approval_result":     agent_results.get("approval"),
            "fraud_score":         final_state.get("fraud_score", 0.5),
            "aml_clear":           final_state.get("aml_clear", True),
            "post_credit_dti":     final_state.get("post_credit_dti", 0.0),
            "default_probability": final_state.get("default_probability", 0.5),
            "suggested_rate":      final_state.get("suggested_rate", 18.0),
            "fraud_flags":         final_state.get("fraud_flags", []),
        }
