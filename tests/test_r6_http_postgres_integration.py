"""Real HTTP + PostgreSQL coverage for the R6 improvement-plan migration.

This test is intentionally opt-in. It refuses to run unless the database is
local and MONITORING_HTTP_TEST_ALLOW_RESET=1 is explicitly set. The database
must be a disposable PostgreSQL instance initialized with the complete
monitoring-db changelog, not a shared or production database.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("sqlalchemy")

from monitoring.adapter.inbound.http.router import build_router  # noqa: E402
from monitoring.adapter.outbound.persistence.repo import SqlAlchemyKpiRepository  # noqa: E402


DATABASE_URL = os.getenv("MONITORING_HTTP_TEST_DATABASE_URL")
ALLOW_RESET = os.getenv("MONITORING_HTTP_TEST_ALLOW_RESET") == "1"
DB_REPO = Path(os.getenv("MONITORING_DB_REPO", Path(__file__).parents[2] / "design-software-monitoring-db"))
ALTER = DB_REPO / "01_ddl" / "04_alter"
ROLLBACK = DB_REPO / "05_rollbacks" / "01_ddl" / "04_alter"


def _safe_local_database(url: str | None) -> bool:
    if not url:
        return False
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    return parsed.host in {"localhost", "127.0.0.1", "::1"} and parsed.database is not None and "r6" in parsed.database.lower()


pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="MONITORING_HTTP_TEST_DATABASE_URL is not configured",
)


class NoopUseCase:
    def handle(self, command):  # pragma: no cover - unrelated endpoint
        return None


class Ready:
    def check(self) -> dict:
        return {"status": "ready", "components": {"database": {"status": "ok"}}}


def _token() -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"features": ["MON_IMPROVEMENT_PLAN_MANAGE"]}).encode()).decode().rstrip("=")
    return f"Bearer header.{payload}.signature"


def _run_sql(connection, path: Path) -> None:
    connection.execute(text(path.read_text(encoding="utf-8")))


@pytest.fixture()
def http_database():
    if not ALLOW_RESET:
        pytest.skip("HTTP integration reset is disabled; set MONITORING_HTTP_TEST_ALLOW_RESET=1 for an isolated DB")
    if not _safe_local_database(DATABASE_URL):
        pytest.fail("HTTP integration requires a local disposable database whose name contains 'r6'")

    engine = create_engine(DATABASE_URL, future=True)
    with engine.begin() as connection:
        required = connection.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema='monitoring' AND table_name IN ('ficha_tracking','improvement_plan')")
        ).scalar_one()
        if required != 2:
            pytest.fail("The HTTP test requires the complete monitoring domain schema")

        data_type = connection.execute(
            text("SELECT data_type FROM information_schema.columns WHERE table_schema='monitoring' AND table_name='improvement_plan' AND column_name='specific_actions'")
        ).scalar_one()
        has_v2 = connection.execute(
            text("SELECT count(*) FROM information_schema.columns WHERE table_schema='monitoring' AND table_name='improvement_plan' AND column_name='specific_actions_v2'")
        ).scalar_one()
        if data_type == "jsonb":
            _run_sql(connection, ROLLBACK / "r6_contract_specific_actions_v2.rollback.sql")
            has_v2 = 1
        if has_v2:
            _run_sql(connection, ROLLBACK / "r6_expand_specific_actions_v2.rollback.sql")

        status_id = connection.execute(text("SELECT id FROM monitoring.kpi_status WHERE code='ON_TRACK' LIMIT 1")).scalar_one_or_none()
        if status_id is None:
            pytest.fail("The complete monitoring domain seed must contain kpi_status=ON_TRACK")

        ficha_id = uuid4()
        tracking_id = uuid4()
        connection.execute(
            text("INSERT INTO monitoring.ficha_tracking (id, ficha_id, assigned_instructor_id, overall_status_id, active_alert_count, created_at, updated_at) VALUES (:id, :ficha_id, :instructor_id, :status_id, 0, now(), now())"),
            {"id": tracking_id, "ficha_id": ficha_id, "instructor_id": uuid4(), "status_id": status_id},
        )

    yield engine, tracking_id

    with engine.begin() as connection:
        data_type = connection.execute(
            text("SELECT data_type FROM information_schema.columns WHERE table_schema='monitoring' AND table_name='improvement_plan' AND column_name='specific_actions'")
        ).scalar_one()
        has_v2 = connection.execute(
            text("SELECT count(*) FROM information_schema.columns WHERE table_schema='monitoring' AND table_name='improvement_plan' AND column_name='specific_actions_v2'")
        ).scalar_one()
        if data_type == "text" and has_v2:
            _run_sql(connection, ALTER / "r6_contract_specific_actions_v2.sql")
    engine.dispose()


def test_r6_http_crud_during_transition_and_after_contract(http_database) -> None:
    engine, tracking_id = http_database
    repository = SqlAlchemyKpiRepository.from_engine(engine)
    app = FastAPI()
    app.include_router(build_router(NoopUseCase(), repository, Ready()), prefix="/api/v1")
    client = TestClient(app)
    headers = {"Authorization": _token()}
    base = {
        "ficha_tracking_id": str(tracking_id),
        "title": "R6 HTTP plan",
        "description": "Real PostgreSQL compatibility",
        "specific_actions": "Acción original ✅",
        "due_date": "2026-12-31",
    }

    before_expand = client.post("/api/v1/improvement-plans", headers=headers, json=base)
    assert before_expand.status_code == 201, before_expand.text
    original = before_expand.json()
    assert original["specific_actions"] == base["specific_actions"]
    assert client.get(f"/api/v1/improvement-plans/{original['id']}", headers=headers).json()["specific_actions"] == base["specific_actions"]

    with engine.begin() as connection:
        _run_sql(connection, ALTER / "r6_expand_specific_actions_v2.sql")
        _run_sql(connection, ALTER / "r6_backfill_specific_actions_v2.sql")

    transitional = {**base, "title": "R6 HTTP transition", "specific_actions": "Acción durante BACKFILL"}
    created = client.post("/api/v1/improvement-plans", headers=headers, json=transitional)
    assert created.status_code == 201, created.text
    plan_id = created.json()["id"]
    updated = client.put(
        f"/api/v1/improvement-plans/{plan_id}",
        headers=headers,
        json={"specific_actions": "Acción actualizada ✅"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["specific_actions"] == "Acción actualizada ✅"

    listed = client.get("/api/v1/improvement-plans", headers=headers).json()
    assert listed["pagination"]["total"] >= 2
    assert {row["specific_actions"] for row in listed["data"]} >= {base["specific_actions"], "Acción actualizada ✅"}

    with engine.begin() as connection:
        mismatch = connection.execute(
            text("SELECT count(*) FROM monitoring.improvement_plan WHERE specific_actions_v2->'actions'->0->>'text' <> specific_actions")
        ).scalar_one()
        assert mismatch == 0
        _run_sql(connection, ALTER / "r6_contract_specific_actions_v2.sql")

    after_contract = client.get(f"/api/v1/improvement-plans/{plan_id}", headers=headers)
    assert after_contract.status_code == 200, after_contract.text
    assert after_contract.json()["specific_actions"] == "Acción actualizada ✅"
    after_update = client.put(
        f"/api/v1/improvement-plans/{plan_id}",
        headers=headers,
        json={"specific_actions": "Acción posterior a CONTRACT"},
    )
    assert after_update.status_code == 200, after_update.text
    assert client.get("/api/v1/improvement-plans", headers=headers).json()["pagination"]["total"] >= 2

    with engine.begin() as connection:
        _run_sql(connection, ROLLBACK / "r6_contract_specific_actions_v2.rollback.sql")
        restored = connection.execute(
            text("SELECT count(*) FROM monitoring.improvement_plan WHERE specific_actions IS NOT NULL AND specific_actions_v2->'actions'->0->>'text' = specific_actions")
        ).scalar_one()
        total = connection.execute(text("SELECT count(*) FROM monitoring.improvement_plan")).scalar_one()
        assert restored == total
