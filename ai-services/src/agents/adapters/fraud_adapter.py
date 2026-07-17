"""
Adaptador: FraudDeepAgent → AgentPort

Convierte el contrato de la infraestructura (application_data dict)
en el contrato del dominio legacy (CreditEvaluationState) y viceversa.
"""

from __future__ import annotations

from typing import Any

from src.agents.adapters.base_adapter import BaseDeepAgentAdapter
from src.agents.deep.base_deep_agent import BaseDeepAgent
from src.agents.deep.fraud_deep_agent import FraudDeepAgent
from src.contracts.agent_result import AgentOutcome, AgentResult
from src.core.state import (
    ApplicationInput,
    ApplicantIdentity,
    ApplicantContact,
    CreditRequest,
    CreditEvaluationState,
    SecurityContext,
)


class FraudDeepAgentAdapter(BaseDeepAgentAdapter):

    def _create_agent(self) -> BaseDeepAgent:
        return FraudDeepAgent()

    def _prepare_input(
        self,
        application_data: dict[str, Any],
        context: dict[str, Any],
    ) -> CreditEvaluationState:
        app_input = _build_application_input(application_data)
        sec_ctx = _build_security_context(application_data)
        return CreditEvaluationState(
            request_id=application_data.get("application_id", ""),
            application_input=app_input,
            security_context=sec_ctx,
        )

    async def execute(
        self,
        application_data: dict[str, Any],
        context: dict[str, Any],
    ) -> AgentResult:
        import time
        import structlog
        log = structlog.get_logger(__name__).bind(agent="FraudDeepAgent")
        start = time.monotonic()

        state = self._prepare_input(application_data, context)
        result_state = await self._agent.run(state)
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)

        fraud_result = result_state.fraud_result
        if fraud_result is None:
            outcome = AgentOutcome.REQUIRES_REVIEW
            confidence = 0.5
            quality = 0.5
            risk = 0.5
            payload: dict[str, Any] = {"error": "No fraud result produced"}
        else:
            if fraud_result.is_blocked or fraud_result.fraud_score >= 0.85:
                outcome = AgentOutcome.REJECTED
            elif fraud_result.fraud_score >= 0.65:
                outcome = AgentOutcome.REQUIRES_REVIEW
            else:
                outcome = AgentOutcome.APPROVED
            confidence = 1.0 - fraud_result.fraud_score
            quality = 0.8 if not fraud_result.error else 0.4
            risk = fraud_result.fraud_score
            payload = {
                "fraud_score": fraud_result.fraud_score,
                "risk_level": fraud_result.risk_level.value,
                "is_blocked": fraud_result.is_blocked,
                "fraud_flags": fraud_result.fraud_flags,
                "explanation": fraud_result.explanation,
                "recommendation": fraud_result.recommendation,
                "contributing_factors": fraud_result.contributing_factors,
                "risk_contribution": fraud_result.fraud_score,
            }

        log.info("fraud_adapter.completed", outcome=outcome.value, elapsed_ms=elapsed_ms)
        return AgentResult(
            agent_name="FraudDeepAgent",
            outcome=outcome,
            confidence=confidence,
            quality_score=quality,
            risk_contribution=risk,
            payload=payload,
            reasoning_chain=[],
            execution_time_ms=elapsed_ms,
        )


def _build_application_input(data: dict[str, Any]) -> ApplicationInput:
    # El backend (src/infrastructure/ai_services/ai_services_client.py
    # _serialize_application) usa estos nombres de campo — no coinciden 1:1
    # con el contrato interno de ai-services (ApplicantIdentity/ApplicantContact).
    identity = ApplicantIdentity(
        full_name=data.get("full_name", ""),
        national_id=data.get("national_id", ""),
        id_type=data.get("id_type", "DNI"),
        date_of_birth=data.get("birth_date", data.get("date_of_birth", "1990-01-01")),
        nationality=data.get("nationality", data.get("country_code", "")),
        tax_id=data.get("tax_id"),
    )
    contact = ApplicantContact(
        email=data.get("email", ""),
        phone=data.get("phone", ""),
        address=data.get("address", ""),
        city=data.get("city", ""),
        country=data.get("country_code", data.get("country", "")),
        postal_code=data.get("postal_code", ""),
    )
    credit_req = CreditRequest(
        requested_amount=float(data.get("requested_amount", 0)),
        term_months=int(data.get("term_months", 12)),
        purpose=data.get("purpose", "personal"),
    )

    years_of_employment = data.get("years_of_employment")
    employment_months = (
        int(round(float(years_of_employment) * 12))
        if years_of_employment is not None
        else int(data.get("employment_months", 0))
    )

    return ApplicationInput(
        identity=identity,
        contact=contact,
        credit_request=credit_req,
        monthly_income=float(data.get("gross_monthly_income", data.get("monthly_income", 0))),
        employment_type=data.get("employment_type", "employed"),
        employer_name=data.get("employer_name"),
        employment_months=employment_months,
        additional_income=float(data.get("additional_income", 0.0)),
        monthly_obligations=float(data.get("monthly_obligations", 0.0)),
        document_references=data.get("document_references", []),
        biometric_token=data.get("biometric_token"),
        device_fingerprint=data.get("device_fingerprint"),
        channel=data.get("channel", "api"),
        ip_address=data.get("ip_address", ""),
        user_agent=data.get("user_agent", ""),
        consent_given=data.get("consent_given", True),
    )


def _build_security_context(data: dict[str, Any]) -> SecurityContext:
    return SecurityContext(
        authenticated=True,
        principal_id=data.get("principal_id", "system"),
        channel=data.get("channel", "api"),
        ip_address=data.get("ip_address", ""),
        device_fingerprint=data.get("device_fingerprint", ""),
        user_agent=data.get("user_agent", ""),
    )
