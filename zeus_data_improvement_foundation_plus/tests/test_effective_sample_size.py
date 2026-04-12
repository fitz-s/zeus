from __future__ import annotations

import pandas as pd

from src.calibration.effective_sample_size import summarize_bucket_health


def test_bucket_health_counts_decision_groups_not_pair_rows() -> None:
    groups = pd.DataFrame(
        [
            {"bucket_key": "A_DJF", "cluster": "A", "season": "DJF", "target_date": "2026-01-01", "n_pair_rows": 11, "n_positive_rows": 1, "lead_days": 1.0},
            {"bucket_key": "A_DJF", "cluster": "A", "season": "DJF", "target_date": "2026-01-02", "n_pair_rows": 11, "n_positive_rows": 1, "lead_days": 1.0},
            {"bucket_key": "A_DJF", "cluster": "A", "season": "DJF", "target_date": "2026-01-03", "n_pair_rows": 11, "n_positive_rows": 1, "lead_days": 1.0},
        ]
    )

    out = summarize_bucket_health(groups)
    row = out.iloc[0]

    assert row["decision_groups"] == 3
    assert row["pair_rows"] == 33
    assert round(row["avg_rows_per_group"], 2) == 11.0
