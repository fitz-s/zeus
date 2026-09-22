# Created: 2026-05-19
# Last reused/audited: 2026-05-23
# Authority basis: docs/operations/task_2026-05-04_tigge_ingest_resilience/DESIGN_PHASE3_LIVE_ROUTING_FIX.md + PIPELINE_REVIEW.md §7
#   DAY0-P1 (2026-05-23): _query_metric changed from MAX(snapshot_id) to FULL_CONTRIBUTOR-first
#   selection (contributes_to_target_extrema=1, POSITIVE attribution, boundary_ambiguous=0).
#   Mirrors _EXTREMA_RANK_ORDER_BY from executable_forecast_reader. Fail-closed: no
#   FULL_CONTRIBUTOR → no rows returned (caller treats as missing forecast).
"""ECMWFOpenDataIngest — DB-backed adapter for the ecmwf_open_data forecast source.

LIVE TRADE BLOCKER FIX (2026-05-19)
------------------------------------
Root cause: src/data/ensemble_client.py:140-160 guard fails closed for ANY source
that has no ``ingest_class``. After K1 DB split commit eba80d2b9d (2026-05-14,
PR #114) dropped the role gate, ``ecmwf_open_data`` (the entry_primary candidate
per Phase 3 routing fix) was blocked unconditionally:

    SourceNotEnabled: ecmwf_open_data has no ingest_class — fetch_ensemble would
    route through the Open-Meteo broker for role='entry_primary' and label the
    result as source_id='ecmwf_open_data' (mis-provenance + training/serving skew)

The data IS available — 504 high_temp + 416 low_temp rows in ensemble_snapshots
for 2026-05-19 with 51 members each.  This class reads that table and returns a
properly-tagged ForecastBundle so the existing guard passes.

Design decision: new module mirrors tigge_db_fetcher patterns (zero blast radius
on the TIGGE path during a live-trade blocker).  No operator gate required:
ecmwf_open_data is enabled_by_default=True with no requires_operator_decision.
Follow-up: consider parameterising fetch_from_db(data_version_prefix, source_id)
to unify both fetchers once the live path is proven stable.

Reads only the exact current coordinate-bound OpenData dataset for each metric.
"""

from __future__ import annotations

import json
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

import numpy as np

from src.config import runtime_coordinate_manifest_json
from src.contracts.ensemble_snapshot_provenance import opendata_source_run_revision_suffix
from src.data.forecast_fetch_plan import data_version_for_track
from src.data.forecast_extrema_authority import POSITIVE_ATTRIBUTION_STATUS_SQL_IN_LIST
from src.data.forecast_ingest_protocol import (
    ForecastBundle,
    ForecastSourceHealth,
)
from src.state.db import get_forecasts_connection

if TYPE_CHECKING:
    from collections.abc import Sequence
    from src.config import City

_log = logging.getLogger(__name__)

SOURCE_ID = "ecmwf_open_data"
AUTHORITY_TIER = "FORECAST"
_FRESHNESS_WINDOW_HOURS = 24


