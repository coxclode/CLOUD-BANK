"""
Adaptador: Event Publisher

Implementa EventPublisherPort usando Redis Streams.
Redis Streams garantiza at-least-once delivery y permite consumer groups
para múltiples suscriptores (auditoría, notificaciones, analytics).

Alternativa de producción: sustituir por Apache Kafka o AWS Kinesis
cambiando solo este adaptador, sin tocar el dominio.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis

from src.application.ports.notification_port import EventPublisherPort
from src.domain.events.credit_events import DomainEvent

logger = structlog.get_logger(__name__)

_STREAM_PREFIX      = "cloudbank:events:"
_MAX_STREAM_LEN     = 100_000
_RETRY_MAX          = 3


class RedisStreamEventPublisher(EventPublisherPort):
    """
    Publica eventos de dominio como mensajes en Redis Streams.

    Cada tipo de evento va a su propio stream:
      - cloudbank:events:credit.application.*
      - cloudbank:events:credit.decision.*
      - cloudbank:events:credit.fraud.*
      etc.
    """

    def __init__(self, redis_client: Redis, stream_prefix: str = _STREAM_PREFIX) -> None:
        self._redis  = redis_client
        self._prefix = stream_prefix

    async def publish(self, event: DomainEvent) -> None:
        stream_name = f"{self._prefix}{event.event_type}"
        message = {
            "event_id":       str(event.event_id),
            "event_type":     event.event_type,
            "occurred_at":    event.occurred_at.isoformat(),
            "schema_version": event.schema_version,
            "payload":        json.dumps(event.to_dict(), default=str),
        }
        for attempt in range(1, _RETRY_MAX + 1):
            try:
                await self._redis.xadd(
                    name=stream_name,
                    fields=message,
                    maxlen=_MAX_STREAM_LEN,
                    approximate=True,
                )
                logger.debug(
                    "event_publisher.published",
                    event_type=event.event_type,
                    event_id=str(event.event_id),
                )
                return
            except Exception as exc:
                if attempt == _RETRY_MAX:
                    logger.error(
                        "event_publisher.publish_failed",
                        event_type=event.event_type,
                        attempt=attempt,
                        error=str(exc),
                    )
                    raise
                logger.warning(
                    "event_publisher.publish_retrying",
                    attempt=attempt,
                    error=str(exc),
                )

    async def publish_batch(self, events: list[DomainEvent]) -> None:
        async with self._redis.pipeline(transaction=False) as pipe:
            for event in events:
                stream_name = f"{self._prefix}{event.event_type}"
                message = {
                    "event_id":    str(event.event_id),
                    "event_type":  event.event_type,
                    "occurred_at": event.occurred_at.isoformat(),
                    "payload":     json.dumps(event.to_dict(), default=str),
                }
                pipe.xadd(stream_name, message, maxlen=_MAX_STREAM_LEN, approximate=True)
            try:
                await pipe.execute()
                logger.debug("event_publisher.batch_published", count=len(events))
            except Exception as exc:
                logger.error("event_publisher.batch_failed", count=len(events), error=str(exc))
                raise


class NullEventPublisher(EventPublisherPort):
    """No-op publisher para tests. Captura eventos para inspección."""

    def __init__(self) -> None:
        self.published_events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.published_events.append(event)

    async def publish_batch(self, events: list[DomainEvent]) -> None:
        self.published_events.extend(events)

    def clear(self) -> None:
        self.published_events.clear()
