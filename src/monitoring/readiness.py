from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text  # type: ignore

try:
    import pika  # type: ignore
except ImportError:  # pragma: no cover
    pika = None  # type: ignore


@dataclass(frozen=True)
class ComponentStatus:
    status: str
    detail: str | None = None


class ReadinessChecker:
    def __init__(self, session_factory: Any | None, amqp_url: str | None, timeout_seconds: float = 2.0) -> None:
        self._session_factory = session_factory
        self._amqp_url = amqp_url
        self._timeout_seconds = timeout_seconds

    def check(self) -> dict[str, Any]:
        db = self._check_database()
        broker = self._check_broker()
        ready = db.status == "ok" and broker.status == "ok"
        return {
            "status": "ready" if ready else "not_ready",
            "components": {
                "database": db.__dict__,
                "broker": broker.__dict__,
            },
        }

    def _check_database(self) -> ComponentStatus:
        if self._session_factory is None:
            return ComponentStatus("error", "DATABASE_URL is not configured")
        try:
            with self._session_factory() as session:
                session.execute(text("SELECT 1")).scalar_one()
            return ComponentStatus("ok")
        except Exception as exc:  # noqa: BLE001 - readiness must report dependency failure.
            return ComponentStatus("error", str(exc))

    def _check_broker(self) -> ComponentStatus:
        if not self._amqp_url:
            return ComponentStatus("error", "AMQP_URL is not configured")
        if pika is None:
            return ComponentStatus("error", "pika dependency is not installed")
        try:
            params = pika.URLParameters(self._amqp_url)
            params.socket_timeout = self._timeout_seconds
            params.blocked_connection_timeout = self._timeout_seconds
            connection = pika.BlockingConnection(params)
            connection.close()
            return ComponentStatus("ok")
        except Exception as exc:  # noqa: BLE001 - readiness must report dependency failure.
            return ComponentStatus("error", str(exc))
