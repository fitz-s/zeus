# Created: 2026-05-19
# Last reused/audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-04_tigge_ingest_resilience/DESIGN_PHASE3_LIVE_ROUTING_FIX.md + PIPELINE_REVIEW.md §7
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

The data IS available — 504 high_temp + 416 low_temp rows in ensemble_snapshots_v2
for 2026-05-19 with 51 members each.  This class reads that table and returns a
properly-tagged ForecastBundle so the existing guard passes.

Design decision: new module mirrors tigge_db_fetcher patterns (zero blast radius
on the TIGGE path during a live-trade blocker).  No operator gate required:
ecmwf_open_data is enabled_by_default=True with no requires_operator_decision.
Follow-up: consider parameterising fetch_from_db(data_version_prefix, source_id)
to unify both fetchers once the live path is proven stable.

Data version filter:
  ``data_version LIKE 'ecmwf_opendata_%'`` captures all four active variants:
    ecmwf_opendata_mx2t3_local_calendar_day_max_v1  (active write path, post-cutover)
    ecmwf_opendata_mn2t3_local_calendar_day_min_v1  (active write path, post-cutover)
    ecmwf_opendata_mx2t6_local_calendar_day_max_v1  (legacy, pre-cutover)
    ecmwf_opendata_mn2t6_local_calendar_day_min_v1  (legacy, pre-cutover)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import numpy as np

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
_DATA_VERSION_PREFIX = "ecmwf_opendata_"
_FRESHNESS_WINDOW_HOURS = 24


class ECMWFOpenDataIngest:
    """ForecastIngestProtocol-compatible adapter for the ECMWF Open Data source.

    Reads from ``ensemble_snapshots_v2`` (zeus-forecasts.db, K1 split) using
    the same grid-assembly strategy as tigge_db_fetcher: each target_date gets
    24 UTC timestamps, the 51-member vector is broadcast across all 24 columns
    so that ``member_maxes_for_target_date`` / ``member_mins_for_target_date``
    return exactly the stored value regardless of which hours are selected.

    No operator gate required — ecmwf_open_data is ``enabled_by_default=True``
    with no ``requires_operator_decision`` flag.  The registry-level gate check
    in ``ensemble_client.fetch_ensemble`` runs before this class is instantiated.

    Metric independence (PIPELINE_REVIEW.md §7):
    When ``temperature_metric`` is supplied ('high' or 'low'), only that metric's
    rows are queried and assembled — no cross-metric dependency.  This prevents
    the fail-closed cross-metric drop where a missing LOW-OK row (91% of LOW rows
    are REJECTED_BOUNDARY_AMBIGUOUS) discards perfectly-good HIGH-OK rows.

    When ``temperature_metric=None`` (default), both metrics are combined as
    before (backward-compatible for diagnostic/crosscheck callers).
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
                f"No VERIFIED ecmwf_open_data rows found in ensemble_snapshots_v2 "
                f"for city={getattr(self._city, 'name', self._city)!r} "
                f"within {_FRESHNESS_WINDOW_HOURS}h of {run_init_utc.isoformat()}"
            )
        return bundle

    def health_check(self) -> ForecastSourceHealth:
        """Report health by probing ensemble_snapshots_v2."""
        ok = False
        message = "ecmwf_open_data: no VERIFIED rows in freshness window"
        try:
            conn = get_forecasts_connection()
            try:
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(hours=_FRESHNESS_WINDOW_HOURS)
                ).isoformat()
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM ensemble_snapshots_v2
                    WHERE source_id = ?
                      AND authority = 'VERIFIED'
                      AND causality_status = 'OK'
                      AND data_version LIKE ?
                      AND datetime(recorded_at) > datetime(?)
                    """,
                    (SOURCE_ID, _DATA_VERSION_PREFIX + "%", cutoff),
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
    """Query ensemble_snapshots_v2 for ecmwf_open_data rows and build a ForecastBundle.

    Metric independence (PIPELINE_REVIEW.md §7):
    When ``temperature_metric`` is 'high' or 'low', ONLY that metric's rows are
    queried and assembled.  The hourly grid is filled with the single metric's
    vector for all 24 hours (no cross-metric coupling).  This preserves the
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

    if temperature_metric is not None:
        # --- Metric-specific path (HIGH-only or LOW-only) ---
        # Only query the requested metric — no cross-metric dependency.
        metric_rows = _query_metric(city.name, temperature_metric, cutoff)
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
            for hour in range(24):
                all_times.append(f"{date_str}T{hour:02d}:00:00+00:00")
                all_member_rows.append(list(vec))

        synthesised_tag = f"ensemble_snapshots_v2.ecmwf_open_data.{temperature_metric}_only"

    else:
        # --- Combined-metric path (backward-compatible) ---
        high_rows = _query_metric(city.name, "high", cutoff)
        low_rows = _query_metric(city.name, "low", cutoff)

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

        for metric_rows, bd in ((high_rows, high_by_date), (low_rows, low_by_date)):
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

        synthesised_tag = "ensemble_snapshots_v2.ecmwf_open_data.high+low"

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
        "available_at": (provenance["available_at"] or run_init_dt.isoformat()),
        "fetch_time": captured_at.isoformat(),
        "captured_at": captured_at.isoformat(),
        "recorded_at": provenance["recorded_at"] or "",
        "synthesised_from": synthesised_tag,
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


def _query_metric(
    city_name: str,
    temperature_metric: str,
    cutoff: str,
) -> list:
    """Return VERIFIED rows from ensemble_snapshots_v2 for one temperature metric."""
    conn = get_forecasts_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                target_date,
                issue_time,
                available_at,
                fetch_time,
                recorded_at,
                members_json
            FROM ensemble_snapshots_v2
            WHERE city = ?
              AND temperature_metric = ?
              AND source_id = ?
              AND authority = 'VERIFIED'
              AND causality_status = 'OK'
              AND data_version LIKE ?
              AND datetime(recorded_at) > datetime(?)
              AND snapshot_id = (
                  SELECT MAX(s2.snapshot_id)
                  FROM ensemble_snapshots_v2 s2
                  WHERE s2.city = ensemble_snapshots_v2.city
                    AND s2.target_date = ensemble_snapshots_v2.target_date
                    AND s2.temperature_metric = ensemble_snapshots_v2.temperature_metric
                    AND s2.source_id = ?
                    AND s2.authority = 'VERIFIED'
                    AND s2.causality_status = 'OK'
                    AND s2.data_version LIKE ?
                    AND datetime(s2.recorded_at) > datetime(?)
              )
            ORDER BY target_date ASC
            """,
            (
                city_name,
                temperature_metric,
                SOURCE_ID,
                _DATA_VERSION_PREFIX + "%",
                cutoff,
                SOURCE_ID,
                _DATA_VERSION_PREFIX + "%",
                cutoff,
            ),
        ).fetchall()
    finally:
        conn.close()
    return rows
