from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


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


def _normalize_family(value: object) -> tuple[str, str, str] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    family = tuple(str(part or "").strip() for part in value)
    if not all(family):
        return None
    return family  # type: ignore[return-value]


def _priority_payload(now: datetime | None = None) -> dict | None:
    path = _priority_marker_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    expires_raw = str(payload.get("expires_at") or "").strip()
    if not expires_raw:
        return None
    try:
        expires_at = datetime.fromisoformat(expires_raw)
    except ValueError:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if current.astimezone(timezone.utc) >= expires_at.astimezone(timezone.utc):
        return None
    return payload


def mark_money_path_substrate_priority(
    *,
    reason: str,
    ttl_seconds: float | None = None,
    families: Iterable[tuple[str, str, str]] | None = None,
) -> None:
    """Tell broad substrate warmers to yield briefly to live-money recapture."""

    now = datetime.now(timezone.utc)
    ttl = _priority_ttl_seconds() if ttl_seconds is None else max(1.0, min(float(ttl_seconds), 180.0))
    merged_families: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    existing = _priority_payload(now)
    existing_families = existing.get("families", []) if isinstance(existing, dict) else []
    for family in existing_families:
        normalized = _normalize_family(family)
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged_families.append(normalized)
    for family in families or ():
        normalized = _normalize_family(family)
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged_families.append(normalized)
    payload = {
        "reason": str(reason or "money_path_substrate_refresh"),
        "pid": os.getpid(),
        "requested_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        "families": [list(family) for family in merged_families],
    }
    path = _priority_marker_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def clear_money_path_substrate_priority(*, pid: int | None = None) -> None:
    """Clear this process' broad-warmer yield marker after the money path is done."""

    path = _priority_marker_path()
    if pid is not None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        try:
            marker_pid = int(payload.get("pid"))
        except (TypeError, ValueError):
            return
        if marker_pid != int(pid):
            return
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def money_path_substrate_priority_active(now: datetime | None = None) -> bool:
    return _priority_payload(now) is not None


def money_path_substrate_priority_families(
    now: datetime | None = None,
) -> list[tuple[str, str, str]]:
    """Current live-money families that must be refreshed before broad backlog."""

    payload = _priority_payload(now)
    if not isinstance(payload, dict):
        return []
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in payload.get("families", []):
        family = _normalize_family(raw)
        if family and family not in seen:
            seen.add(family)
            out.append(family)
    return out
