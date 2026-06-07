import json
import sqlite3

import pytest

from src.data.replacement_forecast_bundle_reader import ReplacementForecastPosteriorBundle


def test_bundle_dataclass_rejects_q_lcb_keys_that_do_not_match_q_keys() -> None:
    with pytest.raises(ValueError, match="q_lcb keys"):
        ReplacementForecastPosteriorBundle(
            posterior_id=1,
            city="Tokyo",
            target_date="2026-06-08",
            temperature_metric="low",
            source_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
            product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_low_v1",
            q={"16C": 0.4, "17C": 0.6},
            q_lcb={"16C": 0.3},
            posterior_method="test",
            source_cycle_time="2026-06-07T00:00:00+00:00",
            source_available_at="2026-06-07T02:00:00+00:00",
            computed_at="2026-06-07T03:00:00+00:00",
            baseline_source_run_id="b0",
            dependency_json={},
            provenance_json={"bin_topology_hash": "hash"},
            trade_authority_status="SHADOW_VETO_ONLY",
        )
