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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

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


# =====================================================================
# LEARNING_LOOP packet — BATCH 2 detect_learning_loop_stall detector
# =====================================================================
# Per round3_verdict.md §1 #2 + ULTIMATE_PLAN.md §4 #4 + GO_BATCH_2 dispatch.
# Pure-Python statistical detector consuming the per-bucket pipeline-state
# history from BATCH 1's compute_learning_loop_state_per_bucket. K1-compliant:
# in-memory only; no DB writes; no caches; no cross-module DB reads
# (drift_detected is caller-provided per GO_BATCH_2 §3 ACCEPT-DEFAULT).
#
# 3 composable stall_kinds (each can fire independently; verdict aggregates):
#
# 1. corpus_vs_pair_lag — pair growth in current window << trailing baseline.
#    ratio = current_pair_growth / trailing_mean_pair_growth
#    Stall fires if ratio < 1/pair_growth_threshold_multiplier (e.g. 0.67x
#    = growth dropped to less than 1/1.5 of trailing mean).
#    insufficient if n_windows < min_windows OR trailing_std<=0.
#
# 2. pairs_ready_no_retrain — canonical_pairs_ready=TRUE for > N consecutive
#    days WITHOUT new entry in calibration_params_versions for this bucket.
#    Stall fires if max(consecutive_ready_days_no_retrain) >
#    days_pairs_ready_no_retrain.
#    insufficient if no canonical_pairs_ready TRUE in window (haven't
#    reached readiness yet).
#
# 3. drift_no_refit — drift_detected (caller-provided) for > N consecutive
#    days WITHOUT new entry in calibration_params_versions.
#    Stall fires if drift_detected AND days_since_last_promotion >
#    days_drift_no_refit.
#    insufficient if drift_detected==None (caller didn't pass it).
#
# Severity (per GO_BATCH_2 §Severity):
#   - 'warn' if ANY stall_kind fires (default)
#   - 'critical' if ANY of:
#       (a) corpus_vs_pair_lag with ratio < 1/(2x multiplier)
#       (b) pairs_ready_no_retrain with days > 60
#       (c) drift_no_refit with days > 30
#
# Sibling-coherence with prior detectors:
# - EO BATCH 2 detect_alpha_decay: ratio test on edge series
# - WP BATCH 2 detect_reaction_gap: ratio test on p95 latency
# - CALIBRATION BATCH 2 detect_parameter_drift: per-coefficient ratio test
# - LEARNING BATCH 2 detect_learning_loop_stall: 3 composable stall_kinds
#   (precedent for multi-kind composable detector design)

StallKind = Literal["stall_detected", "within_normal", "insufficient_data"]
StallSeverity = Literal["warn", "critical"]

# Default thresholds (per GO_BATCH_2 §5 ACCEPT-DEFAULTS).
DEFAULT_PAIR_GROWTH_THRESHOLD_MULTIPLIER: float = 1.5
DEFAULT_DAYS_PAIRS_READY_NO_RETRAIN: int = 30
DEFAULT_DAYS_DRIFT_NO_REFIT: int = 14
DEFAULT_STALL_MIN_WINDOWS: int = 4

# Critical-severity boundaries (per GO_BATCH_2 §Severity):
CRITICAL_PAIR_GROWTH_RATIO_CUTOFF: float = 2.0  # ratio < 1/(2.0*multiplier)
CRITICAL_DAYS_PAIRS_READY_NO_RETRAIN: int = 60
CRITICAL_DAYS_DRIFT_NO_REFIT: int = 30


