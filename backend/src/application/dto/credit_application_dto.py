"""
DTOs de entrada — CreditApplication.

Los DTOs son objetos de transferencia de datos entre capas.
Son mutables, validados con Pydantic, y nunca contienen lógica de negocio.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ApplicantDTO(BaseModel):
    full_name: str = Field(min_length=3, max_length=200)
    national_id: str = Field(min_length=5, max_length=20)
    birth_date: date
    email: str = Field(pattern=r"^[\w._%+\-]+@[\w.\-]+\.[a-zA-Z]{2,}$")
    phone: str = Field(pattern=r"^\+?[1-9]\d{6,14}$")
    employment_type: str
    gross_monthly_income: float = Field(ge=0, le=10_000_000)
    years_of_employment: float = Field(ge=0, le=50)
    country_code: str = Field(min_length=2, max_length=3)
    city: str = Field(min_length=2, max_length=100)

    @field_validator("employment_type")
    @classmethod
    def validate_employment_type(cls, v: str) -> str:
        allowed = {
            "EMPLOYED", "SELF_EMPLOYED", "FREELANCER", "RETIRED",
            "STUDENT", "UNEMPLOYED", "BUSINESS_OWNER",
            "PUBLIC_SECTOR", "INFORMAL", "HOMEMAKER",
        }
        if v.upper() not in allowed:
            raise ValueError(f"Tipo de empleo inválido: '{v}'. Permitidos: {allowed}")
        return v.upper()

    @field_validator("birth_date")
    @classmethod
    def validate_age(cls, v: date) -> date:
        from datetime import date as d_
        today = d_.today()
        age = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if age < 18:
            raise ValueError(f"El solicitante debe ser mayor de 18 años. Edad actual: {age}")
        if age > 85:
            raise ValueError(f"El solicitante debe ser menor de 85 años. Edad actual: {age}")
        return v


class CreditRequestDTO(BaseModel):
    requested_amount: float = Field(gt=0, le=500_000, description="Monto en la moneda base")
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    term_months: int = Field(ge=6, le=84, description="Plazo en meses")
    purpose: str
    channel: str = Field(default="DIGITAL", max_length=50)
    ip_address: Optional[str] = Field(default=None)
    device_fingerprint: Optional[str] = Field(default=None)

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, v: str) -> str:
        allowed = {
            "PERSONAL", "HOME_IMPROVEMENT", "DEBT_CONSOLIDATION", "MEDICAL",
            "EDUCATION", "VEHICLE", "BUSINESS", "TRAVEL", "OTHER",
        }
        if v.upper() not in allowed:
            raise ValueError(f"Propósito inválido: '{v}'. Permitidos: {allowed}")
        return v.upper()


class EvaluateCreditApplicationDTO(BaseModel):
    """DTO de entrada para el caso de uso EvaluateCreditApplication."""
    applicant: ApplicantDTO
    credit_request: CreditRequestDTO
    consent_given: bool = Field(description="Consentimiento explícito para tratamiento de datos")
    correlation_id: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_consent(self) -> "EvaluateCreditApplicationDTO":
        if not self.consent_given:
            raise ValueError(
                "El consentimiento para el tratamiento de datos es obligatorio "
                "(GDPR Art. 6 / Ley de Protección de Datos Personales)."
            )
        return self

    model_config = {"str_strip_whitespace": True, "str_to_upper": False}
