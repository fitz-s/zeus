from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _priority_marker_path() -> Path:
    from src.config import state_path

    path = state_path("locks/market_substrate_priority.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _priority_ttl_seconds(default: float = 35.0) -> float:
    try:
        raw = float(os.environ.get("ZEUS_MARKET_SUBSTRATE_PRIORITY_TTL_SECONDS", default))
    except (TypeError, ValueError):
        raw = default
    return max(1.0, min(raw, 180.0))


def mark_money_path_substrate_priority(*, reason: str, ttl_seconds: float | None = None) -> None:
    """Tell broad substrate warmers to yield briefly to live-money recapture."""

    now = datetime.now(timezone.utc)
    ttl = _priority_ttl_seconds() if ttl_seconds is None else max(1.0, min(float(ttl_seconds), 180.0))
    payload = {
        "reason": str(reason or "money_path_substrate_refresh"),
        "pid": os.getpid(),
        "requested_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
    }
    path = _priority_marker_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def money_path_substrate_priority_active(now: datetime | None = None) -> bool:
    path = _priority_marker_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expires_raw = str(payload.get("expires_at") or "").strip()
    if not expires_raw:
        return False
    try:
        expires_at = datetime.fromisoformat(expires_raw)
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) < expires_at.astimezone(timezone.utc)
