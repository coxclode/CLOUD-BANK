"""
CLOUD BANK — Deep Agents Package
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Agentes Deep con pipeline de 10 capas de razonamiento.
Cada agente implementa: Planificador Interno, Verificador Cross-Agent,
Evaluador de Calidad, Autocorrector y Justificación Regulatoria.
"""

from src.agents.deep.fraud_deep_agent     import FraudDeepAgent
from src.agents.deep.credit_deep_agent    import CreditDeepAgent
from src.agents.deep.actuarial_deep_agent import ActuarialDeepAgent
from src.agents.deep.approval_deep_agent  import ApprovalDeepAgent

__all__ = [
    "FraudDeepAgent",
    "CreditDeepAgent",
    "ActuarialDeepAgent",
    "ApprovalDeepAgent",
]
