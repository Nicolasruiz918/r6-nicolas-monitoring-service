from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from monitoring.domain.model.kpi import KpiMeasurement


@dataclass
class RecordKpiCommand:
    ficha_id: UUID
    kpi_code: str
    value: Decimal
    threshold_value: Decimal = Decimal("0")
    status_code: str = "ON_TRACK"


class RecordKpiUseCase(Protocol):
    """Puerto de entrada."""
    def handle(self, cmd: RecordKpiCommand) -> KpiMeasurement: ...
