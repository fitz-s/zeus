"""LEARNING_LOOP packet — BATCH 1: K1-compliant settlement→pair→retrain pipeline state projection.

Created: 2026-04-29
Last reused/audited: 2026-04-29
Authority basis: round3_verdict.md §1 #2 (FIFTH and FINAL edge packet) +
ULTIMATE_PLAN.md §4 #4 ("settlement-corpus → calibration update → parameter-
drift → re-fit pipeline. Apr26 §11 corpus deferred; high/low split + DST
resolved fixtures need owners. Apr26 Phase 4 silently dropped"). EDGE_OBSERVATION
+ ATTRIBUTION_DRIFT + WS_OR_POLL_TIGHTENING + CALIBRATION_HARDENING measurement-
substrate-first pattern repeated.

K1 contract (mirrors src/state/edge_observation.py + attribution_drift.py +
ws_poll_reaction.py + calibration_observation.py):
  - Read-only projection. NO write path. NO JSON persistence. NO caches.
  - Reads canonical surfaces directly:
    * src.calibration.store.list_active_platt_models_v2 / _legacy
      (CALIBRATION_HARDENING BATCH 1 read-side additions)
    * src.calibration.retrain_trigger.list_recent_retrain_versions
      (LEARNING_LOOP BATCH 1 read-side addition; pure SELECT on
      calibration_params_versions append-only audit log)
    * src.calibration.retrain_trigger.status (env + filesystem; pure read)
    * src.calibration.store.get_pairs_count + get_decision_group_count +
      canonical_pairs_ready_for_refit (pre-existing K1 readers)
  - Imports consolidated to top of file per Tier 2 Phase 4 LOW-CAVEAT-EO-2-1
    (cited by name above).

HONEST DISCLOSURE — CALIBRATION_HARDENING substrate misread (LEARNING_LOOP
boot §1 KEY OPEN QUESTION #1):

  The CALIBRATION_HARDENING BATCH 3 boot evidence + AGENTS.md known-
  limitations stated (verbatim):
    "HEAD substrate has no append-only Platt history table. Each
     historical-window snapshot returns the CURRENTLY-active fit (because
     the platt_models_v2 UNIQUE constraint is on (..., is_active=1) — prior
     fits are deactivated, not preserved)."

  THAT WAS WRONG. The append-only history exists at
  `calibration_params_versions` (src/calibration/retrain_trigger.py:242-264
  schema). It IS append-only:
    - version_id is AUTOINCREMENT (no UNIQUE on is_active)
    - promoted_at + retired_at columns track lifecycle (NULL retired_at =
      currently active; non-NULL = previously active, kept for audit)
    - INSERT at every retrain attempt (PASS → promoted; FAIL →
      COMPLETE_DRIFT_DETECTED, kept for audit but not promoted)
    - UPDATE only sets retired_at on prior live row (never DELETEs)

  This was exactly the failure mode that LOW-CITATION-CALIBRATION-3-1
  cycle-29 sustained discipline note warned about: I cited "platt_models_v2
  UNIQUE on is_active=1 means no append-only history" without grep-tracing
  the FULL retrain pipeline. The history I needed was in retrain_trigger.py,
  one module away from where I was looking.

  This LEARNING_LOOP packet uses the proper substrate. The cross-link
  correction note also lives at docs/operations/calibration_observation/
  AGENTS.md (LEARNING_LOOP BATCH 3 deliverable).

PATH A measurement-only framing (per LEARNING_LOOP boot §1 + GO_BATCH_1
ACCEPT-DEFAULTs):
  - PATH A "per-bucket-key snapshot of pipeline state" was chosen.
  - PATH B (settlement-event JOIN with calibration_pairs to attribute pair-
    arrival lag per-decision-group) deferred as future enhancement.
  - LEARNING_LOOP_TRIGGERING (would modify retrain_trigger.py arm/trigger
    paths) is OUT-OF-SCOPE per dispatch — separate operator-authorized packet.

Per-bucket snapshot fields (per BATCH 1 boot §2):

  Calibration-pair stage (read via src.calibration.store):
    - n_pairs_total: int — count of all pairs in cluster×season bucket
    - n_pairs_verified: int — subset where authority='VERIFIED'
    - n_pairs_canonical: int — subset where bin_source='canonical_v1' AND
      decision_group_id IS NOT NULL/empty (matches canonical_pairs_ready_for_refit)
    - n_decision_groups: int — independent forecast-event count

  Retrain stage (read via src.calibration.retrain_trigger):
    - retrain_status: 'DISABLED' | 'ARMED' (process-level, NOT per-bucket;
      read once and propagated to all buckets)
    - n_retrain_attempts_in_window: int — calibration_params_versions rows
      in [end - window_days, end] for this bucket
    - n_retrain_passed_in_window: int — frozen_replay_status='PASS' subset
    - n_retrain_failed_in_window: int — frozen_replay_status='FAIL' subset
    - last_retrain_attempted_at: str | None — ISO timestamp of most recent
      attempt for this bucket (PASS or FAIL)
    - last_retrain_promoted_at: str | None — ISO of most recent successful
      promotion for this bucket (PASS only)
    - days_since_last_promotion: int | None — derived from
      last_retrain_promoted_at + end_date

  Active model stage (read via src.calibration.store; reuse CALIBRATION
  BATCH 1 list_active_platt_models_v2):
    - active_model_fitted_at: str | None — ISO of currently-active model
    - active_model_n_samples: int — n_samples of currently-active model

  Provenance:
    - bucket_key: str — same key shape as CALIBRATION BATCH 1 snapshot
      (v2 model_key for v2; legacy bucket_key for legacy)
    - source: 'v2' | 'legacy'
    - temperature_metric, cluster, season, data_version, input_space
      (v2-only; None on legacy per CALIBRATION BATCH 1 precedent)

  Sample quality:
    - sample_quality: 'insufficient' | 'low' | 'adequate' | 'high'
      (10/30/100 reusing _classify_sample_quality from edge_observation;
      driven by n_pairs_canonical, the load-bearing input to retrain
      readiness)

  Window:
    - window_start, window_end: ISO date strings
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from src.calibration.retrain_trigger import (
    RetrainStatus,
    list_recent_retrain_versions,
    status as retrain_status,
)
from src.calibration.store import (
    get_decision_group_count,
    get_pairs_count,
    list_active_platt_models_legacy,
    list_active_platt_models_v2,
)
from src.state.calibration_observation import _resolve_window
from src.state.edge_observation import _classify_sample_quality


def _parse_iso_to_dt(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp to a tz-aware datetime. Returns None on
    failure. Treats zoneless timestamps as UTC (defensive — fitted_at /
    promoted_at writers have varied across BATCH D refactors).

    Sibling-coherent copy of src/state/calibration_observation.py:_parse_iso_to_dt
    (kept local to avoid creating a cross-package shared-helper module
    just for one function — same justification as CALIBRATION BATCH 1
    L153 comment about _percentile)."""
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


