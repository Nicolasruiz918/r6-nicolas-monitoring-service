from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    Uuid,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from monitoring.domain.model.kpi import KpiMeasurement
from monitoring.domain.model.event import AlertWorkerResult, DomainEvent, KpiEvaluation

SYSTEM_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000000")

metadata = MetaData(schema="monitoring")

kpi_type = Table(
    "kpi_type",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("code", String(50), nullable=False, unique=True),
    Column("name", String(120), nullable=False),
    Column("description", Text),
    Column("unit", String(20), nullable=False),
    Column("is_active", Boolean, nullable=False),
)

risk_level = Table(
    "risk_level",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("code", String(20), nullable=False, unique=True),
    Column("label", String(60), nullable=False),
    Column("color_hex", String(7), nullable=False),
    Column("priority_order", Integer, nullable=False),
)

kpi_status = Table(
    "kpi_status",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("code", String(20), nullable=False, unique=True),
    Column("description", Text),
    Column("risk_level_id", Uuid(as_uuid=True), ForeignKey("monitoring.risk_level.id")),
)

ficha_tracking = Table(
    "ficha_tracking",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("ficha_id", Uuid(as_uuid=True), nullable=False, unique=True),
    Column("assigned_instructor_id", Uuid(as_uuid=True), nullable=False),
    Column("overall_status_id", Uuid(as_uuid=True), ForeignKey("monitoring.kpi_status.id"), nullable=False),
    Column("active_alert_count", Integer, nullable=False),
    Column("last_tracking_date", Date),
    Column("next_tracking_date", Date),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

kpi_tracking = Table(
    "kpi_tracking",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("ficha_tracking_id", Uuid(as_uuid=True), ForeignKey("monitoring.ficha_tracking.id"), nullable=False),
    Column("kpi_type_id", Uuid(as_uuid=True), ForeignKey("monitoring.kpi_type.id"), nullable=False),
    Column("current_value", Numeric(10, 4), nullable=False),
    Column("threshold_value", Numeric(10, 4), nullable=False),
    Column("kpi_status_id", Uuid(as_uuid=True), ForeignKey("monitoring.kpi_status.id"), nullable=False),
    Column("period_start", Date, nullable=False),
    Column("period_end", Date, nullable=False),
    Column("measured_at", DateTime(timezone=True), primary_key=True, nullable=False),
)

generated_alert = Table(
    "generated_alert",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("ficha_tracking_id", Uuid(as_uuid=True), ForeignKey("monitoring.ficha_tracking.id"), nullable=False),
    Column("alert_type_id", Uuid(as_uuid=True), nullable=False),
    Column("risk_level_id", Uuid(as_uuid=True), nullable=False),
    Column("source_event_id", Uuid(as_uuid=True), nullable=False),
    Column("affected_learner_id", Uuid(as_uuid=True)),
    Column("affected_instructor_id", Uuid(as_uuid=True)),
    Column("triggered_value", Numeric(10, 4)),
    Column("threshold_value", Numeric(10, 4)),
    Column("description", Text, nullable=False),
    Column("is_resolved", Boolean, nullable=False),
    Column("resolved_by", Uuid(as_uuid=True)),
    Column("resolution_notes", Text),
    Column("generated_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

alert_type = Table(
    "alert_type",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("code", String(50), nullable=False, unique=True),
    Column("name", String(120), nullable=False),
    Column("description", Text),
    Column("default_threshold", Numeric(10, 4)),
    Column("threshold_unit", String(20)),
    Column("affects_ficha_status", Boolean, nullable=False),
    Column("is_active", Boolean, nullable=False),
)

tracking_session = Table(
    "tracking_session",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("ficha_tracking_id", Uuid(as_uuid=True), ForeignKey("monitoring.ficha_tracking.id"), nullable=False),
    Column("instructor_id", Uuid(as_uuid=True), nullable=False),
    Column("session_date", Date, nullable=False),
    Column("session_type", String(20), nullable=False),
    Column("attendance_count", Integer),
    Column("total_learners", Integer),
    Column("attendance_percentage", Numeric(5, 2)),
    Column("curriculum_progress_percentage", Numeric(5, 2)),
    Column("observations", Text),
    Column("requires_follow_up", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

improvement_plan = Table(
    "improvement_plan",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("ficha_tracking_id", Uuid(as_uuid=True), ForeignKey("monitoring.ficha_tracking.id"), nullable=False),
    Column("generated_alert_id", Uuid(as_uuid=True)),
    Column("learner_id", Uuid(as_uuid=True)),
    Column("instructor_id", Uuid(as_uuid=True)),
    Column("title", String(200), nullable=False),
    Column("description", Text, nullable=False),
    Column("specific_actions", Text, nullable=False),
    Column("due_date", Date, nullable=False),
    Column("status", String(20), nullable=False),
    Column("completion_notes", Text),
    Column("created_by", Uuid(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

outbox = Table(
    "outbox",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("event_id", Uuid(as_uuid=True), nullable=False, unique=True),
    Column("event_type", String(150), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True)),
)


class PersistenceError(RuntimeError):
    pass


class CatalogNotFound(PersistenceError):
    pass


class SqlAlchemyKpiRepository:
    def __init__(self, session_factory: Callable[[], Session], engine: Engine | None = None) -> None:
        self._session_factory = session_factory
        self._engine = engine

    @property
    def session_factory(self) -> Callable[[], Session]:
        return self._session_factory

    @property
    def engine(self) -> Engine | None:
        return self._engine

    @classmethod
    def from_engine(cls, engine: Engine) -> "SqlAlchemyKpiRepository":
        return cls(sessionmaker(bind=engine, expire_on_commit=False, future=True), engine)

    @classmethod
    def from_url(cls, database_url: str | None) -> "SqlAlchemyKpiRepository":
        if not database_url:
            raise PersistenceError("DATABASE_URL is required for SqlAlchemyKpiRepository")
        return cls.from_engine(create_engine(database_url, future=True))

    def save(self, measurement: KpiMeasurement) -> None:
        try:
            with self._session_factory() as session:
                with session.begin():
                    kpi_type_id = self._required_id(session, kpi_type, measurement.kpi_code)
                    status_id = self._required_id(session, kpi_status, measurement.status_code)
                    tracking_id = self._ensure_ficha_tracking(session, measurement, status_id)
                    row = session.execute(
                        insert(kpi_tracking)
                        .values(
                            ficha_tracking_id=tracking_id,
                            kpi_type_id=kpi_type_id,
                            current_value=measurement.value,
                            threshold_value=measurement.threshold_value,
                            kpi_status_id=status_id,
                            period_start=measurement.period_start or measurement.measured_at.date(),
                            period_end=measurement.period_end or measurement.measured_at.date(),
                            measured_at=measurement.measured_at,
                        )
                        .returning(kpi_tracking.c.id)
                    ).one()
                    measurement.id = row.id
        except CatalogNotFound:
            raise
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not persist KPI measurement") from exc

    def list_by_ficha(self, ficha_id: UUID, limit: int = 100) -> list[KpiMeasurement]:
        try:
            with self._session_factory() as session:
                rows = session.execute(
                    select(
                        kpi_tracking.c.id,
                        kpi_type.c.code,
                        kpi_tracking.c.current_value,
                        kpi_tracking.c.threshold_value,
                        kpi_status.c.code.label("status_code"),
                        kpi_tracking.c.period_start,
                        kpi_tracking.c.period_end,
                        kpi_tracking.c.measured_at,
                    )
                    .join(ficha_tracking, ficha_tracking.c.id == kpi_tracking.c.ficha_tracking_id)
                    .join(kpi_type, kpi_type.c.id == kpi_tracking.c.kpi_type_id)
                    .join(kpi_status, kpi_status.c.id == kpi_tracking.c.kpi_status_id)
                    .where(ficha_tracking.c.ficha_id == ficha_id)
                    .order_by(kpi_tracking.c.measured_at.desc())
                    .limit(limit)
                ).all()
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not query KPI measurements") from exc

        return [
            KpiMeasurement(
                id=row.id,
                ficha_id=ficha_id,
                kpi_code=row.code,
                value=Decimal(row.current_value),
                threshold_value=Decimal(row.threshold_value),
                status_code=row.status_code,
                period_start=row.period_start,
                period_end=row.period_end,
                measured_at=row.measured_at,
            )
            for row in rows
        ]

    def _required_id(self, session: Session, table: Table, code: str) -> UUID:
        value = session.execute(select(table.c.id).where(table.c.code == code.upper())).scalar_one_or_none()
        if value is None:
            raise CatalogNotFound(f"{table.name} code not found: {code}")
        return value

    def _ensure_ficha_tracking(self, session: Session, measurement: KpiMeasurement, status_id: UUID) -> UUID:
        existing = session.execute(
            select(ficha_tracking.c.id).where(ficha_tracking.c.ficha_id == measurement.ficha_id)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        row = session.execute(
            insert(ficha_tracking)
            .values(
                ficha_id=measurement.ficha_id,
                assigned_instructor_id=measurement.assigned_instructor_id or SYSTEM_ACTOR_ID,
                overall_status_id=status_id,
                active_alert_count=0,
                created_at=measurement.measured_at,
                updated_at=measurement.measured_at,
            )
            .returning(ficha_tracking.c.id)
        ).one()
        return row.id


class SqlAlchemyAlertWorkerRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    @classmethod
    def from_url(cls, database_url: str | None) -> "SqlAlchemyAlertWorkerRepository":
        if not database_url:
            raise PersistenceError("DATABASE_URL is required for SqlAlchemyAlertWorkerRepository")
        return cls(sessionmaker(bind=create_engine(database_url, future=True), expire_on_commit=False, future=True))

    def initialize_ficha_tracking(self, event: DomainEvent) -> AlertWorkerResult:
        ficha_id = UUID(str(event.payload["ficha_id"]))
        assigned_instructor_id = _optional_uuid(event.payload.get("assigned_instructor_id")) or SYSTEM_ACTOR_ID
        try:
            with self._session_factory() as session, session.begin():
                status_id = self._required_id(session, kpi_status, "ON_TRACK")
                existing = session.execute(select(ficha_tracking.c.id).where(ficha_tracking.c.ficha_id == ficha_id)).scalar_one_or_none()
                if existing is not None:
                    session.execute(
                        update(ficha_tracking)
                        .where(ficha_tracking.c.id == existing)
                        .values(assigned_instructor_id=assigned_instructor_id, overall_status_id=status_id, updated_at=event.timestamp)
                    )
                    return AlertWorkerResult(processed=True, idempotent=True)
                session.execute(
                    insert(ficha_tracking).values(
                        ficha_id=ficha_id,
                        assigned_instructor_id=assigned_instructor_id,
                        overall_status_id=status_id,
                        active_alert_count=0,
                        created_at=event.timestamp,
                        updated_at=event.timestamp,
                    )
                )
                return AlertWorkerResult(processed=True)
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not initialize ficha tracking") from exc

    def record_kpi_evaluations(self, event: DomainEvent, evaluations: list[KpiEvaluation]) -> AlertWorkerResult:
        alerts_created = 0
        kpis_recorded = 0
        produced: list[str] = []
        try:
            with self._session_factory() as session, session.begin():
                for evaluation in evaluations:
                    threshold = Decimal(str(evaluation.threshold_value)) if evaluation.threshold_value is not None else self._threshold_for_alert(session, evaluation.alert_type_code)
                    status_code, risk_code = _status_for_value(evaluation, threshold)
                    status_id = self._required_id(session, kpi_status, status_code)
                    tracking_id = self._ensure_tracking(session, evaluation, status_id, event.timestamp)
                    kpi_type_id = self._required_id(session, kpi_type, evaluation.kpi_type_code)
                    existing_kpi = session.execute(
                        select(kpi_tracking.c.id).where(
                            kpi_tracking.c.ficha_tracking_id == tracking_id,
                            kpi_tracking.c.kpi_type_id == kpi_type_id,
                            kpi_tracking.c.measured_at == event.timestamp,
                        )
                    ).scalar_one_or_none()
                    if existing_kpi is None:
                        session.execute(
                            insert(kpi_tracking).values(
                                ficha_tracking_id=tracking_id,
                                kpi_type_id=kpi_type_id,
                                current_value=Decimal(str(evaluation.current_value)),
                                threshold_value=threshold,
                                kpi_status_id=status_id,
                                period_start=(evaluation.period_start or event.timestamp).date(),
                                period_end=(evaluation.period_end or event.timestamp).date(),
                                measured_at=event.timestamp,
                            )
                        )
                        kpis_recorded += 1
                    if evaluation.alert_type_code and _breaches_threshold(evaluation, threshold):
                        created = self._create_alert_and_outbox(session, event, evaluation, tracking_id, risk_code, threshold)
                        if created:
                            alerts_created += 1
                            produced.append("monitoring.alert.triggered")
                return AlertWorkerResult(processed=True, idempotent=alerts_created == 0 and kpis_recorded == 0, alerts_created=alerts_created, kpis_recorded=kpis_recorded, produced_event_types=produced)
        except CatalogNotFound:
            raise
        except SQLAlchemyError as exc:
            raise PersistenceError("Could not process alert-worker event") from exc

    def list_pending(self, limit: int) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.execute(
                select(outbox).where(outbox.c.published_at.is_(None)).order_by(outbox.c.created_at).limit(limit)
            ).all()
        return [{key: value for key, value in row._mapping.items()} for row in rows]

    def mark_published(self, outbox_id: str) -> None:
        from datetime import datetime, timezone

        with self._session_factory() as session, session.begin():
            session.execute(update(outbox).where(outbox.c.id == UUID(outbox_id)).values(published_at=datetime.now(timezone.utc)))

    def _required_id(self, session: Session, table: Table, code: str) -> UUID:
        value = session.execute(select(table.c.id).where(table.c.code == code.upper())).scalar_one_or_none()
        if value is None:
            raise CatalogNotFound(f"{table.name} code not found: {code}")
        return value

    def _ensure_tracking(self, session: Session, evaluation: KpiEvaluation, status_id: UUID, timestamp: Any) -> UUID:
        existing = session.execute(select(ficha_tracking.c.id).where(ficha_tracking.c.ficha_id == evaluation.ficha_id)).scalar_one_or_none()
        if existing is not None:
            session.execute(
                update(ficha_tracking)
                .where(ficha_tracking.c.id == existing)
                .values(overall_status_id=status_id, assigned_instructor_id=evaluation.assigned_instructor_id or SYSTEM_ACTOR_ID, updated_at=timestamp)
            )
            return existing
        row = session.execute(
            insert(ficha_tracking)
            .values(
                ficha_id=evaluation.ficha_id,
                assigned_instructor_id=evaluation.assigned_instructor_id or SYSTEM_ACTOR_ID,
                overall_status_id=status_id,
                active_alert_count=0,
                created_at=timestamp,
                updated_at=timestamp,
            )
            .returning(ficha_tracking.c.id)
        ).one()
        return row.id

    def _threshold_for_alert(self, session: Session, alert_code: str | None) -> Decimal:
        if not alert_code:
            return Decimal("0")
        value = session.execute(select(alert_type.c.default_threshold).where(alert_type.c.code == alert_code.upper())).scalar_one_or_none()
        if value is None:
            raise CatalogNotFound(f"alert_type code not found: {alert_code}")
        return Decimal(value)

    def _create_alert_and_outbox(self, session: Session, event: DomainEvent, evaluation: KpiEvaluation, tracking_id: UUID, risk_code: str, threshold: Decimal) -> bool:
        alert_type_id = self._required_id(session, alert_type, evaluation.alert_type_code or "")
        risk_level_id = self._required_id(session, risk_level, risk_code)
        existing = session.execute(
            select(generated_alert.c.id).where(
                generated_alert.c.source_event_id == event.event_id,
                generated_alert.c.alert_type_id == alert_type_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return False
        alert_id = session.execute(
            insert(generated_alert)
            .values(
                ficha_tracking_id=tracking_id,
                alert_type_id=alert_type_id,
                risk_level_id=risk_level_id,
                source_event_id=event.event_id,
                affected_learner_id=evaluation.affected_learner_id,
                affected_instructor_id=evaluation.affected_instructor_id,
                triggered_value=Decimal(str(evaluation.current_value)),
                threshold_value=threshold,
                description=f"{evaluation.alert_type_code} threshold breached for {evaluation.kpi_type_code}",
                is_resolved=False,
                generated_at=event.timestamp,
                updated_at=event.timestamp,
            )
            .returning(generated_alert.c.id)
        ).scalar_one()
        outbox_event_id = uuid4()
        payload = {
            "event_id": str(outbox_event_id),
            "event_type": "monitoring.alert.triggered",
            "version": "1.0",
            "timestamp": event.timestamp.isoformat(),
            "source_service": "monitoring-service",
            "correlation_id": str(event.correlation_id) if event.correlation_id else None,
            "payload": {
                "alert_id": str(alert_id),
                "ficha_tracking_id": str(tracking_id),
                "ficha_id": str(evaluation.ficha_id),
                "alert_type_code": evaluation.alert_type_code,
                "risk_level_code": risk_code,
                "affected_entity_type": _affected_entity_type(evaluation),
                "affected_entity_id": str(evaluation.affected_learner_id or evaluation.affected_instructor_id) if evaluation.affected_learner_id or evaluation.affected_instructor_id else None,
                "source_event_id": str(event.event_id),
                "generated_at": event.timestamp.isoformat(),
            },
        }
        session.execute(insert(outbox).values(event_id=outbox_event_id, event_type="monitoring.alert.triggered", payload=payload, created_at=event.timestamp))
        return True


def _optional_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _status_for_value(evaluation: KpiEvaluation, threshold: Decimal) -> tuple[str, str]:
    if evaluation.alert_type_code == "HIGH_DROPOUT_RISK" and Decimal(str(evaluation.current_value)) >= threshold:
        return "CRITICAL", "CRITICAL"
    if Decimal(str(evaluation.current_value)) < threshold:
        return "AT_RISK", "HIGH"
    return "ON_TRACK", "INFO"


def _breaches_threshold(evaluation: KpiEvaluation, threshold: Decimal) -> bool:
    value = Decimal(str(evaluation.current_value))
    if evaluation.alert_type_code == "HIGH_DROPOUT_RISK":
        return value >= threshold
    return value < threshold


def _affected_entity_type(evaluation: KpiEvaluation) -> str | None:
    if evaluation.affected_learner_id:
        return "Learner"
    if evaluation.affected_instructor_id:
        return "Instructor"
    return None
