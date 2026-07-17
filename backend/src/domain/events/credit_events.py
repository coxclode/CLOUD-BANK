"""
Eventos de dominio — Credit Application.

Los eventos son inmutables y representan hechos que ya ocurrieron.
Son el mecanismo principal de comunicación entre agregados y
la base del audit trail regulatorio.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class DomainEvent:
    """Base de todos los eventos de dominio."""
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    event_type: str = ""
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: str = "1.0"

    def to_dict(self) -> dict:
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class CreditApplicationCreated(DomainEvent):
    application_id: str = ""
    applicant_id: str = ""
    requested_amount: float = 0.0
    currency: str = "USD"
    channel: str = ""
    event_type: str = "credit.application.created"

    def to_dict(self) -> dict:
        return {
            **super().to_dict(),
            "application_id": self.application_id,
            "applicant_id": self.applicant_id,
            "requested_amount": self.requested_amount,
            "currency": self.currency,
            "channel": self.channel,
        }


@dataclass(frozen=True)
class CreditApplicationSubmitted(DomainEvent):
    application_id: str = ""
    event_type: str = "credit.application.submitted"

    def to_dict(self) -> dict:
        return {**super().to_dict(), "application_id": self.application_id}


@dataclass(frozen=True)
class CreditApplicationWithdrawn(DomainEvent):
    application_id: str = ""
    reason: str = ""
    event_type: str = "credit.application.withdrawn"

    def to_dict(self) -> dict:
        return {
            **super().to_dict(),
            "application_id": self.application_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CreditEvaluationStarted(DomainEvent):
    application_id: str = ""
    pipeline_id: str = ""
    event_type: str = "credit.evaluation.started"

    def to_dict(self) -> dict:
        return {
            **super().to_dict(),
            "application_id": self.application_id,
            "pipeline_id": self.pipeline_id,
        }


@dataclass(frozen=True)
class AgentExecutionCompleted(DomainEvent):
    application_id: str = ""
    agent_name: str = ""
    outcome: str = ""
    confidence: float = 0.0
    duration_ms: float = 0.0
    corrections_applied: int = 0
    event_type: str = "credit.agent.completed"

    def to_dict(self) -> dict:
        return {
            **super().to_dict(),
            "application_id": self.application_id,
            "agent_name": self.agent_name,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "duration_ms": self.duration_ms,
            "corrections_applied": self.corrections_applied,
        }


@dataclass(frozen=True)
class CreditDecisionIssued(DomainEvent):
    application_id: str = ""
    decision_id: str = ""
    outcome: str = ""
    confidence: float = 0.0
    risk_score: float = 0.0
    decided_by: str = ""
    human_review_required: bool = False
    event_type: str = "credit.decision.issued"

    def to_dict(self) -> dict:
        return {
            **super().to_dict(),
            "application_id": self.application_id,
            "decision_id": self.decision_id,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "risk_score": self.risk_score,
            "decided_by": self.decided_by,
            "human_review_required": self.human_review_required,
        }


@dataclass(frozen=True)
class FraudAlertRaised(DomainEvent):
    application_id: str = ""
    fraud_score: float = 0.0
    fraud_flags: list[str] = field(default_factory=list)
    is_blocked: bool = False
    event_type: str = "credit.fraud.alert"

    def to_dict(self) -> dict:
        return {
            **super().to_dict(),
            "application_id": self.application_id,
            "fraud_score": self.fraud_score,
            "fraud_flags": self.fraud_flags,
            "is_blocked": self.is_blocked,
        }


@dataclass(frozen=True)
class EscalationRequested(DomainEvent):
    application_id: str = ""
    committee_type: str = ""
    priority: str = ""
    reason: str = ""
    event_type: str = "credit.escalation.requested"

    def to_dict(self) -> dict:
        return {
            **super().to_dict(),
            "application_id": self.application_id,
            "committee_type": self.committee_type,
            "priority": self.priority,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AuditRecordCreated(DomainEvent):
    application_id: str = ""
    audit_action: str = ""
    actor: str = ""
    outcome: str = ""
    pii_accessed: bool = False
    event_type: str = "credit.audit.record_created"

    def to_dict(self) -> dict:
        return {
            **super().to_dict(),
            "application_id": self.application_id,
            "audit_action": self.audit_action,
            "actor": self.actor,
            "outcome": self.outcome,
            "pii_accessed": self.pii_accessed,
        }
