from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from monitoring.application.port.outbound.alert_worker_repository import AlertWorkerRepository, OutboxRepository
from monitoring.domain.model.event import AlertWorkerResult, DomainEvent, KpiEvaluation
from monitoring.observability import get_meter_counter, log_event

SUPPORTED_EVENT_TYPES = {
    "academic.ficha.opened",
    "academic.ficha.closed",
    "scheduling.schedule.published",
    "scheduling.class_session.cancelled",
    "actors.company_visit.completed",
    "monitoring.kpi.tick",
}


class MessagePublisher(Protocol):
    def publish(self, event_type: str, payload: dict) -> bool:
        ...


class DeadLetterPublisher(Protocol):
    def publish_dlq(self, event: DomainEvent, reason: str) -> None:
        ...


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 4
    backoff_seconds: tuple[float, ...] = (1.0, 4.0, 16.0)


class AlertWorker:
    def __init__(self, repository: AlertWorkerRepository) -> None:
        self._repository = repository
        self._events_counter = get_meter_counter("monitoring_worker_events_total", "Events processed by alert-worker")
        self._alerts_counter = get_meter_counter("monitoring_alerts_created_total", "Alerts created by alert-worker")

    def handle(self, event: DomainEvent) -> AlertWorkerResult:
        log_event(
            "alert_worker_event_received",
            event_id=str(event.event_id),
            correlation_id=str(event.correlation_id) if event.correlation_id else None,
            event_type=event.event_type,
        )
        self._events_counter(1, {"event_type": event.event_type})
        if event.event_type not in SUPPORTED_EVENT_TYPES:
            return AlertWorkerResult(processed=False, ignored_reason="unsupported_event_type")
        if event.event_type == "academic.ficha.closed":
            return AlertWorkerResult(processed=True, ignored_reason="closed_ficha_recalculation_not_required")
        if event.event_type == "academic.ficha.opened":
            init_result = self._repository.initialize_ficha_tracking(event)
            evaluations = self._extract_evaluations(event)
            if not evaluations:
                return init_result
            kpi_result = self._repository.record_kpi_evaluations(event, evaluations)
            result = _merge_results(init_result, kpi_result)
            self._alerts_counter(result.alerts_created, {"event_type": event.event_type})
            return result
        evaluations = self._extract_evaluations(event)
        if not evaluations:
            return AlertWorkerResult(processed=True, ignored_reason="insufficient_kpi_data")
        result = self._repository.record_kpi_evaluations(event, evaluations)
        self._alerts_counter(result.alerts_created, {"event_type": event.event_type})
        return result

    def _extract_evaluations(self, event: DomainEvent) -> list[KpiEvaluation]:
        payload = event.payload
        ficha_id = _uuid(payload.get("ficha_id") or payload.get("ficha_tracking_id"))
        if ficha_id is None:
            return []

        evaluations: list[KpiEvaluation] = []
        instructor_id = _uuid(payload.get("assigned_instructor_id") or payload.get("instructor_id"))
        learner_id = _uuid(payload.get("learner_id") or payload.get("affected_learner_id"))
        if event.event_type == "actors.company_visit.completed":
            evaluations.append(
                KpiEvaluation(
                    ficha_id=ficha_id,
                    kpi_type_code="PRODUCTIVE_STAGE_PROGRESS",
                    current_value=float(payload.get("productive_stage_progress", payload.get("progress_percentage", 100))),
                    alert_type_code="PRODUCTIVE_STAGE_DELAY",
                    assigned_instructor_id=instructor_id,
                    affected_learner_id=learner_id,
                )
            )
            return evaluations

        if "attendance_percentage" in payload:
            evaluations.append(
                KpiEvaluation(
                    ficha_id=ficha_id,
                    kpi_type_code="ATTENDANCE",
                    current_value=float(payload["attendance_percentage"]),
                    alert_type_code="LOW_ATTENDANCE",
                    assigned_instructor_id=instructor_id,
                    affected_learner_id=learner_id,
                )
            )
        if "curriculum_progress_percentage" in payload:
            evaluations.append(
                KpiEvaluation(
                    ficha_id=ficha_id,
                    kpi_type_code="CURRICULUM_PROGRESS",
                    current_value=float(payload["curriculum_progress_percentage"]),
                    alert_type_code="CURRICULUM_DELAY",
                    assigned_instructor_id=instructor_id,
                )
            )
        if "dropout_risk_count" in payload:
            evaluations.append(
                KpiEvaluation(
                    ficha_id=ficha_id,
                    kpi_type_code="DROPOUT_RISK",
                    current_value=float(payload["dropout_risk_count"]),
                    alert_type_code="HIGH_DROPOUT_RISK",
                    assigned_instructor_id=instructor_id,
                    affected_learner_id=learner_id,
                )
            )
        if "productive_stage_progress" in payload:
            evaluations.append(
                KpiEvaluation(
                    ficha_id=ficha_id,
                    kpi_type_code="PRODUCTIVE_STAGE_PROGRESS",
                    current_value=float(payload["productive_stage_progress"]),
                    alert_type_code="PRODUCTIVE_STAGE_DELAY",
                    assigned_instructor_id=instructor_id,
                    affected_learner_id=learner_id,
                )
            )
        if event.event_type == "monitoring.kpi.tick":
            for item in payload.get("measurements", []):
                item_ficha = _uuid(item.get("ficha_id") or ficha_id)
                if item_ficha is None:
                    continue
                evaluations.append(
                    KpiEvaluation(
                        ficha_id=item_ficha,
                        kpi_type_code=str(item["kpi_type_code"]),
                        current_value=float(item["current_value"]),
                        threshold_value=float(item["threshold_value"]) if item.get("threshold_value") is not None else None,
                        alert_type_code=item.get("alert_type_code"),
                        assigned_instructor_id=_uuid(item.get("assigned_instructor_id")) or instructor_id,
                    )
                )
        return evaluations


