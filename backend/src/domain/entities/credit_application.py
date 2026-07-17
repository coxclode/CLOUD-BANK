"""
Entidad raíz del agregado CreditApplication.

Invariantes del dominio:
  - El monto solicitado debe ser positivo y dentro del rango permitido por política.
  - El plazo en meses debe estar entre 6 y 84.
  - El solicitante debe ser mayor de 18 años y menor de 85.
  - El consentimiento de tratamiento de datos es obligatorio.
  - Una solicitud aprobada no puede volver a estado pendiente.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from src.domain.events.credit_events import (
    CreditApplicationCreated,
    CreditApplicationSubmitted,
    CreditApplicationWithdrawn,
    DomainEvent,
)
from src.domain.value_objects.applicant import Applicant
from src.domain.value_objects.money import Money
from src.domain.value_objects.risk_score import RiskScore


class ApplicationStatus(str, Enum):
    DRAFT      = "DRAFT"
    SUBMITTED  = "SUBMITTED"
    IN_REVIEW  = "IN_REVIEW"
    APPROVED   = "APPROVED"
    REJECTED   = "REJECTED"
    MORE_DOCS  = "MORE_DOCS_REQUIRED"
    ESCALATED  = "ESCALATED_TO_COMMITTEE"
    WITHDRAWN  = "WITHDRAWN"
    EXPIRED    = "EXPIRED"
    ERROR      = "ERROR"

    @property
    def is_terminal(self) -> bool:
        return self in (
            self.APPROVED, self.REJECTED, self.WITHDRAWN,
            self.EXPIRED, self.ERROR,
        )

    @property
    def allows_review(self) -> bool:
        return self in (self.SUBMITTED, self.IN_REVIEW, self.MORE_DOCS, self.ESCALATED)


class CreditPurpose(str, Enum):
    PERSONAL          = "PERSONAL"
    HOME_IMPROVEMENT  = "HOME_IMPROVEMENT"
    DEBT_CONSOLIDATION = "DEBT_CONSOLIDATION"
    MEDICAL           = "MEDICAL"
    EDUCATION         = "EDUCATION"
    VEHICLE           = "VEHICLE"
    BUSINESS          = "BUSINESS"
    TRAVEL            = "TRAVEL"
    OTHER             = "OTHER"


class CreditApplicationError(Exception):
    """Error de invariante del dominio en CreditApplication."""


@dataclass
class CreditApplication:
    """
    Agregado raíz. Encapsula toda la lógica de una solicitud de crédito.

    El ID es un UUID inmutable asignado en creación.
    Los eventos de dominio se acumulan y se publican cuando la capa de
    aplicación persiste el agregado.
    """

    application_id: uuid.UUID
    applicant: Applicant
    requested_amount: Money
    term_months: int
    purpose: CreditPurpose
    channel: str
    status: ApplicationStatus
    consent_given: bool
    created_at: datetime
    updated_at: datetime
    correlation_id: str
    risk_score: Optional[RiskScore] = None
    rejection_reasons: list[str] = field(default_factory=list)
    reviewer_notes: str = ""
    _events: list[DomainEvent] = field(default_factory=list, repr=False)

    # ── Fábrica ──────────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        *,
        applicant: Applicant,
        requested_amount: Money,
        term_months: int,
        purpose: CreditPurpose,
        channel: str,
        consent_given: bool,
        correlation_id: str = "",
    ) -> "CreditApplication":
        """
        Único punto de entrada para crear una solicitud.
        Valida todas las invariantes del dominio antes de crear el objeto.
        """
        application_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        app = cls(
            application_id=application_id,
            applicant=applicant,
            requested_amount=requested_amount,
            term_months=term_months,
            purpose=purpose,
            channel=channel,
            status=ApplicationStatus.DRAFT,
            consent_given=consent_given,
            created_at=now,
            updated_at=now,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
        app._validate_invariants()
        app._events.append(CreditApplicationCreated(
            application_id=str(application_id),
            applicant_id=str(applicant.applicant_id),
            requested_amount=requested_amount.amount,
            currency=requested_amount.currency,
            channel=channel,
            occurred_at=now,
        ))
        return app

    # ── Comandos de dominio ───────────────────────────────────────────────────

    def submit(self) -> None:
        """Transición DRAFT → SUBMITTED. Valida consentimiento."""
        self._require_status(ApplicationStatus.DRAFT)
        if not self.consent_given:
            raise CreditApplicationError(
                "El consentimiento de tratamiento de datos es obligatorio para enviar la solicitud."
            )
        self._transition_to(ApplicationStatus.SUBMITTED)
        self._events.append(CreditApplicationSubmitted(
            application_id=str(self.application_id),
            occurred_at=self.updated_at,
        ))

    def start_review(self) -> None:
        """Transición SUBMITTED | MORE_DOCS | ESCALATED → IN_REVIEW."""
        self._require_status(
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.MORE_DOCS,
            ApplicationStatus.ESCALATED,
        )
        self._transition_to(ApplicationStatus.IN_REVIEW)

    def approve(self, risk_score: RiskScore, notes: str = "") -> None:
        """Transición IN_REVIEW → APPROVED. Solo si el riesgo lo permite."""
        self._require_status(ApplicationStatus.IN_REVIEW)
        if risk_score.is_unacceptable:
            raise CreditApplicationError(
                f"No se puede aprobar con risk_score={risk_score.value:.4f} "
                f"(umbral máximo: {risk_score.max_acceptable})"
            )
        self.risk_score = risk_score
        self.reviewer_notes = notes
        self._transition_to(ApplicationStatus.APPROVED)

    def reject(self, reasons: list[str], risk_score: Optional[RiskScore] = None) -> None:
        """Transición IN_REVIEW → REJECTED. Requiere al menos una razón."""
        self._require_status(ApplicationStatus.IN_REVIEW, ApplicationStatus.SUBMITTED)
        if not reasons:
            raise CreditApplicationError("El rechazo requiere al menos una razón documentada.")
        self.rejection_reasons = reasons
        if risk_score:
            self.risk_score = risk_score
        self._transition_to(ApplicationStatus.REJECTED)

    def request_more_docs(self, required_docs: list[str]) -> None:
        """Transición IN_REVIEW → MORE_DOCS_REQUIRED."""
        self._require_status(ApplicationStatus.IN_REVIEW)
        if not required_docs:
            raise CreditApplicationError("Se debe especificar qué documentos se requieren.")
        self.reviewer_notes = f"Documentos requeridos: {', '.join(required_docs)}"
        self._transition_to(ApplicationStatus.MORE_DOCS)

    def escalate(self, reason: str, committee_type: str) -> None:
        """Transición IN_REVIEW → ESCALATED_TO_COMMITTEE."""
        self._require_status(ApplicationStatus.IN_REVIEW)
        if not reason:
            raise CreditApplicationError("La escalación requiere una razón documentada.")
        self.reviewer_notes = f"Comité: {committee_type}. Razón: {reason}"
        self._transition_to(ApplicationStatus.ESCALATED)

    def withdraw(self, reason: str = "") -> None:
        """El solicitante retira la solicitud. Solo si no está en estado terminal."""
        if self.status.is_terminal:
            raise CreditApplicationError(
                f"No se puede retirar una solicitud en estado terminal: {self.status.value}"
            )
        self.reviewer_notes = reason
        self._transition_to(ApplicationStatus.WITHDRAWN)
        self._events.append(CreditApplicationWithdrawn(
            application_id=str(self.application_id),
            reason=reason,
            occurred_at=self.updated_at,
        ))

    # ── Consultas de dominio ──────────────────────────────────────────────────

    @property
    def monthly_installment_estimate(self) -> Money:
        """Estimación de cuota mensual a tasa del 0% (para validación de capacidad básica)."""
        if self.term_months <= 0:
            return Money(amount=0.0, currency=self.requested_amount.currency)
        return Money(
            amount=self.requested_amount.amount / self.term_months,
            currency=self.requested_amount.currency,
        )

    @property
    def age_at_maturity(self) -> int:
        """Edad del solicitante al vencimiento del crédito."""
        maturity_year = date.today().year + (self.term_months // 12)
        return maturity_year - self.applicant.birth_date.year

    @property
    def pending_domain_events(self) -> list[DomainEvent]:
        return list(self._events)

    def collect_events(self) -> list[DomainEvent]:
        """Vacía y devuelve los eventos acumulados (llamar después de persistir)."""
        events = list(self._events)
        self._events.clear()
        return events

    # ── Privados ──────────────────────────────────────────────────────────────

    def _validate_invariants(self) -> None:
        errors: list[str] = []

        if not self.consent_given:
            errors.append("Consentimiento de datos obligatorio.")

        if self.requested_amount.amount <= 0:
            errors.append(f"Monto solicitado debe ser positivo: {self.requested_amount.amount}")

        if self.requested_amount.amount > 500_000:
            errors.append(f"Monto excede el límite máximo de 500.000: {self.requested_amount.amount}")

        if not (6 <= self.term_months <= 84):
            errors.append(f"Plazo debe estar entre 6 y 84 meses: {self.term_months}")

        age = self.applicant.age
        if age < 18:
            errors.append(f"El solicitante debe ser mayor de 18 años: {age}")
        if age > 85:
            errors.append(f"El solicitante debe ser menor de 85 años: {age}")

        if self.age_at_maturity > 90:
            errors.append(
                f"La edad al vencimiento ({self.age_at_maturity}) excede el límite de 90 años."
            )

        if errors:
            raise CreditApplicationError(
                f"Solicitud inválida — {len(errors)} error(es): {'; '.join(errors)}"
            )

    def _transition_to(self, new_status: ApplicationStatus) -> None:
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)

    def _require_status(self, *allowed: ApplicationStatus) -> None:
        if self.status not in allowed:
            raise CreditApplicationError(
                f"Operación no permitida en estado '{self.status.value}'. "
                f"Estados permitidos: {[s.value for s in allowed]}"
            )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CreditApplication):
            return NotImplemented
        return self.application_id == other.application_id

    def __hash__(self) -> int:
        return hash(self.application_id)
