"""
DTOs de salida — CreditDecision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CreditTermsResponseDTO(BaseModel):
    approved_amount: float
    currency: str
    interest_rate_annual: float
    term_months: int
    monthly_installment: float
    total_credit_cost: float
    grace_period_days: int
    early_payment_penalty: bool


class EscalationResponseDTO(BaseModel):
    committee_type: str
    priority: str
    escalation_reason: str
    key_concerns: list[str]


class JustificationResponseDTO(BaseModel):
    plain_language_explanation: str
    key_factors: dict[str, str]
    counterfactual: str
    regulatory_references: list[str]


class CreditDecisionResponseDTO(BaseModel):
    """DTO de salida para el caso de uso EvaluateCreditApplication."""
    decision_id: str
    application_id: str
    outcome: str
    confidence: float = Field(ge=0.0, le=1.0)
    risk_band: str
    risk_score: float
    decided_at: datetime
    decided_by: str
    human_review_required: bool

    credit_terms: Optional[CreditTermsResponseDTO] = None
    rejection_reasons: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    escalation: Optional[EscalationResponseDTO] = None
    justification: Optional[JustificationResponseDTO] = None

    processing_time_ms: float = 0.0
    pipeline_id: str = ""


class CreditApplicationStatusDTO(BaseModel):
    """DTO para consultar el estado de una solicitud."""
    application_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    has_decision: bool
    decision_outcome: Optional[str] = None
