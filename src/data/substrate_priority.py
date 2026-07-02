from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


def _priority_marker_path() -> Path:
    from src.config import state_path

    path = state_path("locks/market_substrate_priority.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _priority_receipt_path() -> Path:
    from src.config import state_path

    path = state_path("locks/market_substrate_priority_receipt.json")
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


def _normalize_condition_id(value: object) -> str | None:
    condition_id = str(value or "").strip()
    return condition_id or None


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
    condition_ids: Iterable[str] | None = None,
    merge_existing: bool = False,
) -> dict:
    """Request scoped sidecar substrate capture for current live-money work.

    The marker is a current-request contract, not a backlog.  Live money loops
    may mark different families every few seconds; default replacement prevents
    stale families from being carried forward by repeated TTL extension.
    """

    now = datetime.now(timezone.utc)
    ttl = _priority_ttl_seconds() if ttl_seconds is None else max(1.0, min(float(ttl_seconds), 180.0))
    merged_families: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    merged_condition_ids: list[str] = []
    seen_condition_ids: set[str] = set()
    existing = _priority_payload(now) if merge_existing else None
    if merge_existing:
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
    if merge_existing:
        existing_condition_ids = existing.get("condition_ids", []) if isinstance(existing, dict) else []
        for condition_id in existing_condition_ids:
            normalized = _normalize_condition_id(condition_id)
            if normalized and normalized not in seen_condition_ids:
                seen_condition_ids.add(normalized)
                merged_condition_ids.append(normalized)
    for condition_id in condition_ids or ():
        normalized = _normalize_condition_id(condition_id)
        if normalized and normalized not in seen_condition_ids:
            seen_condition_ids.add(normalized)
            merged_condition_ids.append(normalized)
    payload = {
        "request_id": uuid.uuid4().hex,
        "reason": str(reason or "money_path_substrate_refresh"),
        "pid": os.getpid(),
        "requested_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        "families": [list(family) for family in merged_families],
        "condition_ids": merged_condition_ids,
    }
    path = _priority_marker_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return {
        "request_id": str(payload.get("request_id") or "").strip(),
        "reason": str(payload.get("reason") or "").strip(),
        "requested_at": str(payload.get("requested_at") or "").strip(),
        "expires_at": str(payload.get("expires_at") or "").strip(),
        "pid": payload.get("pid"),
        "families": [tuple(family) for family in merged_families],
        "condition_ids": list(merged_condition_ids),
    }


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


def money_path_substrate_priority_condition_ids(now: datetime | None = None) -> list[str]:
    """Current live-money condition ids that the sidecar must refresh first."""

    payload = _priority_payload(now)
    if not isinstance(payload, dict):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in payload.get("condition_ids", []):
        condition_id = _normalize_condition_id(raw)
        if condition_id and condition_id not in seen:
            seen.add(condition_id)
            out.append(condition_id)
    return out


def money_path_substrate_priority_request(now: datetime | None = None) -> dict | None:
    """Return the active scoped sidecar request, if any.

    This is request evidence only.  Live decisions still require fresh executable
    snapshot rows; this file never proves price truth by itself.
    """

    payload = _priority_payload(now)
    if not isinstance(payload, dict):
        return None
    return {
        "request_id": str(payload.get("request_id") or "").strip(),
        "reason": str(payload.get("reason") or "").strip(),
        "requested_at": str(payload.get("requested_at") or "").strip(),
        "expires_at": str(payload.get("expires_at") or "").strip(),
        "pid": payload.get("pid"),
        "families": money_path_substrate_priority_families(now),
        "condition_ids": money_path_substrate_priority_condition_ids(now),
    }


def record_money_path_substrate_priority_receipt(
    *,
    request: dict | None,
    summary: dict | None,
    now: datetime | None = None,
) -> None:
    """Record sidecar service evidence for the latest scoped request."""

    if not isinstance(request, dict):
        return
    request_id = str(request.get("request_id") or "").strip()
    if not request_id:
        return
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    payload = {
        "request_id": request_id,
        "serviced_at": current.astimezone(timezone.utc).isoformat(),
        "families": [list(family) for family in request.get("families", [])],
        "condition_ids": list(request.get("condition_ids", [])),
        "summary": dict(summary or {}),
    }
    path = _priority_receipt_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def money_path_substrate_priority_receipt(
    *,
    request_id: str | None = None,
    now: datetime | None = None,
    max_age_seconds: float | None = None,
) -> dict | None:
    """Return sidecar service evidence for a priority request.

    A receipt is not executable price truth.  It only proves the sidecar serviced
    the matching request; live callers must still verify fresh snapshot rows for
    the exact condition scope before emitting money-path events.
    """

    path = _priority_receipt_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    expected = str(request_id or "").strip()
    actual = str(payload.get("request_id") or "").strip()
    if expected and actual != expected:
        return None
    if max_age_seconds is not None:
        try:
            max_age = max(0.0, float(max_age_seconds))
            serviced_at = datetime.fromisoformat(str(payload.get("serviced_at") or ""))
        except (TypeError, ValueError):
            return None
        if serviced_at.tzinfo is None:
            serviced_at = serviced_at.replace(tzinfo=timezone.utc)
        current = now if now is not None else datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        age = (
            current.astimezone(timezone.utc) - serviced_at.astimezone(timezone.utc)
        ).total_seconds()
        if age < 0.0 or age > max_age:
            return None
    return payload