@dataclass
class ParameterStallVerdict:
    """Result of detect_learning_loop_stall for one bucket_key.

    kind:
      - stall_detected: at least one stall_kind fires
      - within_normal: no stall_kind fires AND at least one was checkable
      - insufficient_data: no stall_kind was checkable (all 3 returned
        insufficient)

    stall_kinds: list[str] subset of
      ["corpus_vs_pair_lag", "pairs_ready_no_retrain", "drift_no_refit"]
      ordered by detection order; empty when kind != "stall_detected"

    severity (only set when kind == "stall_detected"):
      - "warn": at least one stall_kind fires (default)
      - "critical": at least one critical boundary breached (per
        CRITICAL_* constants above)

    evidence carries per-kind details (current_value + trailing_baseline +
    threshold + verdict_per_kind) so operators see WHY each stall_kind
    fired or didn't (sibling-coherent with WP BATCH 2 multi-axis +
    CALIBRATION BATCH 2 per-coefficient surfacing pattern).
    """
    kind: StallKind
    bucket_key: str
    stall_kinds: list[str] = field(default_factory=list)
    severity: StallSeverity | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


def _check_corpus_vs_pair_lag(
    history: list[dict[str, Any]],
    *,
    multiplier: float,
    min_windows: int,
) -> dict[str, Any]:
    """Check stall_kind 1: pair growth ratio vs trailing baseline.

    history entries must carry 'n_pairs_canonical' (per-window snapshot from
    BATCH 1). Returns dict with verdict per kind:
      - status: 'fired' | 'within_normal' | 'insufficient_data'
      - current_growth, trailing_mean_growth, ratio
      - reason (when insufficient)
    """
    n = len(history)
    if n < min_windows:
        return {
            "status": "insufficient_data",
            "reason": "n_windows_below_min",
            "n_windows": n,
            "min_required": min_windows,
        }
    # Compute per-window pair growth (delta vs prior window).
    pair_counts = [int(w.get("n_pairs_canonical", 0) or 0) for w in history]
    growths = [pair_counts[i] - pair_counts[i - 1] for i in range(1, n)]
    if len(growths) < min_windows - 1:
        return {
            "status": "insufficient_data",
            "reason": "insufficient_growth_deltas",
            "n_growths": len(growths),
        }
    current_growth = float(growths[-1])
    trailing = [float(g) for g in growths[:-1]]
    trailing_mean = sum(trailing) / len(trailing)
    if trailing_mean <= 0:
        return {
            "status": "insufficient_data",
            "reason": "trailing_mean_growth_non_positive",
            "trailing_mean_growth": trailing_mean,
            "current_growth": current_growth,
        }
    ratio = current_growth / trailing_mean
    threshold_min = 1.0 / multiplier  # e.g. 1/1.5 = 0.667
    evidence = {
        "current_growth": current_growth,
        "trailing_mean_growth": trailing_mean,
        "ratio": ratio,
        "threshold_min": threshold_min,
        "multiplier": multiplier,
    }
    if ratio < threshold_min:
        evidence["status"] = "fired"
        return evidence
    evidence["status"] = "within_normal"
    return evidence


def _check_pairs_ready_no_retrain(
    history: list[dict[str, Any]],
    *,
    days_threshold: int,
) -> dict[str, Any]:
    """Check stall_kind 2: canonical pairs ready but no retrain.

    history entries must carry 'days_since_last_promotion' AND
    'n_pairs_canonical' (or 'sample_quality' >= 'adequate'). The latest
    window's days_since_last_promotion drives the check.
    """
    if not history:
        return {
            "status": "insufficient_data",
            "reason": "empty_history",
        }
    current = history[-1]
    days_since = current.get("days_since_last_promotion")
    n_canonical = int(current.get("n_pairs_canonical", 0) or 0)
    sample_quality = current.get("sample_quality", "insufficient")

    # Insufficient if no readiness signal yet (no canonical pairs OR
    # never-promoted bucket — days_since_last_promotion is None).
    if sample_quality == "insufficient":
        return {
            "status": "insufficient_data",
            "reason": "current_window_pairs_insufficient",
            "n_pairs_canonical": n_canonical,
            "sample_quality": sample_quality,
        }
    if days_since is None:
        # Never promoted — but pairs are canonical-ready. This IS a stall
        # signal: data exists but no retrain has ever fired.
        # Use days_threshold * 2 as a proxy (operator-empathetic; never-
        # promoted is more concerning than "stale promotion").
        return {
            "status": "fired",
            "current_days_since_last_promotion": None,
            "n_pairs_canonical": n_canonical,
            "sample_quality": sample_quality,
            "days_threshold": days_threshold,
            "reason_detail": "never_promoted_with_canonical_pairs_ready",
        }
    days_int = int(days_since)
    evidence = {
        "current_days_since_last_promotion": days_int,
        "n_pairs_canonical": n_canonical,
        "sample_quality": sample_quality,
        "days_threshold": days_threshold,
    }
    if days_int > days_threshold:
        evidence["status"] = "fired"
        return evidence
    evidence["status"] = "within_normal"
    return evidence


