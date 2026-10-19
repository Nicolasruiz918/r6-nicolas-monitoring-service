import unittest
from decimal import Decimal
from uuid import uuid4

from monitoring.application.port.inbound.record_kpi import RecordKpiCommand
from monitoring.application.usecase.record_kpi import RecordKpi
from monitoring.domain.model.alert import GeneratedAlertDraft
from monitoring.domain.model.kpi import KpiMeasurement


class FakeRepo:
    def __init__(self) -> None:
        self.saved = 0
        self.measurements: list[KpiMeasurement] = []

    def save(self, measurement: KpiMeasurement) -> None:
        self.saved += 1
        self.measurements.append(measurement)

    def list_by_ficha(self, ficha_id, limit: int = 100) -> list[KpiMeasurement]:
        return self.measurements[:limit]


class RecordKpiTest(unittest.TestCase):
    def test_records_and_persists(self) -> None:
        repo = FakeRepo()
        use_case = RecordKpi(repo)
        ficha_id = uuid4()
        m = use_case.handle(RecordKpiCommand(ficha_id, "ATTENDANCE", Decimal("87.5")))
        self.assertEqual(m.kpi_code, "ATTENDANCE")
        self.assertEqual(m.ficha_id, ficha_id)
        self.assertEqual(m.value, Decimal("87.5"))
        self.assertEqual(repo.saved, 1)

    def test_generated_alert_rejects_two_affected_actor_types(self) -> None:
        with self.assertRaises(ValueError):
            GeneratedAlertDraft(
                ficha_tracking_id=uuid4(),
                alert_type_id=uuid4(),
                risk_level_id=uuid4(),
                source_event_id=uuid4(),
                description="Invalid alert",
                affected_learner_id=uuid4(),
                affected_instructor_id=uuid4(),
            )


if __name__ == "__main__":
    unittest.main()
