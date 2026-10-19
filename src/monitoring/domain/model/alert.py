from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class GeneratedAlertDraft:
    ficha_tracking_id: UUID
    alert_type_id: UUID
    risk_level_id: UUID
    source_event_id: UUID
    description: str
    affected_learner_id: UUID | None = None
    affected_instructor_id: UUID | None = None
    triggered_value: Decimal | None = None
    threshold_value: Decimal | None = None

    def __post_init__(self) -> None:
        if self.affected_learner_id is not None and self.affected_instructor_id is not None:
            raise ValueError(
                "generated_alert can affect either a learner or an instructor, not both"
            )
