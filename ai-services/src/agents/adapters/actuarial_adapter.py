"""
Adaptador: ActuarialDeepAgent → AgentPort
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from src.agents.adapters.fraud_adapter import _build_application_input, _build_security_context
from src.agents.adapters.base_adapter import BaseDeepAgentAdapter
from src.agents.deep.base_deep_agent import BaseDeepAgent
from src.agents.deep.actuarial_deep_agent import ActuarialDeepAgent
from src.contracts.agent_result import AgentOutcome, AgentResult
from src.core.state import CreditEvaluationState


class ActuarialDeepAgentAdapter(BaseDeepAgentAdapter):

    def _create_agent(self) -> BaseDeepAgent:
        return ActuarialDeepAgent()

    def _prepare_input(
        self,
        application_data: dict[str, Any],
        context: dict[str, Any],
    ) -> CreditEvaluationState:
        state = CreditEvaluationState(
            request_id=application_data.get("application_id", ""),
            application_input=_build_application_input(application_data),
            security_context=_build_security_context(application_data),
        )
        # Inyectar resultados previos para el análisis actuarial
        if "fraud_result" in context:
            from src.core.state import FraudAnalysisResult, AgentStatus
            fr = context["fraud_result"]
            state.fraud_result = FraudAnalysisResult(
                status=AgentStatus.SUCCESS,
                fraud_score=fr.get("fraud_score", 0.0),
                is_blocked=fr.get("is_blocked", False),
                fraud_flags=fr.get("fraud_flags", []),
            )
        if "credit_result" in context:
            from src.core.state import CreditHistoryResult, AgentStatus
            cr = context["credit_result"]
            state.credit_result = CreditHistoryResult(
                status=AgentStatus.SUCCESS,
                composite_credit_score=cr.get("composite_credit_score", 650),
                probability_of_default=cr.get("probability_of_default", 0.1),
                debt_to_income_ratio=cr.get("debt_to_income_ratio", 0.3),
            )
        return state

    async def execute(
        self,
        application_data: dict[str, Any],
        context: dict[str, Any],
    ) -> AgentResult:
        log = structlog.get_logger(__name__).bind(agent="ActuarialDeepAgent")
        start = time.monotonic()

        state = self._prepare_input(application_data, context)
        result_state = await self._agent.run(state)
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)

        actuarial_result = result_state.actuarial_result
        if actuarial_result is None:
            return AgentResult(
                agent_name="ActuarialDeepAgent",
                outcome=AgentOutcome.REQUIRES_REVIEW,
                confidence=0.5, quality_score=0.4, risk_contribution=0.5,
                payload={"error": "No actuarial result produced"},
                reasoning_chain=[], execution_time_ms=elapsed_ms,
            )

        pd = getattr(actuarial_result, "loss_given_default", 0.5)
        max_amount = getattr(actuarial_result, "maximum_approved_amount", 0)
        suggested_rate = getattr(actuarial_result, "suggested_interest_rate", 0.15)

        if pd >= 0.70:
            outcome = AgentOutcome.REJECTED
        elif pd >= 0.45:
            outcome = AgentOutcome.REQUIRES_REVIEW
        else:
            outcome = AgentOutcome.APPROVED

        payload = {
            "loss_given_default": pd,
            "maximum_approved_amount": max_amount,
            "suggested_interest_rate": suggested_rate,
            "risk_band": getattr(actuarial_result, "risk_band", ""),
            "risk_contribution": pd,
            "recommendation": getattr(actuarial_result, "recommendation", ""),
        }

        log.info("actuarial_adapter.completed", outcome=outcome.value, elapsed_ms=elapsed_ms)
        return AgentResult(
            agent_name="ActuarialDeepAgent",
            outcome=outcome,
            confidence=max(0.0, 1.0 - pd),
            quality_score=0.8 if not getattr(actuarial_result, "error", None) else 0.4,
            risk_contribution=pd,
            payload=payload,
            reasoning_chain=[],
            execution_time_ms=elapsed_ms,
        )
