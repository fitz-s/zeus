"""Deterministic JSON hashing and idempotency helpers for EDLI events."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def _json_default(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def canonical_json(value: Any) -> str:
    """Return deterministic JSON for hashing and storage."""

    return json.dumps(
        value,
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def payload_hash(payload: Any) -> str:
    return sha256_text(canonical_json(payload))


def stable_event_id(*parts: str) -> str:
    return "edli_evt_" + sha256_text("|".join(parts))


def stable_idempotency_key(*parts: str) -> str:
    return "edli_idem_" + sha256_text("|".join(parts))
