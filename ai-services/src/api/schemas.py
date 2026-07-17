"""
Esquemas HTTP de ai-services — el contrato de POST /v1/pipeline/evaluate.

Ver docs/SERVICE_CONTRACTS.md para la referencia completa consumida por
backend/src/infrastructure/ai_services/ai_services_client.py.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from src.contracts.agent_result import AgentResult


class PipelineEvaluateRequest(BaseModel):
    pipeline_id: str
    application_id: str
    application_data: dict[str, Any]


class AgentResultOut(BaseModel):
    agent_name: str
    outcome: str
    confidence: float
    quality_score: float
    risk_contribution: float
    payload: dict[str, Any]
    reasoning_chain: list[str] = Field(default_factory=list)
    execution_time_ms: float = 0.0
    human_review_required: bool = False
    error_message: Optional[str] = None

    @classmethod
    def from_agent_result(cls, result: AgentResult) -> "AgentResultOut":
        return cls(
            agent_name=result.agent_name,
            outcome=result.outcome.value,
            confidence=result.confidence,
            quality_score=result.quality_score,
            risk_contribution=result.risk_contribution,
            payload=result.payload,
            reasoning_chain=result.reasoning_chain,
            execution_time_ms=result.execution_time_ms,
            human_review_required=result.human_review_required,
            error_message=result.error_message,
        )


class PipelineEvaluateResponse(BaseModel):
    fraud_result: Optional[AgentResultOut] = None
    credit_result: Optional[AgentResultOut] = None
    actuarial_result: Optional[AgentResultOut] = None
    approval_result: Optional[AgentResultOut] = None
    fraud_score: float = 0.5
    aml_clear: bool = True
    post_credit_dti: float = 0.0
    default_probability: float = 0.5
    suggested_rate: float = 18.0
    fraud_flags: list[str] = Field(default_factory=list)
    error: Optional[str] = None

    @classmethod
    def from_pipeline_results(cls, results: dict[str, Any]) -> "PipelineEvaluateResponse":
        def _out(key: str) -> Optional[AgentResultOut]:
            result = results.get(key)
            return AgentResultOut.from_agent_result(result) if result else None

        return cls(
            fraud_result=_out("fraud_result"),
            credit_result=_out("credit_result"),
            actuarial_result=_out("actuarial_result"),
            approval_result=_out("approval_result"),
            fraud_score=results.get("fraud_score", 0.5),
            aml_clear=results.get("aml_clear", True),
            post_credit_dti=results.get("post_credit_dti", 0.0),
            default_probability=results.get("default_probability", 0.5),
            suggested_rate=results.get("suggested_rate", 18.0),
            fraud_flags=results.get("fraud_flags", []),
            error=results.get("error"),
        )
