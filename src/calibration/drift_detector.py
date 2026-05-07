# Created: 2026-04-30
# Last reused/audited: 2026-04-30
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §2.2 + §SC-6
"""Drift detector for Platt calibration models — Phase 2 ingest improvement.

Computes rolling Brier score over recent settlements to detect when a
calibration model has drifted and a refit is warranted.

Public API:
    compute_drift(world_conn, *, city, season, metric_identity, window_days=7)
        -> DriftReport

    DriftReport.recommendation is one of:
        "REFIT_NOW" — delta > 0.01 OR n_settlements >= 50 since last refit
        "WATCH"     — delta between 0.005 and 0.01
        "OK"        — within tolerance, no action needed
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.types.metric_identity import MetricIdentity

logger = logging.getLogger(__name__)

_REFIT_NOW_DELTA = 0.01
_WATCH_DELTA = 0.005
_REFIT_NOW_N_SETTLEMENTS = 50


@dataclass(frozen=True)
class DriftReport:
    city: str
    season: str
    metric_identity: MetricIdentity
    window_brier: float | None
    baseline_brier: float | None
    delta: float | None
    n_settlements_in_window: int
    recommendation: str  # "REFIT_NOW" | "WATCH" | "OK"
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "city": self.city,
            "season": self.season,
            "metric_identity": self.metric_identity.temperature_metric,
            "window_brier": self.window_brier,
            "baseline_brier": self.baseline_brier,
            "delta": self.delta,
            "n_settlements_in_window": self.n_settlements_in_window,
            "recommendation": self.recommendation,
            "message": self.message,
        }


def _brier_score(outcomes: list[int], probabilities: list[float]) -> float:
    """Compute mean Brier score: mean((p - o)^2)."""
    if not outcomes:
        return 0.0
    return sum((p - o) ** 2 for p, o in zip(probabilities, outcomes)) / len(outcomes)


def _get_last_refit_at(world_conn, *, city: str, season: str, metric_identity: MetricIdentity) -> str | None:
    """Return fitted_at ISO string for the most recent active Platt model, or None."""
    try:
        cur = world_conn.execute(
            """
            SELECT fitted_at FROM platt_models_v2
            WHERE city = ? AND season = ? AND temperature_metric = ? AND active = 1
            ORDER BY fitted_at DESC LIMIT 1
            """,
            (city, season, metric_identity.temperature_metric),
        )
        row = cur.fetchone()
        if row:
            return row[0]
    except Exception:
        pass

    # Fallback: try platt_models (v1)
    try:
        cur = world_conn.execute(
            """
            SELECT fitted_at FROM platt_models
            WHERE city = ? AND season = ? AND active = 1
            ORDER BY fitted_at DESC LIMIT 1
            """,
            (city, season),
        )
        row = cur.fetchone()
        if row:
            return row[0]
    except Exception:
        pass

    return None


def _get_settlements_window(
    world_conn,
    *,
    city: str,
    metric_identity: MetricIdentity,
    since_iso: str | None,
    window_cutoff_iso: str,
) -> list[dict]:
    """Return settlement rows in the window, ordered by target_date."""
    temperature_metric = metric_identity.temperature_metric
    # Drift/retrain evidence must preserve the same settlement object identity
    # as live settlement readers: VERIFIED source truth and explicit high/low
    # metric. A calibration_pair without matching settlement authority is not
    # enough to recommend refit.
    since_param = since_iso or window_cutoff_iso
    try:
        cur = world_conn.execute(
            """
            SELECT cp.outcome, cp.p_raw
            FROM calibration_pairs cp
            JOIN settlements s ON (cp.city = s.city AND cp.target_date = s.target_date)
            WHERE cp.city = ?
              AND cp.target_date >= ?
              AND cp.authority = 'VERIFIED'
              AND s.authority = 'VERIFIED'
              AND s.temperature_metric = ?
            ORDER BY cp.target_date
            """,
            (city, since_param, temperature_metric),
        )
        rows = cur.fetchall()
        if rows:
            return [{"outcome": r[0], "p_raw": r[1]} for r in rows]
    except Exception as exc:
        logger.debug("calibration_pairs+settlements join failed for %s: %s", city, exc)

    return []


def _get_baseline_brier(
    world_conn,
    *,
    city: str,
    metric_identity: MetricIdentity,
    baseline_days: int = 90,
    window_cutoff_iso: str,
) -> float | None:
    """Compute baseline Brier over a historical window BEFORE the current window.

    Baseline uses rows from [now - baseline_days, window_cutoff_iso) to
    avoid including the recent window rows in the baseline computation.
    """
    baseline_start = (
        datetime.now(timezone.utc) - timedelta(days=baseline_days)
    ).date().isoformat()
    temperature_metric = metric_identity.temperature_metric
    try:
        cur = world_conn.execute(
            """
            SELECT cp.outcome, cp.p_raw
            FROM calibration_pairs cp
            JOIN settlements s ON (cp.city = s.city AND cp.target_date = s.target_date)
            WHERE cp.city = ?
              AND cp.target_date >= ?
              AND cp.target_date < ?
              AND cp.authority = 'VERIFIED'
              AND s.authority = 'VERIFIED'
              AND s.temperature_metric = ?
            ORDER BY cp.target_date
            """,
            (city, baseline_start, window_cutoff_iso, temperature_metric),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        outcomes = [r[0] for r in rows]
        probs = [r[1] for r in rows]
        return _brier_score(outcomes, probs)
    except Exception as exc:
        logger.debug("Baseline Brier query failed for %s: %s", city, exc)
        return None


def compute_drift(
    world_conn,
    *,
    city: str,
    season: str,
    metric_identity: MetricIdentity,
    window_days: int = 7,
) -> DriftReport:
    """Compute rolling Brier drift for one (city, season, metric) bucket.

    Reads from world.calibration_pairs + world.settlements.
    Returns a DriftReport with recommendation in {REFIT_NOW, WATCH, OK}.

    Heuristic:
      - REFIT_NOW if delta > 0.01 OR n_settlements_in_window >= 50
      - WATCH if 0.005 < delta <= 0.01
      - OK otherwise
    """
    now = datetime.now(timezone.utc)
    window_cutoff = (now - timedelta(days=window_days)).date().isoformat()

    # Get recent settlements for window Brier
    rows = _get_settlements_window(
        world_conn,
        city=city,
        metric_identity=metric_identity,
        since_iso=None,
        window_cutoff_iso=window_cutoff,
    )
    n_in_window = len(rows)

    if not rows:
        return DriftReport(
            city=city,
            season=season,
            metric_identity=metric_identity,
            window_brier=None,
            baseline_brier=None,
            delta=None,
            n_settlements_in_window=0,
            recommendation="OK",
            message="No settlements in window — cannot compute drift",
        )

    outcomes = [r["outcome"] for r in rows]
    probs = [r["p_raw"] for r in rows]
    window_brier = _brier_score(outcomes, probs)

    baseline_brier = _get_baseline_brier(
        world_conn,
        city=city,
        metric_identity=metric_identity,
        window_cutoff_iso=window_cutoff,
    )

    delta: float | None = None
    if baseline_brier is not None:
        delta = window_brier - baseline_brier

    # Heuristic recommendation
    recommendation: str
    message: str

    if n_in_window >= _REFIT_NOW_N_SETTLEMENTS:
        recommendation = "REFIT_NOW"
        message = f"n_settlements_in_window={n_in_window} >= threshold {_REFIT_NOW_N_SETTLEMENTS}"
    elif delta is not None and delta > _REFIT_NOW_DELTA:
        recommendation = "REFIT_NOW"
        message = f"delta={delta:.4f} > threshold {_REFIT_NOW_DELTA}"
    elif delta is not None and delta > _WATCH_DELTA:
        recommendation = "WATCH"
        message = f"delta={delta:.4f} in watch zone ({_WATCH_DELTA}, {_REFIT_NOW_DELTA}]"
    elif delta is None:
        recommendation = "OK"
        message = "No baseline available — treating as OK"
    else:
        recommendation = "OK"
        message = f"delta={delta:.4f} within tolerance"

    logger.info(
        "Drift check %s/%s/%s: window_brier=%.4f baseline=%.4f delta=%s n=%d -> %s",
        city, season, metric_identity.temperature_metric,
        window_brier,
        baseline_brier if baseline_brier is not None else -1,
        f"{delta:.4f}" if delta is not None else "N/A",
        n_in_window,
        recommendation,
    )

    return DriftReport(
        city=city,
        season=season,
        metric_identity=metric_identity,
        window_brier=window_brier,
        baseline_brier=baseline_brier,
        delta=delta,
        n_settlements_in_window=n_in_window,
        recommendation=recommendation,
        message=message,
    )
