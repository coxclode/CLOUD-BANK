"""
Factories de datos de prueba — CLOUD BANK

Usa factory_boy + Faker para generar datos realistas y reproducibles.
Centralizar la creación de datos evita inconsistencias entre tests.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import factory
from faker import Faker

fake = Faker("es_CO")
Faker.seed(42)


# ── DTOs de entrada ───────────────────────────────────────────────────────────

class CreditApplicationDTOFactory(factory.DictFactory):
    """Genera un payload válido para POST /v1/credit/evaluate."""

    application_id   = factory.LazyFunction(lambda: str(uuid.uuid4()))
    full_name        = factory.LazyFunction(fake.name)
    national_id      = factory.LazyFunction(lambda: fake.numerify("##########"))
    date_of_birth    = "1985-06-15"
    nationality      = "CO"
    email            = factory.LazyFunction(fake.email)
    phone            = factory.LazyFunction(lambda: fake.numerify("+57 ### #######"))
    address          = factory.LazyFunction(fake.address)
    city             = factory.LazyFunction(fake.city)
    country          = "CO"
    postal_code      = factory.LazyFunction(lambda: fake.numerify("#####"))
    requested_amount = 15_000.0
    term_months      = 36
    purpose          = "personal"
    monthly_income   = 4_500.0
    employment_type  = "employed"
    employer_name    = factory.LazyFunction(fake.company)
    employment_months = 48
    additional_income = 0.0
    monthly_obligations = 800.0
    document_references = factory.LazyFunction(lambda: [str(uuid.uuid4())])
    channel          = "api"
    ip_address       = factory.LazyFunction(fake.ipv4)
    user_agent       = "TestClient/1.0"
    consent_given    = True
    principal_id     = "test-officer-001"


class HighRiskApplicationFactory(CreditApplicationDTOFactory):
    """Solicitud que debería ser rechazada por riesgo alto."""
    requested_amount    = 200_000.0
    monthly_income      = 2_000.0
    monthly_obligations = 1_800.0
    employment_months   = 3


class FraudulentApplicationFactory(CreditApplicationDTOFactory):
    """Solicitud que debería activar el circuit breaker de fraude."""
    ip_address = "192.168.1.1"


# ── Entidades de dominio ──────────────────────────────────────────────────────

def make_credit_application(
    applicant_id: str | None = None,
    amount: float = 15_000.0,
    term_months: int = 36,
) -> dict:
    """Datos mínimos para crear un CreditApplication entity."""
    return {
        "id": str(uuid.uuid4()),
        "applicant_id": applicant_id or str(uuid.uuid4()),
        "requested_amount": Decimal(str(amount)),
        "term_months": term_months,
        "purpose": "personal",
        "monthly_income": Decimal("4500.00"),
        "monthly_obligations": Decimal("800.00"),
        "status": "DRAFT",
        "created_at": datetime.now(timezone.utc),
    }


def make_agent_result(
    agent_name: str = "FraudDeepAgent",
    outcome: str = "APPROVED",
    fraud_score: float = 0.1,
    pd: float = 0.1,
) -> dict:
    """Resultado genérico de un agente para usar en mocks."""
    return {
        "agent_name": agent_name,
        "outcome": outcome,
        "confidence": 0.9,
        "quality_score": 0.85,
        "risk_contribution": fraud_score if "Fraud" in agent_name else pd,
        "payload": {
            "fraud_score": fraud_score,
            "probability_of_default": pd,
            "risk_contribution": fraud_score,
        },
        "reasoning_chain": [],
        "execution_time_ms": random.uniform(100, 300),
    }
