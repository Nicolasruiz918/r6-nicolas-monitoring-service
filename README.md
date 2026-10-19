# monitoring-service

Python/FastAPI service for the Monitoring domain. It exposes the Monitoring REST API and runs the `alert-worker`, which evaluates KPI events, persists alerts, and publishes `monitoring.alert.triggered` through the transactional outbox.

## Runtime components

- `monitoring-api`: FastAPI app mounted under `/api/v1`.
- `alert-worker`: AMQP consumer for domain events.
- `outbox relay`: publishes pending rows from `monitoring.outbox`.

## Endpoints

- `GET /api/v1/health`: liveness.
- `GET /api/v1/ready`: readiness for database and broker.
- `GET/POST/PUT/DELETE /api/v1/kpi-types`
- `GET/POST/PUT/DELETE /api/v1/alert-types`
- `GET /api/v1/risk-levels`
- `GET /api/v1/kpi-statuses`
- `GET /api/v1/ficha-trackings`
- `GET/POST /api/v1/ficha-trackings/{id}/tracking-sessions`
- `GET/POST /api/v1/kpi-trackings`
- `GET/PUT /api/v1/generated-alerts`
- `GET/POST/PUT /api/v1/improvement-plans`
- `GET /api/v1/reports/kpi-summary`
- `GET /api/v1/reports/indicators`

`POST /api/v1/kpis` was removed in HU-MON-002. The final contract uses `POST /api/v1/kpi-trackings`; no temporary compatibility endpoint is exposed.

## Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `DATABASE_URL` | API ready / worker yes | SQLAlchemy PostgreSQL URL for `monitoring_db`. |
| `AMQP_URL` | readiness / worker yes | RabbitMQ URL. |
| `JWKS_URL` | API auth integration | IAM JWKS endpoint. Current local tests use unsigned fixture JWTs. |
| `API_HOST` | no | HTTP bind host, default `0.0.0.0`. |
| `API_PORT` | no | HTTP port, default `8000`. |
| `REQUEST_TIMEOUT_SECONDS` | no | Outbound timeout budget, default `10`. |
| `READINESS_TIMEOUT_SECONDS` | no | Dependency readiness timeout, default `2`. |
| `OTEL_ENABLED` | no | Enables OpenTelemetry setup, default `true`. |
| `OTEL_SERVICE_NAME` | no | Default `monitoring-service`. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | no | OTLP HTTP collector endpoint. |
| `DEPLOYMENT_ENVIRONMENT` | no | OTel resource attribute, default `local`. |
| `LOG_LEVEL` | no | Python log level, default `INFO`. |
| `ALERT_WORKER_MODE` | no | `consumer` or `relay`, default `consumer`. |

## Local commands

```powershell
python -m pip install -e ".[dev]"
python -m uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
python -m apps.alert_worker.main
$env:ALERT_WORKER_MODE="relay"; python -m apps.alert_worker.main
```

## Quality

```powershell
python -m ruff check apps src tests
python -m compileall apps src tests
python -m pytest -q
```

PostgreSQL integration tests run only when `MONITORING_TEST_DATABASE_URL` is set.

## Observability

Observability follows ADR-008:

- JSON logs include `trace_id`, `correlation_id`, and `event_id` when available.
- FastAPI and SQLAlchemy are instrumented with OpenTelemetry when dependencies are installed and `OTEL_ENABLED=true`.
- The worker emits counters for processed events and generated alerts.
- AMQP messages preserve envelope `correlation_id` and event identifiers.

Monitoring only publishes alert events. Notification owns channels, delivery attempts, and notification delivery records.
