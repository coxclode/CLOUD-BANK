"""
Adaptador: ApprovalDeepAgent → AgentPort
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from src.agents.adapters.fraud_adapter import _build_application_input, _build_security_context
from src.agents.adapters.base_adapter import BaseDeepAgentAdapter
from src.agents.deep.base_deep_agent import BaseDeepAgent
from src.agents.deep.approval_deep_agent import ApprovalDeepAgent
from src.contracts.agent_result import AgentOutcome, AgentResult
from src.core.state import CreditEvaluationState


class ApprovalDeepAgentAdapter(BaseDeepAgentAdapter):

    def _create_agent(self) -> BaseDeepAgent:
        return ApprovalDeepAgent()

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
        # Inyectar todos los resultados previos — el agente aprobador los requiere
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
        if "credit_result" in context:
            from src.core.state import CreditHistoryResult, AgentStatus
            cr = context["credit_result"]
            state.credit_result = CreditHistoryResult(
                status=AgentStatus.SUCCESS,
                composite_credit_score=cr.get("composite_credit_score", 650),
                probability_of_default=cr.get("probability_of_default", 0.1),
                debt_to_income_ratio=cr.get("debt_to_income_ratio", 0.3),
            )
        if "actuarial_result" in context:
            from src.core.state import ActuarialResult, AgentStatus
            ar = context["actuarial_result"]
            state.actuarial_result = ActuarialResult(
                status=AgentStatus.SUCCESS,
                loss_given_default=ar.get("loss_given_default", 0.1),
                maximum_approved_amount=ar.get("maximum_approved_amount", 0),
                suggested_interest_rate=ar.get("suggested_interest_rate", 0.15),
                risk_band=ar.get("risk_band", "C"),
            )
        return state

    async def execute(
        self,
        application_data: dict[str, Any],
        context: dict[str, Any],
    ) -> AgentResult:
        log = structlog.get_logger(__name__).bind(agent="ApprovalDeepAgent")
        start = time.monotonic()

        state = self._prepare_input(application_data, context)
        result_state = await self._agent.run(state)
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)

        approval_result = result_state.approval_result
        if approval_result is None:
            return AgentResult(
                agent_name="ApprovalDeepAgent",
                outcome=AgentOutcome.REQUIRES_REVIEW,
                confidence=0.5, quality_score=0.4, risk_contribution=0.5,
                payload={"error": "No approval result produced"},
                reasoning_chain=[], execution_time_ms=elapsed_ms,
            )

        decision = str(getattr(approval_result, "decision", "")).upper()
        outcome_map = {
            "APPROVED":  AgentOutcome.APPROVED,
            "REJECTED":  AgentOutcome.REJECTED,
            "ESCALATED": AgentOutcome.ESCALATED,
        }
        outcome = outcome_map.get(decision, AgentOutcome.REQUIRES_REVIEW)

        payload = {
            "decision": decision,
            "decision_reason": getattr(approval_result, "decision_reason", ""),
            "approved_amount": getattr(approval_result, "approved_amount", None),
            "interest_rate": getattr(approval_result, "interest_rate", None),
            "term_months": getattr(approval_result, "term_months", None),
            "monthly_installment": getattr(approval_result, "monthly_installment", None),
            "rejection_reasons": getattr(approval_result, "rejection_reasons", []),
            "escalation_reason": getattr(approval_result, "escalation_reason", None),
            "gdpr_explanation": getattr(approval_result, "gdpr_explanation", ""),
            "conditions": getattr(approval_result, "conditions", []),
            "risk_contribution": 0.3,
        }

        log.info("approval_adapter.completed", outcome=outcome.value, elapsed_ms=elapsed_ms)
        return AgentResult(
            agent_name="ApprovalDeepAgent",
            outcome=outcome,
            confidence=getattr(approval_result, "confidence", 0.8),
            quality_score=getattr(approval_result, "quality_score", 0.8),
            risk_contribution=0.3,
            payload=payload,
            reasoning_chain=[],
            execution_time_ms=elapsed_ms,
        )
