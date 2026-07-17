"""
Nodos de ejecución de agentes Deep.

Cada nodo envuelve un Deep Agent, lo ejecuta con manejo de errores,
actualiza el estado con el resultado, y extrae las métricas clave.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

import structlog

from src.contracts.agent_result import (
    AgentPort, AgentResult, AgentOutcome,
    FraudAgentPort, CreditAgentPort, ActuarialAgentPort, ApprovalAgentPort,
)
from src.orchestrator.state.evaluation_state import (
    EvaluationState, NodeName, PipelineError, PipelineStatus,
)

logger = structlog.get_logger(__name__)


class BaseAgentNode(ABC):
    """Nodo base que envuelve un agente Deep con retry y métricas."""

    MAX_RETRIES = 2
    TIMEOUT_SECONDS = 60.0

    @property
    @abstractmethod
    def agent_key(self) -> str:
        """Clave en el dict agent_results."""

    @property
    @abstractmethod
    def node_name(self) -> NodeName:
        """Nombre del nodo en el grafo."""

    @property
    @abstractmethod
    def agent(self) -> AgentPort:
        """Instancia del agente."""

    async def execute(self, state: EvaluationState) -> dict[str, Any]:
        start = time.monotonic()
        log = logger.bind(
            pipeline_id=state.get("pipeline_id"),
            agent=self.agent_key,
        )
        log.info("agent_node.starting")

        context = self._build_context(state)
        result: AgentResult | None = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                import asyncio
                result = await asyncio.wait_for(
                    self.agent.execute(state["application_data"], context),
                    timeout=self.TIMEOUT_SECONDS,
                )
                break
            except asyncio.TimeoutError:
                log.warning("agent_node.timeout", attempt=attempt)
                if attempt == self.MAX_RETRIES:
                    result = self._degraded_result("TIMEOUT: agente no respondió en tiempo")
            except Exception as exc:
                log.error("agent_node.execution_error", attempt=attempt, error=str(exc))
                if attempt == self.MAX_RETRIES:
                    result = self._degraded_result(str(exc))

        duration_ms = (time.monotonic() - start) * 1000
        log.info(
            "agent_node.completed",
            outcome=result.outcome.value if result else "UNKNOWN",
            confidence=result.confidence if result else 0.0,
            duration_ms=round(duration_ms, 2),
        )

        update = {
            "current_node": self.node_name,
            "agent_results": {**state.get("agent_results", {}), self.agent_key: result},
            "node_durations": {**(state.get("node_durations") or {}), self.agent_key: duration_ms},
        }
        update.update(self._extract_metrics(result, state))
        return update

    def _build_context(self, state: EvaluationState) -> dict:
        return {
            "pipeline_id":    state.get("pipeline_id"),
            "application_id": state.get("application_id"),
            "prior_results":  state.get("agent_results", {}),
        }

    def _degraded_result(self, error_msg: str) -> AgentResult:
        return AgentResult(
            agent_name=self.agent_key,
            outcome=AgentOutcome.FAILED,
            confidence=0.0,
            quality_score=0.0,
            risk_contribution=0.5,
            payload={},
            execution_time_ms=0.0,
            error_message=error_msg,
            human_review_required=True,
        )

    def _extract_metrics(self, result: AgentResult, state: EvaluationState) -> dict:
        return {}


class FraudExecutionNode(BaseAgentNode):
    agent_key = "fraud"
    node_name = NodeName.FRAUD_ANALYSIS

    def __init__(self, fraud_agent: FraudAgentPort) -> None:
        self._agent = fraud_agent

    @property
    def agent(self) -> AgentPort:
        return self._agent

    def _extract_metrics(self, result: AgentResult, state: EvaluationState) -> dict:
        if not result or not result.payload:
            return {"fraud_score": 0.5, "fraud_flags": []}
        return {
            "fraud_score": float(result.payload.get("fraud_score", 0.5)),
            "fraud_flags": list(result.payload.get("fraud_flags", [])),
        }


class CreditExecutionNode(BaseAgentNode):
    agent_key = "credit"
    node_name = NodeName.CREDIT_HISTORY

    def __init__(self, credit_agent: CreditAgentPort) -> None:
        self._agent = credit_agent

    @property
    def agent(self) -> AgentPort:
        return self._agent

    def _extract_metrics(self, result: AgentResult, state: EvaluationState) -> dict:
        if not result or not result.payload:
            return {"aml_clear": True, "post_credit_dti": 0.0}
        return {
            "aml_clear":       bool(result.payload.get("aml_clear", True)),
            "post_credit_dti": float(result.payload.get("post_credit_dti", 0.0)),
        }


class ActuarialExecutionNode(BaseAgentNode):
    agent_key = "actuarial"
    node_name = NodeName.ACTUARIAL

    def __init__(self, actuarial_agent: ActuarialAgentPort) -> None:
        self._agent = actuarial_agent

    @property
    def agent(self) -> AgentPort:
        return self._agent

    def _extract_metrics(self, result: AgentResult, state: EvaluationState) -> dict:
        if not result or not result.payload:
            return {"default_probability": 0.5, "suggested_rate": 18.0}
        return {
            "default_probability": float(result.payload.get("default_probability", 0.5)),
            "suggested_rate":      float(result.payload.get("interest_rate_suggestion", 18.0)),
        }


class ApprovalExecutionNode(BaseAgentNode):
    agent_key = "approval"
    node_name = NodeName.APPROVAL

    def __init__(self, approval_agent: ApprovalAgentPort) -> None:
        self._agent = approval_agent

    @property
    def agent(self) -> AgentPort:
        return self._agent

    def _extract_metrics(self, result: AgentResult, state: EvaluationState) -> dict:
        return {}
