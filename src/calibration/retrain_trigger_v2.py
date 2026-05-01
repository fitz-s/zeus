# Created: 2026-04-30
# Last reused/audited: 2026-04-30
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §2.2
"""Drift-triggered Platt refit coordinator — Phase 2 ingest improvement.

check_and_arm_refit(world_conn) iterates all (city, season, metric) buckets,
runs drift_detector.compute_drift, and writes state/refit_armed.json listing
buckets that need refit (recommendation == "REFIT_NOW").

The refit_armed.json is a signal file for an operator or scheduled refit
script to act on. It does NOT trigger the refit itself (that remains behind
the ZEUS_CALIBRATION_RETRAIN_ENABLED gate in retrain_trigger.py).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.calibration.drift_detector import DriftReport, compute_drift
from src.types.metric_identity import HIGH_LOCALDAY_MAX, MetricIdentity

logger = logging.getLogger(__name__)

# Seasons matching the existing calibration convention
_SEASONS = ["DJF", "MAM", "JJA", "SON"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_active_city_season_pairs(world_conn) -> list[tuple[str, str]]:
    """Return distinct (city, season) pairs from calibration_pairs."""
    try:
        cur = world_conn.execute(
            "SELECT DISTINCT city, season FROM calibration_pairs ORDER BY city, season"
        )
        rows = cur.fetchall()
        if rows:
            return [(r[0], r[1]) for r in rows]
    except Exception as exc:
        logger.warning("Failed to query city/season pairs: %s", exc)

    # Fallback: use config cities + all seasons
    try:
        from src.config import cities
        return [
            (city.name, season)
            for city in cities
            for season in _SEASONS
        ]
    except Exception as exc:
        logger.warning("Config fallback for city list failed: %s", exc)
        return []


def check_and_arm_refit(
    world_conn,
    *,
    metric_identity: MetricIdentity = HIGH_LOCALDAY_MAX,
    window_days: int = 7,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """Iterate all (city, season, metric) buckets, compute drift, write refit_armed.json.

    Returns summary dict with counts and bucket list.
    """
    pairs = _get_active_city_season_pairs(world_conn)
    logger.info("Drift check: %d city/season pairs to evaluate", len(pairs))

    buckets_refit_now: list[dict] = []
    buckets_watch: list[dict] = []
    buckets_ok: int = 0
    errors: list[str] = []

    for city, season in pairs:
        try:
            report = compute_drift(
                world_conn,
                city=city,
                season=season,
                metric_identity=metric_identity,
                window_days=window_days,
            )
            if report.recommendation == "REFIT_NOW":
                buckets_refit_now.append(report.to_dict())
            elif report.recommendation == "WATCH":
                buckets_watch.append(report.to_dict())
            else:
                buckets_ok += 1
        except Exception as exc:
            msg = f"{city}/{season}: {exc}"
            logger.warning("Drift check error for %s/%s: %s", city, season, exc)
            errors.append(msg)

    armed = {
        "written_at": _now_iso(),
        "metric_identity": metric_identity.temperature_metric,
        "window_days": window_days,
        "n_evaluated": len(pairs),
        "n_refit_now": len(buckets_refit_now),
        "n_watch": len(buckets_watch),
        "n_ok": buckets_ok,
        "n_errors": len(errors),
        "refit_now_buckets": buckets_refit_now,
        "watch_buckets": buckets_watch,
        "errors": errors[:20],  # cap for file size
    }

    if state_dir is None:
        from src.config import state_path
        out_path = state_path("refit_armed.json")
    else:
        out_path = state_dir / "refit_armed.json"

    tmp = Path(str(out_path) + ".tmp")
    tmp.write_text(json.dumps(armed, indent=2))
    tmp.replace(out_path)

    logger.info(
        "refit_armed.json written: %d REFIT_NOW, %d WATCH, %d OK, %d errors",
        len(buckets_refit_now), len(buckets_watch), buckets_ok, len(errors),
    )
    return armed
