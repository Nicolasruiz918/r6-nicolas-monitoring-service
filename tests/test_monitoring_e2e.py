from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from monitoring.application.usecase.alert_worker import AlertWorker
from monitoring.domain.model.event import AlertWorkerResult
from monitoring.domain.model.event import DomainEvent


class E2ERepository:
    def __init__(self) -> None:
        self.outbox = []

    def initialize_ficha_tracking(self, event: DomainEvent) -> AlertWorkerResult:
        return AlertWorkerResult(processed=True)

    def record_kpi_evaluations(self, event: DomainEvent, evaluations: list) -> AlertWorkerResult:
        self.outbox.append({"event_type": "monitoring.alert.triggered", "source_event_id": str(event.event_id)})
        return AlertWorkerResult(
            processed=True,
            alerts_created=1,
            kpis_recorded=len(evaluations),
            produced_event_types=["monitoring.alert.triggered"],
        )


def _event(event_type: str, **payload) -> DomainEvent:
    return DomainEvent(
        event_id=uuid4(),
        event_type=event_type,
        version="1.0",
        timestamp=datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc),
        source_service="test",
        correlation_id=uuid4(),
        payload={"ficha_id": str(uuid4()), **payload},
    )


def test_minimal_monitoring_flow_event_alert_resolve_and_plan() -> None:
    repository = E2ERepository()
    event = _event("academic.ficha.opened", attendance_percentage=72)

    worker_result = AlertWorker(repository).handle(event)

    app = _fake_monitoring_api(repository, event)
    client = TestClient(app)
    alerts = client.get("/api/v1/generated-alerts").json()["data"]
    alert_id = alerts[0]["id"]

    resolved = client.put(f"/api/v1/generated-alerts/{alert_id}/resolve", json={"resolution_notes": "Reviewed"}).json()
    plan = client.post(
        "/api/v1/improvement-plans",
        json={
            "ficha_tracking_id": str(event.payload["ficha_id"]),
            "generated_alert_id": alert_id,
            "title": "Attendance recovery",
            "description": "Recover attendance",
            "specific_actions": "Schedule follow-up",
            "due_date": "2026-10-30",
        },
    ).json()

    assert worker_result.alerts_created == 1
    assert alerts[0]["source_event_id"] == str(event.event_id)
    assert resolved["is_resolved"] is True
    assert plan["status"] == "PENDING"


def _fake_monitoring_api(repository: E2ERepository, event: DomainEvent) -> FastAPI:
    app = FastAPI()
    alert_id = str(uuid4())
    plans = []
    alert = {
        "id": alert_id,
        "ficha_tracking_id": str(event.payload["ficha_id"]),
        "source_event_id": str(event.event_id),
        "is_resolved": False,
        "resolution_notes": None,
    }

    @app.get("/api/v1/generated-alerts")
    def list_alerts():
        return {"data": [alert], "pagination": {"limit": 50, "next_cursor": None, "has_more": False}}

    @app.put("/api/v1/generated-alerts/{id}/resolve")
    def resolve_alert(id: UUID, body: dict):
        alert["is_resolved"] = True
        alert["resolution_notes"] = body["resolution_notes"]
        return alert

    @app.post("/api/v1/improvement-plans", status_code=201)
    def create_plan(body: dict):
        plan = {"id": str(uuid4()), "status": "PENDING", **body}
        plans.append(plan)
        return plan

    return app
