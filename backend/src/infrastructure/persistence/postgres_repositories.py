"""
Adaptadores: PostgreSQL para CreditApplicationRepository y CreditDecisionRepository.

Usa asyncpg para máximo rendimiento asíncrono.
Serialización: los value objects se mapean a columnas escalares; el estado complejo
va en columnas JSONB para flexibilidad evolutiva sin migraciones frecuentes.

Schema esperado (simplificado):
  CREATE TABLE credit_applications (
      application_id    UUID PRIMARY KEY,
      applicant_id      UUID NOT NULL,
      national_id       TEXT NOT NULL,
      status            TEXT NOT NULL,
      requested_amount  NUMERIC NOT NULL,
      currency          TEXT NOT NULL,
      term_months       INT NOT NULL,
      purpose           TEXT NOT NULL,
      channel           TEXT NOT NULL,
      consent_given     BOOLEAN NOT NULL,
      correlation_id    TEXT,
      rejection_reasons JSONB DEFAULT '[]',
      reviewer_notes    TEXT DEFAULT '',
      risk_score_value  NUMERIC,
      risk_score_pd     NUMERIC,
      applicant_data    JSONB NOT NULL,
      created_at        TIMESTAMPTZ NOT NULL,
      updated_at        TIMESTAMPTZ NOT NULL
  );

  CREATE TABLE credit_decisions (
      decision_id         UUID PRIMARY KEY,
      application_id      UUID NOT NULL REFERENCES credit_applications(application_id),
      outcome             TEXT NOT NULL,
      confidence          NUMERIC NOT NULL,
      decided_at          TIMESTAMPTZ NOT NULL,
      decided_by          TEXT NOT NULL,
      risk_score_value    NUMERIC NOT NULL,
      default_probability NUMERIC NOT NULL,
      credit_terms        JSONB,
      rejection_reasons   JSONB DEFAULT '[]',
      required_documents  JSONB DEFAULT '[]',
      escalation_details  JSONB,
      justification       JSONB,
      human_review_required BOOLEAN DEFAULT FALSE,
      previous_decision_id  UUID
  );

  CREATE INDEX idx_applications_national_id ON credit_applications(national_id);
  CREATE INDEX idx_applications_status ON credit_applications(status);
  CREATE INDEX idx_decisions_application_id ON credit_decisions(application_id);
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

import asyncpg
import structlog

from src.domain.entities.credit_application import (
    ApplicationStatus,
    CreditApplication,
    CreditPurpose,
)
from src.domain.entities.credit_decision import (
    CreditDecision,
    CreditTerms,
    DecisionJustification,
    DecisionOutcome,
    EscalationDetails,
)
from src.domain.repositories.credit_repository import (
    CreditApplicationRepository,
    CreditDecisionRepository,
)
from src.domain.value_objects.applicant import Applicant, EmploymentType
from src.domain.value_objects.money import Money
from src.domain.value_objects.risk_score import RiskScore

logger = structlog.get_logger(__name__)


class PostgresCreditApplicationRepository(CreditApplicationRepository):

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, application: CreditApplication) -> None:
        async with self._pool.acquire() as conn:
            applicant_data = {
                "applicant_id": str(application.applicant.applicant_id),
                "full_name": application.applicant.full_name,
                "national_id": application.applicant.national_id,
                "birth_date": application.applicant.birth_date.isoformat(),
                "email": application.applicant.email,
                "phone": application.applicant.phone,
                "employment_type": application.applicant.employment_type.value,
                "gross_monthly_income": application.applicant.gross_monthly_income,
                "years_of_employment": application.applicant.years_of_employment,
                "country_code": application.applicant.country_code,
                "city": application.applicant.city,
            }
            await conn.execute(
                """
                INSERT INTO credit_applications (
                    application_id, applicant_id, national_id, status,
                    requested_amount, currency, term_months, purpose, channel,
                    consent_given, correlation_id, rejection_reasons,
                    reviewer_notes, risk_score_value, risk_score_pd,
                    applicant_data, created_at, updated_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
                ON CONFLICT (application_id) DO UPDATE SET
                    status            = EXCLUDED.status,
                    rejection_reasons = EXCLUDED.rejection_reasons,
                    reviewer_notes    = EXCLUDED.reviewer_notes,
                    risk_score_value  = EXCLUDED.risk_score_value,
                    risk_score_pd     = EXCLUDED.risk_score_pd,
                    updated_at        = EXCLUDED.updated_at
                """,
                application.application_id,
                application.applicant.applicant_id,
                application.applicant.national_id,
                application.status.value,
                application.requested_amount.amount,
                application.requested_amount.currency,
                application.term_months,
                application.purpose.value,
                application.channel,
                application.consent_given,
                application.correlation_id,
                json.dumps(application.rejection_reasons),
                application.reviewer_notes,
                application.risk_score.value if application.risk_score else None,
                application.risk_score.default_probability if application.risk_score else None,
                json.dumps(applicant_data),
                application.created_at,
                application.updated_at,
            )

    async def find_by_id(self, application_id: uuid.UUID) -> Optional[CreditApplication]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM credit_applications WHERE application_id = $1",
                application_id,
            )
        return self._row_to_entity(row) if row else None

    async def find_by_national_id(self, national_id: str) -> list[CreditApplication]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM credit_applications WHERE national_id = $1 ORDER BY created_at DESC",
                national_id,
            )
        return [self._row_to_entity(r) for r in rows]

    async def find_by_status(
        self, status: ApplicationStatus, limit: int = 100, offset: int = 0
    ) -> list[CreditApplication]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM credit_applications WHERE status=$1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
                status.value, limit, offset,
            )
        return [self._row_to_entity(r) for r in rows]

    async def count_active_applications(self, national_id: str) -> int:
        terminal = ["APPROVED", "REJECTED", "WITHDRAWN", "EXPIRED", "ERROR"]
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM credit_applications WHERE national_id=$1 AND status != ALL($2::text[])",
                national_id, terminal,
            )
        return count or 0

    async def exists(self, application_id: uuid.UUID) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM credit_applications WHERE application_id=$1", application_id
            )
        return row is not None

    async def find_submitted_before(self, before: datetime) -> list[CreditApplication]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM credit_applications WHERE status='SUBMITTED' AND created_at < $1",
                before,
            )
        return [self._row_to_entity(r) for r in rows]

    def _row_to_entity(self, row) -> CreditApplication:
        from datetime import date as d_
        ad = row["applicant_data"] if isinstance(row["applicant_data"], dict) else json.loads(row["applicant_data"])
        applicant = Applicant.create(
            full_name=ad["full_name"],
            national_id=ad["national_id"],
            birth_date=d_.fromisoformat(ad["birth_date"]),
            email=ad["email"],
            phone=ad["phone"],
            employment_type=EmploymentType(ad["employment_type"]),
            gross_monthly_income=float(ad["gross_monthly_income"]),
            years_of_employment=float(ad["years_of_employment"]),
            country_code=ad["country_code"],
            city=ad["city"],
        )
        app = object.__new__(CreditApplication)
        app.application_id    = row["application_id"]
        app.applicant         = applicant
        app.requested_amount  = Money(amount=float(row["requested_amount"]), currency=row["currency"])
        app.term_months       = row["term_months"]
        app.purpose           = CreditPurpose(row["purpose"])
        app.channel           = row["channel"]
        app.status            = ApplicationStatus(row["status"])
        app.consent_given     = row["consent_given"]
        app.created_at        = row["created_at"]
        app.updated_at        = row["updated_at"]
        app.correlation_id    = row["correlation_id"] or ""
        app.rejection_reasons = json.loads(row["rejection_reasons"] or "[]")
        app.reviewer_notes    = row["reviewer_notes"] or ""
        app.risk_score        = None
        app._events           = []
        return app


class PostgresCreditDecisionRepository(CreditDecisionRepository):

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, decision: CreditDecision) -> None:
        async with self._pool.acquire() as conn:
            credit_terms_json = None
            if decision.credit_terms:
                t = decision.credit_terms
                credit_terms_json = json.dumps({
                    "approved_amount": t.approved_amount.amount,
                    "currency": t.approved_amount.currency,
                    "interest_rate_annual": t.interest_rate_annual,
                    "term_months": t.term_months,
                    "monthly_installment": t.monthly_installment.amount,
                    "total_credit_cost": t.total_credit_cost.amount,
                    "grace_period_days": t.grace_period_days,
                    "early_payment_penalty": t.early_payment_penalty,
                })
            escalation_json = None
            if decision.escalation_details:
                e = decision.escalation_details
                escalation_json = json.dumps({
                    "committee_type": e.committee_type,
                    "priority": e.priority,
                    "escalation_reason": e.escalation_reason,
                    "key_concerns": e.key_concerns,
                })
            justif_json = None
            if decision.justification:
                j = decision.justification
                justif_json = json.dumps({
                    "plain_language_explanation": j.plain_language_explanation,
                    "key_factors": j.key_factors,
                    "counterfactual": j.counterfactual,
                    "regulatory_references": j.regulatory_references,
                    "model_version": j.model_version,
                })
            await conn.execute(
                """
                INSERT INTO credit_decisions (
                    decision_id, application_id, outcome, confidence,
                    decided_at, decided_by, risk_score_value, default_probability,
                    credit_terms, rejection_reasons, required_documents,
                    escalation_details, justification, human_review_required,
                    previous_decision_id
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                ON CONFLICT (decision_id) DO NOTHING
                """,
                decision.decision_id,
                decision.application_id,
                decision.outcome.value,
                decision.confidence,
                decision.decided_at,
                decision.decided_by,
                decision.risk_score.value,
                decision.risk_score.default_probability,
                credit_terms_json,
                json.dumps(decision.rejection_reasons),
                json.dumps(decision.required_documents),
                escalation_json,
                justif_json,
                decision.human_review_required,
                decision.previous_decision_id,
            )

    async def find_by_id(self, decision_id: uuid.UUID) -> Optional[CreditDecision]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM credit_decisions WHERE decision_id=$1", decision_id
            )
        return self._row_to_entity(row) if row else None

    async def find_by_application_id(self, application_id: uuid.UUID) -> Optional[CreditDecision]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM credit_decisions WHERE application_id=$1 ORDER BY decided_at DESC LIMIT 1",
                application_id,
            )
        return self._row_to_entity(row) if row else None

    async def find_all_by_application_id(self, application_id: uuid.UUID) -> list[CreditDecision]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM credit_decisions WHERE application_id=$1 ORDER BY decided_at ASC",
                application_id,
            )
        return [self._row_to_entity(r) for r in rows]

    async def find_escalated_pending_review(self, limit: int = 50) -> list[CreditDecision]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM credit_decisions WHERE outcome='ESCALATED_TO_COMMITTEE' "
                "AND human_review_required=TRUE ORDER BY decided_at ASC LIMIT $1",
                limit,
            )
        return [self._row_to_entity(r) for r in rows]

    def _row_to_entity(self, row) -> CreditDecision:
        rs = RiskScore.compose(
            fraud_score=0.0, credit_score=0.0, actuarial_score=0.0,
            default_probability=float(row["default_probability"]),
        )
        object.__setattr__(rs, "value", float(row["risk_score_value"]))

        ct = None
        if row["credit_terms"]:
            d = row["credit_terms"] if isinstance(row["credit_terms"], dict) else json.loads(row["credit_terms"])
            ct = CreditTerms.compute(
                approved_amount=Money(amount=float(d["approved_amount"]), currency=d["currency"]),
                annual_rate=float(d["interest_rate_annual"]),
                term_months=int(d["term_months"]),
                grace_period_days=int(d.get("grace_period_days", 0)),
                early_payment_penalty=bool(d.get("early_payment_penalty", False)),
            )

        esc = None
        if row["escalation_details"]:
            e = row["escalation_details"] if isinstance(row["escalation_details"], dict) else json.loads(row["escalation_details"])
            esc = EscalationDetails(
                committee_type=e["committee_type"],
                priority=e["priority"],
                escalation_reason=e["escalation_reason"],
                key_concerns=e.get("key_concerns", []),
            )

        justif = None
        if row["justification"]:
            j = row["justification"] if isinstance(row["justification"], dict) else json.loads(row["justification"])
            justif = DecisionJustification(
                plain_language_explanation=j["plain_language_explanation"],
                key_factors=j.get("key_factors", {}),
                counterfactual=j.get("counterfactual", ""),
                regulatory_references=j.get("regulatory_references", []),
                model_version=j.get("model_version", ""),
            )

        dec = object.__new__(CreditDecision)
        object.__setattr__(dec, "decision_id", row["decision_id"])
        object.__setattr__(dec, "application_id", row["application_id"])
        object.__setattr__(dec, "outcome", DecisionOutcome(row["outcome"]))
        object.__setattr__(dec, "risk_score", rs)
        object.__setattr__(dec, "confidence", float(row["confidence"]))
        object.__setattr__(dec, "decided_at", row["decided_at"])
        object.__setattr__(dec, "decided_by", row["decided_by"])
        object.__setattr__(dec, "credit_terms", ct)
        object.__setattr__(dec, "rejection_reasons", json.loads(row["rejection_reasons"] or "[]"))
        object.__setattr__(dec, "required_documents", json.loads(row["required_documents"] or "[]"))
        object.__setattr__(dec, "escalation_details", esc)
        object.__setattr__(dec, "justification", justif)
        object.__setattr__(dec, "human_review_required", bool(row["human_review_required"]))
        object.__setattr__(dec, "previous_decision_id", row.get("previous_decision_id"))
        return dec
