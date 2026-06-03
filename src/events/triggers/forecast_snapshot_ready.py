"""ForecastSnapshotReadyTrigger for EDLI v1."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from src.data.forecast_target_contract import OPENDATA_MAX_STEP_HOURS
from src.events.event_writer import EventWriter, EventWriteResult
from src.events.opportunity_event import (
    ForecastSnapshotReadyPayload,
    OpportunityEvent,
    make_opportunity_event,
)

UTC = timezone.utc

LiveEligibilityReader = Callable[[dict[str, Any], dict[str, Any], dict[str, Any], datetime], bool]


@dataclass(frozen=True)
class ForecastSnapshotClassification:
    completeness_status: str
    required_steps_present: bool
    required_fields_present: bool
    live_eligible: bool
    reason: str


def ecmwf_open_data_expected_steps(cycle_hour: int) -> tuple[int, ...]:
    """Return the OpenData candidate step grid Zeus fetches, capped at the 5-day horizon.

    5-day cap (2026-05-29): Polymarket retired markets beyond 5 days, so Zeus fetches
    only the 3h-native grid through OPENDATA_MAX_STEP_HOURS (144h). The former 0/12
    long tail (150-360h) is no longer fetched; demanding it here would leave the
    completeness path fail-closed. All four cycles now share the same 0..144h grid.
    Callers window-filter this candidate grid to the target market's window.
    """

    if cycle_hour in {0, 12, 6, 18}:
        return tuple(range(0, OPENDATA_MAX_STEP_HOURS + 1, 3))
    raise ValueError(f"unsupported ECMWF cycle hour: {cycle_hour}")


def classify_forecast_snapshot(
    *,
    source_run: dict[str, Any],
    coverage: dict[str, Any],
    snapshot: dict[str, Any],
    decision_time: datetime,
    min_members_floor: int = 40,
    live_eligibility_reader: LiveEligibilityReader | None = None,
) -> ForecastSnapshotClassification:
    decision_utc = decision_time.astimezone(UTC)
    available_at = _parse_utc(
        snapshot.get("available_at")
        or source_run.get("source_available_at")
        or coverage.get("computed_at"),
        "available_at",
    )
    if available_at > decision_utc:
        return ForecastSnapshotClassification(
            "PARTIAL_BLOCKED", False, False, False, "AVAILABLE_AT_IN_FUTURE"
        )
    for value, reason in (
        (source_run.get("source_available_at"), "SOURCE_AVAILABLE_AT_IN_FUTURE"),
        (coverage.get("computed_at"), "COVERAGE_COMPUTED_AT_IN_FUTURE"),
    ):
        if value and _parse_utc(value, reason) > decision_utc:
            return ForecastSnapshotClassification(
                "PARTIAL_BLOCKED", False, False, False, reason
            )

    expected_steps = _required_expected_steps(source_run=source_run, coverage=coverage)
    if not expected_steps:
        return ForecastSnapshotClassification(
            "PARTIAL_BLOCKED", False, False, False, "EXPECTED_STEPS_UNKNOWN"
        )
    observed_steps = _json_list(coverage.get("observed_steps_json") or source_run.get("observed_steps_json"))
    required_steps_present = set(expected_steps).issubset(set(observed_steps))

    expected_members = _int_value(
        coverage.get("expected_members") or source_run.get("expected_members") or 51
    )
    observed_members = _int_value(
        coverage.get("observed_members")
        or source_run.get("observed_members")
        or snapshot.get("member_count")
        or len(_json_list(snapshot.get("members_json")))
    )
    required_fields_present = all(
        snapshot.get(field)
        for field in (
            "snapshot_id",
            "snapshot_hash",
            "city",
            "target_date",
            "temperature_metric",
            "source_run_id",
            "available_at",
        )
    )

    # Coverage (source_run_coverage) is the WINDOW-SCOPED completeness authority per the
    # redesign. The whole-run source_run.completeness_status reflects full-run fetch state
    # (PARTIAL under the OpenData 5-day/144h cap, plus member-count accounting orphaned from
    # the snapshot write) and must NOT veto a forecast the coverage layer has certified
    # complete for the target window. This is an authority correction, not a relaxation:
    # the COMPLETE branch below STILL requires required_steps_present (window steps observed),
    # observed_members >= expected_members, and reader_live (the executable-forecast reader).
    source_complete = (
        coverage.get("completeness_status") == "COMPLETE"
        and coverage.get("readiness_status") == "LIVE_ELIGIBLE"
    )
    reader_live = (
        bool(live_eligibility_reader(source_run, coverage, snapshot, decision_time))
        if live_eligibility_reader is not None
        else source_complete
    )

    if (
        source_complete
        and required_steps_present
        and required_fields_present
        and observed_members >= expected_members
        and reader_live
    ):
        return ForecastSnapshotClassification("COMPLETE", True, True, True, "COMPLETE")

    if required_steps_present and required_fields_present and observed_members >= min_members_floor:
        return ForecastSnapshotClassification(
            "PARTIAL_ALLOWED", True, True, False, "EVIDENCE_ONLY_NOT_COMPLETE"
        )

    return ForecastSnapshotClassification(
        "PARTIAL_BLOCKED",
        required_steps_present,
        required_fields_present,
        False,
        "MISSING_REQUIRED_STEPS_OR_MEMBERS",
    )


def build_forecast_snapshot_ready_event(
    *,
    source_run: dict[str, Any],
    coverage: dict[str, Any],
    snapshot: dict[str, Any],
    decision_time: datetime,
    received_at: str,
    min_members_floor: int = 40,
    live_eligibility_reader: LiveEligibilityReader | None = None,
    source: str | None = None,
) -> OpportunityEvent:
    classification = classify_forecast_snapshot(
        source_run=source_run,
        coverage=coverage,
        snapshot=snapshot,
        decision_time=decision_time,
        min_members_floor=min_members_floor,
        live_eligibility_reader=live_eligibility_reader,
    )
    expected_steps = _required_expected_steps(source_run=source_run, coverage=coverage)
    observed_steps = _json_list(coverage.get("observed_steps_json") or source_run.get("observed_steps_json"))
    expected_members = _int_value(coverage.get("expected_members") or source_run.get("expected_members") or 51)
    observed_members = _int_value(
        coverage.get("observed_members")
        or source_run.get("observed_members")
        or snapshot.get("member_count")
        or len(_json_list(snapshot.get("members_json")))
    )
    available_at = str(snapshot.get("available_at") or source_run.get("source_available_at"))
    payload = ForecastSnapshotReadyPayload(
        city=str(snapshot.get("city") or coverage.get("city")),
        target_date=str(snapshot.get("target_date") or coverage.get("target_local_date")),
        metric=str(snapshot.get("temperature_metric") or coverage.get("temperature_metric")),
        source_id=str(source_run.get("source_id") or coverage.get("source_id")),
        source_run_id=str(source_run.get("source_run_id") or coverage.get("source_run_id")),
        cycle=str(source_run.get("source_cycle_time") or ""),
        track=str(source_run.get("track") or coverage.get("track") or ""),
        snapshot_id=str(snapshot.get("snapshot_id")),
        snapshot_hash=str(snapshot.get("snapshot_hash") or snapshot.get("manifest_hash") or ""),
        captured_at=str(source_run.get("captured_at") or snapshot.get("fetch_time") or received_at),
        available_at=available_at,
        required_fields_present=classification.required_fields_present,
        required_steps_present=classification.required_steps_present,
        member_count=observed_members,
        min_members_floor=min_members_floor,
        completeness_status=classification.completeness_status,  # type: ignore[arg-type]
        required_steps=[int(step) for step in expected_steps],
        observed_steps=[int(step) for step in observed_steps],
        expected_members=expected_members,
        source_run_status=str(source_run.get("status") or ""),
        source_run_completeness_status=str(source_run.get("completeness_status") or ""),
        coverage_completeness_status=str(coverage.get("completeness_status") or ""),
        coverage_readiness_status=str(coverage.get("readiness_status") or ""),
    )
    entity_key = "|".join((payload.city, payload.target_date, payload.metric, payload.source_run_id))
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=entity_key,
        # Per-cycle distinct source (continuous re-decision) → distinct idempotency_key → the same
        # committed family re-emits a fresh FSR-equivalent each reactor cycle instead of deduping to
        # the consumed one. Default source preserves the one-shot catch-up behavior.
        source=source if source is not None else "forecast_snapshot_ready_trigger",
        observed_at=str(source_run.get("captured_at") or snapshot.get("fetch_time") or available_at),
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=payload.snapshot_id,
        payload=payload,
        # COMPLETE families emit at an elevated priority so freshly-captured, market-backed
        # families (only these emit now — see the market_events filter in scan_committed_snapshots)
        # are processed ahead of any older lower-priority backlog under the reactor's
        # `ORDER BY priority DESC, available_at ASC` fetch. Without this, newest-available_at
        # families are perpetually starved at the tail behind the existing backlog.
        priority=50 if classification.completeness_status == "COMPLETE" else 0,
    )


class ForecastSnapshotReadyTrigger:
    def __init__(
        self,
        writer: EventWriter,
        *,
        live_eligibility_reader: LiveEligibilityReader | None = None,
        min_members_floor: int = 40,
    ) -> None:
        self._writer = writer
        self._live_eligibility_reader = live_eligibility_reader
        self._min_members_floor = min_members_floor

    def emit_from_rows(
        self,
        *,
        source_run: dict[str, Any],
        coverage: dict[str, Any],
        snapshot: dict[str, Any],
        decision_time: datetime,
        received_at: str,
        source: str | None = None,
    ) -> EventWriteResult:
        event = build_forecast_snapshot_ready_event(
            source_run=source_run,
            coverage=coverage,
            snapshot=snapshot,
            decision_time=decision_time,
            received_at=received_at,
            min_members_floor=self._min_members_floor,
            live_eligibility_reader=self._live_eligibility_reader,
            source=source,
        )
        return self._writer.write(event)

    def scan_committed_snapshots(
        self,
        *,
        forecasts_conn: sqlite3.Connection,
        decision_time: datetime,
        received_at: str,
        limit: int = 100,
        source: str | None = None,
        already_pending_keys: set[str] | None = None,
    ) -> list[EventWriteResult]:
        """Catch up from committed source_run/source_run_coverage/snapshot rows.

        When ``source`` is supplied (continuous re-decision), each emitted event uses it as the
        event ``source`` so the idempotency_key differs per cycle and committed families re-emit a
        fresh FSR-equivalent (instead of deduping to the consumed one) → the reactor re-decides
        every cycle against just-in-time-refreshed prices. ``already_pending_keys`` (entity_keys with
        an unprocessed event) are skipped so the re-decision scan does not pile duplicates onto the
        pending queue. Both default-None → the original one-shot catch-up behavior is unchanged.
        """

        if not all(_table_exists(forecasts_conn, table) for table in _FORECAST_TABLES):
            return []
        # Decision-first emission: a family with no Polymarket market (no market_events row)
        # can never trade, so it must not consume the reactor's bounded decision-proof budget
        # (market-less families would otherwise starve the market-backed ones at 10/cycle).
        # Fail-open: if market_events is entirely empty (no market knowledge yet, e.g. fresh
        # boot / tests) emit all and let the executable-snapshot gate filter downstream; once
        # any market exists, require the family to have one. Non-permanent — re-scanned every
        # cycle, so a family emits as soon as its market is discovered.
        market_filter = ""
        if _table_exists(forecasts_conn, "market_events"):
            market_filter = (
                " AND (NOT EXISTS (SELECT 1 FROM market_events)"
                " OR EXISTS (SELECT 1 FROM market_events m"
                " WHERE m.city = c.city"
                " AND m.target_date = c.target_local_date"
                " AND m.temperature_metric = c.temperature_metric))"
            )
        rows = _dict_rows(
            forecasts_conn,
            f"""
            SELECT
                c.*,
                sr.source_cycle_time AS sr_source_cycle_time,
                sr.source_issue_time AS sr_source_issue_time,
                sr.source_release_time AS sr_source_release_time,
                sr.source_available_at AS sr_source_available_at,
                sr.fetch_started_at AS sr_fetch_started_at,
                sr.fetch_finished_at AS sr_fetch_finished_at,
                sr.captured_at AS sr_captured_at,
                sr.status AS sr_status,
                sr.completeness_status AS sr_completeness_status,
                sr.expected_steps_json AS sr_expected_steps_json,
                sr.observed_steps_json AS sr_observed_steps_json,
                sr.expected_members AS sr_expected_members,
                sr.observed_members AS sr_observed_members,
                s.snapshot_id,
                s.city AS snapshot_city,
                s.target_date AS snapshot_target_date,
                s.temperature_metric AS snapshot_temperature_metric,
                s.available_at AS snapshot_available_at,
                s.fetch_time AS snapshot_fetch_time,
                s.manifest_hash AS snapshot_manifest_hash,
                s.members_json AS snapshot_members_json
            FROM source_run_coverage c
            JOIN source_run sr ON sr.source_run_id = c.source_run_id
            JOIN ensemble_snapshots s
              ON s.source_run_id = c.source_run_id
             AND s.city = c.city
             AND s.target_date = c.target_local_date
             AND s.temperature_metric = c.temperature_metric
            WHERE COALESCE(s.available_at, sr.source_available_at, c.computed_at) <= ?
              AND (sr.source_available_at IS NULL OR sr.source_available_at <= ?)
              AND (c.computed_at IS NULL OR c.computed_at <= ?){market_filter}
            ORDER BY
                CASE WHEN c.readiness_status = 'LIVE_ELIGIBLE' THEN 0 ELSE 1 END ASC,
                c.computed_at DESC, s.available_at DESC, s.snapshot_id DESC
            LIMIT ?
            """,
            (
                decision_time.astimezone(UTC).isoformat(),
                decision_time.astimezone(UTC).isoformat(),
                decision_time.astimezone(UTC).isoformat(),
                limit,
            ),
        )
        pending_skip = already_pending_keys or set()
        results: list[EventWriteResult] = []
        for row in reversed(rows):
            source_run = _source_run_from_join(row)
            coverage = _coverage_from_join(row)
            snapshot = _snapshot_from_join(row)
            if pending_skip:
                city = str(snapshot.get("city") or coverage.get("city") or "")
                target_date = str(snapshot.get("target_date") or coverage.get("target_local_date") or "")
                metric = str(snapshot.get("temperature_metric") or coverage.get("temperature_metric") or "")
                source_run_id = str(source_run.get("source_run_id") or coverage.get("source_run_id") or "")
                entity_key = "|".join((city, target_date, metric, source_run_id))
                if entity_key in pending_skip:
                    continue
            results.append(
                self.emit_from_rows(
                    source_run=source_run,
                    coverage=coverage,
                    snapshot=snapshot,
                    decision_time=decision_time,
                    received_at=received_at,
                    source=source,
                )
            )
        return results


def executable_forecast_live_eligible_reader(
    forecasts_conn: sqlite3.Connection,
) -> LiveEligibilityReader:
    """Build a reader that delegates live eligibility to executable_forecast_reader."""

    def _read(
        source_run: dict[str, Any],
        coverage: dict[str, Any],
        snapshot: dict[str, Any],
        decision_time: datetime,
    ) -> bool:
        from src.data.executable_forecast_reader import SOURCE_TRANSPORT, read_executable_forecast_snapshot
        from src.data.forecast_target_contract import ForecastTargetScope

        target_date = date.fromisoformat(str(snapshot.get("target_date") or coverage["target_local_date"]))
        scope = ForecastTargetScope(
            city_id=str(coverage.get("city_id") or ""),
            city_name=str(snapshot.get("city") or coverage["city"]),
            city_timezone=str(coverage.get("city_timezone") or "UTC"),
            target_local_date=target_date,
            temperature_metric=str(snapshot.get("temperature_metric") or coverage["temperature_metric"]),
            data_version=str(coverage.get("data_version") or snapshot.get("data_version") or "v1"),
            target_window_start_utc=_parse_utc(coverage["target_window_start_utc"], "target_window_start_utc"),
            target_window_end_utc=_parse_utc(coverage["target_window_end_utc"], "target_window_end_utc"),
            source_cycle_time=_parse_utc(source_run["source_cycle_time"], "source_cycle_time"),
            required_step_hours=tuple(
                int(step) for step in _required_expected_steps(source_run=source_run, coverage=coverage)
            ),
            market_refs=(),
        )
        result = read_executable_forecast_snapshot(
            forecasts_conn,
            scope=scope,
            source_id=str(source_run["source_id"]),
            source_transport=str(coverage.get("source_transport") or SOURCE_TRANSPORT),
            source_run_id=str(source_run["source_run_id"]),
            now_utc=decision_time,
        )
        return result.ok and result.snapshot is not None and str(result.snapshot.snapshot_id) == str(snapshot["snapshot_id"])

    return _read


def _json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, tuple):
        return list(raw)
    if isinstance(raw, str):
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("expected JSON list")
        return parsed
    raise ValueError(f"expected list-like value, got {type(raw).__name__}")


def _required_expected_steps(*, source_run: dict[str, Any], coverage: dict[str, Any]) -> list[Any]:
    raw_steps = coverage.get("expected_steps_json") or source_run.get("expected_steps_json")
    steps = _json_list(raw_steps)
    if steps:
        return steps
    cycle = source_run.get("source_cycle_time") or coverage.get("source_cycle_time")
    if not cycle:
        return []
    cycle_time = _parse_utc(cycle, "source_cycle_time")
    window_start_raw = coverage.get("target_window_start_utc")
    window_end_raw = coverage.get("target_window_end_utc")
    if not (window_start_raw and window_end_raw):
        return []
    try:
        window_start = _parse_utc(window_start_raw, "target_window_start_utc")
        window_end = _parse_utc(window_end_raw, "target_window_end_utc")
    except ValueError:
        return []
    if window_end < window_start:
        return []
    cycle_hour = cycle_time.hour
    try:
        cycle_steps = ecmwf_open_data_expected_steps(cycle_hour)
    except ValueError:
        return []
    required_steps: list[int] = []
    for step in cycle_steps:
        valid_at = cycle_time + timedelta(hours=int(step))
        if window_start <= valid_at <= window_end:
            required_steps.append(int(step))
    return required_steps


def _parse_utc(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field_name} is required")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _int_value(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


_FORECAST_TABLES = ("source_run", "source_run_coverage", "ensemble_snapshots")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _dict_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    names = [description[0] for description in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _source_run_from_join(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_run_id": row["source_run_id"],
        "source_id": row["source_id"],
        "track": row["track"],
        "source_cycle_time": row.get("sr_source_cycle_time") or row.get("source_cycle_time"),
        "source_issue_time": row.get("sr_source_issue_time"),
        "source_release_time": row.get("sr_source_release_time"),
        "source_available_at": row.get("sr_source_available_at") or row.get("snapshot_available_at"),
        "fetch_started_at": row.get("sr_fetch_started_at"),
        "fetch_finished_at": row.get("sr_fetch_finished_at"),
        "captured_at": row.get("sr_captured_at") or row.get("snapshot_fetch_time"),
        "status": row.get("sr_status"),
        "completeness_status": row.get("sr_completeness_status"),
        "expected_members": row.get("sr_expected_members") or row.get("expected_members"),
        "observed_members": row.get("sr_observed_members") or row.get("observed_members"),
        "expected_steps_json": row.get("sr_expected_steps_json") or row.get("expected_steps_json"),
        "observed_steps_json": row.get("sr_observed_steps_json") or row.get("observed_steps_json"),
    }


def _coverage_from_join(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "coverage_id",
            "source_run_id",
            "source_id",
            "source_transport",
            "release_calendar_key",
            "track",
            "city_id",
            "city",
            "city_timezone",
            "target_local_date",
            "temperature_metric",
            "data_version",
            "expected_members",
            "observed_members",
            "expected_steps_json",
            "observed_steps_json",
            "snapshot_ids_json",
            "target_window_start_utc",
            "target_window_end_utc",
            "completeness_status",
            "readiness_status",
            "computed_at",
        )
    }


def _snapshot_from_join(row: dict[str, Any]) -> dict[str, Any]:
    members = _json_list(row.get("snapshot_members_json"))
    return {
        "snapshot_id": str(row["snapshot_id"]),
        "snapshot_hash": row.get("snapshot_manifest_hash") or row.get("snapshot_id"),
        "city": row.get("snapshot_city") or row.get("city"),
        "target_date": row.get("snapshot_target_date") or row.get("target_local_date"),
        "temperature_metric": row.get("snapshot_temperature_metric") or row.get("temperature_metric"),
        "source_run_id": row.get("source_run_id"),
        "available_at": row.get("snapshot_available_at") or row.get("sr_source_available_at"),
        "fetch_time": row.get("snapshot_fetch_time"),
        "member_count": len(members),
        "members_json": list(members),
        "data_version": row.get("data_version"),
    }
