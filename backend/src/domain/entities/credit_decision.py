"""
Entidad CreditDecision — resultado final inmutable del pipeline de evaluación.

Una decisión, una vez emitida, es INMUTABLE. Las correcciones se crean como
nuevas decisiones con referencia a la anterior (audit trail regulatorio).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from src.domain.value_objects.money import Money
from src.domain.value_objects.risk_score import RiskScore


class DecisionOutcome(str, Enum):
    APPROVED   = "APPROVED"
    REJECTED   = "REJECTED"
    MORE_DOCS  = "MORE_DOCS_REQUIRED"
    ESCALATED  = "ESCALATED_TO_COMMITTEE"

    @property
    def is_positive(self) -> bool:
        return self == self.APPROVED

    @property
    def is_negative(self) -> bool:
        return self == self.REJECTED

    @property
    def requires_human(self) -> bool:
        return self in (self.ESCALATED, self.MORE_DOCS)


@dataclass(frozen=True)
class CreditTerms:
    """Términos del crédito aprobado. Inmutable por diseño."""
    approved_amount: Money
    interest_rate_annual: float         # Porcentaje: 12.50 = 12.50%
    term_months: int
    monthly_installment: Money
    total_credit_cost: Money
    grace_period_days: int = 0
    early_payment_penalty: bool = False

    def __post_init__(self) -> None:
        if self.interest_rate_annual < 0:
            raise ValueError(f"Tasa de interés no puede ser negativa: {self.interest_rate_annual}")
        if self.term_months <= 0:
            raise ValueError(f"Plazo debe ser positivo: {self.term_months}")
        if self.approved_amount.amount <= 0:
            raise ValueError(f"Monto aprobado debe ser positivo: {self.approved_amount.amount}")

    @classmethod
    def compute(
        cls,
        approved_amount: Money,
        annual_rate: float,
        term_months: int,
        grace_period_days: int = 0,
        early_payment_penalty: bool = False,
    ) -> "CreditTerms":
        """Calcula los términos completos usando la fórmula francesa (cuota fija)."""
        monthly_rate = annual_rate / 100 / 12
        if monthly_rate > 0:
            installment_amount = (
                approved_amount.amount
                * monthly_rate
                / (1 - (1 + monthly_rate) ** (-term_months))
            )
        else:
            installment_amount = approved_amount.amount / max(term_months, 1)

        installment = Money(amount=round(installment_amount, 2), currency=approved_amount.currency)
        total_cost  = Money(
            amount=round(installment_amount * term_months, 2),
            currency=approved_amount.currency,
        )
        return cls(
            approved_amount=approved_amount,
            interest_rate_annual=annual_rate,
            term_months=term_months,
            monthly_installment=installment,
            total_credit_cost=total_cost,
            grace_period_days=grace_period_days,
            early_payment_penalty=early_payment_penalty,
        )


@dataclass(frozen=True)
class EscalationDetails:
    """Detalle del paquete de escalación para el comité."""
    committee_type: str
    priority: str
    escalation_reason: str
    key_concerns: list[str]
    recommended_review_date: Optional[datetime] = None
    supporting_metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionJustification:
    """
    Justificación regulatoria de la decisión (GDPR Art. 22, Basel III).
    Inmutable: forma parte del expediente regulatorio.
    """
    plain_language_explanation: str
    key_factors: dict[str, str]
    counterfactual: str
    regulatory_references: list[str]
    model_version: str
    model_documentation_url: str = ""

    def is_gdpr_compliant(self) -> bool:
        """Verifica que tenga los elementos mínimos para cumplir GDPR Art. 22."""
        return bool(
            self.plain_language_explanation
            and self.key_factors
            and self.counterfactual
            and any("GDPR" in ref for ref in self.regulatory_references)
        )


@dataclass(frozen=True)
class CreditDecision:
    """
    Resultado final de la evaluación crediticia. Inmutable por diseño.
    Es el output final del pipeline de agentes.
    """

    decision_id: uuid.UUID
    application_id: uuid.UUID
    outcome: DecisionOutcome
    risk_score: RiskScore
    confidence: float
    decided_at: datetime
    decided_by: str

    # Datos opcionales según el outcome
    credit_terms: Optional[CreditTerms] = None
    rejection_reasons: list[str] = field(default_factory=list)
    required_documents: list[str] = field(default_factory=list)
    escalation_details: Optional[EscalationDetails] = None
    justification: Optional[DecisionJustification] = None

    human_review_required: bool = False
    previous_decision_id: Optional[uuid.UUID] = None

    @classmethod
    def approve(
        cls,
        *,
        application_id: uuid.UUID,
        credit_terms: CreditTerms,
        risk_score: RiskScore,
        confidence: float,
        justification: DecisionJustification,
        decided_by: str,
        human_review_required: bool = False,
    ) -> "CreditDecision":
        return cls(
            decision_id=uuid.uuid4(),
            application_id=application_id,
            outcome=DecisionOutcome.APPROVED,
            risk_score=risk_score,
            confidence=confidence,
            decided_at=datetime.now(timezone.utc),
            decided_by=decided_by,
            credit_terms=credit_terms,
            justification=justification,
            human_review_required=human_review_required,
        )

    @classmethod
    def reject(
        cls,
        *,
        application_id: uuid.UUID,
        reasons: list[str],
        risk_score: RiskScore,
        confidence: float,
        justification: DecisionJustification,
        decided_by: str,
    ) -> "CreditDecision":
        if len(reasons) < 1:
            raise ValueError("Un rechazo requiere al menos una razón documentada.")
        return cls(
            decision_id=uuid.uuid4(),
            application_id=application_id,
            outcome=DecisionOutcome.REJECTED,
            risk_score=risk_score,
            confidence=confidence,
            decided_at=datetime.now(timezone.utc),
            decided_by=decided_by,
            rejection_reasons=reasons,
            justification=justification,
        )

    @classmethod
    def escalate(
        cls,
        *,
        application_id: uuid.UUID,
        escalation: EscalationDetails,
        risk_score: RiskScore,
        confidence: float,
        decided_by: str,
    ) -> "CreditDecision":
        return cls(
            decision_id=uuid.uuid4(),
            application_id=application_id,
            outcome=DecisionOutcome.ESCALATED,
            risk_score=risk_score,
            confidence=confidence,
            decided_at=datetime.now(timezone.utc),
            decided_by=decided_by,
            escalation_details=escalation,
            human_review_required=True,
        )

    @classmethod
    def request_docs(
        cls,
        *,
        application_id: uuid.UUID,
        required_documents: list[str],
        risk_score: RiskScore,
        confidence: float,
        decided_by: str,
    ) -> "CreditDecision":
        if not required_documents:
            raise ValueError("Se debe especificar qué documentos se requieren.")
        return cls(
            decision_id=uuid.uuid4(),
            application_id=application_id,
            outcome=DecisionOutcome.MORE_DOCS,
            risk_score=risk_score,
            confidence=confidence,
            decided_at=datetime.now(timezone.utc),
            decided_by=decided_by,
            required_documents=required_documents,
        )

    # ── Consultas de dominio ──────────────────────────────────────────────────

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= 0.85

    @property
    def is_regulatorily_documented(self) -> bool:
        return self.justification is not None and self.justification.is_gdpr_compliant()

    def effective_annual_cost(self) -> Optional[float]:
        """CAE — Costo Anual Efectivo. Requiere crédito aprobado."""
        if not self.credit_terms:
            return None
        total_paid = self.credit_terms.total_credit_cost.amount
        principal  = self.credit_terms.approved_amount.amount
        years      = self.credit_terms.term_months / 12
        if principal <= 0 or years <= 0:
            return None
        return ((total_paid / principal) ** (1 / years) - 1) * 100

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CreditDecision):
            return NotImplemented
        return self.decision_id == other.decision_id

    def __hash__(self) -> int:
        return hash(self.decision_id)
