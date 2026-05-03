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
    HORIZON_OUT_OF_RANGE = "HORIZON_OUT_OF_RANGE"
    CALENDAR_UNKNOWN_BLOCKED = "CALENDAR_UNKNOWN_BLOCKED"
    OFF_CYCLE_BLOCKED = "OFF_CYCLE_BLOCKED"
    BACKFILL_ONLY_BLOCKED = "BACKFILL_ONLY_BLOCKED"
    PARTIAL_EXPECTED_RETRY = "PARTIAL_EXPECTED_RETRY"
    STALE_BLOCKED = "STALE_BLOCKED"


@dataclass(frozen=True)
class ReleaseCycleProfile:
    cycle_hours_utc: tuple[int, ...]
    horizon_profile: str
    max_step_hours: int
    live_max_step_hours: int | None
    default_lag_minutes: int
    min_partial_lag_minutes: int | None
    full_horizon_live_authorization: bool
    live_authorization: bool
    reason: str | None = None


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
    cycle_profiles: tuple[ReleaseCycleProfile, ...]
    source_transport_required: str | None = None

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


def _parse_cycle_hours(value: object) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("cycle_hours_utc must be a non-empty list")
    normalized_cycle_hours: list[int] = []
    for hour in value:
        if not isinstance(hour, int) or hour < 0 or hour > 23:
            raise ValueError(f"cycle hour must be 0..23; got {hour!r}")
        normalized_cycle_hours.append(hour)
    return tuple(normalized_cycle_hours)


def _parse_safe_fetch(safe_fetch: Mapping[str, Any]) -> tuple[int, int | None]:
    default_lag = safe_fetch.get("default_lag_minutes")
    if default_lag is None:
        default_lag = safe_fetch.get("conservative_not_before_minutes")
    if default_lag is None:
        default_lag = safe_fetch.get("derived_0_240_not_before_minutes")
    if not isinstance(default_lag, int) or default_lag < 0:
        raise ValueError("safe_fetch.default_lag_minutes must be a non-negative integer")
    min_partial_lag = safe_fetch.get("min_partial_lag_minutes")
    if min_partial_lag is not None and (not isinstance(min_partial_lag, int) or min_partial_lag < 0):
        raise ValueError("safe_fetch.min_partial_lag_minutes must be a non-negative integer")
    return default_lag, min_partial_lag


def _parse_cycle_profile(raw_profile: object) -> ReleaseCycleProfile:
    profile = _require_mapping(raw_profile, context="cycle profile")
    safe_fetch = _require_mapping(profile.get("safe_fetch"), context="cycle_profile.safe_fetch")
    default_lag, min_partial_lag = _parse_safe_fetch(safe_fetch)
    max_step_hours = _require_int(profile, "max_step_hours")
    live_max_step_hours = profile.get("live_max_step_hours")
    if live_max_step_hours is not None and (not isinstance(live_max_step_hours, int) or live_max_step_hours < 0):
        raise ValueError("cycle profile live_max_step_hours must be a non-negative integer")
    return ReleaseCycleProfile(
        cycle_hours_utc=_parse_cycle_hours(profile.get("cycle_hours_utc")),
        horizon_profile=_require_str(profile, "horizon_profile"),
        max_step_hours=max_step_hours,
        live_max_step_hours=live_max_step_hours,
        default_lag_minutes=default_lag,
        min_partial_lag_minutes=min_partial_lag,
        full_horizon_live_authorization=bool(profile.get("full_horizon_live_authorization", False)),
        live_authorization=bool(profile.get("live_authorization", profile.get("full_horizon_live_authorization", False))),
        reason=profile.get("reason") if isinstance(profile.get("reason"), str) else None,
    )