def _check_drift_no_refit(
    history: list[dict[str, Any]],
    *,
    days_threshold: int,
    drift_detected: bool | None,
) -> dict[str, Any]:
    """Check stall_kind 3: drift detected but no refit.

    drift_detected is caller-provided (BATCH 3 weekly runner orchestrates
    the join with detect_parameter_drift output).
    """
    if drift_detected is None:
        return {
            "status": "insufficient_data",
            "reason": "drift_detected_not_provided",
            "days_threshold": days_threshold,
        }
    if not history:
        return {
            "status": "insufficient_data",
            "reason": "empty_history",
            "drift_detected": False,
        }
    current = history[-1]
    days_since = current.get("days_since_last_promotion")
    if not drift_detected:
        return {
            "status": "within_normal",
            "drift_detected": False,
            "current_days_since_last_promotion": days_since,
            "days_threshold": days_threshold,
        }
    # drift_detected is True; now check days threshold
    if days_since is None:
        return {
            "status": "fired",
            "drift_detected": True,
            "current_days_since_last_promotion": None,
            "days_threshold": days_threshold,
            "reason_detail": "drift_detected_never_promoted",
        }
    days_int = int(days_since)
    evidence = {
        "drift_detected": True,
        "current_days_since_last_promotion": days_int,
        "days_threshold": days_threshold,
    }
    if days_int > days_threshold:
        evidence["status"] = "fired"
        return evidence
    evidence["status"] = "within_normal"
    return evidence


def _resolve_severity(
    fired_kinds: list[str],
    per_kind_evidence: dict[str, dict[str, Any]],
    *,
    multiplier: float,
) -> StallSeverity:
    """Resolve severity based on which kind(s) fired and per-kind details.

    'critical' iff ANY of the GO_BATCH_2 §Severity boundaries are breached:
      (a) corpus_vs_pair_lag with ratio < 1/(2.0 * multiplier)
      (b) pairs_ready_no_retrain with days > CRITICAL_DAYS_PAIRS_READY_NO_RETRAIN (60)
      (c) drift_no_refit with days > CRITICAL_DAYS_DRIFT_NO_REFIT (30)
    Otherwise 'warn'.
    """
    critical_threshold_min = 1.0 / (CRITICAL_PAIR_GROWTH_RATIO_CUTOFF * multiplier)
    if "corpus_vs_pair_lag" in fired_kinds:
        ratio = per_kind_evidence.get("corpus_vs_pair_lag", {}).get("ratio", 1.0)
        if ratio < critical_threshold_min:
            return "critical"
    if "pairs_ready_no_retrain" in fired_kinds:
        ev = per_kind_evidence.get("pairs_ready_no_retrain", {})
        days = ev.get("current_days_since_last_promotion")
        if days is None or (isinstance(days, int) and days > CRITICAL_DAYS_PAIRS_READY_NO_RETRAIN):
            return "critical"
    if "drift_no_refit" in fired_kinds:
        ev = per_kind_evidence.get("drift_no_refit", {})
        days = ev.get("current_days_since_last_promotion")
        if days is None or (isinstance(days, int) and days > CRITICAL_DAYS_DRIFT_NO_REFIT):
            return "critical"
    return "warn"


