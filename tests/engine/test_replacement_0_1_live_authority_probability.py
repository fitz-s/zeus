# Created: 2026-06-07
# Last reused/audited: 2026-06-07
# Authority basis: Operator 2026-06-07 live cutover directive: replacement 0.1
#   posterior is the live forecast authority; NO probabilities must not be
#   inferred from YES complements.

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.calibration.qlcb_provenance import _qlcb_float
from src.contracts.execution_price import ExecutionPrice
from src.engine import event_reactor_adapter as adapter
from src.types.market import Bin


def _family() -> SimpleNamespace:
    return SimpleNamespace(
        city="Testopolis",
        target_date="2026-06-09",
        metric="high",
        candidates=(
            SimpleNamespace(
                condition_id="cond-27",
                yes_token_id="yes-27",
                no_token_id="no-27",
                bin=Bin(low=27.0, high=27.0, unit="C", label="27°C"),
            ),
            SimpleNamespace(
                condition_id="cond-28",
                yes_token_id="yes-28",
                no_token_id="no-28",
                bin=Bin(low=28.0, high=28.0, unit="C", label="28°C"),
            ),
        ),
    )


def _replacement_bundle() -> SimpleNamespace:
    return SimpleNamespace(
        posterior_id=123,
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        q={
            "bin-27": 0.20,
            "bin-28": 0.80,
        },
        q_lcb=None,
        provenance_json={
            "aifs_member_count": 51,
            "aifs_probabilities": {
                "bin-27": 10 / 51,
                "bin-28": 41 / 51,
            },
            "bin_topology": [
                {"bin_id": "bin-27", "lower_c": 27.0, "upper_c": 27.0},
                {"bin_id": "bin-28", "lower_c": 28.0, "upper_c": 28.0},
            ],
        },
    )


def test_replacement_0_1_authority_uses_yes_posterior_and_blocks_no_without_native_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config import settings
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import replacement_forecast_hook_factory as hook_factory

    feature_flags = dict(settings._data.get("feature_flags", {}))
    feature_flags["openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled"] = True
    monkeypatch.setitem(settings._data, "feature_flags", feature_flags)
    monkeypatch.setattr(hook_factory, "_latest_replacement_readiness", lambda *a, **k: object())
    monkeypatch.setattr(
        reader,
        "read_replacement_forecast_bundle",
        lambda *a, **k: SimpleNamespace(ok=True, bundle=_replacement_bundle(), reason_code="READY"),
    )

    q_by_condition, lcb_by_direction, p_values, prefilter, evidence = (
        adapter._replacement_authority_probability_and_fdr_proof(
            event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
            payload={},
            family=_family(),
            conn=object(),
            native_costs={
                ("cond-27", "buy_yes"): (None, ExecutionPrice(0.30, "ask", fee_deducted=True, currency="probability_units"), 0.30, None, None),
                ("cond-28", "buy_yes"): (None, ExecutionPrice(0.55, "ask", fee_deducted=True, currency="probability_units"), 0.55, None, None),
                ("cond-27", "buy_no"): (None, ExecutionPrice(0.70, "ask", fee_deducted=True, currency="probability_units"), 0.70, None, None),
                ("cond-28", "buy_no"): (None, ExecutionPrice(0.45, "ask", fee_deducted=True, currency="probability_units"), 0.45, None, None),
            },
            decision_time=datetime(2026, 6, 7, tzinfo=timezone.utc),
        )
    )

    assert evidence["probability_authority"] == "replacement_0_1"
    assert q_by_condition == {"cond-27": pytest.approx(0.20), "cond-28": pytest.approx(0.80)}
    assert _qlcb_float(lcb_by_direction[("cond-28", "buy_yes")]) > 0.55
    assert _qlcb_float(lcb_by_direction[("cond-27", "buy_no")]) == 0.0
    assert _qlcb_float(lcb_by_direction[("cond-28", "buy_no")]) == 0.0
    assert p_values[("cond-28", "buy_no")] == 1.0
    assert prefilter[("cond-28", "buy_no")] is False