def _parse_entry(raw_entry: object) -> ReleaseCalendarEntry:
    entry = _require_mapping(raw_entry, context="release calendar entry")
    safe_fetch = _require_mapping(entry.get("safe_fetch"), context="safe_fetch")
    default_lag, min_partial_lag = _parse_safe_fetch(safe_fetch)
    cycle_profiles_raw = entry.get("cycle_profiles")
    if cycle_profiles_raw is not None:
        if not isinstance(cycle_profiles_raw, list) or not cycle_profiles_raw:
            raise ValueError("cycle_profiles must be a non-empty list")
        cycle_profiles = tuple(_parse_cycle_profile(profile) for profile in cycle_profiles_raw)
        normalized_cycle_hours = tuple(
            dict.fromkeys(hour for profile in cycle_profiles for hour in profile.cycle_hours_utc)
        )
    else:
        normalized_cycle_hours = _parse_cycle_hours(entry.get("cycle_hours_utc"))
        cycle_profiles = (
            ReleaseCycleProfile(
                cycle_hours_utc=normalized_cycle_hours,
                horizon_profile="legacy_flat",
                max_step_hours=_require_int(entry, "max_step_hours") if "max_step_hours" in entry else 10_000,
                live_max_step_hours=entry.get("live_max_step_hours") if isinstance(entry.get("live_max_step_hours"), int) else None,
                default_lag_minutes=default_lag,
                min_partial_lag_minutes=min_partial_lag,
                full_horizon_live_authorization=bool(entry.get("live_authorization", False)),
                live_authorization=bool(entry.get("live_authorization", False)),
            ),
        )

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
        cycle_profiles=cycle_profiles,
        source_transport_required=(
            entry.get("source_transport_required")
            if isinstance(entry.get("source_transport_required"), str)
            else None
        ),
    )


def load_calendar_config(path: Path = DEFAULT_CALENDAR_PATH) -> dict[tuple[str, str], ReleaseCalendarEntry]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency is present in repo envs
        raise RuntimeError("PyYAML is required to load source_release_calendar.yaml") from exc

    raw = yaml.safe_load(path.read_text()) or {}
    root = _require_mapping(raw, context="source release calendar")
    if root.get("schema_version") not in {1, 2}:
        raise ValueError("source release calendar schema_version must be 1 or 2")
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


def cycle_profile_for_hour(entry: ReleaseCalendarEntry, cycle_hour_utc: int) -> ReleaseCycleProfile | None:
    for profile in entry.cycle_profiles:
        if cycle_hour_utc in profile.cycle_hours_utc:
            return profile
    return None


