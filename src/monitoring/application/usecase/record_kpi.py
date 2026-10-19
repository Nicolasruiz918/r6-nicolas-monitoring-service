from datetime import datetime, timezone
from decimal import Decimal

from monitoring.application.port.inbound.record_kpi import RecordKpiCommand
from monitoring.application.port.outbound.repository import KpiRepository
from monitoring.domain.model.kpi import KpiMeasurement


class RecordKpi:
    def __init__(self, repo: KpiRepository) -> None:
        self._repo = repo

    def handle(self, cmd: RecordKpiCommand) -> KpiMeasurement:
        measurement = KpiMeasurement(
            ficha_id=cmd.ficha_id,
            kpi_code=cmd.kpi_code,
            value=Decimal(str(cmd.value)),
            threshold_value=Decimal(str(cmd.threshold_value)),
            status_code=cmd.status_code,
            measured_at=datetime.now(timezone.utc),
        )
        self._repo.save(measurement)
        return measurement
