"""
Value Object: Applicant
Datos del solicitante. Inmutable. Valida coherencia interna.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from enum import Enum


class EmploymentType(str, Enum):
    EMPLOYED        = "EMPLOYED"
    SELF_EMPLOYED   = "SELF_EMPLOYED"
    FREELANCER      = "FREELANCER"
    RETIRED         = "RETIRED"
    STUDENT         = "STUDENT"
    UNEMPLOYED      = "UNEMPLOYED"
    BUSINESS_OWNER  = "BUSINESS_OWNER"
    PUBLIC_SECTOR   = "PUBLIC_SECTOR"
    INFORMAL        = "INFORMAL"
    HOMEMAKER       = "HOMEMAKER"


_EMAIL_RE    = re.compile(r"^[\w._%+\-]+@[\w.\-]+\.[a-zA-Z]{2,}$")
_PHONE_RE    = re.compile(r"^\+?[1-9]\d{6,14}$")
_NATIONAL_ID = re.compile(r"^[A-Za-z0-9\-\.]{5,20}$")


@dataclass(frozen=True)
class Applicant:
    """
    Solicitante del crédito.
    Los datos personales son inmutables una vez capturados para garantizar
    integridad del expediente regulatorio.
    """

    applicant_id: uuid.UUID
    full_name: str
    national_id: str
    birth_date: date
    email: str
    phone: str
    employment_type: EmploymentType
    gross_monthly_income: float
    years_of_employment: float
    country_code: str
    city: str

    def __post_init__(self) -> None:
        errors: list[str] = []

        if not self.full_name or len(self.full_name.strip()) < 3:
            errors.append("El nombre completo debe tener al menos 3 caracteres.")

        if not _NATIONAL_ID.match(self.national_id):
            errors.append(f"Formato de identificación inválido: '{self.national_id}'")

        if not _EMAIL_RE.match(self.email):
            errors.append(f"Correo electrónico inválido: '{self.email}'")

        if not _PHONE_RE.match(self.phone):
            errors.append(f"Teléfono inválido: '{self.phone}'")

        if self.gross_monthly_income < 0:
            errors.append(f"Ingreso mensual no puede ser negativo: {self.gross_monthly_income}")

        if self.years_of_employment < 0:
            errors.append(f"Años de empleo no pueden ser negativos: {self.years_of_employment}")

        if errors:
            raise ValueError(f"Applicant inválido: {'; '.join(errors)}")

    @classmethod
    def create(
        cls,
        *,
        full_name: str,
        national_id: str,
        birth_date: date,
        email: str,
        phone: str,
        employment_type: EmploymentType,
        gross_monthly_income: float,
        years_of_employment: float,
        country_code: str,
        city: str,
    ) -> "Applicant":
        return cls(
            applicant_id=uuid.uuid4(),
            full_name=full_name.strip(),
            national_id=national_id.strip().upper(),
            birth_date=birth_date,
            email=email.strip().lower(),
            phone=phone.strip(),
            employment_type=employment_type,
            gross_monthly_income=round(gross_monthly_income, 2),
            years_of_employment=round(years_of_employment, 1),
            country_code=country_code.upper(),
            city=city.strip(),
        )

    @property
    def age(self) -> int:
        today = date.today()
        return (
            today.year - self.birth_date.year
            - ((today.month, today.day) < (self.birth_date.month, self.birth_date.day))
        )

    @property
    def is_employed(self) -> bool:
        return self.employment_type in (
            EmploymentType.EMPLOYED,
            EmploymentType.SELF_EMPLOYED,
            EmploymentType.BUSINESS_OWNER,
            EmploymentType.FREELANCER,
            EmploymentType.PUBLIC_SECTOR,
            EmploymentType.INFORMAL,
        )

    @property
    def masked_email(self) -> str:
        parts = self.email.split("@")
        if len(parts) != 2:
            return "***@***"
        local, domain = parts
        return f"{local[:2]}***@{domain}"

    @property
    def masked_national_id(self) -> str:
        nid = self.national_id
        if len(nid) <= 4:
            return "****"
        return f"{'*' * (len(nid) - 4)}{nid[-4:]}"