class OutboxRelay:
    def __init__(self, repository: OutboxRepository, publisher: MessagePublisher, batch_size: int = 50) -> None:
        self._repository = repository
        self._publisher = publisher
        self._batch_size = batch_size

    def publish_pending_once(self) -> int:
        published = 0
        for item in self._repository.list_pending(self._batch_size):
            if self._publisher.publish(item["event_type"], item["payload"]):
                self._repository.mark_published(str(item["id"]))
                log_event(
                    "outbox_event_published",
                    event_id=str(item["payload"].get("event_id")),
                    correlation_id=item["payload"].get("correlation_id"),
                    event_type=item["event_type"],
                )
                published += 1
        return published


class RetryingEventConsumer:
    def __init__(self, worker: AlertWorker, dlq: DeadLetterPublisher, retry_policy: RetryPolicy | None = None) -> None:
        self._worker = worker
        self._dlq = dlq
        self._retry_policy = retry_policy or RetryPolicy()

    def handle_with_retry(self, event: DomainEvent) -> AlertWorkerResult:
        last_error: Exception | None = None
        for attempt in range(self._retry_policy.attempts):
            try:
                return self._worker.handle(event)
            except Exception as exc:  # noqa: BLE001 - message consumers must trap persistent failures.
                last_error = exc
                if attempt < self._retry_policy.attempts - 1:
                    delay = self._retry_policy.backoff_seconds[min(attempt, len(self._retry_policy.backoff_seconds) - 1)]
                    time.sleep(delay)
        reason = str(last_error) if last_error else "unknown error"
        log_event(
            "alert_worker_event_sent_to_dlq",
            event_id=str(event.event_id),
            correlation_id=str(event.correlation_id) if event.correlation_id else None,
            reason=reason,
        )
        self._dlq.publish_dlq(event, reason)
        return AlertWorkerResult(processed=False, ignored_reason="sent_to_dlq")


def _uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _merge_results(left: AlertWorkerResult, right: AlertWorkerResult) -> AlertWorkerResult:
    return AlertWorkerResult(
        processed=left.processed or right.processed,
        idempotent=left.idempotent and right.idempotent,
        alerts_created=left.alerts_created + right.alerts_created,
        kpis_recorded=left.kpis_recorded + right.kpis_recorded,
        ignored_reason=right.ignored_reason or left.ignored_reason,
        produced_event_types=[*left.produced_event_types, *right.produced_event_types],
    )
