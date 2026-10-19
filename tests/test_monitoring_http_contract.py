import base64
import json
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from monitoring.adapter.inbound.http.router import build_router


class NoopUseCase:
    def handle(self, cmd):  # pragma: no cover - exercised by legacy endpoint only
        return None


class FakeRepository:
    @property
    def session_factory(self):
        def factory():  # pragma: no cover - validation tests should not reach persistence
            raise AssertionError("unexpected persistence access")

        return factory


class FakeReadiness:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    def check(self) -> dict:
        return {
            "status": "ready" if self.ready else "not_ready",
            "components": {
                "database": {"status": "ok" if self.ready else "error", "detail": None},
                "broker": {"status": "ok" if self.ready else "error", "detail": None},
            },
        }


def _app(repository=None, readiness=None) -> FastAPI:
    app = FastAPI()
    app.include_router(build_router(NoopUseCase(), repository, readiness or FakeReadiness()), prefix="/api/v1")
    return app


def _token(features: list[str]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"features": features}).encode()).decode().rstrip("=")
    return f"Bearer header.{payload}.signature"


def test_contract_paths_are_registered() -> None:
    contract = Path(__file__).parents[2] / "design-software-docs" / "07-api" / "contracts" / "openapi" / "monitoring.yaml"
    expected = {
        f"/api/v1{line.split(':', 1)[0].strip()}"
        for line in contract.read_text(encoding="utf-8").splitlines()
        if line.startswith("  /")
    }
    registered = {route.path for route in _app().routes}

    assert expected.issubset(registered)
    assert "/api/v1/kpi-trackings" in registered
    assert "/api/v1/kpis" not in registered


def test_protected_endpoint_without_token_returns_401() -> None:
    response = TestClient(_app()).get("/api/v1/kpi-types")

    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "UNAUTHORIZED"


def test_health_and_ready_are_public_operability_endpoints() -> None:
    client = TestClient(_app())

    assert client.get("/api/v1/health").status_code == 200
    ready = client.get("/api/v1/ready")
    assert ready.status_code == 200
    assert ready.json()["components"]["database"]["status"] == "ok"


def test_readiness_failure_returns_503() -> None:
    response = TestClient(_app(readiness=FakeReadiness(False))).get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "not_ready"


def test_protected_endpoint_without_required_feature_returns_403() -> None:
    response = TestClient(_app()).get(
        "/api/v1/kpi-types",
        headers={"Authorization": _token(["MON_ALERT_VIEW"])},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "FORBIDDEN"


def test_tracking_session_invalid_percentage_returns_422() -> None:
    response = TestClient(_app(FakeRepository())).post(
        f"/api/v1/ficha-trackings/{UUID(int=1)}/tracking-sessions",
        headers={"Authorization": _token(["MON_TRACKING_SESSION_CREATE"])},
        json={
            "instructor_id": str(UUID(int=2)),
            "session_date": "2026-10-14",
            "session_type": "ACADEMIC",
            "curriculum_progress_percentage": 101,
        },
    )

    assert response.status_code == 422
