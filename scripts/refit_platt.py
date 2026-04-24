"""Automated Platt model refit from current calibration_pairs.

Run explicitly after the canonical post-fillback cascade.
Uses sklearn LogisticRegression -- must run in venv with sklearn installed.

K4: All SELECT queries now include AND authority = 'VERIFIED' to ensure
only provenance-verified pairs train the Platt models. After all new
city-keyed models are written, K3 soft-deleted rows (is_active=0) are
hard-deleted per the K3-to-K4 handoff plan.
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_world_connection as get_connection, init_schema
from src.config import calibration_n_bootstrap
from src.calibration.effective_sample_size import build_decision_groups
from src.calibration.manager import maturity_level, regularization_for_level
from src.calibration.platt import ExtendedPlattCalibrator
from src.calibration.store import canonical_pairs_ready_for_refit, infer_bin_width_from_label


def _validate_p_raw_domain(bucket_key: str, p_raw: np.ndarray) -> None:
    if not np.isfinite(p_raw).all() or np.any((p_raw < 0.0) | (p_raw > 1.0)):
        raise RuntimeError(
            f"refit_platt refused to fit {bucket_key}: p_raw outside [0, 1]"
        )


def _ensure_authority_column(conn: sqlite3.Connection) -> None:
    """Add authority column to calibration_pairs if not present (worktree shim)."""
    info = conn.execute("PRAGMA table_info(calibration_pairs)").fetchall()
    cols = {row[1] for row in info}
    if "authority" not in cols:
        conn.execute(
            "ALTER TABLE calibration_pairs ADD COLUMN "
            "authority TEXT NOT NULL DEFAULT 'UNVERIFIED'"
        )
        conn.commit()


def refit_all():
    conn = get_connection()
    init_schema(conn)
    _ensure_authority_column(conn)
    if not canonical_pairs_ready_for_refit(conn):
        raise RuntimeError(
            "refit_platt refused to fit: canonical_v1 VERIFIED calibration_pairs "
            "are missing or mixed with noncanonical/blank-ID VERIFIED rows"
        )

    null_groups = conn.execute("""
        SELECT COUNT(*) FROM calibration_pairs
        WHERE authority = 'VERIFIED'
          AND (decision_group_id IS NULL OR decision_group_id = '')
    """).fetchone()[0]
    if null_groups:
        raise RuntimeError(
            "refit_platt refused to fit: VERIFIED calibration_pairs include "
            f"{null_groups} rows with NULL/blank decision_group_id"
        )
    build_decision_groups(conn, authority_filter="VERIFIED")

    # K4: only count VERIFIED decision groups toward the maturity threshold
    buckets = conn.execute("""
        SELECT cluster, season, COUNT(DISTINCT decision_group_id) as n_eff
        FROM calibration_pairs
        WHERE authority = 'VERIFIED'
        GROUP BY cluster, season
        HAVING n_eff >= 15
    """).fetchall()
    if not buckets:
        raise RuntimeError(
            "refit_platt refused to fit: no bucket has at least 15 canonical "
            "decision groups"
        )

    refit_count = 0
    failed_buckets: list[str] = []
    for bucket in buckets:
        cluster = bucket["cluster"]
        season = bucket["season"]
        bucket_key = f"{cluster}_{season}"

        # K4: filter to VERIFIED pairs only
        pairs = conn.execute("""
            SELECT p_raw, lead_days, outcome, range_label, decision_group_id FROM calibration_pairs
            WHERE cluster = ? AND season = ? AND authority = 'VERIFIED'
              AND p_raw IS NOT NULL
        """, (cluster, season)).fetchall()

        n_eff = len({p["decision_group_id"] for p in pairs})
        if n_eff < 15:
            continue

        p_raw = np.array([p["p_raw"] for p in pairs])
        _validate_p_raw_domain(bucket_key, p_raw)
        lead_days = np.array([p["lead_days"] for p in pairs])
        outcomes = np.array([p["outcome"] for p in pairs])
        bin_widths = np.array([infer_bin_width_from_label(p["range_label"]) for p in pairs], dtype=object)
        decision_group_ids = np.array([p["decision_group_id"] for p in pairs], dtype=object)

        try:
            cal = ExtendedPlattCalibrator()
            reg_C = regularization_for_level(maturity_level(n_eff))
            cal.fit(
                p_raw,
                lead_days,
                outcomes,
                bin_widths=bin_widths,
                decision_group_ids=decision_group_ids,
                n_bootstrap=calibration_n_bootstrap(),
                regularization_C=reg_C,
            )

            brier_scores = []
            for i in range(len(p_raw)):
                p_cal = cal.predict_for_bin(
                    float(p_raw[i]),
                    float(lead_days[i]),
                    bin_width=bin_widths[i],
                )
                brier_scores.append((p_cal - outcomes[i]) ** 2)
            brier_insample = float(np.mean(brier_scores))

            bootstrap_json = json.dumps(cal.bootstrap_params)
            now_iso = datetime.now(timezone.utc).isoformat()

            # K4/C1 fix: write new model with authority='VERIFIED' explicitly in column list
            conn.execute("""
                INSERT OR REPLACE INTO platt_models
                (bucket_key, param_A, param_B, param_C, bootstrap_params_json,
                 n_samples, brier_insample, fitted_at, is_active, input_space, authority)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'VERIFIED')
            """, (bucket_key, cal.A, cal.B, cal.C, bootstrap_json,
                  n_eff, brier_insample, now_iso, cal.input_space))

            refit_count += 1
            print(f"OK  {bucket_key:25} A={cal.A:+.3f} B={cal.B:+.3f} C={cal.C:+.3f} "
                  f"n_eff={n_eff} rows={len(pairs)} Brier={brier_insample:.4f}")

        except Exception as e:
            print(f"ERR {bucket_key:25} failed: {e}")
            failed_buckets.append(bucket_key)

    if failed_buckets:
        conn.rollback()
        conn.close()
        raise RuntimeError(
            "refit_platt failed for eligible buckets: "
            + ", ".join(sorted(failed_buckets))
        )
    conn.commit()

    # K4 / K3-to-K4 handoff: hard-delete K3 soft-deleted rows now that new
    # VERIFIED city-keyed models are in place.
    deleted = conn.execute(
        "DELETE FROM platt_models WHERE is_active = 0"
    ).rowcount
    if deleted:
        print(f"Purged {deleted} K3 soft-deleted platt_models rows (is_active=0)")
    conn.commit()

    conn.close()
    print(f"\nRefit {refit_count} Platt models")


if __name__ == "__main__":
    refit_all()
