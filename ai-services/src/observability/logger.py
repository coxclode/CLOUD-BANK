"""
CLOUD BANK — Sistema de Logging Estructurado
Logs en formato JSON con correlación de solicitudes, enmascaramiento de PII
y niveles de clasificación de datos. Compatible con ELK / Grafana Loki.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger


# ─── Procesadores de seguridad ────────────────────────────────────────────────

_PII_KEYS = {
    "national_id", "tax_id", "full_name", "name", "email",
    "phone", "address", "date_of_birth", "password", "token",
    "api_key", "secret", "biometric_token", "device_fingerprint",
}


def _redact_pii(logger: WrappedLogger, method: str, event_dict: EventDict) -> EventDict:
    """Procesador structlog: enmascara campos PII antes de serializar."""
    for key in list(event_dict.keys()):
        if key.lower() in _PII_KEYS:
            value = event_dict[key]
            if isinstance(value, str) and len(value) > 4:
                event_dict[key] = value[:2] + "****" + value[-2:]
            else:
                event_dict[key] = "****"
    return event_dict


def _add_service_context(logger: WrappedLogger, method: str, event_dict: EventDict) -> EventDict:
    event_dict.setdefault("service", "cloudbank-credit-engine")
    event_dict.setdefault("version", "2.0")
    return event_dict


def _classify_data(logger: WrappedLogger, method: str, event_dict: EventDict) -> EventDict:
    """Agrega clasificación de datos para cumplimiento regulatorio."""
    has_pii = any(k.lower() in _PII_KEYS for k in event_dict)
    event_dict["data_classification"] = "CONFIDENTIAL" if has_pii else "INTERNAL"
    return event_dict


# ─── Inicialización ───────────────────────────────────────────────────────────

def configure_logging(log_level: str = "INFO", json_format: bool = True) -> None:
    """
    Configura structlog con procesadores de seguridad, correlación y serialización JSON.
    Llamar una sola vez al inicio de la aplicación.
    """
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_service_context,
        _redact_pii,
        _classify_data,
        structlog.processors.StackInfoRenderer(),
    ]

    if json_format:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)


def bind_request_context(request_id: str, correlation_id: str, node: str = "") -> None:
    """Vincula IDs de correlación al contexto de la solicitud actual."""
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        correlation_id=correlation_id,
        node=node,
    )


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()
