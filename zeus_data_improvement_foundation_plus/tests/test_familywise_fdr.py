from __future__ import annotations

import pandas as pd

from src.strategy.selection_family import apply_familywise_fdr


def test_fdr_runs_on_full_tested_family_not_prefiltered_subset() -> None:
    df = pd.DataFrame(
        [
            {"family_id": "fam1", "p_value": 0.001, "tested": True, "passed_prefilter": True},
            {"family_id": "fam1", "p_value": 0.020, "tested": True, "passed_prefilter": False},
            {"family_id": "fam1", "p_value": 0.040, "tested": True, "passed_prefilter": False},
            {"family_id": "fam1", "p_value": 0.500, "tested": True, "passed_prefilter": False},
        ]
    )

    out = apply_familywise_fdr(df, q=0.10)

    # The best p-value should survive.
    assert int(out["selected_post_fdr"].sum()) >= 1
    # q-values should exist for all tested hypotheses in the family.
    assert out["q_value"].notna().sum() == 4
