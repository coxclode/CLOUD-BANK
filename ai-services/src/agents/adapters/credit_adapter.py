"""
Adaptador: CreditDeepAgent → AgentPort
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from src.agents.adapters.fraud_adapter import _build_application_input, _build_security_context
from src.agents.adapters.base_adapter import BaseDeepAgentAdapter
from src.agents.deep.base_deep_agent import BaseDeepAgent
from src.agents.deep.credit_deep_agent import CreditDeepAgent
from src.contracts.agent_result import AgentOutcome, AgentResult
from src.core.state import CreditEvaluationState


class CreditDeepAgentAdapter(BaseDeepAgentAdapter):

    def _create_agent(self) -> BaseDeepAgent:
        return CreditDeepAgent()

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
        # Inyectar resultado del agente de fraude si está disponible
        if "fraud_result" in context:
            from src.core.state import FraudAnalysisResult, AgentStatus
            fr = context["fraud_result"]
            state.fraud_result = FraudAnalysisResult(
                status=AgentStatus.SUCCESS,
                fraud_score=fr.get("fraud_score", 0.0),
                is_blocked=fr.get("is_blocked", False),
                fraud_flags=fr.get("fraud_flags", []),
                explanation=fr.get("explanation", ""),
            )
        return state

    async def execute(
        self,
        application_data: dict[str, Any],
        context: dict[str, Any],
    ) -> AgentResult:
        log = structlog.get_logger(__name__).bind(agent="CreditDeepAgent")
        start = time.monotonic()

        state = self._prepare_input(application_data, context)
        result_state = await self._agent.run(state)
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)

        credit_result = result_state.credit_result
        if credit_result is None:
            return AgentResult(
                agent_name="CreditDeepAgent",
                outcome=AgentOutcome.REQUIRES_REVIEW,
                confidence=0.5, quality_score=0.4, risk_contribution=0.5,
                payload={"error": "No credit result produced"},
                reasoning_chain=[], execution_time_ms=elapsed_ms,
            )

        score = credit_result.composite_credit_score if hasattr(credit_result, "composite_credit_score") else 0
        pd = credit_result.probability_of_default if hasattr(credit_result, "probability_of_default") else 0.5
        dti = credit_result.debt_to_income_ratio if hasattr(credit_result, "debt_to_income_ratio") else 0.5

        if pd >= 0.70 or dti > 0.50 or score < 500:
            outcome = AgentOutcome.REJECTED
        elif pd >= 0.45 or score < 620:
            outcome = AgentOutcome.REQUIRES_REVIEW
        else:
            outcome = AgentOutcome.APPROVED

        payload = {
            "composite_credit_score": score,
            "probability_of_default": pd,
            "debt_to_income_ratio": dti,
            "risk_contribution": pd,
            "recommendation": getattr(credit_result, "recommendation", ""),
        }

        log.info("credit_adapter.completed", outcome=outcome.value, elapsed_ms=elapsed_ms)
        return AgentResult(
            agent_name="CreditDeepAgent",
            outcome=outcome,
            confidence=max(0.0, 1.0 - pd),
            quality_score=0.8 if not getattr(credit_result, "error", None) else 0.4,
            risk_contribution=pd,
            payload=payload,
            reasoning_chain=[],
            execution_time_ms=elapsed_ms,
        )
