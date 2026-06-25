# Created: 2026-06-07
# Last reused or audited: 2026-06-07
# Authority basis: docs/the_path/QLCB_HONESTY.md FIX-B (wire the existing K3
#   settlement_backward_coverage shrink into the LIVE replacement_0_1 path.
"""TDD for ITEM 2 (K3 wiring into the replacement path).

  ITEM 2: _replacement_authority_probability_and_fdr_proof must call the EXISTING
          _maybe_apply_settlement_coverage_to_lcb on its lcb_by_direction. No duplicate
          helper. Thin settled data remains a typed INSUFFICIENT_DATA no-op.

"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

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
                condition_id="cond-28",
                yes_token_id="yes-28",
                no_token_id="no-28",
                bin=Bin(low=28.0, high=28.0, unit="C", label="28°C"),
            ),
        ),
    )


def _replacement_bundle() -> SimpleNamespace:
    wilson_28 = adapter._wilson_lower_bound(41.0, 51.0)
    return SimpleNamespace(
        posterior_id=123,
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        q={"bin-28": 0.80},
        q_lcb={"bin-28": wilson_28},
        q_ucb={"bin-28": 0.90},
        provenance_json={
            # FIX 1 (2026-06-09): live-eligible q-mode so this fixture reaches the K3
            # coverage relationship it tests (the gate itself is covered separately).
            "replacement_q_mode": "FUSED_NORMAL_FULL",
            "q_shape": "fused_normal_direct",
            "q_lcb_basis": "fused_center_bootstrap_p05",
            "anchor_value_c": 28.0,
            "aifs_member_count": 51,
            "aifs_probabilities": {"bin-28": 41 / 51},
            "bin_topology": [{"bin_id": "bin-28", "lower_c": 28.0, "upper_c": 28.0}],
        },
    )


def _native_costs() -> dict:
    return {
        ("cond-28", "buy_yes"): (None, ExecutionPrice(0.55, "ask", fee_deducted=True, currency="probability_units"), 0.55, None, None),
        ("cond-28", "buy_no"): (None, ExecutionPrice(0.45, "ask", fee_deducted=True, currency="probability_units"), 0.45, None, None),
    }


def _setup(monkeypatch):
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
    monkeypatch.setattr(adapter, "settlement_sigma_floor", lambda *a, **k: 3.18)


def _run(monkeypatch):
    return adapter._replacement_authority_probability_and_fdr_proof(
        event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
        payload={},
        family=_family(),
        conn=object(),
        native_costs=_native_costs(),
        decision_time=datetime(2026, 6, 7, tzinfo=timezone.utc),
        promotion_evidence=None,
        capital_objective_evidence=None,
    )


def test_replacement_path_calls_k3_coverage_helper(monkeypatch) -> None:
    """ITEM 2: the replacement path invokes the EXISTING coverage helper (same helper,
    no duplicate) on its lcb_by_direction, threading the forecast_conn."""
    _setup(monkeypatch)
    calls: list = []

    real_helper = adapter._maybe_apply_settlement_coverage_to_lcb

    def _spy(*, family, forecast_conn, lcb_by_direction):
        calls.append((family, forecast_conn, lcb_by_direction))
        return real_helper(family=family, forecast_conn=forecast_conn, lcb_by_direction=lcb_by_direction)

    monkeypatch.setattr(adapter, "_maybe_apply_settlement_coverage_to_lcb", _spy)
    _run(monkeypatch)
    assert len(calls) == 1, "replacement path must call the K3 coverage helper exactly once"
    _fam, conn_arg, lcb_arg = calls[0]
    assert lcb_arg is not None
    # The helper receives a forecast connection (the same conn the path was given).
    assert conn_arg is not None


def test_replacement_path_thin_coverage_is_noop(monkeypatch) -> None:
    """ITEM 2: with no settled claim history, coverage is INSUFFICIENT_DATA and q_lcb
    remains the bundle's bootstrap/Wilson value."""
    _setup(monkeypatch)
    from src.calibration.qlcb_provenance import _qlcb_float

    _q, lcb, _p, _pf, _ev = _run(monkeypatch)
    wilson_28 = adapter._wilson_lower_bound(41.0, 51.0)
    assert _qlcb_float(lcb[("cond-28", "buy_yes")]) == pytest.approx(wilson_28)
    # No SETTLEMENT_ISOTONIC shrink when the settled record is thin.
    assert lcb[("cond-28", "buy_yes")].calibration_source != "SETTLEMENT_ISOTONIC"
