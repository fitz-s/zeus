"""CALIBRATION_HARDENING packet — BATCH 1: K1-compliant Platt parameter projection.

Created: 2026-04-29
Last reused/audited: 2026-04-29
Authority basis: round3_verdict.md §1 #2 (FOURTH edge packet) + ULTIMATE_PLAN.md
§4 #2 ("Extended Platt (A·logit + B·lead_days + C) parameter monitoring; Monte
Carlo noise calibration vs realized; α-fusion weight tuning; double-bootstrap CI
tightness on small-sample bins"). EDGE_OBSERVATION + ATTRIBUTION_DRIFT + WS_OR_POLL
measurement-substrate-first pattern repeated.

K1 contract (mirrors src/state/edge_observation.py + attribution_drift.py + ws_poll_reaction.py):
  - Read-only projection. NO write path. NO JSON persistence. NO caches.
  - Reads canonical surfaces directly via src.calibration.store.list_active_platt_models_v2
    + list_active_platt_models_legacy (BATCH 1 read-side additions to store.py;
    pure SELECT, mirror load_platt_model[_v2] read filters: is_active=1 AND
    authority='VERIFIED').
  - Imports consolidated to top of file per Tier 2 Phase 4 LOW-CAVEAT-EO-2-1
    (cited by name above; mid-file imports with noqa are an anti-pattern).

KNOWN LIMITATIONS (per BATCH 1 boot §1 KEY OPEN QUESTIONS + GO_BATCH_1 PATH A
operator decision):

  PATH A "per-bucket-key snapshot" was chosen (PATH B decision-log JOIN
  attribution deferred as future enhancement; PATH C extending the writer
  to add strategy_key column is OUT-OF-SCOPE per dispatch
  "ANY mutation of platt_models_v2 ... tables (writer-side change)").

  - The dispatch's "(city, target_date, strategy_key)" framing was the
    EVALUATION-TIME identity; PERSISTENCE-TIME identity at HEAD is BUCKET-
    KEYED only:
      * platt_models (legacy): UNIQUE on bucket_key TEXT (= f"{cluster}_{season}"
        per src/calibration/manager.py:73)
      * platt_models_v2: UNIQUE on (temperature_metric, cluster, season,
        data_version, input_space, is_active) per
        src/state/schema/v2_schema.py:227-249
    Neither table carries strategy_key. Neither carries city as a separate
    column (cluster ≈ city per K3 / "one-cluster-per-city" per
    load_platt_model_v2 docstring). Neither carries target_date (Platt's
    lead_days is an INPUT FEATURE, not a key).
  - `strategy_key` is therefore NOT in the return shape. A future
    PATH B packet that JOINs against trade_decisions.calibration_model_version
    (src/state/db.py:592) could provide synthetic strategy attribution at
    measurement time; a future PATH C packet that adds a writer column
    would provide structural attribution.
  - drift.py exists at src/calibration/drift.py with a Hosmer-Lemeshow χ²
    test on (forecast, outcome) pairs — that measures FORECAST-CALIBRATION
    drift (output drift). This module's BATCH 2 detect_parameter_drift
    measures PARAMETER-TRAJECTORY drift over consecutive refits — they are
    parametrically different signals; both are valuable; neither subsumes
    the other.

UPSTREAM-CLIPPING INVARIANT (LOW-NUANCE-WP-2-1 carry-forward, WS_POLL
critic 24th cycle precedent):
  list_active_platt_models_v2 + _legacy filter to authority='VERIFIED' at
  the source (the read function's WHERE clause). By the time per-bucket
  dicts reach compute_platt_parameter_snapshot_per_bucket here,
  authority is GUARANTEED to be 'VERIFIED'. If a future caller bypasses
  these readers and feeds raw UNVERIFIED snapshots directly, that is an
  upstream contract violation (not a defect of this projection).

Per-bucket snapshot fields (per BATCH 1 boot §2 + GO_BATCH_1 §6.6
bootstrap-spread surfacing):
  - param_A, param_B, param_C: Platt coefficients
  - n_samples: training row count for the active fit
  - brier_insample: in-sample Brier score | None
  - fitted_at: ISO timestamp of the active fit
  - bootstrap_count: len(bootstrap_params) (typically 200 per platt.py L7)
  - bootstrap_A_std, bootstrap_B_std, bootstrap_C_std: σ_parameter from
    the persisted bootstrap_params_json — measures DBS-CI tightness
    WITHOUT tuning (KEY OPEN QUESTION #5 resolution; α-fusion + DBS-CI
    tuning explicitly OUT-OF-SCOPE per dispatch §NOT-IN-SCOPE)
  - bootstrap_A_p5, bootstrap_A_p95, bootstrap_B_p5, ..., bootstrap_C_p95:
    5/95 percentile bands per coefficient
  - sample_quality: 'insufficient' | 'low' | 'adequate' | 'high'
    (reuses _classify_sample_quality from edge_observation; 10/30/100
    thresholds are sibling-coherent across EO/AD/WP packets)
  - source: 'v2' | 'legacy' (KEY OPEN QUESTION #6 + GO_BATCH_1 §6.5
    answer: BOTH surfaces visible with explicit tag; matches manager.py
    L42-62 v2-then-legacy fallback dedup)
  - in_window: True iff fitted_at falls in [end - window_days, end]
  - window_start, window_end
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from src.calibration.store import (
    list_active_platt_models_legacy,
    list_active_platt_models_v2,
)
from src.state.edge_observation import _classify_sample_quality


def _parse_iso_to_dt(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp to a tz-aware datetime. Returns None on
    failure. Treats zoneless timestamps as UTC (defensive — fitted_at
    writers have varied across BATCH D refactors)."""
    if not ts:
        return None
    s = ts.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _resolve_window(window_days: int, end_date: str | None) -> tuple[str, str, datetime, datetime]:
    """Return (window_start_iso, window_end_iso, window_start_dt, window_end_dt).

    Sibling-coherent with src/state/ws_poll_reaction.py:_resolve_window
    (same calendar-day inclusive end + UTC anchoring)."""
    if window_days <= 0:
        raise ValueError(f"window_days must be positive; got {window_days}")
    if end_date is None:
        end = datetime.now(timezone.utc).date()
    else:
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    start = end - timedelta(days=window_days)
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc)
    return start.isoformat(), end.isoformat(), start_dt, end_dt


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Compute percentile (0..100) on a pre-sorted list. Linear-interpolation
    over the rank position. Returns None on empty input.

    Sibling-coherent copy of src/state/ws_poll_reaction.py:_percentile —
    duplicated rather than imported because edge_observation does not
    re-export it (yet); a future refactor could consolidate into a shared
    src/state/_stats_helpers.py if a third caller appears.
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo_idx = int(rank)
    hi_idx = min(lo_idx + 1, len(sorted_values) - 1)
    frac = rank - lo_idx
    return float(sorted_values[lo_idx] + frac * (sorted_values[hi_idx] - sorted_values[lo_idx]))