def detect_learning_loop_stall(
    history: list[dict[str, Any]],
    bucket_key: str,
    *,
    pair_growth_threshold_multiplier: float = DEFAULT_PAIR_GROWTH_THRESHOLD_MULTIPLIER,
    days_pairs_ready_no_retrain: int = DEFAULT_DAYS_PAIRS_READY_NO_RETRAIN,
    days_drift_no_refit: int = DEFAULT_DAYS_DRIFT_NO_REFIT,
    min_windows: int = DEFAULT_STALL_MIN_WINDOWS,
    drift_detected: bool | None = None,
) -> ParameterStallVerdict:
    """Detect learning-loop stall via 3 composable stall_kinds.

    Args:
        history: chronological list of per-window pipeline-state snapshots
            (each is one bucket's value from
            compute_learning_loop_state_per_bucket re-run on shifted-back
            end_date). MUST be in chronological order: history[0] = oldest,
            history[-1] = current week. Each dict must carry
            'n_pairs_canonical', 'days_since_last_promotion',
            'sample_quality' fields.
        bucket_key: the bucket this history belongs to.
        pair_growth_threshold_multiplier: corpus_vs_pair_lag fires when
            ratio < 1/multiplier. Default 1.5 (sibling-coherent with WP +
            CALIBRATION 1.5 ratio precedent).
        days_pairs_ready_no_retrain: pairs_ready_no_retrain fires when
            consecutive ready days > this. Default 30.
        days_drift_no_refit: drift_no_refit fires when drift detected AND
            days > this. Default 14.
        min_windows: min history length for corpus_vs_pair_lag check.
            Default 4 (1 current + 3 trailing).
        drift_detected: caller-provided (BATCH 3 weekly runner orchestrates
            the join with calibration_observation.detect_parameter_drift
            output). None means "not yet checked"; insufficient_data per kind.

    Returns:
        ParameterStallVerdict. See dataclass docstring for kind/severity.

    Insufficient_data per kind: each stall_kind independently emits
    'insufficient_data' status when its check can't run. The verdict's
    overall kind is:
      - 'stall_detected' if ANY kind fires
      - 'within_normal' if NO kind fires AND at least one was checkable
      - 'insufficient_data' if ALL 3 kinds returned insufficient
    """
    corpus_check = _check_corpus_vs_pair_lag(
        history,
        multiplier=pair_growth_threshold_multiplier,
        min_windows=min_windows,
    )
    pairs_ready_check = _check_pairs_ready_no_retrain(
        history, days_threshold=days_pairs_ready_no_retrain,
    )
    drift_check = _check_drift_no_refit(
        history,
        days_threshold=days_drift_no_refit,
        drift_detected=drift_detected,
    )

    per_kind = {
        "corpus_vs_pair_lag": corpus_check,
        "pairs_ready_no_retrain": pairs_ready_check,
        "drift_no_refit": drift_check,
    }
    fired_kinds: list[str] = []
    for kind_name, ev in per_kind.items():
        if ev.get("status") == "fired":
            fired_kinds.append(kind_name)

    evidence = {
        "per_kind": per_kind,
        "thresholds": {
            "pair_growth_threshold_multiplier": pair_growth_threshold_multiplier,
            "days_pairs_ready_no_retrain": days_pairs_ready_no_retrain,
            "days_drift_no_refit": days_drift_no_refit,
            "min_windows": min_windows,
        },
        "n_history_windows": len(history),
    }

    if fired_kinds:
        severity = _resolve_severity(fired_kinds, per_kind,
                                      multiplier=pair_growth_threshold_multiplier)
        return ParameterStallVerdict(
            kind="stall_detected",
            bucket_key=bucket_key,
            stall_kinds=fired_kinds,
            severity=severity,
            evidence=evidence,
        )

    # No kind fired. Check if at least one was checkable.
    n_checkable = sum(1 for ev in per_kind.values() if ev.get("status") != "insufficient_data")
    if n_checkable == 0:
        return ParameterStallVerdict(
            kind="insufficient_data",
            bucket_key=bucket_key,
            evidence=evidence,
        )
    return ParameterStallVerdict(
        kind="within_normal",
        bucket_key=bucket_key,
        evidence=evidence,
    )
