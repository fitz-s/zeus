"""Stable JSON canonicalization and hashing for decision certificates."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

CANONICALIZATION_VERSION = "decision-kernel-json-v1"


def normalize(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return normalize(dataclasses.asdict(value))
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): normalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    if isinstance(value, set):
        return [normalize(item) for item in sorted(value, key=lambda item: repr(item))]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
