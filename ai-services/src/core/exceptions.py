"""
CLOUD BANK — Jerarquía de Excepciones del Sistema
Todas las excepciones del sistema heredan de CloudBankError para
facilitar logging uniforme y circuit breaking.
"""

from typing import Optional


class CloudBankError(Exception):
    """Base para todas las excepciones del sistema."""
    def __init__(self, message: str, code: str = "INTERNAL_ERROR", details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class SecurityViolationError(CloudBankError):
    """Violación de política de seguridad. Siempre fatal."""
    def __init__(self, message: str, violation_type: str = "UNKNOWN"):
        super().__init__(message, code="SECURITY_VIOLATION", details={"violation_type": violation_type})


class PromptInjectionError(SecurityViolationError):
    def __init__(self, message: str = "Intento de inyección de prompt detectado"):
        super().__init__(message, violation_type="PROMPT_INJECTION")


class JailbreakAttemptError(SecurityViolationError):
    def __init__(self, message: str = "Intento de jailbreak detectado"):
        super().__init__(message, violation_type="JAILBREAK")


class InputValidationError(CloudBankError):
    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message, code="INPUT_VALIDATION", details={"field": field})


class AgentExecutionError(CloudBankError):
    def __init__(self, message: str, agent: str, is_retryable: bool = True):
        super().__init__(message, code="AGENT_EXECUTION", details={"agent": agent, "retryable": is_retryable})
        self.agent = agent
        self.is_retryable = is_retryable


class AgentTimeoutError(AgentExecutionError):
    def __init__(self, agent: str, timeout_s: int):
        super().__init__(f"Agente {agent} superó timeout de {timeout_s}s", agent=agent, is_retryable=True)


class ExternalServiceError(CloudBankError):
    def __init__(self, message: str, service: str, status_code: Optional[int] = None):
        super().__init__(message, code="EXTERNAL_SERVICE", details={"service": service, "status_code": status_code})
        self.service = service
        self.is_retryable = status_code in {429, 500, 502, 503, 504} if status_code else True


class FraudCriticalError(CloudBankError):
    """Fraude crítico detectado. Bloquea la solicitud definitivamente."""
    def __init__(self, fraud_score: float, flags: list[str]):
        super().__init__(
            f"Fraude crítico detectado (score={fraud_score:.3f})",
            code="FRAUD_CRITICAL",
            details={"fraud_score": fraud_score, "flags": flags},
        )


class MaxRetriesExceededError(CloudBankError):
    def __init__(self, node: str, attempts: int):
        super().__init__(
            f"Nodo {node} superó máximo de {attempts} reintentos",
            code="MAX_RETRIES",
            details={"node": node, "attempts": attempts},
        )


class StateCorruptionError(CloudBankError):
    """El estado del grafo presenta inconsistencias irrecuperables."""
    def __init__(self, message: str):
        super().__init__(message, code="STATE_CORRUPTION")


class DataPoisoningError(SecurityViolationError):
    def __init__(self, message: str = "Datos envenenados detectados en entrada"):
        super().__init__(message, violation_type="DATA_POISONING")
