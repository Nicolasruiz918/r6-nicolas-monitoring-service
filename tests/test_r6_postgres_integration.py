"""PostgreSQL integration coverage for R6.

Run with MONITORING_R6_TEST_DATABASE_URL pointing to an isolated PostgreSQL database.
The test deliberately uses the migration SQL files shipped by monitoring-db; the
PowerShell guide runs the same files through Liquibase in Docker.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

DATABASE_URL = os.getenv("MONITORING_R6_TEST_DATABASE_URL")
ALLOW_SCHEMA_RESET = os.getenv("MONITORING_R6_TEST_ALLOW_SCHEMA_RESET") == "1"
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="MONITORING_R6_TEST_DATABASE_URL is not configured")

DB_REPO = Path(os.getenv("MONITORING_DB_REPO", Path(__file__).parents[2] / "design-software-monitoring-db"))
ALTER = DB_REPO / "01_ddl" / "04_alter"
ROLLBACK = DB_REPO / "05_rollbacks" / "01_ddl" / "04_alter"


def sql_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _safe_local_database(url: str) -> bool:
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    return parsed.host in {"localhost", "127.0.0.1", "::1"} and parsed.database is not None and "r6" in parsed.database.lower()


@pytest.fixture()
def database():
    if not ALLOW_SCHEMA_RESET:
        pytest.skip("R6 schema reset is disabled; set MONITORING_R6_TEST_ALLOW_SCHEMA_RESET=1 for an isolated DB")
    if not _safe_local_database(DATABASE_URL):
        pytest.fail("R6 PostgreSQL integration requires a local disposable database whose name contains 'r6'")
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS monitoring CASCADE")
        connection.execute("CREATE SCHEMA monitoring")
        connection.execute("CREATE TABLE monitoring.improvement_plan (id uuid PRIMARY KEY, specific_actions text NOT NULL)")
        connection.execute("INSERT INTO monitoring.improvement_plan VALUES ('00000000-0000-0000-0000-000000000001', %s)", ("Tutoría semanal — acción 1\nAcción 2",))
        yield connection
        connection.execute("DROP SCHEMA IF EXISTS monitoring CASCADE")


def columns(connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='monitoring' AND table_name='improvement_plan'")}


def test_r6_expand_backfill_contract_and_rollbacks(database) -> None:
    connection = database
    original = connection.execute("SELECT specific_actions FROM monitoring.improvement_plan").fetchone()[0]

    connection.execute(sql_file(ALTER / "r6_expand_specific_actions_v2.sql"))
    assert {"specific_actions", "specific_actions_v2"} <= columns(connection)

    # Compatible CRUD during EXPAND: legacy value remains the HTTP contract.
    connection.execute("INSERT INTO monitoring.improvement_plan VALUES ('00000000-0000-0000-0000-000000000002', %s)", ("Plan nuevo",))
    connection.execute(sql_file(ALTER / "r6_backfill_specific_actions_v2.sql"))
    assert connection.execute("SELECT count(*) FROM monitoring.improvement_plan WHERE specific_actions_v2 IS NOT NULL").fetchone()[0] == 2
    assert connection.execute("SELECT count(*) FROM monitoring.improvement_plan WHERE specific_actions_v2 IS NULL OR (specific_actions_v2->'actions'->0->>'text') IS DISTINCT FROM specific_actions").fetchone()[0] == 0

    # A second connection can still read while the compatible writes run.
    with psycopg.connect(DATABASE_URL) as continuity:
        for value in ("Actualización compatible", "Actualización Unicode ✅"):
            connection.execute("UPDATE monitoring.improvement_plan SET specific_actions=%s, specific_actions_v2=jsonb_build_object('version',1,'actions',jsonb_build_array(jsonb_build_object('text',CAST(%s AS text)))) WHERE id='00000000-0000-0000-0000-000000000002'", (value, value))
            assert continuity.execute("SELECT specific_actions FROM monitoring.improvement_plan WHERE id='00000000-0000-0000-0000-000000000002'").fetchone()[0] == value

    # Rollback BACKFILL restores the legacy value before EXPAND is removed.
    connection.execute(sql_file(ROLLBACK / "r6_backfill_specific_actions_v2.rollback.sql"))
    assert connection.execute("SELECT count(*) FROM monitoring.improvement_plan WHERE specific_actions_v2 IS NOT NULL").fetchone()[0] == 2

    # Rollback EXPAND restores the original structure and values.
    connection.execute(sql_file(ROLLBACK / "r6_expand_specific_actions_v2.rollback.sql"))
    assert columns(connection) == {"id", "specific_actions"}
    assert connection.execute("SELECT count(*) FROM monitoring.improvement_plan WHERE specific_actions IS NOT NULL").fetchone()[0] == 2
    # Recreate the transition for the controlled CONTRACT path.
    connection.execute(sql_file(ALTER / "r6_expand_specific_actions_v2.sql"))
    connection.execute(sql_file(ALTER / "r6_backfill_specific_actions_v2.sql"))
    connection.execute(sql_file(ALTER / "r6_contract_specific_actions_v2.sql"))
    assert columns(connection) == {"id", "specific_actions"}
    assert connection.execute("SELECT specific_actions->'actions'->0->>'text' FROM monitoring.improvement_plan WHERE id='00000000-0000-0000-0000-000000000001'").fetchone()[0] == original

    # Rollback CONTRACT recreates TEXT plus the transitional JSONB column.
    connection.execute(sql_file(ROLLBACK / "r6_contract_specific_actions_v2.rollback.sql"))
    assert {"specific_actions", "specific_actions_v2"} <= columns(connection)
    assert connection.execute("SELECT specific_actions FROM monitoring.improvement_plan WHERE id='00000000-0000-0000-0000-000000000001'").fetchone()[0] == original
