"""
Logger estructurado — JSON para producción, texto coloreado para desarrollo.

Características:
  - JSON estructurado compatible con ELK/Loki/CloudWatch
  - Redacción automática de PII en logs
  - Contexto de request propagado via contextvars
  - Correlación de traces con OpenTelemetry
  - Sanitización de datos sensibles antes de serializar
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any, Optional

import structlog
from structlog.types import EventDict, WrappedLogger

# Variables de contexto de request (se propagan automáticamente en async)
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
_pipeline_id_var: ContextVar[str] = ContextVar("pipeline_id", default="")
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

_PII_FIELDS = frozenset({
    "national_id", "email", "phone", "birth_date", "full_name",
    "address", "password", "token", "api_key", "secret",
    "credit_card", "bank_account", "ssn",
})

_FINANCIAL_FIELDS = frozenset({
    "gross_monthly_income", "net_income", "total_debt",
    "approved_amount", "credit_limit",
})


def _redact_pii(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Processor structlog: redacta PII automáticamente en cada evento."""
    for key in list(event_dict.keys()):
        if key in _PII_FIELDS:
            val = str(event_dict[key])
            event_dict[key] = f"{val[:2]}***" if len(val) > 2 else "***"
        elif key in _FINANCIAL_FIELDS:
            event_dict[key] = "***REDACTED***"
    return event_dict


def _add_request_context(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Processor: agrega el contexto de request al evento."""
    if request_id := _request_id_var.get():
        event_dict["request_id"] = request_id
    if correlation_id := _correlation_id_var.get():
        event_dict["correlation_id"] = correlation_id
    if pipeline_id := _pipeline_id_var.get():
        event_dict["pipeline_id"] = pipeline_id
    if trace_id := _trace_id_var.get():
        event_dict["trace_id"] = trace_id
    return event_dict


def _add_log_level(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    event_dict["level"] = method_name.upper()
    return event_dict


def configure_logging(environment: str = "production", log_level: str = "INFO") -> None:
    """
    Configura structlog para el entorno especificado.
    Llamar una sola vez al inicio de la aplicación.
    """
    shared_processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _add_request_context,
        _add_log_level,
        _redact_pii,
    ]

    if environment == "production":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    for noisy in ("uvicorn.access", "uvicorn", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def bind_request_context(
    request_id: str,
    correlation_id: str = "",
    pipeline_id: str = "",
    trace_id: str = "",
) -> None:
    """Vincula el contexto de request al contexto actual (task de asyncio)."""
    _request_id_var.set(request_id)
    if correlation_id:
        _correlation_id_var.set(correlation_id)
    if pipeline_id:
        _pipeline_id_var.set(pipeline_id)
    if trace_id:
        _trace_id_var.set(trace_id)


def clear_request_context() -> None:
    _request_id_var.set("")
    _correlation_id_var.set("")
    _pipeline_id_var.set("")
    _trace_id_var.set("")
