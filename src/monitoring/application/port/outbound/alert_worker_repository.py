from __future__ import annotations

from typing import Protocol

from monitoring.domain.model.event import AlertWorkerResult, DomainEvent, KpiEvaluation


class AlertWorkerRepository(Protocol):
    def initialize_ficha_tracking(self, event: DomainEvent) -> AlertWorkerResult:
        ...

    def record_kpi_evaluations(self, event: DomainEvent, evaluations: list[KpiEvaluation]) -> AlertWorkerResult:
        ...


class OutboxRepository(Protocol):
    def list_pending(self, limit: int) -> list[dict]:
        ...

    def mark_published(self, outbox_id: str) -> None:
        ...
