"""
Interfaces (ports) de repositorio — Domain Layer.

Regla de dependencia: el dominio define QUÉ necesita, no CÓMO se implementa.
Las implementaciones concretas viven en infrastructure/.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional
from uuid import UUID

from src.domain.entities.credit_application import CreditApplication, ApplicationStatus
from src.domain.entities.credit_decision import CreditDecision


class CreditApplicationRepository(ABC):
    """Puerto de salida para persistencia de solicitudes de crédito."""

    @abstractmethod
    async def save(self, application: CreditApplication) -> None:
        """Persiste una solicitud. Crea o actualiza según exista el ID."""

    @abstractmethod
    async def find_by_id(self, application_id: UUID) -> Optional[CreditApplication]:
        """Recupera una solicitud por su ID. None si no existe."""

    @abstractmethod
    async def find_by_national_id(
        self, national_id: str
    ) -> list[CreditApplication]:
        """Recupera todas las solicitudes de un solicitante por su documento."""

    @abstractmethod
    async def find_by_status(
        self,
        status: ApplicationStatus,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CreditApplication]:
        """Lista solicitudes por estado con paginación."""

    @abstractmethod
    async def count_active_applications(self, national_id: str) -> int:
        """Cuenta solicitudes activas (no terminales) para un solicitante."""

    @abstractmethod
    async def exists(self, application_id: UUID) -> bool:
        """Verifica si existe una solicitud con el ID dado."""

    @abstractmethod
    async def find_submitted_before(
        self, before: datetime
    ) -> list[CreditApplication]:
        """Solicitudes enviadas antes de una fecha (para expiración automática)."""


class CreditDecisionRepository(ABC):
    """Puerto de salida para persistencia de decisiones crediticias."""

    @abstractmethod
    async def save(self, decision: CreditDecision) -> None:
        """Persiste una decisión. Las decisiones son inmutables — solo inserta."""

    @abstractmethod
    async def find_by_id(self, decision_id: UUID) -> Optional[CreditDecision]:
        """Recupera una decisión por su ID."""

    @abstractmethod
    async def find_by_application_id(
        self, application_id: UUID
    ) -> Optional[CreditDecision]:
        """Recupera la decisión más reciente de una solicitud."""

    @abstractmethod
    async def find_all_by_application_id(
        self, application_id: UUID
    ) -> list[CreditDecision]:
        """Recupera el historial completo de decisiones de una solicitud."""

    @abstractmethod
    async def find_escalated_pending_review(
        self, limit: int = 50
    ) -> list[CreditDecision]:
        """Decisiones escaladas que aún no tienen revisión humana."""


class EvaluationStateStore(ABC):
    """Puerto de salida para el estado de ejecución del pipeline (LangGraph checkpoint)."""

    @abstractmethod
    async def save_state(self, pipeline_id: str, state: dict) -> None:
        """Persiste el estado parcial del pipeline para recuperación ante fallos."""

    @abstractmethod
    async def load_state(self, pipeline_id: str) -> Optional[dict]:
        """Recupera el último estado guardado de un pipeline."""

    @abstractmethod
    async def delete_state(self, pipeline_id: str) -> None:
        """Limpia el estado de un pipeline completado."""

    @abstractmethod
    async def list_pending_pipelines(self) -> list[str]:
        """Lista los pipeline_ids con estado pendiente de recuperación."""
