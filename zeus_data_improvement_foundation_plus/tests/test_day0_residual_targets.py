from __future__ import annotations

import pandas as pd


def test_day0_residual_target_is_non_negative() -> None:
    df = pd.DataFrame(
        {
            "settlement_value": [70.0, 68.0, 71.0],
            "running_max": [65.0, 69.0, 71.0],
        }
    )
    df["residual_upside"] = (df["settlement_value"] - df["running_max"]).clip(lower=0.0)
    df["has_upside"] = (df["settlement_value"] > df["running_max"]).astype(int)

    assert df["residual_upside"].tolist() == [5.0, 0.0, 0.0]
    assert df["has_upside"].tolist() == [1, 0, 0]