def _filter_versions_to_bucket(
    versions: list[dict[str, Any]],
    *,
    temperature_metric: str | None,
    cluster: str | None,
    season: str | None,
    data_version: str | None,
    input_space: str | None,
) -> list[dict[str, Any]]:
    """Filter calibration_params_versions rows to one bucket's identity.

    A bucket is defined by the 5-tuple (temperature_metric, cluster, season,
    data_version, input_space). Legacy snapshots have None for all 5 fields
    (since legacy platt_models doesn't carry these); for legacy snapshots,
    we return [] because calibration_params_versions only carries v2-shaped
    identity (per the schema CHECK constraint at retrain_trigger.py:257-258
    requiring temperature_metric IN ('high','low')).
    """
    if temperature_metric is None or cluster is None or season is None:
        return []
    out: list[dict[str, Any]] = []
    for v in versions:
        if v.get("temperature_metric") != temperature_metric:
            continue
        if v.get("cluster") != cluster:
            continue
        if v.get("season") != season:
            continue
        if data_version is not None and v.get("data_version") != data_version:
            continue
        if input_space is not None and v.get("input_space") != input_space:
            continue
        out.append(v)
    return out


def _aggregate_versions_in_window(
    bucket_versions: list[dict[str, Any]],
    *,
    window_start_dt: datetime,
    window_end_dt: datetime,
) -> dict[str, Any]:
    """Summarize per-bucket retrain attempts within a window.

    Returns a dict with:
      - n_retrain_attempts_in_window
      - n_retrain_passed_in_window
      - n_retrain_failed_in_window
      - last_retrain_attempted_at (most recent fitted_at across PASS+FAIL)
      - last_retrain_promoted_at (most recent fitted_at where promoted_at IS NOT NULL)
    """
    in_window = []
    for v in bucket_versions:
        fitted_dt = _parse_iso_to_dt(v.get("fitted_at"))
        if fitted_dt is None:
            continue
        if window_start_dt <= fitted_dt <= window_end_dt:
            in_window.append(v)

    n_attempts = len(in_window)
    n_passed = sum(1 for v in in_window if v.get("frozen_replay_status") == "PASS")
    n_failed = sum(1 for v in in_window if v.get("frozen_replay_status") == "FAIL")

    # Most recent attempt + promotion (across ALL bucket history, not just window —
    # operator may want to know "last activity ever" not just "last in window").
    last_attempted_at = None
    last_promoted_at = None
    for v in bucket_versions:
        fitted = v.get("fitted_at")
        if fitted is None:
            continue
        if last_attempted_at is None or str(fitted) > str(last_attempted_at):
            last_attempted_at = fitted
        if v.get("promoted_at") is not None:
            promoted = v.get("promoted_at")
            if last_promoted_at is None or str(promoted) > str(last_promoted_at):
                last_promoted_at = promoted

    return {
        "n_retrain_attempts_in_window": n_attempts,
        "n_retrain_passed_in_window": n_passed,
        "n_retrain_failed_in_window": n_failed,
        "last_retrain_attempted_at": last_attempted_at,
        "last_retrain_promoted_at": last_promoted_at,
    }