def _stddev(values: list[float]) -> float | None:
    """Population stddev on a list of floats. Returns None on empty input
    or single-value input (need >=2 to define spread).

    Population (not sample) stddev: matches the bootstrap-resample
    interpretation — the 200 bootstrap_params ARE the distribution, not
    a sample drawn from it. This is the same convention numpy.std uses
    by default (ddof=0).
    """
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    return float((sum((v - mean) ** 2 for v in values) / n) ** 0.5)


def _summarize_bootstrap(bootstrap_params: list) -> dict[str, Any]:
    """Compute per-coefficient std + p5/p95 bands from a bootstrap_params list.

    bootstrap_params is a list of [A_i, B_i, C_i] tuples (or 2-tuples for
    legacy bootstrap that pre-dates param_C). Returns a dict with
    bootstrap_count + per-coefficient std + percentile bands. Tolerant of
    empty / single-row / 2-vs-3-tuple bootstrap.

    Per BATCH 1 boot §1 KEY OPEN QUESTION #5: this surfaces DBS-CI
    tightness WITHOUT tuning anything. α-fusion weight tuning + DBS-CI
    tightness adjustment are explicitly OUT-OF-SCOPE per dispatch.
    """
    n = len(bootstrap_params)
    out: dict[str, Any] = {"bootstrap_count": n}
    if n == 0:
        for ch in ("A", "B", "C"):
            out[f"bootstrap_{ch}_std"] = None
            out[f"bootstrap_{ch}_p5"] = None
            out[f"bootstrap_{ch}_p95"] = None
        return out

    a_vals: list[float] = []
    b_vals: list[float] = []
    c_vals: list[float] = []
    for tup in bootstrap_params:
        if not isinstance(tup, (list, tuple)):
            continue
        if len(tup) >= 1:
            a_vals.append(float(tup[0]))
        if len(tup) >= 2:
            b_vals.append(float(tup[1]))
        if len(tup) >= 3:
            c_vals.append(float(tup[2]))

    for ch, vals in (("A", a_vals), ("B", b_vals), ("C", c_vals)):
        out[f"bootstrap_{ch}_std"] = _stddev(vals)
        sorted_vals = sorted(vals)
        out[f"bootstrap_{ch}_p5"] = _percentile(sorted_vals, 5.0)
        out[f"bootstrap_{ch}_p95"] = _percentile(sorted_vals, 95.0)
    return out


