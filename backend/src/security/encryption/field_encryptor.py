"""
Cifrado de campos PII — AES-256-GCM con Fernet

Campos sensibles que siempre se cifran en reposo y en tránsito interno:
  - national_id
  - email
  - phone
  - gross_monthly_income
  - fecha de nacimiento

La clave de cifrado se almacena en Vault, nunca en variables de entorno
en producción. La rotación de clave se realiza re-cifrando todos los
registros afectados en un job separado.
"""

from __future__ import annotations

import base64
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
import structlog

logger = structlog.get_logger(__name__)

_PII_FIELDS: frozenset[str] = frozenset({
    "national_id", "email", "phone", "birth_date", "full_name",
    "address", "bank_account", "card_number",
})

_FINANCIAL_FIELDS: frozenset[str] = frozenset({
    "gross_monthly_income", "net_monthly_income", "total_debt",
    "credit_limit", "approved_amount",
})


class FieldEncryptor:
    """
    Cifra y descifra campos individuales usando Fernet (AES-128-CBC + HMAC).
    La clave se debe recuperar de Vault — este componente recibe la clave,
    no sabe cómo obtenerla.
    """

    def __init__(self, encryption_key: str) -> None:
        self._fernet = Fernet(self._normalize_key(encryption_key))

    def encrypt(self, plaintext: str) -> str:
        """Cifra un string. Devuelve el ciphertext en base64-url."""
        if not plaintext:
            return plaintext
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """Descifra un ciphertext. Lanza DecryptionError si la clave es incorrecta."""
        if not ciphertext:
            return ciphertext
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise DecryptionError("Ciphertext inválido o clave incorrecta.") from exc

    def encrypt_dict(self, data: dict, fields: Optional[set[str]] = None) -> dict:
        """Cifra todos los campos sensibles de un dict."""
        to_encrypt = fields or (_PII_FIELDS | _FINANCIAL_FIELDS)
        result = {}
        for k, v in data.items():
            if k in to_encrypt and isinstance(v, str) and v:
                result[k] = self.encrypt(v)
            else:
                result[k] = v
        return result

    def decrypt_dict(self, data: dict, fields: Optional[set[str]] = None) -> dict:
        """Descifra todos los campos sensibles de un dict."""
        to_decrypt = fields or (_PII_FIELDS | _FINANCIAL_FIELDS)
        result = {}
        for k, v in data.items():
            if k in to_decrypt and isinstance(v, str) and v:
                try:
                    result[k] = self.decrypt(v)
                except DecryptionError:
                    result[k] = v
                    logger.warning("field_encryptor.decrypt_failed", field=k)
            else:
                result[k] = v
        return result

    def mask_pii(self, data: dict) -> dict:
        """
        Crea una copia del dict con PII enmascarada para logs.
        NO descifra — solo enmascara el valor visible.
        """
        result = {}
        for k, v in data.items():
            if k in _PII_FIELDS:
                result[k] = self._mask_value(str(v) if v else "")
            elif k in _FINANCIAL_FIELDS:
                result[k] = "***"
            else:
                result[k] = v
        return result

    @staticmethod
    def _mask_value(value: str) -> str:
        if len(value) <= 4:
            return "****"
        return f"{'*' * (len(value) - 4)}{value[-4:]}"

    @staticmethod
    def _normalize_key(key: str) -> bytes:
        raw = key.encode("utf-8")
        if len(raw) < 32:
            raw = raw.ljust(32, b"\x00")
        else:
            raw = raw[:32]
        return base64.urlsafe_b64encode(raw)


class DecryptionError(Exception):
    pass