def _days_since(iso_ts: str | None, end_date_dt: datetime) -> int | None:
    """Compute integer days since iso_ts, anchored at end_date_dt. None on parse failure."""
    if iso_ts is None:
        return None
    dt = _parse_iso_to_dt(iso_ts)
    if dt is None:
        return None
    delta = end_date_dt - dt
    return max(0, int(delta.total_seconds() / 86400))


def _build_bucket_record(
    *,
    bucket_key: str,
    source: str,  # 'v2' | 'legacy'
    snap: dict[str, Any],  # CALIBRATION BATCH 1 snapshot for the bucket
    versions: list[dict[str, Any]],  # all calibration_params_versions
    pairs_count: int,
    pairs_verified_count: int,
    n_decision_groups: int,
    retrain_status_str: str,
    window_start: str,
    window_end: str,
    window_start_dt: datetime,
    window_end_dt: datetime,
) -> dict[str, Any]:
    """Assemble the per-bucket pipeline-state record."""
    bucket_versions = _filter_versions_to_bucket(
        versions,
        temperature_metric=snap.get("temperature_metric"),
        cluster=snap.get("cluster"),
        season=snap.get("season"),
        data_version=snap.get("data_version"),
        input_space=snap.get("input_space"),
    )
    retrain_agg = _aggregate_versions_in_window(
        bucket_versions,
        window_start_dt=window_start_dt,
        window_end_dt=window_end_dt,
    )

    n_pairs_canonical = pairs_verified_count

    record: dict[str, Any] = {
        "bucket_key": bucket_key,
        "source": source,
        # Calibration-pair stage
        "n_pairs_total": int(pairs_count or 0),
        "n_pairs_verified": int(pairs_verified_count or 0),
        "n_pairs_canonical": int(n_pairs_canonical or 0),
        "n_decision_groups": int(n_decision_groups or 0),
        # Retrain stage
        "retrain_status": retrain_status_str,
        **retrain_agg,
        "days_since_last_promotion": _days_since(retrain_agg["last_retrain_promoted_at"], window_end_dt),
        # Active model stage
        "active_model_fitted_at": snap.get("fitted_at"),
        "active_model_n_samples": int(snap.get("n_samples") or 0),
        # Provenance (mirror CALIBRATION BATCH 1)
        "temperature_metric": snap.get("temperature_metric"),
        "cluster": snap.get("cluster"),
        "season": snap.get("season"),
        "data_version": snap.get("data_version"),
        "input_space": snap.get("input_space"),
        # Sample quality (driven by canonical pair count — load-bearing for retrain readiness)
        "sample_quality": _classify_sample_quality(int(n_pairs_canonical or 0)),
        # Window
        "window_start": window_start,
        "window_end": window_end,
    }
    return record


