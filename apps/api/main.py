# Composition root del contenedor API (uvicorn apps.api.main:app).
from fastapi import FastAPI  # type: ignore

from monitoring.adapter.inbound.http.router import build_router
from monitoring.adapter.outbound.persistence.repo import SqlAlchemyKpiRepository
from monitoring.application.usecase.record_kpi import RecordKpi
from monitoring.config import Settings
from monitoring.observability import configure_logging, configure_otel, request_logging_middleware
from monitoring.readiness import ReadinessChecker

settings = Settings.from_env()
configure_logging(settings)
app = FastAPI(title="monitoring-service")
_repository = SqlAlchemyKpiRepository.from_url(settings.database_url) if settings.database_url else None
_use_case = RecordKpi(_repository) if _repository else None
_readiness = ReadinessChecker(
    getattr(_repository, "session_factory", None),
    settings.amqp_url,
    settings.readiness_timeout_seconds,
)
configure_otel(settings, app=app, engine=getattr(_repository, "engine", None))
app.middleware("http")(request_logging_middleware)
app.include_router(build_router(_use_case, _repository, _readiness), prefix="/api/v1")
