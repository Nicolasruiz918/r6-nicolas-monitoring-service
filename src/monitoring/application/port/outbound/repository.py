from typing import Protocol
from uuid import UUID

from monitoring.domain.model.kpi import KpiMeasurement


class KpiRepository(Protocol):
    """Puerto de salida (lo implementa el adapter de persistencia)."""
    def save(self, measurement: KpiMeasurement) -> None: ...
    def list_by_ficha(self, ficha_id: UUID, limit: int = 100) -> list[KpiMeasurement]: ...
