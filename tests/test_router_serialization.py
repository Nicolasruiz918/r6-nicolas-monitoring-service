from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from monitoring.adapter.inbound.http.router import _serialize


def test_serialize_uuid_as_http_string() -> None:
    value = UUID("00000000-0000-0000-0000-000000000123")

    assert _serialize(value) == "00000000-0000-0000-0000-000000000123"


def test_serialize_dates_as_isoformat() -> None:
    assert _serialize(date(2026, 10, 19)) == "2026-10-19"
    assert _serialize(datetime(2026, 10, 19, 12, 30, tzinfo=timezone.utc)) == "2026-10-19T12:30:00+00:00"


def test_serialize_decimal_as_json_number() -> None:
    assert _serialize(Decimal("87.5000")) == 87.5
