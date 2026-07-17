"""
CLOUD BANK — Trazabilidad Distribuida con OpenTelemetry
Instrumentación de cada nodo del grafo, agente y llamada a servicio externo.
Backend: Jaeger / Tempo. Visualización: Grafana.
"""

from __future__ import annotations

import functools
import time
from contextlib import contextmanager
from typing import Any, Callable, Generator, TypeVar

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

logger = structlog.get_logger(__name__)
F = TypeVar("F", bound=Callable[..., Any])

_provider: TracerProvider | None = None
_tracer: trace.Tracer | None = None


def configure_tracer(
    service_name: str,
    environment: str,
    otlp_endpoint: str,
    version: str = "2.0",
) -> None:
    """Inicializa el TracerProvider de OpenTelemetry con exportador OTLP."""
    global _provider, _tracer

    resource = Resource.create({
        "service.name": service_name,
        "service.version": version,
        "deployment.environment": environment,
        "cloud.provider": "cloudbank",
    })

    _provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    _provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(_provider)
    _tracer = trace.get_tracer(service_name, version)
    logger.info("tracer.configured", endpoint=otlp_endpoint)


def get_tracer() -> trace.Tracer:
    global _tracer
    if _tracer is None:
        _tracer = trace.get_tracer("cloudbank-credit-engine", "2.0")
    return _tracer


@contextmanager
def trace_node(
    node_name: str,
    request_id: str,
    correlation_id: str,
    attributes: dict[str, Any] | None = None,
) -> Generator[trace.Span, None, None]:
    """
    Context manager para trazar la ejecución de un nodo del grafo.
    Captura duración, estado y atributos de negocio.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(
        f"cloudbank.node.{node_name}",
        kind=SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("cloudbank.node", node_name)
        span.set_attribute("cloudbank.request_id", request_id)
        span.set_attribute("cloudbank.correlation_id", correlation_id)
        if attributes:
            for k, v in attributes.items():
                if isinstance(v, (str, bool, int, float)):
                    span.set_attribute(f"cloudbank.{k}", v)
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise


@contextmanager
def trace_agent(
    agent_name: str,
    request_id: str,
) -> Generator[trace.Span, None, None]:
    """Context manager para trazar la ejecución de un agente."""
    tracer = get_tracer()
    with tracer.start_as_current_span(
        f"cloudbank.agent.{agent_name}",
        kind=SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("cloudbank.agent", agent_name)
        span.set_attribute("cloudbank.request_id", request_id)
        start = time.monotonic()
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise
        finally:
            span.set_attribute("cloudbank.duration_ms", (time.monotonic() - start) * 1000)


@contextmanager
def trace_external_call(
    service: str,
    operation: str,
) -> Generator[trace.Span, None, None]:
    """Context manager para trazar llamadas a servicios externos."""
    tracer = get_tracer()
    with tracer.start_as_current_span(
        f"cloudbank.external.{service}.{operation}",
        kind=SpanKind.CLIENT,
    ) as span:
        span.set_attribute("peer.service", service)
        span.set_attribute("db.operation", operation)
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise


def traced(node_name: str) -> Callable[[F], F]:
    """Decorador para trazar funciones de nodo automáticamente."""
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(state, *args, **kwargs):
            request_id = getattr(state, "request_id", "unknown")
            correlation_id = getattr(state, "correlation_id", "unknown")
            async with trace_node(node_name, request_id, correlation_id):
                return await func(state, *args, **kwargs)
        return wrapper  # type: ignore
    return decorator