def evaluate_safe_fetch(
    source_id: str,
    track: str,
    cycle_time: datetime,
    now_utc: datetime,
    *,
    allow_partial: bool = False,
    required_max_step_hours: int | None = None,
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
    profile = cycle_profile_for_hour(entry, cycle_utc.hour)
    if profile is None:
        return FetchDecision.OFF_CYCLE_BLOCKED, {
            "reason": "source cycle time has no configured cycle profile",
            "configured_cycle_hours_utc": entry.cycle_hours_utc,
            "cycle_time": cycle_utc,
            "authority_tier": entry.authority_tier,
        }
    if required_max_step_hours is not None:
        live_max = profile.live_max_step_hours if profile.live_max_step_hours is not None else profile.max_step_hours
        if required_max_step_hours > live_max:
            return FetchDecision.HORIZON_OUT_OF_RANGE, {
                "reason": profile.reason or "required target horizon exceeds cycle profile live horizon",
                "required_max_step_hours": required_max_step_hours,
                "max_step_hours": profile.max_step_hours,
                "live_max_step_hours": live_max,
                "horizon_profile": profile.horizon_profile,
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

    required_lag_minutes = profile.default_lag_minutes
    if allow_partial and profile.min_partial_lag_minutes is not None:
        required_lag_minutes = profile.min_partial_lag_minutes
    next_safe_fetch_at = cycle_utc + timedelta(minutes=required_lag_minutes)
    if now < next_safe_fetch_at:
        return FetchDecision.SKIPPED_NOT_RELEASED, {
            "reason": "source cycle not past safe-fetch lag",
            "next_safe_fetch_at": next_safe_fetch_at,
            "lag_minutes_required": required_lag_minutes,
            "lag_minutes_elapsed": int(elapsed.total_seconds() // 60),
            "authority_tier": entry.authority_tier,
        }

    full_safe_fetch_at = cycle_utc + timedelta(minutes=profile.default_lag_minutes)
    if allow_partial and now < full_safe_fetch_at and entry.partial_policy == "BLOCK_LIVE":
        return FetchDecision.PARTIAL_EXPECTED_RETRY, {
            "reason": "partial fetch window reached but live policy blocks partial data",
            "next_safe_fetch_at": full_safe_fetch_at,
            "lag_minutes_required": profile.default_lag_minutes,
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
        "horizon_profile": profile.horizon_profile,
        "live_max_step_hours": profile.live_max_step_hours,
    }


def select_source_run_for_target_horizon(
    *,
    now_utc: datetime,
    source_id: str,
    track: str,
    required_max_step_hours: int,
    cycle_policy: str = "latest_complete_full_horizon",
    path: Path = DEFAULT_CALENDAR_PATH,
) -> tuple[FetchDecision, dict[str, object]]:
    if cycle_policy != "latest_complete_full_horizon":
        raise ValueError(f"unsupported source cycle policy: {cycle_policy}")
    now = _to_utc(now_utc, "now_utc")
    entry = get_entry(source_id, track, path=path)
    if entry is None:
        return FetchDecision.CALENDAR_UNKNOWN_BLOCKED, {"reason": "calendar entry missing"}
    if entry.backfill_only:
        return FetchDecision.BACKFILL_ONLY_BLOCKED, {"reason": "source track is backfill-only"}

    live_profiles = tuple(profile for profile in entry.cycle_profiles if profile.live_authorization)
    if not live_profiles:
        return FetchDecision.HORIZON_OUT_OF_RANGE, {"reason": "no live-authorized cycle profile"}
    max_live_step = max(
        profile.live_max_step_hours if profile.live_max_step_hours is not None else profile.max_step_hours
        for profile in live_profiles
    )
    if required_max_step_hours > max_live_step:
        return FetchDecision.HORIZON_OUT_OF_RANGE, {
            "reason": "required target horizon exceeds all live-authorized profiles",
            "required_max_step_hours": required_max_step_hours,
            "max_live_step_hours": max_live_step,
        }

    candidate_cycles: list[datetime] = []
    for profile in live_profiles:
        live_max = profile.live_max_step_hours if profile.live_max_step_hours is not None else profile.max_step_hours
        if required_max_step_hours > live_max:
            continue
        for day_offset in (0, 1):
            base_date = (now - timedelta(days=day_offset)).date()
            for hour in profile.cycle_hours_utc:
                cycle = datetime.combine(base_date, datetime.min.time(), tzinfo=timezone.utc).replace(hour=hour)
                if cycle <= now:
                    candidate_cycles.append(cycle)
    for cycle_time in sorted(candidate_cycles, reverse=True):
        decision, metadata = evaluate_safe_fetch(
            source_id,
            track,
            cycle_time,
            now,
            required_max_step_hours=required_max_step_hours,
            path=path,
        )
        metadata["selected_cycle_time"] = cycle_time
        if decision is FetchDecision.FETCH_ALLOWED:
            return decision, metadata
    if candidate_cycles:
        cycle_time = sorted(candidate_cycles, reverse=True)[0]
        decision, metadata = evaluate_safe_fetch(
            source_id,
            track,
            cycle_time,
            now,
            required_max_step_hours=required_max_step_hours,
            path=path,
        )
        metadata["selected_cycle_time"] = cycle_time
        return decision, metadata
    return FetchDecision.HORIZON_OUT_OF_RANGE, {
        "reason": "no cycle profile can cover required target horizon",
        "required_max_step_hours": required_max_step_hours,
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
