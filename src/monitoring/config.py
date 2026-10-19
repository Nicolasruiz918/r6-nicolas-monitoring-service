from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    amqp_url: str | None
    jwks_url: str | None
    api_host: str
    api_port: int
    request_timeout_seconds: float
    readiness_timeout_seconds: float
    otel_enabled: bool
    otel_service_name: str
    otel_exporter_otlp_endpoint: str | None
    deployment_environment: str
    log_level: str
    alert_worker_mode: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("DATABASE_URL"),
            amqp_url=os.environ.get("AMQP_URL"),
            jwks_url=os.environ.get("JWKS_URL"),
            api_host=os.environ.get("API_HOST", "0.0.0.0"),
            api_port=int(os.environ.get("API_PORT", "8000")),
            request_timeout_seconds=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "10")),
            readiness_timeout_seconds=float(os.environ.get("READINESS_TIMEOUT_SECONDS", "2")),
            otel_enabled=os.environ.get("OTEL_ENABLED", "true").lower() == "true",
            otel_service_name=os.environ.get("OTEL_SERVICE_NAME", "monitoring-service"),
            otel_exporter_otlp_endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
            deployment_environment=os.environ.get("DEPLOYMENT_ENVIRONMENT", "local"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            alert_worker_mode=os.environ.get("ALERT_WORKER_MODE", "consumer"),
        )
