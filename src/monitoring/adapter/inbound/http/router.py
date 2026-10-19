from __future__ import annotations

import base64
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status  # type: ignore
from pydantic import BaseModel, Field, field_validator  # type: ignore
from sqlalchemy import Select, and_, func, insert, select, update  # type: ignore
from sqlalchemy.exc import IntegrityError, SQLAlchemyError  # type: ignore
from sqlalchemy.orm import Session  # type: ignore

from monitoring.adapter.outbound.persistence.repo import (
    alert_type,
    ficha_tracking,
    generated_alert,
    improvement_plan,
    kpi_status,
    kpi_tracking,
    kpi_type,
    risk_level,
    tracking_session,
)
from monitoring.application.port.inbound.record_kpi import RecordKpiUseCase

SessionFactory = Callable[[], Session]

CATALOG_MANAGE = "MON_CATALOG_MANAGE"
REPORT_VIEW = "MON_REPORT_VIEW"
VALID_PLAN_TRANSITIONS = {
    "PENDING": {"IN_PROGRESS"},
    "IN_PROGRESS": {"COMPLETED", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}


class KpiTypeCreate(BaseModel):
    code: str = Field(max_length=50)
    name: str = Field(max_length=120)
    description: str | None = None
    unit: str


class KpiTypeUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = None
    unit: str | None = None
    is_active: bool | None = None


class AlertTypeCreate(BaseModel):
    code: str = Field(max_length=50)
    name: str = Field(max_length=120)
    description: str | None = None
    default_threshold: Decimal | None = None
    threshold_unit: str | None = None
    affects_ficha_status: bool = False


class AlertTypeUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = None
    default_threshold: Decimal | None = None
    threshold_unit: str | None = None
    affects_ficha_status: bool | None = None
    is_active: bool | None = None


class TrackingSessionCreate(BaseModel):
    instructor_id: UUID
    session_date: date
    session_type: str
    attendance_count: int | None = Field(default=None, ge=0)
    total_learners: int | None = Field(default=None, ge=0)
    curriculum_progress_percentage: Decimal | None = Field(default=None, ge=0, le=100)
    observations: str | None = None
    requires_follow_up: bool = False

    @field_validator("session_type")
    @classmethod
    def valid_session_type(cls, value: str) -> str:
        if value not in {"ACADEMIC", "WELLNESS", "PROJECT", "PRODUCTIVE_STAGE"}:
            raise ValueError("invalid session_type")
        return value


class KpiTrackingCreate(BaseModel):
    ficha_tracking_id: UUID
    kpi_type_id: UUID
    current_value: Decimal = Field(ge=0)
    threshold_value: Decimal = Field(ge=0)
    kpi_status_id: UUID
    period_start: date
    period_end: date
    measured_at: datetime | None = None

    @field_validator("period_end")
    @classmethod
    def valid_period(cls, value: date, info: Any) -> date:
        period_start = info.data.get("period_start")
        if period_start and value < period_start:
            raise ValueError("period_end must be greater than or equal to period_start")
        return value


class GeneratedAlertResolve(BaseModel):
    resolution_notes: str


class ImprovementPlanCreate(BaseModel):
    ficha_tracking_id: UUID
    generated_alert_id: UUID | None = None
    learner_id: UUID | None = None
    instructor_id: UUID | None = None
    title: str = Field(max_length=200)
    description: str
    specific_actions: str
    due_date: date


class ImprovementPlanUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    specific_actions: str | None = None
    due_date: date | None = None
    status: str | None = None
    completion_notes: str | None = None

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str | None) -> str | None:
        if value is not None and value not in VALID_PLAN_TRANSITIONS:
            raise ValueError("invalid improvement plan status")
        return value


