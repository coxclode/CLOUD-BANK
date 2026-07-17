"""
Puerto de salida: notificaciones y eventos de dominio.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.events.credit_events import DomainEvent


class EventPublisherPort(ABC):
    """Publica eventos de dominio hacia sistemas externos (cola de mensajes)."""

    @abstractmethod
    async def publish(self, event: DomainEvent) -> None:
        """Publica un evento. Garantiza at-least-once delivery."""

    @abstractmethod
    async def publish_batch(self, events: list[DomainEvent]) -> None:
        """Publica un lote de eventos en una transacción."""


class NotificationPort(ABC):
    """Envía notificaciones al solicitante y al equipo interno."""

    @abstractmethod
    async def notify_decision(
        self,
        *,
        email: str,
        applicant_name: str,
        outcome: str,
        application_id: str,
    ) -> None:
        """Notifica la decisión al solicitante."""

    @abstractmethod
    async def notify_escalation(
        self,
        *,
        committee_type: str,
        priority: str,
        application_id: str,
        summary: str,
    ) -> None:
        """Alerta al comité cuando hay una escalación."""
