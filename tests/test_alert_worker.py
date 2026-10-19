from datetime import datetime, timezone
from uuid import UUID, uuid4

from monitoring.application.usecase.alert_worker import AlertWorker, OutboxRelay, RetryingEventConsumer, RetryPolicy
from monitoring.domain.model.event import AlertWorkerResult, DomainEvent


class InMemoryAlertRepository:
    def __init__(self) -> None:
        self.tracked_fichas: set[UUID] = set()
        self.kpis: set[tuple[UUID, str, UUID]] = set()
        self.alerts: set[tuple[UUID, str]] = set()
        self.outbox: list[dict] = []

    def initialize_ficha_tracking(self, event: DomainEvent) -> AlertWorkerResult:
        ficha_id = UUID(str(event.payload["ficha_id"]))
        idempotent = ficha_id in self.tracked_fichas
        self.tracked_fichas.add(ficha_id)
        return AlertWorkerResult(processed=True, idempotent=idempotent)

    def record_kpi_evaluations(self, event: DomainEvent, evaluations: list) -> AlertWorkerResult:
        alerts_created = 0
        kpis_recorded = 0
        produced: list[str] = []
        for evaluation in evaluations:
            kpi_key = (event.event_id, evaluation.kpi_type_code, evaluation.ficha_id)
            if kpi_key not in self.kpis:
                self.kpis.add(kpi_key)
                kpis_recorded += 1
            threshold = evaluation.threshold_value
            if threshold is None:
                threshold = 80 if evaluation.alert_type_code == "LOW_ATTENDANCE" else 70
            breached = evaluation.current_value < threshold
            alert_key = (event.event_id, evaluation.alert_type_code or "")
            if breached and alert_key not in self.alerts:
                self.alerts.add(alert_key)
                alerts_created += 1
                produced.append("monitoring.alert.triggered")
                self.outbox.append(
                    {
                        "id": str(uuid4()),
                        "event_type": "monitoring.alert.triggered",
                        "payload": {
                            "event_id": str(uuid4()),
                            "event_type": "monitoring.alert.triggered",
                            "correlation_id": str(event.correlation_id) if event.correlation_id else None,
                            "payload": {"source_event_id": str(event.event_id)},
                        },
                        "published": False,
                    }
                )
        return AlertWorkerResult(
            processed=True,
            idempotent=alerts_created == 0 and kpis_recorded == 0,
            alerts_created=alerts_created,
            kpis_recorded=kpis_recorded,
            produced_event_types=produced,
        )

    def list_pending(self, limit: int) -> list[dict]:
        return [item for item in self.outbox if not item["published"]][:limit]

    def mark_published(self, outbox_id: str) -> None:
        for item in self.outbox:
            if item["id"] == outbox_id:
                item["published"] = True


class FakePublisher:
    def __init__(self, ack: bool = True) -> None:
        self.ack = ack
        self.published: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> bool:
        self.published.append((event_type, payload))
        return self.ack


class FakeDlq:
    def __init__(self) -> None:
        self.messages: list[tuple[DomainEvent, str]] = []

    def publish_dlq(self, event: DomainEvent, reason: str) -> None:
        self.messages.append((event, reason))


class FailingWorker:
    def handle(self, event: DomainEvent) -> AlertWorkerResult:
        raise RuntimeError("persistent failure")


def _event(event_type: str = "academic.ficha.opened", **payload) -> DomainEvent:
    return DomainEvent(
        event_id=uuid4(),
        event_type=event_type,
        version="1.0",
        timestamp=datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc),
        source_service="test",
        correlation_id=uuid4(),
        payload={"ficha_id": str(uuid4()), **payload},
    )


def test_ficha_opened_initializes_tracking_generates_alert_and_outbox_once() -> None:
    repository = InMemoryAlertRepository()
    worker = AlertWorker(repository)
    event = _event(attendance_percentage=72)

    first = worker.handle(event)
    second = worker.handle(event)

    assert first.kpis_recorded == 1
    assert first.alerts_created == 1
    assert first.produced_event_types == ["monitoring.alert.triggered"]
    assert second.idempotent is True
    assert len(repository.outbox) == 1
    assert repository.outbox[0]["payload"]["correlation_id"] == str(event.correlation_id)


def test_worker_ignores_events_without_sufficient_kpi_data() -> None:
    result = AlertWorker(InMemoryAlertRepository()).handle(_event("scheduling.schedule.published"))

    assert result.processed is True
    assert result.ignored_reason == "insufficient_kpi_data"


def test_outbox_relay_marks_published_only_after_ack() -> None:
    repository = InMemoryAlertRepository()
    event = _event(attendance_percentage=72)
    AlertWorker(repository).handle(event)

    nack_publisher = FakePublisher(ack=False)
    assert OutboxRelay(repository, nack_publisher).publish_pending_once() == 0
    assert repository.outbox[0]["published"] is False

    ack_publisher = FakePublisher(ack=True)
    assert OutboxRelay(repository, ack_publisher).publish_pending_once() == 1
    assert repository.outbox[0]["published"] is True


def test_persistent_consumer_failure_goes_to_monitoring_dlq() -> None:
    dlq = FakeDlq()
    consumer = RetryingEventConsumer(FailingWorker(), dlq, RetryPolicy(attempts=2, backoff_seconds=(0,)))
    event = _event("monitoring.kpi.tick")

    result = consumer.handle_with_retry(event)

    assert result.processed is False
    assert result.ignored_reason == "sent_to_dlq"
    assert len(dlq.messages) == 1
    assert dlq.messages[0][0].event_id == event.event_id
