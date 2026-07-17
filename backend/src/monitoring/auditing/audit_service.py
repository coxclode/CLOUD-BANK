"""
Servicio de Auditoría — CLOUD BANK

Requisitos regulatorios:
  - GDPR Art. 5(2): Responsabilidad proactiva — todo acceso a datos personales se registra
  - GDPR Art. 22: Decisión automatizada — cada decisión tiene justificación trazable
  - Basel III / Circular SBS: Trazabilidad de decisiones crediticias por 7 años
  - PCI DSS: Logs de acceso inmutables a datos financieros

Diseño:
  - Los registros de auditoría son INMUTABLES una vez creados (append-only)
  - Se almacenan en PostgreSQL con WAL (Write-Ahead Log) para durabilidad
  - Se replican a S3/GCS para retención a largo plazo
  - Cada registro tiene checksum SHA-256 para verificar integridad
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


class AuditAction(str, Enum):
    # Aplicación
    APPLICATION_RECEIVED   = "APPLICATION_RECEIVED"
    APPLICATION_SUBMITTED  = "APPLICATION_SUBMITTED"
    APPLICATION_REVIEWED   = "APPLICATION_REVIEWED"

    # Pipeline
    PIPELINE_STARTED       = "PIPELINE_STARTED"
    AGENT_EXECUTED         = "AGENT_EXECUTED"
    PIPELINE_COMPLETED     = "PIPELINE_COMPLETED"

    # Decisión
    DECISION_ISSUED        = "DECISION_ISSUED"
    DECISION_APPROVED      = "DECISION_APPROVED"
    DECISION_REJECTED      = "DECISION_REJECTED"
    DECISION_ESCALATED     = "DECISION_ESCALATED"

    # Seguridad
    AUTHENTICATION_SUCCESS = "AUTHENTICATION_SUCCESS"
    AUTHENTICATION_FAILURE = "AUTHENTICATION_FAILURE"
    AUTHORIZATION_DENIED   = "AUTHORIZATION_DENIED"
    PROMPT_INJECTION_BLOCK = "PROMPT_INJECTION_BLOCKED"
    RATE_LIMIT_EXCEEDED    = "RATE_LIMIT_EXCEEDED"

    # Acceso a datos
    PII_ACCESSED           = "PII_ACCESSED"
    PII_MASKED             = "PII_MASKED"
    DATA_EXPORTED          = "DATA_EXPORTED"

    # Errores
    SYSTEM_ERROR           = "SYSTEM_ERROR"
    AGENT_FAILURE          = "AGENT_FAILURE"


@dataclass
class AuditRecord:
    """
    Registro inmutable de auditoría.
    El checksum garantiza que no fue modificado post-creación.
    """
    audit_id: str
    timestamp: datetime
    action: AuditAction
    actor: str
    resource_type: str
    resource_id: str
    outcome: str
    pii_accessed: bool
    ip_address: Optional[str]
    request_id: Optional[str]
    pipeline_id: Optional[str]
    correlation_id: Optional[str]
    metadata: dict[str, Any]
    checksum: str

    @classmethod
    def create(
        cls,
        action: AuditAction,
        actor: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        pii_accessed: bool = False,
        ip_address: Optional[str] = None,
        request_id: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> "AuditRecord":
        audit_id  = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc)
        meta      = metadata or {}

        payload = {
            "audit_id":      audit_id,
            "timestamp":     timestamp.isoformat(),
            "action":        action.value,
            "actor":         actor,
            "resource_type": resource_type,
            "resource_id":   resource_id,
            "outcome":       outcome,
            "metadata":      meta,
        }
        checksum = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

        return cls(
            audit_id=audit_id,
            timestamp=timestamp,
            action=action,
            actor=actor,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            pii_accessed=pii_accessed,
            ip_address=ip_address,
            request_id=request_id,
            pipeline_id=pipeline_id,
            correlation_id=correlation_id,
            metadata=meta,
            checksum=checksum,
        )

    def verify_integrity(self) -> bool:
        """Verifica que el registro no fue modificado desde su creación."""
        payload = {
            "audit_id":      self.audit_id,
            "timestamp":     self.timestamp.isoformat(),
            "action":        self.action.value,
            "actor":         self.actor,
            "resource_type": self.resource_type,
            "resource_id":   self.resource_id,
            "outcome":       self.outcome,
            "metadata":      self.metadata,
        }
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return self.checksum == expected


class AuditService:
    """
    Servicio de auditoría. Crea y persiste registros inmutables.
    El storage backend es intercambiable (PostgreSQL, Elasticsearch, S3).
    """

    def __init__(self, storage_backend) -> None:
        self._storage = storage_backend

    async def record(
        self,
        action: AuditAction,
        actor: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        **kwargs,
    ) -> AuditRecord:
        record = AuditRecord.create(
            action=action,
            actor=actor,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            **kwargs,
        )
        try:
            await self._storage.append(record)
        except Exception as exc:
            logger.error(
                "audit_service.storage_failed",
                action=action.value,
                resource_id=resource_id,
                error=str(exc),
            )
        logger.info(
            "audit.record_created",
            action=action.value,
            actor=actor,
            resource_id=resource_id,
            outcome=outcome,
            pii_accessed=kwargs.get("pii_accessed", False),
        )
        return record

    async def record_decision(
        self,
        application_id: str,
        outcome: str,
        decided_by: str,
        confidence: float,
        risk_score: float,
        pipeline_id: Optional[str] = None,
        **kwargs,
    ) -> AuditRecord:
        return await self.record(
            action=AuditAction.DECISION_ISSUED,
            actor=decided_by,
            resource_type="credit_application",
            resource_id=application_id,
            outcome=outcome,
            pipeline_id=pipeline_id,
            metadata={
                "confidence": confidence,
                "risk_score": risk_score,
                "decided_by": decided_by,
            },
            **kwargs,
        )

    async def record_security_event(
        self,
        action: AuditAction,
        actor: str,
        resource_id: str,
        outcome: str,
        ip_address: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> AuditRecord:
        return await self.record(
            action=action,
            actor=actor,
            resource_type="security",
            resource_id=resource_id,
            outcome=outcome,
            ip_address=ip_address,
            metadata=metadata or {},
        )
