from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID


@dataclass
class KpiMeasurement:
    """Entidad de dominio pura (sin ORM)."""
    ficha_id: UUID
    kpi_code: str
    value: Decimal
    measured_at: datetime
    threshold_value: Decimal = Decimal("0")
    status_code: str = "ON_TRACK"
    period_start: date | None = None
    period_end: date | None = None
    assigned_instructor_id: UUID | None = None
    id: UUID | None = None

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("KPI value cannot be negative")
        if self.threshold_value < 0:
            raise ValueError("KPI threshold cannot be negative")
        self.kpi_code = self.kpi_code.upper()
        self.status_code = self.status_code.upper()
