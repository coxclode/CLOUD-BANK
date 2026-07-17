"""
Adaptadores DeepAgent → AgentPort

Patron Adapter (GoF): convierte la interfaz de los DeepAgents en la
interfaz AgentPort que espera el orquestador. Esto desacopla completamente
los agentes de la capa de orquestación.

Flujo:
  AgentPort.execute(application_data, context)
    → adapter._prepare_input(application_data)
    → DeepAgent.execute(agent_input)
    → adapter._map_result(deep_result) → AgentResult
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

import structlog

from src.contracts.agent_result import AgentPort, AgentResult, AgentOutcome
from src.agents.deep.base_deep_agent import BaseDeepAgent
from src.core.state import CreditEvaluationState

logger = structlog.get_logger(__name__)


class BaseDeepAgentAdapter(AgentPort, ABC):
    """
    Adaptador base. Cada agente concreto extiende esta clase y proporciona:
      - _create_agent() → instancia del DeepAgent
      - _prepare_input(application_data, context) → CreditEvaluationState
      - _map_outcome(deep_result) → AgentOutcome
    """

    def __init__(self) -> None:
        self._agent: BaseDeepAgent = self._create_agent()

    @abstractmethod
    def _create_agent(self) -> BaseDeepAgent:
        """Instanciar el DeepAgent concreto."""

    @abstractmethod
    def _prepare_input(
        self,
        application_data: dict[str, Any],
        context: dict[str, Any],
    ) -> CreditEvaluationState:
        """Mapear datos de la aplicación al formato del DeepAgent."""

    def _map_outcome(self, deep_result: dict[str, Any]) -> AgentOutcome:
        decision = str(deep_result.get("decision", "")).upper()
        mapping = {
            "APPROVE":   AgentOutcome.APPROVED,
            "APPROVED":  AgentOutcome.APPROVED,
            "REJECT":    AgentOutcome.REJECTED,
            "REJECTED":  AgentOutcome.REJECTED,
            "ESCALATE":  AgentOutcome.ESCALATED,
            "ESCALATED": AgentOutcome.ESCALATED,
            "REVIEW":    AgentOutcome.REQUIRES_REVIEW,
            "CLEAR":     AgentOutcome.APPROVED,
            "HIGH_RISK": AgentOutcome.REJECTED,
        }
        return mapping.get(decision, AgentOutcome.REQUIRES_REVIEW)

    async def execute(
        self,
        application_data: dict[str, Any],
        context: dict[str, Any],
    ) -> AgentResult:
        log = logger.bind(
            agent=self._agent.__class__.__name__,
            application_id=application_data.get("application_id"),
        )
        log.debug("adapter.executing")
        start = time.monotonic()

        agent_input = self._prepare_input(application_data, context)
        deep_result = await self._agent.execute(agent_input)

        elapsed = time.monotonic() - start
        log.info("adapter.completed", elapsed_ms=round(elapsed * 1000, 1))

        outcome = self._map_outcome(deep_result.final_output)

        return AgentResult(
            agent_name=self._agent.__class__.__name__,
            outcome=outcome,
            confidence=deep_result.confidence,
            quality_score=deep_result.quality_score,
            risk_contribution=deep_result.final_output.get("risk_contribution", 0.5),
            payload=deep_result.final_output,
            reasoning_chain=[
                layer.summary for layer in deep_result.reasoning_layers
            ],
            execution_time_ms=round(elapsed * 1000, 1),
        )
