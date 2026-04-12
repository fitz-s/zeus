# Shadow-only: outputs are additive facts, not live blockers
"""Blocked out-of-sample calibration evaluation.

This module turns `model_eval_run` / `model_eval_point` from inert schema into a
behavior-neutral reporting surface. It fits Platt models on an earlier target
date block and evaluates them on a later target date block without changing the
active calibration routing.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from src.calibration.manager import bucket_key, maturity_level, regularization_for_level
from src.calibration.platt import ExtendedPlattCalibrator
from src.calibration.store import infer_bin_width_from_label
from src.state.db import log_model_eval_point, log_model_eval_run

EPS = 1e-6


@dataclass(frozen=True)
class CalibrationEvalRow:
    pair_id: int
    city: str
    target_date: str
    range_label: str
    p_raw: float
    outcome: int
    lead_days: float
    season: str
    cluster: str
    forecast_available_at: str

    @property
    def bucket_key(self) -> str:
        return bucket_key(self.cluster, self.season)

    @property
    def group_id(self) -> str:
        return f"{self.city}|{self.target_date}|{self.forecast_available_at}"


def _stable_run_id(*, train_start: str, train_end: str, test_start: str, test_end: str) -> str:
    payload = json.dumps(
        {
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
        },
        sort_keys=True,
    )
    return "calibration_oos:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _fetch_rows(conn: sqlite3.Connection, *, start: str, end: str) -> list[CalibrationEvalRow]:
    rows = conn.execute(
        """
        SELECT
            id, city, target_date, range_label, p_raw, outcome, lead_days,
            season, cluster, forecast_available_at
        FROM calibration_pairs
        WHERE target_date >= ? AND target_date <= ?
        ORDER BY cluster, season, target_date, city, id
        """,
        (start, end),
    ).fetchall()
    return [
        CalibrationEvalRow(
            pair_id=int(row["id"]),
            city=str(row["city"]),
            target_date=str(row["target_date"]),
            range_label=str(row["range_label"]),
            p_raw=float(row["p_raw"]),
            outcome=int(row["outcome"]),
            lead_days=float(row["lead_days"]),
            season=str(row["season"]),
            cluster=str(row["cluster"]),
            forecast_available_at=str(row["forecast_available_at"]),
        )
        for row in rows
    ]


def _fit_bucket(rows: list[CalibrationEvalRow]) -> ExtendedPlattCalibrator | None:
    if len(rows) < 15:
        return None
    outcomes = np.array([row.outcome for row in rows], dtype=int)
    if len(set(outcomes.tolist())) < 2:
        return None
    cal = ExtendedPlattCalibrator()
    try:
        cal.fit(
            np.array([row.p_raw for row in rows], dtype=np.float64),
            np.array([row.lead_days for row in rows], dtype=np.float64),
            outcomes,
            bin_widths=np.array(
                [infer_bin_width_from_label(row.range_label) for row in rows],
                dtype=object,
            ),
            n_bootstrap=0,
            regularization_C=regularization_for_level(maturity_level(len(rows))),
        )
    except Exception:
        return None
    return cal


def _predict(cal: ExtendedPlattCalibrator | None, row: CalibrationEvalRow) -> tuple[float, str]:
    if cal is None:
        return row.p_raw, "raw_fallback"
    p_cal = cal.predict_for_bin(
        row.p_raw,
        row.lead_days,
        bin_width=infer_bin_width_from_label(row.range_label),
    )
    return p_cal, "platt_fit"


def _brier(p: float, y: int) -> float:
    return float((float(p) - float(y)) ** 2)


def _log_loss(p: float, y: int) -> float:
    p = min(1.0 - EPS, max(EPS, float(p)))
    return float(-(y * math.log(p) + (1 - y) * math.log(1.0 - p)))


def _mean(values: list[float]) -> float | None:
    return round(float(np.mean(values)), 8) if values else None


def evaluate_blocked_oos_calibration(
    conn: sqlite3.Connection,
    *,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    run_id: str | None = None,
    model_name: str = "extended_platt",
    model_version: str = "blocked_oos_v1",
    write: bool = True,
    created_at: str | None = None,
) -> dict:
    """Fit on one date block, evaluate on a later date block, and optionally log.

    Returns a compact report with aggregate metrics. If `write=True`, the same
    report is stored in `model_eval_run`, with one `model_eval_point` per test
    calibration-pair row.
    """
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    run_id = run_id or _stable_run_id(
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
    )
    train_rows = _fetch_rows(conn, start=train_start, end=train_end)
    test_rows = _fetch_rows(conn, start=test_start, end=test_end)

    by_bucket: dict[str, list[CalibrationEvalRow]] = {}
    for row in train_rows:
        by_bucket.setdefault(row.bucket_key, []).append(row)
    models = {bucket: _fit_bucket(rows) for bucket, rows in by_bucket.items()}

    if write:
        log_model_eval_run(
            conn,
            run_id=run_id,
            model_name=model_name,
            model_version=model_version,
            task_name="calibration",
            data_source="calibration_pairs",
            split_method="blocked_time",
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            scorer={"brier": True, "log_loss": True},
            config={"grouping": "cluster_season", "fallback": "p_raw"},
            metrics={},
            status="running",
            created_at=created_at,
        )

    brier_raw: list[float] = []
    brier_calibrated: list[float] = []
    log_loss_raw: list[float] = []
    log_loss_calibrated: list[float] = []
    fallback_points = 0
    for row in test_rows:
        model = models.get(row.bucket_key)
        p_cal, source = _predict(model, row)
        if source == "raw_fallback":
            fallback_points += 1
        raw_brier = _brier(row.p_raw, row.outcome)
        calibrated_brier = _brier(p_cal, row.outcome)
        raw_log_loss = _log_loss(row.p_raw, row.outcome)
        calibrated_log_loss = _log_loss(p_cal, row.outcome)
        brier_raw.append(raw_brier)
        brier_calibrated.append(calibrated_brier)
        log_loss_raw.append(raw_log_loss)
        log_loss_calibrated.append(calibrated_log_loss)
        if write:
            log_model_eval_point(
                conn,
                point_id=f"{run_id}:pair:{row.pair_id}",
                run_id=run_id,
                point_type="calibration_group",
                reference_id=f"{row.group_id}|{row.range_label}",
                city=row.city,
                target_date=row.target_date,
                bucket_key=row.bucket_key,
                lead_days=row.lead_days,
                y_true=float(row.outcome),
                p_raw=row.p_raw,
                p_cal=p_cal,
                brier=calibrated_brier,
                log_loss=calibrated_log_loss,
                meta={
                    "calibration_pair_id": row.pair_id,
                    "calibration_source": source,
                    "raw_brier": raw_brier,
                    "raw_log_loss": raw_log_loss,
                    "range_label": row.range_label,
                },
                recorded_at=created_at,
            )

    metrics = {
        "n_train_rows": len(train_rows),
        "n_test_rows": len(test_rows),
        "n_train_groups": len({row.group_id for row in train_rows}),
        "n_test_groups": len({row.group_id for row in test_rows}),
        "fit_bucket_count": sum(1 for model in models.values() if model is not None),
        "fallback_points": fallback_points,
        "brier_raw": _mean(brier_raw),
        "brier_calibrated": _mean(brier_calibrated),
        "brier_improvement": (
            round(float(np.mean(brier_raw) - np.mean(brier_calibrated)), 8)
            if brier_raw and brier_calibrated
            else None
        ),
        "log_loss_raw": _mean(log_loss_raw),
        "log_loss_calibrated": _mean(log_loss_calibrated),
    }
    report = {
        "run_id": run_id,
        "model_name": model_name,
        "model_version": model_version,
        "train_start": train_start,
        "train_end": train_end,
        "test_start": test_start,
        "test_end": test_end,
        "metrics": metrics,
    }
    if write:
        log_model_eval_run(
            conn,
            run_id=run_id,
            model_name=model_name,
            model_version=model_version,
            task_name="calibration",
            data_source="calibration_pairs",
            split_method="blocked_time",
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            scorer={"brier": True, "log_loss": True},
            config={"grouping": "cluster_season", "fallback": "p_raw"},
            metrics=metrics,
            status="completed",
            created_at=created_at,
            completed_at=created_at,
        )
    return report


def recommend_calibration_promotion(
    report: dict,
    *,
    min_test_groups: int = 30,
    min_brier_improvement: float = 0.0,
    max_fallback_rate: float = 0.25,
) -> dict:
    """Convert a blocked-OOS report into a promotion-registry decision draft.

    The helper is intentionally side-effect free. Callers can decide whether to
    persist the returned status through `upsert_promotion_registry`.
    """
    metrics = dict(report.get("metrics") or {})
    n_test_groups = int(metrics.get("n_test_groups") or 0)
    n_test_rows = int(metrics.get("n_test_rows") or 0)
    fallback_points = int(metrics.get("fallback_points") or 0)
    brier_improvement = metrics.get("brier_improvement")
    fallback_rate = fallback_points / n_test_rows if n_test_rows else 1.0

    blockers = []
    if n_test_groups < min_test_groups:
        blockers.append(f"insufficient_test_groups:{n_test_groups}<{min_test_groups}")
    if brier_improvement is None or float(brier_improvement) < min_brier_improvement:
        blockers.append(f"brier_improvement:{brier_improvement}<{min_brier_improvement}")
    if fallback_rate > max_fallback_rate:
        blockers.append(f"fallback_rate:{fallback_rate:.3f}>{max_fallback_rate:.3f}")

    if blockers:
        status = "shadow"
        decision_reason = ";".join(blockers)
    else:
        status = "candidate"
        decision_reason = "blocked_oos_passed"

    return {
        "promotion_id": f"promotion:{report.get('run_id')}",
        "model_name": report.get("model_name", "extended_platt"),
        "model_version": report.get("model_version", "blocked_oos_v1"),
        "task_name": "calibration",
        "status": status,
        "eval_run_id": report.get("run_id"),
        "decision_reason": decision_reason,
        "meta": {
            "n_test_groups": n_test_groups,
            "n_test_rows": n_test_rows,
            "fallback_rate": round(fallback_rate, 6),
            "brier_improvement": brier_improvement,
            "thresholds": {
                "min_test_groups": min_test_groups,
                "min_brier_improvement": min_brier_improvement,
                "max_fallback_rate": max_fallback_rate,
            },
        },
    }