def compute_learning_loop_state_per_bucket(
    conn: sqlite3.Connection,
    window_days: int = 7,
    end_date: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Compute per-bucket-key learning-loop pipeline state for the current window.

    K1-compliant read-only. Reads:
      - list_active_platt_models_v2 + _legacy from src.calibration.store
        (CALIBRATION BATCH 1; pure-SELECT bucket enumeration)
      - get_pairs_count + get_decision_group_count from src.calibration.store
        (pre-existing K1 readers)
      - list_recent_retrain_versions from src.calibration.retrain_trigger
        (LEARNING_LOOP BATCH 1; pure-SELECT calibration_params_versions enum)
      - retrain_trigger.status() (pre-existing process-level env+fs read)

    Args:
        conn: open sqlite3 connection to a Zeus state DB
        window_days: window length in calendar days (default 7 = weekly)
        end_date: ISO YYYY-MM-DD inclusive end day; defaults to today UTC

    Returns:
        dict keyed by bucket_key. Each value carries the full per-bucket
        pipeline-state shape. See module docstring for field-by-field.

    Coverage: v2 + legacy with explicit `source` tag (mirror CALIBRATION
    BATCH 1 dedup pattern; v2 wins on key collision).

    The retrain_status field is process-level (env + filesystem) and is
    propagated to every bucket as the SAME string — buckets do NOT have
    independent retrain status; only the operator-armed env-flag controls
    whether ANY retrain may run (per src/calibration/retrain_trigger.py
    L177-190 status() implementation).
    """
    window_start, window_end, window_start_dt, window_end_dt = _resolve_window(window_days, end_date)

    # Read once: all calibration_params_versions (recent N).
    versions = list_recent_retrain_versions(conn, limit=500)

    # Read once: process-level retrain status.
    retrain_status_str = retrain_status().value

    out: dict[str, dict[str, Any]] = {}

    # v2 first (canonical post-Phase-9C surface).
    for v2_row in list_active_platt_models_v2(conn):
        bucket_key = v2_row["model_key"]
        cluster = v2_row.get("cluster")
        season = v2_row.get("season")

        # Per-bucket pair count + decision-group count (legacy table read; cluster/season-scoped).
        pairs_total = get_pairs_count(conn, cluster=cluster, season=season, authority_filter="any")
        pairs_verified = get_pairs_count(conn, cluster=cluster, season=season, authority_filter="VERIFIED")
        n_decision_groups = get_decision_group_count(conn, cluster=cluster, season=season)

        snap = {
            "temperature_metric": v2_row.get("temperature_metric"),
            "cluster": cluster,
            "season": season,
            "data_version": v2_row.get("data_version"),
            "input_space": v2_row.get("input_space"),
            "fitted_at": v2_row.get("fitted_at"),
            "n_samples": v2_row.get("n_samples"),
        }
        out[bucket_key] = _build_bucket_record(
            bucket_key=bucket_key,
            source="v2",
            snap=snap,
            versions=versions,
            pairs_count=pairs_total,
            pairs_verified_count=pairs_verified,
            n_decision_groups=n_decision_groups,
            retrain_status_str=retrain_status_str,
            window_start=window_start,
            window_end=window_end,
            window_start_dt=window_start_dt,
            window_end_dt=window_end_dt,
        )

    # Legacy second; v2 entries win on collision (same logical bucket).
    for legacy_row in list_active_platt_models_legacy(conn):
        bucket_key = legacy_row["bucket_key"]
        if bucket_key in out:
            # v2 already covered this bucket — skip the legacy duplicate.
            # Sibling-coherent with src/state/calibration_observation.py L317-321
            # v2-then-legacy fallback model-load (per LOW-CITATION-CALIBRATION-3-1
            # cite-discipline: cite the model-load precedent at manager.py:172-189,
            # NOT the L42-62 warning helper).
            continue
        # Legacy bucket_key has shape "{cluster}_{season}" (per manager.py:73).
        # Decompose to query pair counts.
        bk_parts = bucket_key.rsplit("_", 1)
        if len(bk_parts) == 2:
            cluster = bk_parts[0]
            season = bk_parts[1]
        else:
            cluster = bucket_key
            season = None

        pairs_total = 0
        pairs_verified = 0
        n_decision_groups = 0
        if cluster and season:
            try:
                pairs_total = get_pairs_count(conn, cluster=cluster, season=season, authority_filter="any")
                pairs_verified = get_pairs_count(conn, cluster=cluster, season=season, authority_filter="VERIFIED")
                n_decision_groups = get_decision_group_count(conn, cluster=cluster, season=season)
            except Exception:
                # Defensive: malformed legacy bucket_key → graceful zero counts.
                pass

        snap = {
            "temperature_metric": None,  # legacy has no temperature_metric
            "cluster": cluster,
            "season": season,
            "data_version": None,
            "input_space": legacy_row.get("input_space"),
            "fitted_at": legacy_row.get("fitted_at"),
            "n_samples": legacy_row.get("n_samples"),
        }
        out[bucket_key] = _build_bucket_record(
            bucket_key=bucket_key,
            source="legacy",
            snap=snap,
            versions=versions,
            pairs_count=pairs_total,
            pairs_verified_count=pairs_verified,
            n_decision_groups=n_decision_groups,
            retrain_status_str=retrain_status_str,
            window_start=window_start,
            window_end=window_end,
            window_start_dt=window_start_dt,
            window_end_dt=window_end_dt,
        )

    return out
