# Created: 2026-05-05
# Last reused/audited: 2026-05-05
# Authority basis: architecture/calibration_transfer_oos_design_2026-05-05.md Phase X.2
"""OOS evaluator: writes ``validated_calibration_transfers`` rows.

Phase X.2 of the calibration-transfer evidence pipeline.  Iterates all
active Platt models in ``platt_models_v2``, skips same-domain routes
(handled by the fast-path in ``evaluate_calibration_transfer_policy_with_evidence``),
and for each cross-domain route fetches held-out calibration pairs, applies
the source-trained Platt, computes the OOS Brier score, and writes/updates
a row in ``validated_calibration_transfers``.

OOS held-out split convention
------------------------------
No time-based train/test column exists on ``calibration_pairs_v2``.
We use a deterministic 80/20 split keyed on ``pair_id``:
    held-out: pair_id % 5 == 0
    training: pair_id % 5 != 0
This is stable across reruns — same pairs are always held out.

Usage (from zeus repo root, zeus venv active)::

    python scripts/evaluate_calibration_transfer_oos.py [--dry-run] \\
        [--policy-id OOS_BRIER_DIFF_v1] \\
        [--target-source-id ecmwf_open_data] \\
        [--limit-models N] \\
        [--skip-lock-check]

Feature flag
------------
Script checks ``ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED`` but does NOT
require it to be true — this evaluator *produces* evidence independently of
whether the policy gate reads from it.  The flag governs the reader; this
script is the writer.

Today (2026-05-05): calibration_pairs_v2 is 100% (tigge_mars, 00z).
All active Platt models are (tigge_mars, 00z).  Zero cross-domain candidates
exist → script writes 0 rows.  Ready for Phase 1 12z TIGGE ingest.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("oos_evaluator")

DEFAULT_POLICY_ID = "OOS_BRIER_DIFF_v1"
DEFAULT_BRIER_DIFF_THRESHOLD = 0.005
MIN_PAIRS = 200


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _logit(p: float) -> float:
    """logit(p) = log(p / (1 - p)).  Clamps p away from 0/1."""
    p = max(1e-7, min(1.0 - 1e-7, p))
    return math.log(p / (1.0 - p))


def _apply_platt(p_raw: float, lead_days: float, A: float, B: float, C: float) -> float:
    """Apply source Platt: p_cal = sigmoid(A * logit(p_raw) + B * lead_days + C)."""
    return _sigmoid(A * _logit(p_raw) + B * lead_days + C)


def _brier_score(predictions: list[float], outcomes: list[int]) -> float:
    """Mean squared error Brier score."""
    n = len(predictions)
    if n == 0:
        return float("nan")
    total = sum((p - o) ** 2 for p, o in zip(predictions, outcomes))
    return total / n


# ---------------------------------------------------------------------------
# Daemon-lock check (mirrors migrate_phase2_cycle_stratification.py:74-94)
# ---------------------------------------------------------------------------

def _check_daemon_down(conn) -> tuple[bool, str]:
    """Verify trade daemon locked by operator-precedence (>= 200) override."""
    rows = conn.execute(
        """
        SELECT issued_by, value, precedence, effective_until
          FROM control_overrides
         WHERE target_type='global' AND target_key='entries' AND action_type='gate'
           AND value='true'
           AND (effective_until IS NULL
                OR effective_until > strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
         ORDER BY precedence DESC
        """
    ).fetchall()
    if not rows:
        return False, "no active entries-paused override; trade daemon may be live"
    top = rows[0]
    if top[2] < 200:
        return False, f"top precedence is {top[2]} (< 200); not operator-issued"
    return True, f"locked by {top[0]} precedence={top[2]} until={top[3] or 'NEVER'}"


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _load_brier_threshold(policy_id: str) -> float:
    """Read calibration_transfer_brier_diff_threshold from settings.json.

    Falls back to DEFAULT_BRIER_DIFF_THRESHOLD (0.005) when key is absent.
    Supports per-policy override: settings["calibration_transfer_brier_diff_threshold"]
    can be a flat float or a dict keyed by policy_id.
    """
    try:
        settings_path = ZEUS_ROOT / "config" / "settings.json"
        data = json.loads(settings_path.read_text())
        raw = data.get("calibration_transfer_brier_diff_threshold", DEFAULT_BRIER_DIFF_THRESHOLD)
        if isinstance(raw, dict):
            return float(raw.get(policy_id, DEFAULT_BRIER_DIFF_THRESHOLD))
        return float(raw)
    except Exception as exc:
        logger.warning("Could not load brier threshold from settings.json: %s; using default", exc)
        return DEFAULT_BRIER_DIFF_THRESHOLD


# ---------------------------------------------------------------------------
# Core DB queries
# ---------------------------------------------------------------------------

def _iter_active_platt_models(conn, limit: int | None = None) -> Iterator[dict]:
    """Yield active Platt model rows as dicts."""
    sql = """
        SELECT model_key, temperature_metric AS metric, cluster, season,
               data_version, param_A, param_B, param_C,
               brier_insample, cycle, source_id, horizon_profile
          FROM platt_models_v2
         WHERE is_active = 1
         ORDER BY model_key
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    for row in conn.execute(sql).fetchall():
        yield {
            "model_key": row[0],
            "metric": row[1],
            "cluster": row[2],
            "season": row[3],
            "data_version": row[4],
            "param_A": row[5],
            "param_B": row[6],
            "param_C": row[7],
            "brier_insample": row[8],
            "cycle": row[9],
            "source_id": row[10],
            "horizon_profile": row[11],
        }


def _enumerate_target_domains(conn) -> list[tuple[str, str]]:
    """Return distinct (source_id, cycle) pairs present in calibration_pairs_v2."""
    rows = conn.execute(
        """
        SELECT DISTINCT source_id, cycle
          FROM calibration_pairs_v2
         ORDER BY source_id, cycle
        """
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _fetch_held_out_pairs(
    conn,
    *,
    target_source_id: str,
    target_cycle: str,
    season: str,
    cluster: str,
    metric: str,
    horizon_profile: str,
    target_source_id_filter: str | None,
) -> list[dict]:
    """Fetch held-out pairs (pair_id % 5 == 0) for the given route.

    Column mapping: ``temperature_metric`` = metric (high|low),
    ``target_date`` = the observation date used as the evidence window proxy.
    """
    if target_source_id_filter is not None and target_source_id != target_source_id_filter:
        return []
    rows = conn.execute(
        """
        SELECT pair_id, p_raw, lead_days, outcome,
               MIN(target_date) AS window_start,
               MAX(target_date) AS window_end
          FROM calibration_pairs_v2
         WHERE source_id           = ?
           AND cycle               = ?
           AND season              = ?
           AND cluster             = ?
           AND temperature_metric  = ?
           AND horizon_profile     = ?
           AND (pair_id % 5)       = 0
         GROUP BY pair_id, p_raw, lead_days, outcome
         ORDER BY pair_id
        """,
        (target_source_id, target_cycle, season, cluster, metric, horizon_profile),
    ).fetchall()
    return [
        {
            "pair_id": r[0],
            "p_raw": r[1],
            "lead_days": r[2],
            "outcome": r[3],
            "window_start": r[4],
            "window_end": r[5],
        }
        for r in rows
    ]


def _fetch_date_window(
    conn,
    *,
    target_source_id: str,
    target_cycle: str,
    season: str,
    cluster: str,
    metric: str,
    horizon_profile: str,
) -> tuple[str, str]:
    """Return (min_target_date, max_target_date) for the route's held-out pairs."""
    row = conn.execute(
        """
        SELECT MIN(target_date), MAX(target_date)
          FROM calibration_pairs_v2
         WHERE source_id           = ?
           AND cycle               = ?
           AND season              = ?
           AND cluster             = ?
           AND temperature_metric  = ?
           AND horizon_profile     = ?
           AND (pair_id % 5)       = 0
        """,
        (target_source_id, target_cycle, season, cluster, metric, horizon_profile),
    ).fetchone()
    if row is None or row[0] is None:
        return ("", "")
    return (str(row[0]), str(row[1]))


# ---------------------------------------------------------------------------
# UPSERT
# ---------------------------------------------------------------------------

def _upsert_row(
    conn,
    *,
    policy_id: str,
    model: dict,
    target_source_id: str,
    target_cycle: str,
    n_pairs: int,
    brier_target: float,
    brier_diff: float,
    brier_diff_threshold: float,
    status: str,
    evidence_window_start: str,
    evidence_window_end: str,
    evaluated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO validated_calibration_transfers (
            policy_id, source_id, target_source_id,
            source_cycle, target_cycle, horizon_profile,
            season, cluster, metric,
            n_pairs, brier_source, brier_target, brier_diff,
            brier_diff_threshold, status,
            evidence_window_start, evidence_window_end,
            platt_model_key, evaluated_at
        ) VALUES (
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?
        )
        ON CONFLICT (policy_id, target_source_id, target_cycle, season,
                     cluster, metric, horizon_profile, platt_model_key)
        DO UPDATE SET
            n_pairs               = excluded.n_pairs,
            brier_source          = excluded.brier_source,
            brier_target          = excluded.brier_target,
            brier_diff            = excluded.brier_diff,
            brier_diff_threshold  = excluded.brier_diff_threshold,
            status                = excluded.status,
            evidence_window_start = excluded.evidence_window_start,
            evidence_window_end   = excluded.evidence_window_end,
            evaluated_at          = excluded.evaluated_at
        """,
        (
            policy_id,
            model["source_id"],
            target_source_id,
            model["cycle"],
            target_cycle,
            model["horizon_profile"],
            model["season"],
            model["cluster"],
            model["metric"],
            n_pairs,
            model["brier_insample"] if model["brier_insample"] is not None else 0.0,
            brier_target,
            brier_diff,
            brier_diff_threshold,
            status,
            evidence_window_start,
            evidence_window_end,
            model["model_key"],
            evaluated_at,
        ),
    )


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------

def run_oos_evaluation(
    conn,
    *,
    policy_id: str = DEFAULT_POLICY_ID,
    target_source_id_filter: str | None = None,
    limit_models: int | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict:
    """Run OOS evaluation; returns summary dict."""
    if now is None:
        now = datetime.now(timezone.utc)

    evaluated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    brier_diff_threshold = _load_brier_threshold(policy_id)

    target_domains = _enumerate_target_domains(conn)
    logger.info("target domains in calibration_pairs_v2: %s", target_domains)

    models = list(_iter_active_platt_models(conn, limit=limit_models))
    logger.info("active Platt models to iterate: %d", len(models))

    stats = {
        "active_platt_models_iterated": len(models),
        "candidate_routes_evaluated": 0,
        "same_domain_skipped": 0,
        "rows_written": 0,
        "status_distribution": {
            "LIVE_ELIGIBLE": 0,
            "TRANSFER_UNSAFE": 0,
            "INSUFFICIENT_SAMPLE": 0,
        },
        "dry_run": dry_run,
    }

    for model in models:
        source_domain = (model["source_id"], model["cycle"])

        for (tgt_source_id, tgt_cycle) in target_domains:
            # Apply optional CLI filter
            if target_source_id_filter is not None and tgt_source_id != target_source_id_filter:
                continue

            # Skip same-domain (fast-path territory)
            if (tgt_source_id, tgt_cycle) == source_domain:
                stats["same_domain_skipped"] += 1
                logger.debug(
                    "same-domain skip: model=%s source=(%s,%s)",
                    model["model_key"], tgt_source_id, tgt_cycle,
                )
                continue

            stats["candidate_routes_evaluated"] += 1
            logger.info(
                "evaluating model=%s source=(%s,%s) → target=(%s,%s) season=%s cluster=%s metric=%s",
                model["model_key"],
                model["source_id"], model["cycle"],
                tgt_source_id, tgt_cycle,
                model["season"], model["cluster"], model["metric"],
            )

            pairs = _fetch_held_out_pairs(
                conn,
                target_source_id=tgt_source_id,
                target_cycle=tgt_cycle,
                season=model["season"],
                cluster=model["cluster"],
                metric=model["metric"],
                horizon_profile=model["horizon_profile"],
                target_source_id_filter=target_source_id_filter,
            )
            n_pairs = len(pairs)

            if n_pairs < MIN_PAIRS:
                status = "INSUFFICIENT_SAMPLE"
                brier_target = 0.0
                brier_diff = 0.0
                window_start, window_end = _fetch_date_window(
                    conn,
                    target_source_id=tgt_source_id,
                    target_cycle=tgt_cycle,
                    season=model["season"],
                    cluster=model["cluster"],
                    metric=model["metric"],
                    horizon_profile=model["horizon_profile"],
                )
            else:
                A = model["param_A"]
                B = model["param_B"]
                C = model["param_C"]
                predictions = [
                    _apply_platt(p["p_raw"], p["lead_days"], A, B, C)
                    for p in pairs
                ]
                outcomes = [p["outcome"] for p in pairs]
                brier_target = _brier_score(predictions, outcomes)
                brier_source = (
                    model["brier_insample"] if model["brier_insample"] is not None else 0.0
                )
                brier_diff = brier_target - brier_source

                if brier_diff > brier_diff_threshold:
                    status = "TRANSFER_UNSAFE"
                else:
                    status = "LIVE_ELIGIBLE"

                window_start = pairs[0]["window_start"] or ""
                window_end = pairs[-1]["window_end"] or ""

            logger.info(
                "  → n_pairs=%d brier_target=%.5f brier_diff=%.5f status=%s",
                n_pairs, brier_target, brier_diff, status,
            )

            stats["status_distribution"][status] += 1
            stats["rows_written"] += 1

            if not dry_run:
                _upsert_row(
                    conn,
                    policy_id=policy_id,
                    model=model,
                    target_source_id=tgt_source_id,
                    target_cycle=tgt_cycle,
                    n_pairs=n_pairs,
                    brier_target=brier_target,
                    brier_diff=brier_diff,
                    brier_diff_threshold=brier_diff_threshold,
                    status=status,
                    evidence_window_start=window_start,
                    evidence_window_end=window_end,
                    evaluated_at=evaluated_at,
                )
                conn.commit()

    return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="OOS evaluator: writes validated_calibration_transfers rows."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute evidence but do not write to DB.")
    parser.add_argument("--policy-id", default=DEFAULT_POLICY_ID,
                        help=f"Policy ID (default: {DEFAULT_POLICY_ID}).")
    parser.add_argument("--target-source-id", default=None,
                        help="Filter: only evaluate against this target source_id.")
    parser.add_argument("--limit-models", type=int, default=None,
                        help="Process at most N active Platt models (debugging).")
    parser.add_argument("--skip-lock-check", action="store_true",
                        help="DANGEROUS: bypass trade-daemon-locked precondition.")
    args = parser.parse_args()

    from src.state.db import get_world_connection

    conn = get_world_connection()
    conn.execute("PRAGMA busy_timeout = 30000")
    logger.info("busy_timeout=30000ms set on connection")

    try:
        if not args.skip_lock_check:
            ok, msg = _check_daemon_down(conn)
            logger.info("daemon-lock check: %s — %s", "PASS" if ok else "FAIL", msg)
            if not ok and not args.dry_run:
                logger.error("Refusing to write: %s", msg)
                logger.error("Pass --skip-lock-check to override (NOT RECOMMENDED).")
                return 2

        summary = run_oos_evaluation(
            conn,
            policy_id=args.policy_id,
            target_source_id_filter=args.target_source_id,
            limit_models=args.limit_models,
            dry_run=args.dry_run,
        )

        print(json.dumps(summary, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
