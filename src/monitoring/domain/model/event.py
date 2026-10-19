from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class DomainEvent:
    event_id: UUID
    event_type: str
    version: str
    timestamp: datetime
    source_service: str
    payload: dict[str, Any]
    correlation_id: UUID | None = None

    @classmethod
    def from_envelope(cls, envelope: dict[str, Any]) -> "DomainEvent":
        timestamp = envelope.get("timestamp")
        parsed_timestamp = (
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if isinstance(timestamp, str)
            else datetime.now(timezone.utc)
        )
        return cls(
            event_id=UUID(str(envelope["event_id"])),
            event_type=str(envelope["event_type"]),
            version=str(envelope.get("version", "1.0")),
            timestamp=parsed_timestamp,
            source_service=str(envelope.get("source_service", "unknown")),
            payload=dict(envelope.get("payload") or {}),
            correlation_id=UUID(str(envelope["correlation_id"])) if envelope.get("correlation_id") else None,
        )


@dataclass(frozen=True)
class KpiEvaluation:
    ficha_id: UUID
    kpi_type_code: str
    current_value: float
    threshold_value: float | None = None
    alert_type_code: str | None = None
    assigned_instructor_id: UUID | None = None
    affected_learner_id: UUID | None = None
    affected_instructor_id: UUID | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None


@dataclass
class AlertWorkerResult:
    processed: bool
    idempotent: bool = False
    alerts_created: int = 0
    kpis_recorded: int = 0
    ignored_reason: str | None = None
    produced_event_types: list[str] = field(default_factory=list)
