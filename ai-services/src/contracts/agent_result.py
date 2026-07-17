"""
Contrato de salida de los agentes — copia local de ai-services.

Este tipo se serializa a JSON en la respuesta de POST /v1/pipeline/evaluate.
El backend mantiene su propia copia en src/application/ports/agent_port.py
para reconstruir AgentResult desde esa respuesta — es la única forma de dato
que cruza la frontera de red entre ai-services y backend, y por eso se
duplica en vez de compartir un paquete entre dos servicios desplegables
de forma independiente.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class AgentOutcome(str, Enum):
    APPROVED        = "APPROVED"
    REJECTED        = "REJECTED"
    ESCALATED       = "ESCALATED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    FAILED          = "FAILED"


@dataclass
class AgentResult:
    """Resultado normalizado que cada adaptador de agente devuelve al orquestador."""

    agent_name: str
    outcome: AgentOutcome
    confidence: float
    quality_score: float
    risk_contribution: float
    payload: dict[str, Any]
    reasoning_chain: list[str] = field(default_factory=list)
    execution_time_ms: float = 0.0
    human_review_required: bool = False
    error_message: Optional[str] = None

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
    """Puerto que todo adaptador de agente debe cumplir frente al orquestador."""

    @property
    def agent_name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    async def execute(
        self,
        application_data: dict[str, Any],
        context: dict[str, Any],
    ) -> AgentResult:
        """Ejecuta el análisis del agente y devuelve un AgentResult normalizado."""

    async def health_check(self) -> bool:
        """Verifica que el agente esté operativo. Override si hay dependencias externas que chequear."""
        return True


class FraudAgentPort(AgentPort, ABC):
    """Especialización informativa para el agente de fraude (documenta el contrato de dominio)."""


class CreditAgentPort(AgentPort, ABC):
    """Especialización informativa para el agente de historial crediticio."""


class ActuarialAgentPort(AgentPort, ABC):
    """Especialización informativa para el agente actuarial."""


class ApprovalAgentPort(AgentPort, ABC):
    """Especialización informativa para el agente aprobador."""