def build_router(use_case: RecordKpiUseCase | None, repository: Any | None = None, readiness_checker: Any | None = None) -> APIRouter:
    router = APIRouter()
    session_factory = getattr(repository, "session_factory", None)

    def require_session_factory() -> SessionFactory:
        if session_factory is None:
            raise HTTPException(status_code=503, detail=error_body("SERVICE_UNAVAILABLE", "Database is not configured"))
        return session_factory

    def auth(feature: str | None = None, any_feature: list[str] | None = None) -> Callable[..., dict[str, Any]]:
        def dependency(authorization: str | None = Header(default=None)) -> dict[str, Any]:
            if not authorization or not authorization.startswith("Bearer "):
                raise HTTPException(status_code=401, detail=error_body("UNAUTHORIZED", "Missing bearer token"))
            claims = _decode_unverified_jwt(authorization.removeprefix("Bearer ").strip())
            exp = claims.get("exp")
            if isinstance(exp, int) and datetime.now(timezone.utc).timestamp() >= exp:
                raise HTTPException(status_code=401, detail=error_body("UNAUTHORIZED", "Expired bearer token"))
            granted = _claim_features(claims)
            required = any_feature or ([feature] if feature else [])
            if required and not any(_has_feature(granted, item) for item in required):
                raise HTTPException(status_code=403, detail=error_body("FORBIDDEN", "Insufficient permissions"))
            return claims

        return dependency

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/ready")
    def ready() -> dict[str, Any]:
        if readiness_checker is None:
            raise HTTPException(status_code=503, detail=error_body("NOT_READY", "Readiness checker is not configured"))
        result = readiness_checker.check()
        if result["status"] != "ready":
            raise HTTPException(status_code=503, detail=result)
        return result

    @router.get("/kpi-types")
    def list_kpi_types(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: dict[str, Any] = Depends(auth("MON_KPI_VIEW")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _page(sf, select(kpi_type).order_by(kpi_type.c.code), page, page_size)

    @router.post("/kpi-types", status_code=status.HTTP_201_CREATED)
    def create_kpi_type(body: KpiTypeCreate, _: dict[str, Any] = Depends(auth(CATALOG_MANAGE)), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _insert_catalog(sf, kpi_type, body.model_dump())

    @router.get("/kpi-types/{id}")
    def get_kpi_type(id: UUID, _: dict[str, Any] = Depends(auth("MON_KPI_VIEW")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _get_by_id(sf, kpi_type, id, "KPI type")

    @router.put("/kpi-types/{id}")
    def update_kpi_type(id: UUID, body: KpiTypeUpdate, _: dict[str, Any] = Depends(auth(CATALOG_MANAGE)), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _update_by_id(sf, kpi_type, id, body.model_dump(exclude_unset=True), "KPI type")

    @router.delete("/kpi-types/{id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_kpi_type(id: UUID, _: dict[str, Any] = Depends(auth(CATALOG_MANAGE)), sf: SessionFactory = Depends(require_session_factory)) -> Response:
        _soft_delete(sf, kpi_type, id, "KPI type")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/alert-types")
    def list_alert_types(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: dict[str, Any] = Depends(auth("MON_ALERT_VIEW")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _page(sf, select(alert_type).order_by(alert_type.c.code), page, page_size)

    @router.post("/alert-types", status_code=status.HTTP_201_CREATED)
    def create_alert_type(body: AlertTypeCreate, _: dict[str, Any] = Depends(auth(CATALOG_MANAGE)), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _insert_catalog(sf, alert_type, body.model_dump())

    @router.get("/alert-types/{id}")
    def get_alert_type(id: UUID, _: dict[str, Any] = Depends(auth("MON_ALERT_VIEW")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _get_by_id(sf, alert_type, id, "Alert type")

    @router.put("/alert-types/{id}")
    def update_alert_type(id: UUID, body: AlertTypeUpdate, _: dict[str, Any] = Depends(auth(CATALOG_MANAGE)), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _update_by_id(sf, alert_type, id, body.model_dump(exclude_unset=True), "Alert type")

    @router.delete("/alert-types/{id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_alert_type(id: UUID, _: dict[str, Any] = Depends(auth(CATALOG_MANAGE)), sf: SessionFactory = Depends(require_session_factory)) -> Response:
        _soft_delete(sf, alert_type, id, "Alert type")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/risk-levels")
    def list_risk_levels(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: dict[str, Any] = Depends(auth("MON_ALERT_VIEW")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _page(sf, select(risk_level).order_by(risk_level.c.priority_order), page, page_size)

    @router.get("/kpi-statuses")
    def list_kpi_statuses(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: dict[str, Any] = Depends(auth("MON_KPI_VIEW")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _page(sf, select(kpi_status).order_by(kpi_status.c.code), page, page_size)

    @router.get("/ficha-trackings")
    def list_ficha_trackings(ficha_id: UUID | None = None, assigned_instructor_id: UUID | None = None, overall_status: str | None = None, limit: int = Query(50, ge=1, le=200), cursor: str | None = None, _: dict[str, Any] = Depends(auth(any_feature=["MON_DASHBOARD_FULL", "MON_DASHBOARD_OWN"])), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        stmt = select(ficha_tracking).order_by(ficha_tracking.c.created_at.desc(), ficha_tracking.c.id.desc())
        if ficha_id:
            stmt = stmt.where(ficha_tracking.c.ficha_id == ficha_id)
        if assigned_instructor_id:
            stmt = stmt.where(ficha_tracking.c.assigned_instructor_id == assigned_instructor_id)
        if overall_status:
            stmt = stmt.join(kpi_status, kpi_status.c.id == ficha_tracking.c.overall_status_id).where(kpi_status.c.code == overall_status)
        return _cursor(sf, stmt, ficha_tracking.c.id, limit, cursor)

    @router.get("/ficha-trackings/{id}")
    def get_ficha_tracking(id: UUID, _: dict[str, Any] = Depends(auth("MON_DASHBOARD_OWN")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _get_by_id(sf, ficha_tracking, id, "Ficha tracking")

    @router.get("/ficha-trackings/{id}/tracking-sessions")
    def list_tracking_sessions(id: UUID, limit: int = Query(50, ge=1, le=200), cursor: str | None = None, _: dict[str, Any] = Depends(auth("MON_TRACKING_SESSION_VIEW")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        stmt = select(tracking_session).where(tracking_session.c.ficha_tracking_id == id).order_by(tracking_session.c.created_at.desc(), tracking_session.c.id.desc())
        return _cursor(sf, stmt, tracking_session.c.id, limit, cursor)

    @router.post("/ficha-trackings/{id}/tracking-sessions", status_code=status.HTTP_201_CREATED)
    def create_tracking_session(id: UUID, body: TrackingSessionCreate, _: dict[str, Any] = Depends(auth("MON_TRACKING_SESSION_CREATE")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        values = body.model_dump()
        values.update(ficha_tracking_id=id, created_at=datetime.now(timezone.utc))
        try:
            with sf() as session, session.begin():
                _ensure_exists(session, ficha_tracking, id, "Ficha tracking")
                row = session.execute(insert(tracking_session).values(**values).returning(tracking_session)).one()
                return _row(row)
        except SQLAlchemyError as exc:
            raise _http_error(400, "BAD_REQUEST", "Could not create tracking session") from exc

    @router.get("/ficha-trackings/{id}/tracking-sessions/{session_id}")
    def get_tracking_session(id: UUID, session_id: UUID, _: dict[str, Any] = Depends(auth("MON_TRACKING_SESSION_VIEW")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        with sf() as session:
            row = session.execute(select(tracking_session).where(tracking_session.c.id == session_id, tracking_session.c.ficha_tracking_id == id)).one_or_none()
        if row is None:
            raise _http_error(404, "NOT_FOUND", "Tracking session not found")
        return _row(row)

    @router.get("/kpi-trackings")
    def list_kpi_trackings(ficha_tracking_id: UUID | None = None, kpi_type_id: UUID | None = None, kpi_status_id: UUID | None = None, from_: datetime | None = Query(default=None, alias="from"), to: datetime | None = None, limit: int = Query(50, ge=1, le=200), cursor: str | None = None, _: dict[str, Any] = Depends(auth("MON_KPI_VIEW")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        stmt = select(kpi_tracking).order_by(kpi_tracking.c.measured_at.desc(), kpi_tracking.c.id.desc())
        if ficha_tracking_id:
            stmt = stmt.where(kpi_tracking.c.ficha_tracking_id == ficha_tracking_id)
        if kpi_type_id:
            stmt = stmt.where(kpi_tracking.c.kpi_type_id == kpi_type_id)
        if kpi_status_id:
            stmt = stmt.where(kpi_tracking.c.kpi_status_id == kpi_status_id)
        if from_:
            stmt = stmt.where(kpi_tracking.c.measured_at >= from_)
        if to:
            stmt = stmt.where(kpi_tracking.c.measured_at < to)
        return _cursor(sf, stmt, kpi_tracking.c.id, limit, cursor)

    @router.post("/kpi-trackings", status_code=status.HTTP_201_CREATED)
    def create_kpi_tracking(body: KpiTrackingCreate, _: dict[str, Any] = Depends(auth("MON_TRACKING_SESSION_CREATE")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        values = body.model_dump()
        values["measured_at"] = values["measured_at"] or datetime.now(timezone.utc)
        try:
            with sf() as session, session.begin():
                row = session.execute(insert(kpi_tracking).values(**values).returning(kpi_tracking)).one()
                return _row(row)
        except SQLAlchemyError as exc:
            raise _http_error(400, "BAD_REQUEST", "Could not record KPI tracking") from exc

    @router.get("/kpi-trackings/{id}")
    def get_kpi_tracking(id: UUID, _: dict[str, Any] = Depends(auth("MON_KPI_VIEW")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _get_by_id(sf, kpi_tracking, id, "KPI tracking")

    @router.get("/generated-alerts")
    def list_generated_alerts(ficha_tracking_id: UUID | None = None, alert_type_id: UUID | None = None, risk_level_id: UUID | None = None, is_resolved: bool | None = None, limit: int = Query(50, ge=1, le=200), cursor: str | None = None, _: dict[str, Any] = Depends(auth("MON_ALERT_VIEW")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        stmt = select(generated_alert).order_by(generated_alert.c.generated_at.desc(), generated_alert.c.id.desc())
        for column, value in ((generated_alert.c.ficha_tracking_id, ficha_tracking_id), (generated_alert.c.alert_type_id, alert_type_id), (generated_alert.c.risk_level_id, risk_level_id), (generated_alert.c.is_resolved, is_resolved)):
            if value is not None:
                stmt = stmt.where(column == value)
        return _cursor(sf, stmt, generated_alert.c.id, limit, cursor)

    @router.get("/generated-alerts/{id}")
    def get_generated_alert(id: UUID, _: dict[str, Any] = Depends(auth("MON_ALERT_VIEW")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _get_by_id(sf, generated_alert, id, "Generated alert")

    @router.put("/generated-alerts/{id}/resolve")
    def resolve_generated_alert(id: UUID, body: GeneratedAlertResolve, claims: dict[str, Any] = Depends(auth("MON_ALERT_RESOLVE")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with sf() as session, session.begin():
            current = session.execute(select(generated_alert).where(generated_alert.c.id == id)).one_or_none()
            if current is None:
                raise _http_error(404, "NOT_FOUND", "Generated alert not found")
            current_row = _row(current)
            if current_row["is_resolved"]:
                return current_row
            row = session.execute(update(generated_alert).where(generated_alert.c.id == id).values(is_resolved=True, resolved_by=_uuid_or_system(claims.get("sub")), resolution_notes=body.resolution_notes, resolved_at=now, updated_at=now).returning(generated_alert)).one()
            return _row(row)

    @router.get("/improvement-plans")
    def list_improvement_plans(ficha_tracking_id: UUID | None = None, status_: str | None = Query(default=None, alias="status"), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: dict[str, Any] = Depends(auth("MON_IMPROVEMENT_PLAN_MANAGE")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        stmt = select(improvement_plan).order_by(improvement_plan.c.created_at.desc())
        if ficha_tracking_id:
            stmt = stmt.where(improvement_plan.c.ficha_tracking_id == ficha_tracking_id)
        if status_:
            stmt = stmt.where(improvement_plan.c.status == status_)
        return _page(sf, stmt, page, page_size)

    @router.post("/improvement-plans", status_code=status.HTTP_201_CREATED)
    def create_improvement_plan(body: ImprovementPlanCreate, claims: dict[str, Any] = Depends(auth("MON_IMPROVEMENT_PLAN_MANAGE")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        values = body.model_dump()
        values.update(status="PENDING", created_by=_uuid_or_system(claims.get("sub")), created_at=now, updated_at=now)
        try:
            with sf() as session, session.begin():
                row = session.execute(insert(improvement_plan).values(**values).returning(improvement_plan)).one()
                return _row(row)
        except SQLAlchemyError as exc:
            raise _http_error(400, "BAD_REQUEST", "Could not create improvement plan") from exc

    @router.get("/improvement-plans/{id}")
    def get_improvement_plan(id: UUID, _: dict[str, Any] = Depends(auth("MON_IMPROVEMENT_PLAN_MANAGE")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        return _get_by_id(sf, improvement_plan, id, "Improvement plan")

    @router.put("/improvement-plans/{id}")
    def update_improvement_plan(id: UUID, body: ImprovementPlanUpdate, _: dict[str, Any] = Depends(auth("MON_IMPROVEMENT_PLAN_MANAGE")), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        values = body.model_dump(exclude_unset=True)
        with sf() as session, session.begin():
            current = session.execute(select(improvement_plan).where(improvement_plan.c.id == id)).one_or_none()
            if current is None:
                raise _http_error(404, "NOT_FOUND", "Improvement plan not found")
            current_row = _row(current)
            requested_status = values.get("status")
            if requested_status and requested_status != current_row["status"] and requested_status not in VALID_PLAN_TRANSITIONS[current_row["status"]]:
                raise _http_error(409, "INVALID_STATE_TRANSITION", "Invalid improvement plan transition")
            values["updated_at"] = datetime.now(timezone.utc)
            row = session.execute(update(improvement_plan).where(improvement_plan.c.id == id).values(**values).returning(improvement_plan)).one()
            return _row(row)

    @router.get("/reports/kpi-summary")
    def report_kpi_summary(ficha_tracking_id: UUID | None = None, kpi_type_code: str | None = Query(default=None, alias="kpi_type"), status_code: str | None = Query(default=None, alias="status"), from_: datetime | None = Query(default=None, alias="from"), to: datetime | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: dict[str, Any] = Depends(auth(REPORT_VIEW)), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        latest = select(kpi_tracking.c.ficha_tracking_id, kpi_tracking.c.kpi_type_id, func.max(kpi_tracking.c.measured_at).label("measured_at")).group_by(kpi_tracking.c.ficha_tracking_id, kpi_tracking.c.kpi_type_id).subquery()
        stmt = select(kpi_tracking.c.ficha_tracking_id, kpi_type.c.code.label("kpi_type_code"), kpi_type.c.name.label("kpi_type_name"), kpi_tracking.c.current_value, kpi_type.c.unit, kpi_tracking.c.threshold_value, kpi_status.c.code.label("kpi_status_code"), risk_level.c.code.label("risk_level_code"), kpi_tracking.c.period_start, kpi_tracking.c.period_end, kpi_tracking.c.measured_at).join(latest, and_(latest.c.ficha_tracking_id == kpi_tracking.c.ficha_tracking_id, latest.c.kpi_type_id == kpi_tracking.c.kpi_type_id, latest.c.measured_at == kpi_tracking.c.measured_at)).join(kpi_type, kpi_type.c.id == kpi_tracking.c.kpi_type_id).join(kpi_status, kpi_status.c.id == kpi_tracking.c.kpi_status_id).join(risk_level, risk_level.c.id == kpi_status.c.risk_level_id).order_by(kpi_tracking.c.measured_at.desc())
        if ficha_tracking_id:
            stmt = stmt.where(kpi_tracking.c.ficha_tracking_id == ficha_tracking_id)
        if kpi_type_code:
            stmt = stmt.where(kpi_type.c.code == kpi_type_code)
        if status_code:
            stmt = stmt.where(kpi_status.c.code == status_code)
        if from_:
            stmt = stmt.where(kpi_tracking.c.measured_at >= from_)
        if to:
            stmt = stmt.where(kpi_tracking.c.measured_at < to)
        return _page(sf, stmt, page, page_size)

    @router.get("/reports/indicators")
    def report_indicators(status_code: str | None = Query(default=None, alias="status"), assigned_instructor_id: UUID | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), _: dict[str, Any] = Depends(auth(REPORT_VIEW)), sf: SessionFactory = Depends(require_session_factory)) -> dict[str, Any]:
        stmt = select(kpi_status.c.code.label("kpi_status_code"), risk_level.c.code.label("risk_level_code"), func.count(ficha_tracking.c.id).label("ficha_count"), func.avg(ficha_tracking.c.active_alert_count).label("avg_active_alert_count"), func.now().label("as_of")).select_from(ficha_tracking).join(kpi_status, kpi_status.c.id == ficha_tracking.c.overall_status_id).join(risk_level, risk_level.c.id == kpi_status.c.risk_level_id).group_by(kpi_status.c.code, risk_level.c.code).order_by(kpi_status.c.code)
        if status_code:
            stmt = stmt.where(kpi_status.c.code == status_code)
        if assigned_instructor_id:
            stmt = stmt.where(ficha_tracking.c.assigned_instructor_id == assigned_instructor_id)
        return _page(sf, stmt, page, page_size)

    return router


def error_body(code: str, message: str, details: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or []}}


def _http_error(http_status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=http_status, detail=error_body(code, message))


def _decode_unverified_jwt(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except (IndexError, ValueError, json.JSONDecodeError):
        raise _http_error(401, "UNAUTHORIZED", "Invalid bearer token")


def _claim_features(claims: dict[str, Any]) -> set[str]:
    raw = claims.get("features", [])
    if isinstance(raw, str):
        raw = [raw]
    scope = claims.get("scope", "")
    if isinstance(scope, str):
        raw = [*raw, *scope.split()]
    return {str(item) for item in raw}


def _has_feature(granted: set[str], required: str) -> bool:
    return required in granted or any(item.startswith(f"{required}:") for item in granted)


def _uuid_or_system(value: Any) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return UUID("00000000-0000-0000-0000-000000000000")


def _serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (UUID, datetime, date)):
        return value.isoformat()
    return value


def _row(row: Any) -> dict[str, Any]:
    mapping = row._mapping
    if len(mapping) == 1:
        first = next(iter(mapping.values()))
        if hasattr(first, "_mapping"):
            mapping = first._mapping
    return {key: _serialize(value) for key, value in mapping.items()}


def _page(sf: SessionFactory, stmt: Select[Any], page: int, page_size: int) -> dict[str, Any]:
    with sf() as session:
        total = session.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
        rows = session.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return {"data": [_row(row) for row in rows], "pagination": {"page": page, "page_size": page_size, "total": total}}


def _cursor(sf: SessionFactory, stmt: Select[Any], id_column: Any, limit: int, cursor: str | None) -> dict[str, Any]:
    if cursor:
        stmt = stmt.where(id_column < UUID(cursor))
    with sf() as session:
        rows = session.execute(stmt.limit(limit + 1)).all()
    has_more = len(rows) > limit
    data = [_row(row) for row in rows[:limit]]
    return {"data": data, "pagination": {"limit": limit, "next_cursor": data[-1]["id"] if has_more and data else None, "has_more": has_more}}


def _ensure_exists(session: Session, table: Any, id_: UUID, label: str) -> None:
    if session.execute(select(table.c.id).where(table.c.id == id_)).scalar_one_or_none() is None:
        raise _http_error(404, "NOT_FOUND", f"{label} not found")


def _get_by_id(sf: SessionFactory, table: Any, id_: UUID, label: str) -> dict[str, Any]:
    with sf() as session:
        row = session.execute(select(table).where(table.c.id == id_)).one_or_none()
    if row is None:
        raise _http_error(404, "NOT_FOUND", f"{label} not found")
    return _row(row)


def _insert_catalog(sf: SessionFactory, table: Any, values: dict[str, Any]) -> dict[str, Any]:
    values["code"] = values["code"].upper()
    try:
        with sf() as session, session.begin():
            row = session.execute(insert(table).values(**values).returning(table)).one()
            return _row(row)
    except IntegrityError as exc:
        raise _http_error(409, "CATALOG_CODE_CONFLICT", "Catalog code already exists") from exc
    except SQLAlchemyError as exc:
        raise _http_error(400, "BAD_REQUEST", "Could not create catalog item") from exc


def _update_by_id(sf: SessionFactory, table: Any, id_: UUID, values: dict[str, Any], label: str) -> dict[str, Any]:
    try:
        with sf() as session, session.begin():
            row = session.execute(update(table).where(table.c.id == id_).values(**values).returning(table)).one_or_none()
    except IntegrityError as exc:
        raise _http_error(409, "CATALOG_CODE_CONFLICT", "Catalog code already exists") from exc
    except SQLAlchemyError as exc:
        raise _http_error(400, "BAD_REQUEST", f"Could not update {label.lower()}") from exc
    if row is None:
        raise _http_error(404, "NOT_FOUND", f"{label} not found")
    return _row(row)


def _soft_delete(sf: SessionFactory, table: Any, id_: UUID, label: str) -> None:
    with sf() as session, session.begin():
        result = session.execute(update(table).where(table.c.id == id_).values(is_active=False))
    if result.rowcount == 0:
        raise _http_error(404, "NOT_FOUND", f"{label} not found")