def _build_snapshot_record(
    *,
    bucket_key: str,
    source: str,  # 'v2' | 'legacy'
    raw: dict,
    window_start: str,
    window_end: str,
    window_start_dt: datetime,
    window_end_dt: datetime,
) -> dict[str, Any]:
    """Assemble the per-bucket snapshot dict from a raw store.py reader row."""
    fitted_dt = _parse_iso_to_dt(raw.get("fitted_at"))
    in_window = fitted_dt is not None and (window_start_dt <= fitted_dt <= window_end_dt)
    bootstrap_summary = _summarize_bootstrap(raw.get("bootstrap_params") or [])
    record: dict[str, Any] = {
        "bucket_key": bucket_key,
        "source": source,
        "param_A": raw.get("param_A"),
        "param_B": raw.get("param_B"),
        "param_C": raw.get("param_C"),
        "n_samples": int(raw.get("n_samples") or 0),
        "brier_insample": raw.get("brier_insample"),
        "fitted_at": raw.get("fitted_at"),
        "input_space": raw.get("input_space"),
        "sample_quality": _classify_sample_quality(int(raw.get("n_samples") or 0)),
        "in_window": in_window,
        "window_start": window_start,
        "window_end": window_end,
    }
    record.update(bootstrap_summary)
    # v2-only fields surfaced for downstream readers; left as None on legacy.
    record["temperature_metric"] = raw.get("temperature_metric")
    record["cluster"] = raw.get("cluster")
    record["season"] = raw.get("season")
    record["data_version"] = raw.get("data_version")
    return record


def compute_platt_parameter_snapshot_per_bucket(
    conn: sqlite3.Connection,
    window_days: int = 7,
    end_date: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Compute per-bucket-key Platt parameter snapshot for the current window.

    K1-compliant read-only. Reads list_active_platt_models_v2 +
    list_active_platt_models_legacy (canonical store.py readers; pure
    SELECT; is_active=1 + authority='VERIFIED' filter applied at the
    source). Returns a dict keyed by bucket_key (v2 model_key for v2
    rows; legacy bucket_key for legacy rows) with the full snapshot
    shape per row.

    Args:
        conn: open sqlite3 connection to a Zeus state DB
        window_days: window length in calendar days (default 7 = weekly)
        end_date: ISO YYYY-MM-DD inclusive end day; defaults to today UTC

    Returns:
        dict keyed by bucket_key. Each value:
        {
            bucket_key: str,
            source: 'v2' | 'legacy',
            param_A, param_B, param_C: float | None,
            n_samples: int,
            brier_insample: float | None,
            fitted_at: str (ISO),
            input_space: str,
            sample_quality: 'insufficient' | 'low' | 'adequate' | 'high',
            in_window: bool,
            window_start, window_end: ISO date,
            bootstrap_count: int,
            bootstrap_A_std, bootstrap_B_std, bootstrap_C_std: float | None,
            bootstrap_A_p5, bootstrap_A_p95: float | None,
            bootstrap_B_p5, bootstrap_B_p95: float | None,
            bootstrap_C_p5, bootstrap_C_p95: float | None,
            # v2-only (None on legacy):
            temperature_metric, cluster, season, data_version: str | None,
        }

    Coverage: v2 + legacy with explicit `source` tag. Mirrors
    src/calibration/manager.py L42-62 v2-then-legacy fallback dedup
    pattern. v2 rows take precedence when same logical bucket exists in
    both (legacy collision is uncommon post-migration but possible).
    """
    window_start, window_end, window_start_dt, window_end_dt = _resolve_window(window_days, end_date)

    out: dict[str, dict[str, Any]] = {}

    # v2 first (canonical post-Phase-9C surface).
    for raw in list_active_platt_models_v2(conn):
        bucket_key = raw["model_key"]
        out[bucket_key] = _build_snapshot_record(
            bucket_key=bucket_key,
            source="v2",
            raw=raw,
            window_start=window_start,
            window_end=window_end,
            window_start_dt=window_start_dt,
            window_end_dt=window_end_dt,
        )

    # Legacy second; v2 entries win on collision (same logical bucket).
    for raw in list_active_platt_models_legacy(conn):
        bucket_key = raw["bucket_key"]
        if bucket_key in out:
            # v2 already covered this bucket key — skip the legacy duplicate.
            # (Sibling-coherent with manager.py:L42-62 fallback warning dedup.)
            continue
        out[bucket_key] = _build_snapshot_record(
            bucket_key=bucket_key,
            source="legacy",
            raw=raw,
            window_start=window_start,
            window_end=window_end,
            window_start_dt=window_start_dt,
            window_end_dt=window_end_dt,
        )

    return out
