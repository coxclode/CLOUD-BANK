"""
Puerto de salida: AgentPort

Define el contrato que todos los agentes deben cumplir.
La capa de aplicación no conoce los detalles de implementación de los agentes —
solo conoce esta interfaz.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class AgentOutcome(str, Enum):
    """
    Espejo del AgentOutcome de ai-services (ai-services/src/contracts/agent_result.py).
    Los valores deben coincidir exactamente: son el vocabulario que cruza la red
    en la respuesta de POST /v1/pipeline/evaluate.
    """
    APPROVED        = "APPROVED"
    REJECTED        = "REJECTED"
    ESCALATED       = "ESCALATED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    FAILED          = "FAILED"


@dataclass
class AgentResult:
    """
    Resultado normalizado que cualquier agente devuelve a la capa de aplicación.
    Los detalles específicos de cada agente van en `payload`.
    """
    agent_name: str
    outcome: AgentOutcome
    confidence: float
    quality_score: float
    risk_contribution: float
    payload: dict[str, Any]
    execution_time_ms: float
    corrections_applied: int = 0
    human_review_required: bool = False
    error_message: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_reliable(self) -> bool:
        return (
            self.outcome != AgentOutcome.FAILED
            and self.confidence >= 0.50
            and self.quality_score >= 0.40
        )

    @property
    def is_critical_failure(self) -> bool:
        return self.outcome == AgentOutcome.FAILED and not self.is_reliable


class AgentPort(ABC):
    """
    Puerto de salida para la ejecución de agentes de análisis crediticio.
    Implementado por los adaptadores en infrastructure/agents/ o por los
    Deep Agents directamente con un adaptador wrapper.
    """

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Identificador único del agente."""

    @abstractmethod
    async def execute(
        self,
        application_data: dict[str, Any],
        context: dict[str, Any],
    ) -> AgentResult:
        """
        Ejecuta el análisis del agente y devuelve un AgentResult normalizado.

        application_data: datos de la solicitud de crédito
        context        : resultados de agentes previos y metadata del pipeline
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Verifica que el agente esté operativo."""


class FraudAgentPort(AgentPort, ABC):
    """Especialización para el agente de fraude."""

    @abstractmethod
    async def get_fraud_score(self, application_data: dict) -> float:
        """Score de fraude 0.0-1.0 de forma síncrona para decisiones rápidas."""


class CreditAgentPort(AgentPort, ABC):
    """Especialización para el agente de historial crediticio."""

    @abstractmethod
    async def get_payment_capacity(self, application_data: dict) -> dict:
        """Capacidad de pago del solicitante."""


class ActuarialAgentPort(AgentPort, ABC):
    """Especialización para el agente actuarial."""

    @abstractmethod
    async def get_default_probability(self, application_data: dict) -> float:
        """Probabilidad de incumplimiento a 12 meses."""


class ApprovalAgentPort(AgentPort, ABC):
    """Especialización para el agente aprobador."""

    @abstractmethod
    async def get_final_decision(
        self,
        application_data: dict,
        prior_agent_results: dict[str, AgentResult],
    ) -> dict:
        """Decisión final integrando los 3 análisis previos."""
