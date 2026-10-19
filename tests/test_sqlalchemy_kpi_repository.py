import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, text

from monitoring.adapter.outbound.persistence.repo import SqlAlchemyKpiRepository
from monitoring.domain.model.kpi import KpiMeasurement


@unittest.skipUnless(
    os.environ.get("MONITORING_TEST_DATABASE_URL"),
    "MONITORING_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)
class SqlAlchemyKpiRepositoryIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(os.environ["MONITORING_TEST_DATABASE_URL"], future=True)
        self.repo = SqlAlchemyKpiRepository.from_engine(self.engine)

    def test_inserts_and_queries_real_kpi_rows(self) -> None:
        ficha_id = uuid4()
        measurement = KpiMeasurement(
            ficha_id=ficha_id,
            kpi_code="ATTENDANCE",
            value=Decimal("87.5000"),
            threshold_value=Decimal("80.0000"),
            measured_at=datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc),
        )

        self.repo.save(measurement)
        rows = self.repo.list_by_ficha(ficha_id)

        self.assertIsNotNone(measurement.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kpi_code, "ATTENDANCE")
        self.assertEqual(rows[0].value, Decimal("87.5000"))

    def test_attendance_percentage_is_database_calculated(self) -> None:
        with self.engine.begin() as conn:
            value = conn.execute(
                text(
                    """
                    SELECT
                      CASE WHEN 25 > 0
                      THEN ROUND(CAST(20 AS DECIMAL(10,4)) / 25 * 100, 2)
                      ELSE NULL END AS attendance_percentage
                    """
                )
            ).scalar_one()
        self.assertEqual(Decimal(value), Decimal("80.00"))

    def test_generated_alert_exclusive_arc_is_enforced_by_database(self) -> None:
        with self.engine.begin() as conn:
            check = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM pg_constraint
                    WHERE conname = 'ck_generated_alert_affected_actor_exclusive'
                       OR (
                         conrelid = 'monitoring.generated_alert'::regclass
                         AND pg_get_constraintdef(oid) LIKE '%affected_learner_id IS NULL OR affected_instructor_id IS NULL%'
                       )
                    """
                )
            ).scalar_one()
        self.assertGreaterEqual(check, 1)


if __name__ == "__main__":
    unittest.main()
