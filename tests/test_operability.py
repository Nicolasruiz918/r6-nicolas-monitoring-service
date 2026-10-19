import json
import logging
from io import StringIO
from uuid import uuid4

from monitoring.config import Settings
from monitoring.observability import JsonFormatter, configure_otel, log_event
from monitoring.readiness import ReadinessChecker


class OkSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement):
        return self

    def scalar_one(self):
        return 1


def test_structured_logs_include_trace_correlation_and_event_id() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("monitoring")
    old_handlers = logger.handlers
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    event_id = str(uuid4())
    correlation_id = str(uuid4())

    try:
        log_event("test_event", event_id=event_id, correlation_id=correlation_id)
    finally:
        logger.handlers = old_handlers
        logger.propagate = True

    payload = json.loads(stream.getvalue())
    assert payload["message"] == "test_event"
    assert payload["event_id"] == event_id
    assert payload["correlation_id"] == correlation_id
    assert "trace_id" in payload


def test_readiness_checker_validates_database_and_broker_dependency(monkeypatch) -> None:
    class FakeConnection:
        def close(self):
            return None

    class FakePika:
        @staticmethod
        def URLParameters(url):
            return type("Params", (), {"socket_timeout": None, "blocked_connection_timeout": None})()

        @staticmethod
        def BlockingConnection(params):
            return FakeConnection()

    monkeypatch.setattr("monitoring.readiness.pika", FakePika)
    checker = ReadinessChecker(lambda: OkSession(), "amqp://guest:guest@localhost:5672/", 1)

    assert checker.check()["status"] == "ready"


def test_otel_configuration_is_safe_when_exporter_is_disabled() -> None:
    settings = Settings(
        database_url=None,
        amqp_url=None,
        jwks_url=None,
        api_host="0.0.0.0",
        api_port=8000,
        request_timeout_seconds=10,
        readiness_timeout_seconds=2,
        otel_enabled=False,
        otel_service_name="monitoring-service-test",
        otel_exporter_otlp_endpoint=None,
        deployment_environment="test",
        log_level="INFO",
        alert_worker_mode="consumer",
    )

    configure_otel(settings)
