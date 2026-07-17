"""
Adaptador HTTP hacia ai-services.

Implementa OrchestratorPort (application/use_cases/evaluate_credit_application.py)
llamando por REST al servicio ai-services en vez de ejecutar LangGraph in-process.
Es el único punto del backend que sabe que ai-services existe — el resto de la
aplicación solo conoce OrchestratorPort. Ver docs/SERVICE_CONTRACTS.md.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
import structlog

from src.application.ports.agent_port import AgentOutcome, AgentResult
from src.application.use_cases.evaluate_credit_application import OrchestratorPort
from src.domain.entities.credit_application import CreditApplication

logger = structlog.get_logger(__name__)


class AiServicesOrchestratorAdapter(OrchestratorPort):
    def __init__(self, http_client: httpx.AsyncClient, base_url: str) -> None:
        self._client = http_client
        self._base_url = base_url.rstrip("/")

    async def run_evaluation_pipeline(
        self,
        application: CreditApplication,
        pipeline_id: str,
    ) -> dict:
        application_id = str(application.application_id)
        log = logger.bind(pipeline_id=pipeline_id, application_id=application_id)

        try:
            response = await self._client.post(
                f"{self._base_url}/v1/pipeline/evaluate",
                json={
                    "pipeline_id": pipeline_id,
                    "application_id": application_id,
                    "application_data": self._serialize_application(application),
                },
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            log.error("ai_services_client.request_failed", error=str(exc))
            return self._degraded_result(str(exc))

        log.info("ai_services_client.pipeline_completed")
        return self._deserialize(data)

    def _serialize_application(self, application: CreditApplication) -> dict[str, Any]:
        return {
            "application_id":       str(application.application_id),
            "applicant_id":         str(application.applicant.applicant_id),
            "national_id":          application.applicant.national_id,
            "full_name":            application.applicant.full_name,
            "birth_date":           application.applicant.birth_date.isoformat(),
            "email":                application.applicant.masked_email,
            "phone":                application.applicant.phone,
            "employment_type":      application.applicant.employment_type.value,
            "gross_monthly_income": application.applicant.gross_monthly_income,
            "years_of_employment":  application.applicant.years_of_employment,
            "country_code":         application.applicant.country_code,
            "city":                 application.applicant.city,
            "requested_amount":     application.requested_amount.amount,
            "currency":             application.requested_amount.currency,
            "term_months":          application.term_months,
            "purpose":              application.purpose.value,
            "channel":              application.channel,
            "correlation_id":       application.correlation_id,
        }

    def _to_agent_result(self, raw: Optional[dict[str, Any]]) -> Optional[AgentResult]:
        if not raw:
            return None
        return AgentResult(
            agent_name=raw["agent_name"],
            outcome=AgentOutcome(raw["outcome"]),
            confidence=raw["confidence"],
            quality_score=raw["quality_score"],
            risk_contribution=raw["risk_contribution"],
            payload=raw.get("payload", {}),
            execution_time_ms=raw.get("execution_time_ms", 0.0),
            human_review_required=raw.get("human_review_required", False),
            error_message=raw.get("error_message"),
        )

    def _deserialize(self, data: dict[str, Any]) -> dict:
        return {
            "fraud_result":        self._to_agent_result(data.get("fraud_result")),
            "credit_result":       self._to_agent_result(data.get("credit_result")),
            "actuarial_result":    self._to_agent_result(data.get("actuarial_result")),
            "approval_result":     self._to_agent_result(data.get("approval_result")),
            "fraud_score":         data.get("fraud_score", 0.5),
            "aml_clear":           data.get("aml_clear", True),
            "post_credit_dti":     data.get("post_credit_dti", 0.0),
            "default_probability": data.get("default_probability", 0.5),
            "suggested_rate":      data.get("suggested_rate", 18.0),
            "fraud_flags":         data.get("fraud_flags", []),
        }

    def _degraded_result(self, error: str) -> dict:
        return {
            "fraud_result": None, "credit_result": None,
            "actuarial_result": None, "approval_result": None,
            "fraud_score": 0.5, "aml_clear": False,
            "post_credit_dti": 0.0, "default_probability": 0.5,
            "suggested_rate": 18.0, "fraud_flags": [],
            "error": error,
        }
