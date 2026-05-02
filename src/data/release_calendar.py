# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b release-calendar contract.
"""Source release calendar query layer for data-daemon readiness.

This module is deliberately read-only. It loads deployed machine law from
config/source_release_calendar.yaml and answers safe-fetch questions without
performing network I/O or writing readiness state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CALENDAR_PATH = PROJECT_ROOT / "config" / "source_release_calendar.yaml"


class FetchDecision(str, Enum):
    FETCH_ALLOWED = "FETCH_ALLOWED"
    SKIPPED_NOT_RELEASED = "SKIPPED_NOT_RELEASED"
    CALENDAR_UNKNOWN_BLOCKED = "CALENDAR_UNKNOWN_BLOCKED"
    OFF_CYCLE_BLOCKED = "OFF_CYCLE_BLOCKED"
    BACKFILL_ONLY_BLOCKED = "BACKFILL_ONLY_BLOCKED"
    PARTIAL_EXPECTED_RETRY = "PARTIAL_EXPECTED_RETRY"
    STALE_BLOCKED = "STALE_BLOCKED"


@dataclass(frozen=True)
class ReleaseCalendarEntry:
    calendar_id: str
    source_id: str
    track: str
    plane: str
    timezone_name: str
    cycle_hours_utc: tuple[int, ...]
    default_lag_minutes: int
    min_partial_lag_minutes: int | None
    max_source_lag_seconds: int
    parameter: str | None
    metric: str | None
    period_semantics: str | None
    expected_members: int | None
    expected_step_rule: str | None
    partial_policy: str
    live_authorization: bool
    authority_tier: str
    backfill_only: bool

    @property
    def key(self) -> tuple[str, str]:
        return (self.source_id, self.track)


def _require_mapping(value: object, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a mapping")
    return value


def _require_str(entry: Mapping[str, Any], field: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"release calendar entry missing non-empty {field}")
    return value


def _require_int(entry: Mapping[str, Any], field: str) -> int:
    value = entry.get(field)
    if not isinstance(value, int):
        raise ValueError(f"release calendar entry {field} must be an integer")
    return value


def _parse_entry(raw_entry: object) -> ReleaseCalendarEntry:
    entry = _require_mapping(raw_entry, context="release calendar entry")
    safe_fetch = _require_mapping(entry.get("safe_fetch"), context="safe_fetch")
    cycle_hours = entry.get("cycle_hours_utc")
    if not isinstance(cycle_hours, list) or not cycle_hours:
        raise ValueError("cycle_hours_utc must be a non-empty list")
    normalized_cycle_hours: list[int] = []
    for hour in cycle_hours:
        if not isinstance(hour, int) or hour < 0 or hour > 23:
            raise ValueError(f"cycle hour must be 0..23; got {hour!r}")
        normalized_cycle_hours.append(hour)

    default_lag = safe_fetch.get("default_lag_minutes")
    if not isinstance(default_lag, int) or default_lag < 0:
        raise ValueError("safe_fetch.default_lag_minutes must be a non-negative integer")
    min_partial_lag = safe_fetch.get("min_partial_lag_minutes")
    if min_partial_lag is not None and (not isinstance(min_partial_lag, int) or min_partial_lag < 0):
        raise ValueError("safe_fetch.min_partial_lag_minutes must be a non-negative integer")

    return ReleaseCalendarEntry(
        calendar_id=_require_str(entry, "calendar_id"),
        source_id=_require_str(entry, "source_id"),
        track=_require_str(entry, "track"),
        plane=_require_str(entry, "plane"),
        timezone_name=_require_str(entry, "timezone"),
        cycle_hours_utc=tuple(normalized_cycle_hours),
        default_lag_minutes=default_lag,
        min_partial_lag_minutes=min_partial_lag,
        max_source_lag_seconds=_require_int(entry, "max_source_lag_seconds"),
        parameter=entry.get("parameter") if isinstance(entry.get("parameter"), str) else None,
        metric=entry.get("metric") if isinstance(entry.get("metric"), str) else None,
        period_semantics=entry.get("period_semantics") if isinstance(entry.get("period_semantics"), str) else None,
        expected_members=entry.get("expected_members") if isinstance(entry.get("expected_members"), int) else None,
        expected_step_rule=entry.get("expected_step_rule") if isinstance(entry.get("expected_step_rule"), str) else None,
        partial_policy=_require_str(entry, "partial_policy"),
        live_authorization=bool(entry.get("live_authorization", False)),
        authority_tier=_require_str(entry, "authority_tier"),
        backfill_only=bool(entry.get("backfill_only", False)),
    )


def load_calendar_config(path: Path = DEFAULT_CALENDAR_PATH) -> dict[tuple[str, str], ReleaseCalendarEntry]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency is present in repo envs
        raise RuntimeError("PyYAML is required to load source_release_calendar.yaml") from exc

    raw = yaml.safe_load(path.read_text()) or {}
    root = _require_mapping(raw, context="source release calendar")
    if root.get("schema_version") != 1:
        raise ValueError("source release calendar schema_version must be 1")
    entries_raw = root.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        raise ValueError("source release calendar must contain entries")

    entries: dict[tuple[str, str], ReleaseCalendarEntry] = {}
    for raw_entry in entries_raw:
        entry = _parse_entry(raw_entry)
        if entry.key in entries:
            raise ValueError(f"duplicate release calendar entry for {entry.key!r}")
        entries[entry.key] = entry
    return entries


def get_entry(
    source_id: str,
    track: str = "default",
    *,
    path: Path = DEFAULT_CALENDAR_PATH,
    entries: Mapping[tuple[str, str], ReleaseCalendarEntry] | None = None,
) -> ReleaseCalendarEntry | None:
    registry = entries or load_calendar_config(path)
    return registry.get((source_id, track))


def _to_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def evaluate_safe_fetch(
    source_id: str,
    track: str,
    cycle_time: datetime,
    now_utc: datetime,
    *,
    allow_partial: bool = False,
    path: Path = DEFAULT_CALENDAR_PATH,
    entries: Mapping[tuple[str, str], ReleaseCalendarEntry] | None = None,
) -> tuple[FetchDecision, dict[str, object]]:
    entry = get_entry(source_id, track, path=path, entries=entries)
    if entry is None:
        return FetchDecision.CALENDAR_UNKNOWN_BLOCKED, {"reason": "calendar entry missing"}
    if entry.backfill_only:
        return FetchDecision.BACKFILL_ONLY_BLOCKED, {"reason": "source track is backfill-only"}

    cycle_utc = _to_utc(cycle_time, "cycle_time")
    now = _to_utc(now_utc, "now_utc")
    if (
        cycle_utc.hour not in entry.cycle_hours_utc
        or cycle_utc.minute != 0
        or cycle_utc.second != 0
        or cycle_utc.microsecond != 0
    ):
        return FetchDecision.OFF_CYCLE_BLOCKED, {
            "reason": "source cycle time is not a configured release cycle",
            "configured_cycle_hours_utc": entry.cycle_hours_utc,
            "cycle_time": cycle_utc,
            "authority_tier": entry.authority_tier,
        }
    elapsed = now - cycle_utc
    max_lag = timedelta(seconds=entry.max_source_lag_seconds)
    if elapsed > max_lag:
        return FetchDecision.STALE_BLOCKED, {
            "reason": "source cycle exceeded max lag",
            "authority_tier": entry.authority_tier,
            "elapsed_seconds": int(elapsed.total_seconds()),
        }

    required_lag_minutes = entry.default_lag_minutes
    if allow_partial and entry.min_partial_lag_minutes is not None:
        required_lag_minutes = entry.min_partial_lag_minutes
    next_safe_fetch_at = cycle_utc + timedelta(minutes=required_lag_minutes)
    if now < next_safe_fetch_at:
        return FetchDecision.SKIPPED_NOT_RELEASED, {
            "reason": "source cycle not past safe-fetch lag",
            "next_safe_fetch_at": next_safe_fetch_at,
            "lag_minutes_required": required_lag_minutes,
            "lag_minutes_elapsed": int(elapsed.total_seconds() // 60),
            "authority_tier": entry.authority_tier,
        }

    full_safe_fetch_at = cycle_utc + timedelta(minutes=entry.default_lag_minutes)
    if allow_partial and now < full_safe_fetch_at and entry.partial_policy == "BLOCK_LIVE":
        return FetchDecision.PARTIAL_EXPECTED_RETRY, {
            "reason": "partial fetch window reached but live policy blocks partial data",
            "next_safe_fetch_at": full_safe_fetch_at,
            "lag_minutes_required": entry.default_lag_minutes,
            "lag_minutes_elapsed": int(elapsed.total_seconds() // 60),
            "authority_tier": entry.authority_tier,
        }

    return FetchDecision.FETCH_ALLOWED, {
        "reason": "source cycle is past safe-fetch lag",
        "next_safe_fetch_at": next_safe_fetch_at,
        "lag_minutes_required": required_lag_minutes,
        "lag_minutes_elapsed": int(elapsed.total_seconds() // 60),
        "authority_tier": entry.authority_tier,
        "live_authorization": entry.live_authorization,
    }


def source_has_live_authorization(
    source_id: str,
    track: str = "default",
    *,
    path: Path = DEFAULT_CALENDAR_PATH,
) -> bool:
    entry = get_entry(source_id, track, path=path)
    return bool(entry and entry.live_authorization and not entry.backfill_only)


def list_entries_for_plane(
    plane: str,
    *,
    path: Path = DEFAULT_CALENDAR_PATH,
) -> tuple[ReleaseCalendarEntry, ...]:
    entries = load_calendar_config(path)
    return tuple(entry for entry in entries.values() if entry.plane == plane)
