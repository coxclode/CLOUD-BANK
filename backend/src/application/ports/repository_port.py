"""
Puerto de salida: repositorios — re-exporta las interfaces del dominio.

La capa de aplicación importa desde aquí para mantener una única fuente de verdad.
"""

from src.domain.repositories.credit_repository import (
    CreditApplicationRepository,
    CreditDecisionRepository,
    EvaluationStateStore,
)

__all__ = [
    "CreditApplicationRepository",
    "CreditDecisionRepository",
    "EvaluationStateStore",
]
