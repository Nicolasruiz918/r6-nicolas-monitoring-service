"""Compatibility helpers for R6's TEXT -> JSONB expand/contract migration."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import bindparam, cast, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.types import Text


def encode_specific_actions(value: str) -> dict[str, Any]:
    """Encode without splitting or normalizing user text."""
    return {"version": 1, "actions": [{"text": value}]}


def decode_specific_actions(value: Any) -> str:
    """Return the legacy API contract from either TEXT or the R6 document."""
    if isinstance(value, dict):
        actions = value.get("actions")
        if isinstance(actions, list) and len(actions) == 1 and isinstance(actions[0], dict):
            text_value = actions[0].get("text")
            if isinstance(text_value, str):
                return text_value
    return "" if value is None else str(value)


def has_v2_column(session: Session) -> bool:
    columns = inspect(session.bind).get_columns("improvement_plan", schema="monitoring")
    return any(column["name"] == "specific_actions_v2" for column in columns)


def uses_jsonb_contract(session: Session) -> bool:
    columns = inspect(session.bind).get_columns("improvement_plan", schema="monitoring")
    column = next((item for item in columns if item["name"] == "specific_actions"), None)
    return column is not None and "JSONB" in str(column["type"]).upper()


def jsonb_value(value: str) -> Any:
    """SQL expression used only after CONTRACT; avoids binding a dict as TEXT."""
    payload = bindparam(
        "specific_actions_json",
        json.dumps(encode_specific_actions(value), ensure_ascii=False),
        type_=Text(),
    )
    return cast(payload, JSONB)


def dual_write_v2(session: Session, plan_id: Any, value: str) -> None:
    """Write the transitional column when present; old schemas remain valid."""
    if has_v2_column(session):
        session.execute(
            text("UPDATE monitoring.improvement_plan SET specific_actions_v2 = CAST(:value AS jsonb) WHERE id = :id"),
            {"value": json.dumps(encode_specific_actions(value), ensure_ascii=False), "id": plan_id},
        )
