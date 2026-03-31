"""Automated Platt model refit from current calibration_pairs.

Called by the weekly etl_recalibrate scheduler job.
Uses sklearn LogisticRegression — must run in venv with sklearn installed.
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection, init_schema
from src.calibration.platt import ExtendedPlattCalibrator


def refit_all():
    conn = get_connection()
    init_schema(conn)

    buckets = conn.execute("""
        SELECT DISTINCT cluster, season, COUNT(*) as n 
        FROM calibration_pairs 
        GROUP BY cluster, season 
        HAVING n >= 15
    """).fetchall()

    refit_count = 0
    for bucket in buckets:
        cluster = bucket["cluster"]
        season = bucket["season"]
        bucket_key = f"{cluster}_{season}"

        pairs = conn.execute("""
            SELECT p_raw, lead_days, outcome FROM calibration_pairs
            WHERE cluster = ? AND season = ? AND p_raw > 0.001 AND p_raw < 0.999
        """, (cluster, season)).fetchall()

        if len(pairs) < 15:
            continue

        p_raw = np.array([p["p_raw"] for p in pairs])
        lead_days = np.array([p["lead_days"] for p in pairs])
        outcomes = np.array([p["outcome"] for p in pairs])

        try:
            cal = ExtendedPlattCalibrator()
            reg_C = 1.0 if len(pairs) >= 50 else 0.1
            cal.fit(p_raw, lead_days, outcomes, n_bootstrap=200, regularization_C=reg_C)

            brier_scores = []
            for i in range(len(p_raw)):
                p_cal = cal.predict(float(p_raw[i]), float(lead_days[i]))
                brier_scores.append((p_cal - outcomes[i]) ** 2)
            brier_insample = float(np.mean(brier_scores))

            bootstrap_json = json.dumps(cal.bootstrap_params)
            now_iso = datetime.now(timezone.utc).isoformat()

            conn.execute("""
                INSERT OR REPLACE INTO platt_models 
                (bucket_key, param_A, param_B, param_C, bootstrap_params_json, 
                 n_samples, brier_insample, fitted_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (bucket_key, cal.A, cal.B, cal.C, bootstrap_json,
                  len(pairs), brier_insample, now_iso))

            refit_count += 1
            print(f"✅ {bucket_key:25} A={cal.A:+.3f} B={cal.B:+.3f} C={cal.C:+.3f} "
                  f"n={len(pairs)} Brier={brier_insample:.4f}")

        except Exception as e:
            print(f"❌ {bucket_key:25} failed: {e}")

    conn.commit()
    conn.close()
    print(f"\nRefit {refit_count} Platt models")


if __name__ == "__main__":
    refit_all()
