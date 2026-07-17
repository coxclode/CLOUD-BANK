"""
CLOUD BANK — Validador de Entradas
Primera línea de defensa. Valida y normaliza todo input antes de ingresar al grafo.
Aplica Zero Trust: ningún dato de entrada se considera confiable.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import datetime, timezone
from typing import Any

import structlog
import validators

from src.core.exceptions import InputValidationError
from src.core.state import ApplicationInput, SecurityContext
from src.security.prompt_guard import PromptGuard

logger = structlog.get_logger(__name__)

_guard = PromptGuard(strict_mode=True)


# ─── Reglas de validación ────────────────────────────────────────────────────

_NAME_PATTERN       = re.compile(r"^[\w\s\-\'\.]{2,100}$", re.UNICODE)
_NATIONAL_ID_PATTERN = re.compile(r"^[A-Z0-9\-]{5,20}$")
_PHONE_PATTERN      = re.compile(r"^\+?[0-9\s\-\(\)]{7,20}$")
_POSTAL_PATTERN     = re.compile(r"^[A-Z0-9\-\s]{3,12}$", re.IGNORECASE)
_EMPLOYER_PATTERN   = re.compile(r"^[\w\s\-\.\,\&]{2,150}$", re.UNICODE)

_ALLOWED_EMPLOYMENT_TYPES = {
    "employed_full_time", "employed_part_time", "self_employed",
    "contractor", "civil_servant", "retired", "student", "unemployed",
}

_ALLOWED_CHANNELS = {"web", "mobile", "api", "branch", "atm"}


class InputValidator:

    def validate_application(self, raw: dict[str, Any]) -> ApplicationInput:
        """
        Valida, sanitiza y construye el objeto ApplicationInput.
        Lanza InputValidationError ante cualquier inconsistencia.
        """
        log = logger.bind(validator="input_validator")

        # 1. Validar estructura raíz
        self._require_keys(raw, {"identity", "contact", "credit_request"}, "root")

        # 2. Validar consentimiento antes de cualquier procesamiento de PII
        if not raw.get("consent_given"):
            raise InputValidationError("Consentimiento explícito requerido", field="consent_given")

        # 3. Validar identidad
        identity = self._validate_identity(raw["identity"])

        # 4. Validar contacto
        contact = self._validate_contact(raw["contact"])

        # 5. Validar solicitud de crédito
        credit = self._validate_credit_request(raw["credit_request"])

        # 6. Validar financials
        financial = self._validate_financials(raw)

        # 7. Validar channel e IP
        channel, ip_address, user_agent = self._validate_channel_info(raw)

        log.info("input_validator.success", national_id_hash=self._mask_id(identity["national_id"]))

        return ApplicationInput(
            identity=identity,
            contact=contact,
            credit_request=credit,
            channel=channel,
            ip_address=ip_address,
            user_agent=user_agent,
            **financial,
            consent_given=True,
        )

    def validate_security_context(self, raw: dict[str, Any]) -> SecurityContext:
        """Construye el SecurityContext desde los headers y metadata de la petición."""
        ip_str = raw.get("ip_address", "")
        is_vpn = bool(raw.get("is_vpn", False))
        is_tor = bool(raw.get("is_tor", False))
        is_datacenter = bool(raw.get("is_datacenter_ip", False))

        flags: list[str] = []
        if is_vpn:
            flags.append("VPN_DETECTED")
        if is_tor:
            flags.append("TOR_DETECTED")
        if is_datacenter:
            flags.append("DATACENTER_IP")

        return SecurityContext(
            authenticated=bool(raw.get("authenticated", False)),
            principal_id=str(raw.get("principal_id", "")),
            channel=str(raw.get("channel", "web")),
            ip_address=ip_str,
            device_fingerprint=str(raw.get("device_fingerprint", "")),
            user_agent=str(raw.get("user_agent", "")),
            geo_country=str(raw.get("geo_country", "")),
            geo_city=str(raw.get("geo_city", "")),
            is_vpn=is_vpn,
            is_tor=is_tor,
            is_datacenter_ip=is_datacenter,
            security_flags=flags,
        )

    def compute_input_hash(self, app: ApplicationInput) -> str:
        """SHA-256 del payload para integridad e idempotencia."""
        canonical = app.model_dump_json(exclude={"biometric_token", "device_fingerprint"})
        return hashlib.sha256(canonical.encode()).hexdigest()

    # ─── Validadores internos ─────────────────────────────────────────────────

    def _validate_identity(self, data: dict) -> dict:
        self._require_keys(data, {"national_id", "id_type", "full_name", "date_of_birth", "nationality"}, "identity")

        national_id = _guard.sanitize(data["national_id"], "national_id").upper()
        if not _NATIONAL_ID_PATTERN.match(national_id):
            raise InputValidationError("Formato de ID nacional inválido", field="national_id")

        full_name = _guard.sanitize(data["full_name"], "full_name")
        if not _NAME_PATTERN.match(full_name):
            raise InputValidationError("Nombre completo inválido", field="full_name")

        dob = self._parse_date(data["date_of_birth"], "date_of_birth")
        age = (datetime.now(timezone.utc).date() - dob).days // 365
        if age < 18:
            raise InputValidationError("Solicitante menor de edad", field="date_of_birth")
        if age > 85:
            raise InputValidationError("Edad fuera de rango permitido", field="date_of_birth")

        return {
            "national_id": national_id,
            "id_type": _guard.sanitize(data["id_type"], "id_type"),
            "full_name": full_name,
            "date_of_birth": data["date_of_birth"],
            "nationality": _guard.sanitize(data["nationality"], "nationality"),
            "tax_id": _guard.sanitize(data.get("tax_id", ""), "tax_id") or None,
        }

    def _validate_contact(self, data: dict) -> dict:
        self._require_keys(data, {"email", "phone", "address", "city", "country"}, "contact")

        email = data["email"].strip().lower()
        if not validators.email(email):
            raise InputValidationError("Email inválido", field="email")

        phone = data["phone"].strip()
        if not _PHONE_PATTERN.match(phone):
            raise InputValidationError("Teléfono inválido", field="phone")

        return {
            "email": email,
            "phone": phone,
            "address": _guard.sanitize(data["address"], "address"),
            "city": _guard.sanitize(data["city"], "city"),
            "country": _guard.sanitize(data["country"], "country"),
            "postal_code": _guard.sanitize(data.get("postal_code", ""), "postal_code"),
        }

    def _validate_credit_request(self, data: dict) -> dict:
        self._require_keys(data, {"requested_amount", "term_months", "purpose"}, "credit_request")

        amount = float(data["requested_amount"])
        if amount <= 0 or amount > 500_000:
            raise InputValidationError("Monto fuera de rango [1, 500000]", field="requested_amount")

        term = int(data["term_months"])
        if term < 6 or term > 84:
            raise InputValidationError("Plazo fuera de rango [6, 84] meses", field="term_months")

        purpose = _guard.sanitize(data["purpose"], "purpose")

        return {
            "requested_amount": round(amount, 2),
            "term_months": term,
            "purpose": purpose,
            "currency": _guard.sanitize(data.get("currency", "USD"), "currency"),
        }

    def _validate_financials(self, data: dict) -> dict:
        monthly_income = float(data.get("monthly_income", 0))
        if monthly_income <= 0:
            raise InputValidationError("Ingreso mensual debe ser positivo", field="monthly_income")
        if monthly_income > 1_000_000:
            raise InputValidationError("Ingreso mensual excede límite de validación", field="monthly_income")

        employment_type = _guard.sanitize(str(data.get("employment_type", "")), "employment_type")
        if employment_type not in _ALLOWED_EMPLOYMENT_TYPES:
            raise InputValidationError(
                f"Tipo de empleo inválido. Permitidos: {_ALLOWED_EMPLOYMENT_TYPES}",
                field="employment_type",
            )

        employment_months = int(data.get("employment_months", 0))
        if employment_months < 0 or employment_months > 600:
            raise InputValidationError("Meses de empleo fuera de rango", field="employment_months")

        additional_income = float(data.get("additional_income", 0))
        monthly_obligations = float(data.get("monthly_obligations", 0))

        employer_name = data.get("employer_name", "")
        if employer_name:
            employer_name = _guard.sanitize(str(employer_name), "employer_name")

        return {
            "monthly_income": round(monthly_income, 2),
            "employment_type": employment_type,
            "employer_name": employer_name or None,
            "employment_months": employment_months,
            "additional_income": round(max(0.0, additional_income), 2),
            "monthly_obligations": round(max(0.0, monthly_obligations), 2),
            "document_references": [str(d) for d in data.get("document_references", [])],
            "biometric_token": data.get("biometric_token"),
            "device_fingerprint": data.get("device_fingerprint"),
        }

    def _validate_channel_info(self, data: dict) -> tuple[str, str, str]:
        channel = _guard.sanitize(str(data.get("channel", "web")), "channel").lower()
        if channel not in _ALLOWED_CHANNELS:
            channel = "web"

        ip_str = str(data.get("ip_address", "0.0.0.0"))
        try:
            ipaddress.ip_address(ip_str)
        except ValueError:
            raise InputValidationError("Dirección IP inválida", field="ip_address")

        user_agent = _guard.sanitize(str(data.get("user_agent", ""))[:512], "user_agent")
        return channel, ip_str, user_agent

    # ─── Utilidades ───────────────────────────────────────────────────────────

    @staticmethod
    def _require_keys(data: dict, required: set[str], context: str) -> None:
        missing = required - set(data.keys())
        if missing:
            raise InputValidationError(
                f"Campos requeridos ausentes en '{context}': {missing}",
                field=context,
            )

    @staticmethod
    def _parse_date(value: str, field: str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise InputValidationError(f"Formato de fecha inválido en {field}", field=field)

    @staticmethod
    def _mask_id(value: str) -> str:
        if len(value) <= 4:
            return "****"
        return "*" * (len(value) - 4) + value[-4:]


_validator = InputValidator()


def validate_application_input(raw: dict) -> ApplicationInput:
    return _validator.validate_application(raw)


def validate_security_context(raw: dict) -> SecurityContext:
    return _validator.validate_security_context(raw)


def compute_input_hash(app: ApplicationInput) -> str:
    return _validator.compute_input_hash(app)