class ECMWFOpenDataIngest:
    """ForecastIngestProtocol-compatible adapter for the ECMWF Open Data source.

    Reads from ``ensemble_snapshots`` (zeus-forecasts.db, K1 split) using
    metric-specific local-calendar-day windows. The 51-member extrema vector
    repeats across that local day's UTC instants, including 23/25-hour DST days,
    so target-date extraction preserves the stored member values.

    No operator gate required — ecmwf_open_data is ``enabled_by_default=True``
    with no ``requires_operator_decision`` flag.  The registry-level gate check
    in ``ensemble_client.fetch_ensemble`` runs before this class is instantiated.

    Metric independence (PIPELINE_REVIEW.md §7):
    When ``temperature_metric`` is supplied ('high' or 'low'), only that metric's
    rows are queried and assembled — no cross-metric dependency.  This prevents
    the fail-closed cross-metric drop where a missing LOW-OK row (91% of LOW rows
    are REJECTED_BOUNDARY_AMBIGUOUS) discards perfectly-good HIGH-OK rows.

    When ``temperature_metric=None`` (default), both metrics are combined as
    before (backward-compatible for telemetry/crosscheck callers).
    """

    source_id = SOURCE_ID
    authority_tier = AUTHORITY_TIER

    def __init__(
        self,
        city: "City | None" = None,
        temperature_metric: "str | None" = None,
    ) -> None:
        if temperature_metric is not None and temperature_metric not in ("high", "low"):
            raise ValueError(
                f"temperature_metric must be 'high', 'low', or None; got {temperature_metric!r}"
            )
        self._city = city
        self._temperature_metric = temperature_metric

    def fetch(
        self,
        run_init_utc: datetime,
        lead_hours: "Sequence[int]",
    ) -> ForecastBundle:
        """Return a source-stamped ECMWF Open Data bundle from the DB."""
        if self._city is None:
            raise ValueError("ECMWFOpenDataIngest requires a city to read from DB")
        bundle = _fetch_db_payload(
            self._city, run_init_utc, temperature_metric=self._temperature_metric
        )
        if bundle is None:
            raise ValueError(
                f"No VERIFIED ecmwf_open_data rows found in ensemble_snapshots "
                f"for city={getattr(self._city, 'name', self._city)!r} "
                f"within {_FRESHNESS_WINDOW_HOURS}h of {run_init_utc.isoformat()}"
            )
        return bundle

    def health_check(self) -> ForecastSourceHealth:
        """Report health by probing ensemble_snapshots."""
        ok = False
        message = "ecmwf_open_data: no VERIFIED rows in freshness window"
        try:
            conn = get_forecasts_connection()
            try:
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(hours=_FRESHNESS_WINDOW_HOURS)
                ).isoformat()
                manifest = runtime_coordinate_manifest_json()
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS cnt FROM ensemble_snapshots
                    WHERE source_id = ? AND authority = 'VERIFIED'
                      AND causality_status = 'OK'
                      AND dataset_id IN (?, ?)
                      AND json_extract(CASE WHEN json_valid(provenance_json) THEN provenance_json ELSE '{}' END, '$.manifest_sha256') = ?
                      AND datetime(recorded_at) > datetime(?)
                    """,
                    (SOURCE_ID, data_version_for_track("mx2t6_high", manifest),
                     data_version_for_track("mn2t6_low", manifest),
                     hashlib.sha256(manifest.encode()).hexdigest(), cutoff),
                ).fetchone()
                count = row[0] if row else 0
                ok = count > 0
                message = f"ecmwf_open_data: {count} VERIFIED rows within {_FRESHNESS_WINDOW_HOURS}h"
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            message = f"ecmwf_open_data: health check failed — {exc}"
        return ForecastSourceHealth(
            source_id=SOURCE_ID,
            ok=ok,
            checked_at=datetime.now(timezone.utc),
            message=message,
        )


def _coerce_utc_datetime(value: object, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return fallback
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fetch_db_payload(
    city: "City",
    fetch_time: datetime,
    temperature_metric: "str | None" = None,
) -> Optional[ForecastBundle]:
    """Query ensemble_snapshots for ecmwf_open_data rows and build a ForecastBundle.

    Metric independence (PIPELINE_REVIEW.md §7):
    When ``temperature_metric`` is 'high' or 'low', ONLY that metric's rows are
    queried and assembled.  The hourly grid is filled with the single metric's
    vector for all local-day hours (no cross-metric coupling).  This preserves the
    no-opposite-metric-substitution invariant while decoupling HIGH-OK availability
    from LOW-OK availability — removing the fail-closed cross-metric drop that
    killed HIGH-OK entries whenever LOW-OK rows were missing (91% of LOW rows are
    REJECTED_BOUNDARY_AMBIGUOUS due to boundary-policy §7.3).

    When ``temperature_metric=None`` (default / backward-compatible mode), both
    metrics are combined into the classic symmetric grid: morning hours (00-11 UTC)
    carry LOW, afternoon hours (12-23 UTC) carry HIGH.  Dates where only one metric
    is present are still skipped in combined mode (no opposite-metric substitution).

    Returns None when no qualifying rows are found; ``ECMWFOpenDataIngest.fetch()``
    raises ``ValueError`` on None (fails closed — no silent empty result).
    """
    if fetch_time.tzinfo is None:
        fetch_time = fetch_time.replace(tzinfo=timezone.utc)
    fetch_time = fetch_time.astimezone(timezone.utc)
    cutoff = (fetch_time - timedelta(hours=_FRESHNESS_WINDOW_HOURS)).isoformat()
    manifest_json = runtime_coordinate_manifest_json()
    identities: dict[str, dict[str, dict]] = {}

    if temperature_metric is not None:
        # --- Metric-specific path (HIGH-only or LOW-only) ---
        # Only query the requested metric — no cross-metric dependency.
        metric_rows = _query_metric(city.name, temperature_metric, cutoff, manifest_json=manifest_json, decision_time=fetch_time.isoformat())
        if not metric_rows:
            _log.warning(
                "ecmwf_open_data_ingest: no VERIFIED %s rows for city=%s within %dh",
                temperature_metric,
                city.name,
                _FRESHNESS_WINDOW_HOURS,
            )
            return None

        by_date: dict[str, list[float]] = {}
        provenance: dict[str, str | None] = {
            "issue_time": None,
            "available_at": None,
            "fetch_time": None,
            "recorded_at": None,
        }
        for row in metric_rows:
            target_date: str = row["target_date"]
            members_raw: list[float] = json.loads(row["members_json"])
            if len(members_raw) != 51:
                _log.warning(
                    "ecmwf_open_data_ingest: city=%s target_date=%s %s has %d members (expected 51), skipping",
                    city.name,
                    target_date,
                    temperature_metric,
                    len(members_raw),
                )
                continue
            by_date[target_date] = members_raw
            identities.setdefault(temperature_metric, {})[target_date] = _snapshot_identity(row)
            for key in ("issue_time", "available_at", "fetch_time", "recorded_at"):
                val = row[key]
                if val and (provenance[key] is None or val > provenance[key]):  # type: ignore[operator]
                    provenance[key] = val

        all_dates = sorted(by_date)
        if not all_dates:
            return None

        all_times: list[str] = []
        all_member_rows: list[list[float]] = []
        for date_str in all_dates:
            vec = by_date[date_str]
            local_start = datetime.fromisoformat(date_str).replace(tzinfo=ZoneInfo(city.timezone))
            instant = local_start.astimezone(timezone.utc)
            end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
            while instant < end:
                all_times.append(instant.isoformat())
                all_member_rows.append(list(vec))
                instant += timedelta(hours=1)

        synthesised_tag = f"ensemble_snapshots.ecmwf_open_data.{temperature_metric}_only"

    else:
        # --- Combined-metric path (backward-compatible) ---
        high_rows = _query_metric(city.name, "high", cutoff, manifest_json=manifest_json, decision_time=fetch_time.isoformat())
        low_rows = _query_metric(city.name, "low", cutoff, manifest_json=manifest_json, decision_time=fetch_time.isoformat())

        if not high_rows and not low_rows:
            _log.warning(
                "ecmwf_open_data_ingest: no VERIFIED rows for city=%s within %dh",
                city.name,
                _FRESHNESS_WINDOW_HOURS,
            )
            return None

        high_by_date: dict[str, list[float]] = {}
        low_by_date: dict[str, list[float]] = {}
        provenance = {
            "issue_time": None,
            "available_at": None,
            "fetch_time": None,
            "recorded_at": None,
        }

        for metric, metric_rows, bd in (("high", high_rows, high_by_date), ("low", low_rows, low_by_date)):
            for row in metric_rows:
                target_date = row["target_date"]
                members_raw = json.loads(row["members_json"])
                if len(members_raw) != 51:
                    _log.warning(
                        "ecmwf_open_data_ingest: city=%s target_date=%s has %d members (expected 51), skipping",
                        city.name,
                        target_date,
                        len(members_raw),
                    )
                    continue
                bd[target_date] = members_raw
                identities.setdefault(metric, {})[target_date] = _snapshot_identity(row)
                for key in ("issue_time", "available_at", "fetch_time", "recorded_at"):
                    val = row[key]
                    if val and (provenance[key] is None or val > provenance[key]):  # type: ignore[operator]
                        provenance[key] = val

        all_dates = sorted(set(high_by_date) | set(low_by_date))
        if not all_dates:
            return None

        all_times = []
        all_member_rows = []
        for date_str in all_dates:
            high_vec = high_by_date.get(date_str)
            low_vec = low_by_date.get(date_str)
            if high_vec is None or low_vec is None:
                # Fail closed for combined mode: opposite-metric substitution is mis-provenance.
                _log.warning(
                    "ecmwf_open_data_ingest: city=%s target_date=%s missing metric=%s "
                    "in combined mode — skipping date (no opposite-metric substitution)",
                    city.name,
                    date_str,
                    "high" if high_vec is None else "low",
                )
                continue
            for hour in range(24):
                all_times.append(f"{date_str}T{hour:02d}:00:00+00:00")
                use_high = hour >= 12
                chosen = high_vec if use_high else low_vec
                all_member_rows.append(list(chosen))

        synthesised_tag = "ensemble_snapshots.ecmwf_open_data.high+low"

    if not all_times:
        return None

    n_members = 51
    # Shape: (n_hours, 51) → transpose → (51, n_hours)
    members_hourly = np.array(all_member_rows, dtype=np.float64).T
    assert members_hourly.shape[0] == n_members

    fallback_dt = datetime.now(timezone.utc)
    run_init_dt = _coerce_utc_datetime(provenance["issue_time"], fallback_dt)
    captured_at = _coerce_utc_datetime(
        provenance["fetch_time"] or provenance["recorded_at"], fallback_dt
    )

    from src.data.forecast_source_registry import stable_payload_hash

    raw_payload: dict = {
        "source_id": SOURCE_ID,
        "times": all_times,
        "members_hourly": members_hourly.tolist(),
        "issue_time": (provenance["issue_time"] or run_init_dt.isoformat()),
        # C1-AVAIL-CLOCK (2026-06-16): NEVER fall back to run_init_dt (the model cycle) for
        # available_at — that stamped the cycle as proof-of-possession (~8.4h early) and poisoned
        # every downstream lineage clock + the fusion arrival gate. The real possession time is
        # fetch_time, stamped at the snapshot writer (evaluator.py:_store_ens_snapshot via
        # proof_of_possession_available_at). When no genuine possession time exists in the
        # provenance rows here, emit None (honest absence) — never a cycle guess. Downstream is
        # None-safe: ensemble_client only sets available_at when non-None, and the writer derives
        # it from fetch_time regardless.
        "available_at": provenance["available_at"],
        "fetch_time": captured_at.isoformat(),
        "captured_at": captured_at.isoformat(),
        "recorded_at": provenance["recorded_at"] or "",
        "synthesised_from": synthesised_tag,
        "coordinate_manifest_sha": hashlib.sha256(manifest_json.encode()).hexdigest(),
        "snapshot_identity_by_metric_target_date": identities,
        "data_version_by_metric": {
            metric: data_version_for_track("mx2t6_high" if metric == "high" else "mn2t6_low", manifest_json)
            for metric in identities
        },
        "data_version": (data_version_for_track("mx2t6_high" if temperature_metric == "high" else "mn2t6_low", manifest_json) if temperature_metric else None),
    }

    return ForecastBundle(
        source_id=SOURCE_ID,
        run_init_utc=run_init_dt,
        lead_hours=tuple(range(len(all_times))),
        captured_at=captured_at,
        raw_payload_hash=stable_payload_hash(raw_payload),
        authority_tier=AUTHORITY_TIER,
        ensemble_members=tuple(members_hourly.tolist()),
        raw_payload=raw_payload,
    )


def _snapshot_identity(row) -> dict:
    identity = {key: row[key] for key in (
        "snapshot_id", "dataset_id", "source_run_id", "manifest_hash",
        "issue_time", "available_at", "members_unit",
    )}
    identity["coordinate_manifest_sha"] = json.loads(row["provenance_json"])["manifest_sha256"]
    return identity


def _query_metric(
    city_name: str,
    temperature_metric: str,
    cutoff: str,
    *,
    manifest_json: str,
    decision_time: str,
) -> list:
    """Return VERIFIED rows from ensemble_snapshots for one temperature metric.

    DAY0-P1 run-selection rule (2026-05-23): selects the FULL_CONTRIBUTOR snapshot
    per (city, target_date, temperature_metric), not the latest-inserted one.

    Priority order (mirrors executable_forecast_reader._EXTREMA_RANK_ORDER_BY):
      1. FULL_CONTRIBUTOR first:
           contributes_to_target_extrema = 1
           AND forecast_window_attribution_status IN POSITIVE_SET
           AND boundary_ambiguous = 0
      2. Latest issue_time (DESC) — freshest FULL_CONTRIBUTOR run preferred
      3. snapshot_id DESC as tiebreaker (latest ingested within same cycle)

    Fail-closed: if no FULL_CONTRIBUTOR exists for a (city, target_date, metric),
    no row is returned for that date. _fetch_db_payload returns None and the caller
    (ECMWFOpenDataIngest.fetch) raises ValueError, causing ENS_FETCH_FAILED rejection.
    """
    # Use the central POSITIVE set from forecast_extrema_authority so any future
    # status additions propagate automatically to this query.
    _pos = POSITIVE_ATTRIBUTION_STATUS_SQL_IN_LIST  # e.g. ('CONTRIBUTES','EXPLICIT',...)
    track = "mx2t6_high" if temperature_metric == "high" else "mn2t6_low"
    data_version = data_version_for_track(track, manifest_json)
    manifest_sha = hashlib.sha256(manifest_json.encode()).hexdigest()
    manifest_run_suffix = manifest_sha + opendata_source_run_revision_suffix(data_version)
    source_run_prefix = f"{SOURCE_ID}:{track}:"
    conn = get_forecasts_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT
                snapshot_id, dataset_id, source_run_id, manifest_hash, members_unit, provenance_json,
                target_date,
                issue_time,
                available_at,
                fetch_time,
                recorded_at,
                members_json
            FROM ensemble_snapshots
            WHERE city = ?
              AND temperature_metric = ?
              AND source_id = ?
              AND authority = 'VERIFIED'
              AND causality_status = 'OK'
              AND dataset_id = ?
              AND json_extract(CASE WHEN json_valid(provenance_json) THEN provenance_json ELSE '{{}}' END, '$.manifest_sha256') = ?
              AND source_run_id = ? || strftime('%Y-%m-%dT%HZ', issue_time) || ':coordsha:' || ?
              AND available_at <= ?
              AND issue_time <= ?
              AND fetch_time <= ?
              AND julianday(recorded_at) <= julianday(?)
              AND datetime(recorded_at) > datetime(?)
              AND contributes_to_target_extrema = 1
              AND COALESCE(forecast_window_attribution_status, '') IN {_pos}
              AND COALESCE(boundary_ambiguous, 0) = 0
              AND snapshot_id = (
                  SELECT s2.snapshot_id
                  FROM ensemble_snapshots s2
                  WHERE s2.city = ensemble_snapshots.city
                    AND s2.target_date = ensemble_snapshots.target_date
                    AND s2.temperature_metric = ensemble_snapshots.temperature_metric
                    AND s2.source_id = ?
                    AND s2.authority = 'VERIFIED'
                    AND s2.causality_status = 'OK'
                    AND s2.dataset_id = ?
                    AND json_extract(CASE WHEN json_valid(s2.provenance_json) THEN s2.provenance_json ELSE '{{}}' END, '$.manifest_sha256') = ?
                    AND s2.source_run_id = ? || strftime('%Y-%m-%dT%HZ', s2.issue_time) || ':coordsha:' || ?
                    AND s2.available_at <= ?
                    AND s2.issue_time <= ?
                    AND s2.fetch_time <= ?
                    AND julianday(s2.recorded_at) <= julianday(?)
                    AND datetime(s2.recorded_at) > datetime(?)
                    AND s2.contributes_to_target_extrema = 1
                    AND COALESCE(s2.forecast_window_attribution_status, '') IN {_pos}
                    AND COALESCE(s2.boundary_ambiguous, 0) = 0
                  ORDER BY
                    s2.issue_time DESC,
                    s2.snapshot_id DESC
                  LIMIT 1
              )
            ORDER BY target_date ASC
            """,
            (
                city_name,
                temperature_metric,
                SOURCE_ID,
                data_version, manifest_sha, source_run_prefix, manifest_run_suffix,
                decision_time, decision_time, decision_time, decision_time,
                cutoff,
                SOURCE_ID,
                data_version, manifest_sha, source_run_prefix, manifest_run_suffix,
                decision_time, decision_time, decision_time, decision_time,
                cutoff,
            ),
        ).fetchall()
    finally:
        conn.close()
    return rows
