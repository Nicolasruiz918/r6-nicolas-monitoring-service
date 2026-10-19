from __future__ import annotations

import json
from typing import Callable

try:
    import pika  # type: ignore
except ImportError:  # pragma: no cover - exercised only when RabbitMQ adapter is started without dependency.
    pika = None  # type: ignore

from monitoring.application.usecase.alert_worker import AlertWorker, RetryingEventConsumer
from monitoring.domain.model.event import DomainEvent

MONITORING_EVENTS_EXCHANGE = "monitoring-events"
MONITORING_DLQ_ROUTING_KEY = "monitoring.alert-worker.dlq"


class RabbitMqPublisher:
    def __init__(self, amqp_url: str, exchange: str = MONITORING_EVENTS_EXCHANGE) -> None:
        if pika is None:
            raise RuntimeError("pika is required to use RabbitMqPublisher")
        self._connection = pika.BlockingConnection(pika.URLParameters(amqp_url))
        self._channel = self._connection.channel()
        self._exchange = exchange
        self._channel.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
        self._channel.confirm_delivery()

    def publish(self, event_type: str, payload: dict) -> bool:
        return bool(
            self._channel.basic_publish(
                exchange=self._exchange,
                routing_key=event_type,
                body=json.dumps(payload, default=str).encode("utf-8"),
                properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
                mandatory=True,
            )
        )


class RabbitMqDeadLetterPublisher:
    def __init__(self, amqp_url: str, exchange: str = MONITORING_EVENTS_EXCHANGE) -> None:
        if pika is None:
            raise RuntimeError("pika is required to use RabbitMqDeadLetterPublisher")
        self._connection = pika.BlockingConnection(pika.URLParameters(amqp_url))
        self._channel = self._connection.channel()
        self._exchange = exchange
        self._channel.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)

    def publish_dlq(self, event: DomainEvent, reason: str) -> None:
        self._channel.basic_publish(
            exchange=self._exchange,
            routing_key=MONITORING_DLQ_ROUTING_KEY,
            body=json.dumps({"event": event.__dict__, "reason": reason}, default=str).encode("utf-8"),
            properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
        )


class RabbitMqAlertWorkerConsumer:
    def __init__(self, amqp_url: str, worker: AlertWorker, dlq_publisher: RabbitMqDeadLetterPublisher, queue_name: str = "monitoring.alert-worker") -> None:
        if pika is None:
            raise RuntimeError("pika is required to use RabbitMqAlertWorkerConsumer")
        self._connection = pika.BlockingConnection(pika.URLParameters(amqp_url))
        self._channel = self._connection.channel()
        self._consumer = RetryingEventConsumer(worker, dlq_publisher)
        self._queue_name = queue_name
        self._bindings = [
            ("academic-events", "academic.ficha.opened"),
            ("academic-events", "academic.ficha.closed"),
            ("scheduling-events", "scheduling.schedule.published"),
            ("scheduling-events", "scheduling.class_session.cancelled"),
            ("actors-events", "actors.company_visit.completed"),
            (MONITORING_EVENTS_EXCHANGE, "monitoring.kpi.tick"),
        ]

    def start(self) -> None:
        self._channel.queue_declare(queue=self._queue_name, durable=True)
        for exchange, routing_key in self._bindings:
            self._channel.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
            self._channel.queue_bind(queue=self._queue_name, exchange=exchange, routing_key=routing_key)
        self._channel.basic_qos(prefetch_count=1)
        self._channel.basic_consume(queue=self._queue_name, on_message_callback=self._callback)
        self._channel.start_consuming()

    def _callback(self, channel, method, properties, body: bytes) -> None:  # type: ignore[no-untyped-def]
        try:
            envelope = json.loads(body.decode("utf-8"))
            self._consumer.handle_with_retry(DomainEvent.from_envelope(envelope))
            channel.basic_ack(delivery_tag=method.delivery_tag)
        except Exception:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


def run_forever(start: Callable[[], None]) -> None:
    start()
