"""
Caso de Uso: RetrieveCreditDecision

Recupera el estado y la decisión de una solicitud de crédito.
Caso de uso de lectura — no modifica ningún estado.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from src.application.dto.credit_decision_dto import (
    CreditApplicationStatusDTO,
    CreditDecisionResponseDTO,
    CreditTermsResponseDTO,
    EscalationResponseDTO,
    JustificationResponseDTO,
)
from src.application.ports.repository_port import (
    CreditApplicationRepository,
    CreditDecisionRepository,
)

logger = structlog.get_logger(__name__)


class ApplicationNotFoundError(Exception):
    def __init__(self, application_id: str) -> None:
        super().__init__(f"Solicitud no encontrada: {application_id}")


class DecisionNotFoundError(Exception):
    def __init__(self, application_id: str) -> None:
        super().__init__(f"Aún no hay decisión para la solicitud: {application_id}")


class RetrieveCreditDecisionUseCase:
    """Recupera estado y decisión de una solicitud de crédito por su ID."""

    def __init__(
        self,
        application_repo: CreditApplicationRepository,
        decision_repo: CreditDecisionRepository,
    ) -> None:
        self._app_repo = application_repo
        self._dec_repo = decision_repo

    async def get_status(self, application_id: UUID) -> CreditApplicationStatusDTO:
        application = await self._app_repo.find_by_id(application_id)
        if not application:
            raise ApplicationNotFoundError(str(application_id))

        decision = await self._dec_repo.find_by_application_id(application_id)

        return CreditApplicationStatusDTO(
            application_id=str(application_id),
            status=application.status.value,
            created_at=application.created_at,
            updated_at=application.updated_at,
            has_decision=decision is not None,
            decision_outcome=decision.outcome.value if decision else None,
        )

    async def get_decision(self, application_id: UUID) -> CreditDecisionResponseDTO:
        application = await self._app_repo.find_by_id(application_id)
        if not application:
            raise ApplicationNotFoundError(str(application_id))

        decision = await self._dec_repo.find_by_application_id(application_id)
        if not decision:
            raise DecisionNotFoundError(str(application_id))

        credit_terms_dto = None
        if decision.credit_terms:
            t = decision.credit_terms
            credit_terms_dto = CreditTermsResponseDTO(
                approved_amount=t.approved_amount.amount,
                currency=t.approved_amount.currency,
                interest_rate_annual=t.interest_rate_annual,
                term_months=t.term_months,
                monthly_installment=t.monthly_installment.amount,
                total_credit_cost=t.total_credit_cost.amount,
                grace_period_days=t.grace_period_days,
                early_payment_penalty=t.early_payment_penalty,
            )

        esc_dto = None
        if decision.escalation_details:
            e = decision.escalation_details
            esc_dto = EscalationResponseDTO(
                committee_type=e.committee_type,
                priority=e.priority,
                escalation_reason=e.escalation_reason,
                key_concerns=e.key_concerns,
            )

        justif_dto = None
        if decision.justification:
            j = decision.justification
            justif_dto = JustificationResponseDTO(
                plain_language_explanation=j.plain_language_explanation,
                key_factors=j.key_factors,
                counterfactual=j.counterfactual,
                regulatory_references=j.regulatory_references,
            )

        return CreditDecisionResponseDTO(
            decision_id=str(decision.decision_id),
            application_id=str(decision.application_id),
            outcome=decision.outcome.value,
            confidence=decision.confidence,
            risk_band=decision.risk_score.band.value,
            risk_score=decision.risk_score.value,
            decided_at=decision.decided_at,
            decided_by=decision.decided_by,
            human_review_required=decision.human_review_required,
            credit_terms=credit_terms_dto,
            rejection_reasons=decision.rejection_reasons,
            required_documents=decision.required_documents,
            escalation=esc_dto,
            justification=justif_dto,
        )
