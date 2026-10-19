from __future__ import annotations

from monitoring.adapter.outbound.messaging.amqp import (
    RabbitMqAlertWorkerConsumer,
    RabbitMqDeadLetterPublisher,
    RabbitMqPublisher,
    run_forever,
)
from monitoring.adapter.outbound.persistence.repo import SqlAlchemyAlertWorkerRepository
from monitoring.application.usecase.alert_worker import AlertWorker, OutboxRelay
from monitoring.config import Settings
from monitoring.observability import configure_logging, configure_otel, log_event


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings)
    configure_otel(settings)
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for alert-worker")
    if not settings.amqp_url:
        raise RuntimeError("AMQP_URL is required for alert-worker")
    repository = SqlAlchemyAlertWorkerRepository.from_url(settings.database_url)

    mode = settings.alert_worker_mode
    log_event("alert_worker_starting", correlation_id=None, mode=mode)
    if mode == "relay":
        relay = OutboxRelay(repository, RabbitMqPublisher(settings.amqp_url))
        published = relay.publish_pending_once()
        log_event("outbox_relay_completed", correlation_id=None, published=published)
        return

    worker = AlertWorker(repository)
    dlq = RabbitMqDeadLetterPublisher(settings.amqp_url)
    consumer = RabbitMqAlertWorkerConsumer(settings.amqp_url, worker, dlq)
    run_forever(consumer.start)


if __name__ == "__main__":
    main()
