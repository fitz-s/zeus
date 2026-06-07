# Created: 2026-06-07
# Last reused or audited: 2026-06-07
# Authority basis: docs/the_path/QLCB_HONESTY.md FIX-B (wire the existing K3
#   settlement_backward_coverage shrink into the LIVE replacement_0_1 path; the
#   helper's sole call site was the canonical path ~5755) + Item 3 (shadow-log
#   claimed -> floored -> coverage-shrunk on every replacement q_lcb decision).
"""TDD for ITEM 2 (K3 wiring into the replacement path) + ITEM 3 (shadow-log).

  ITEM 2: _replacement_authority_probability_and_fdr_proof must call the EXISTING
          _maybe_apply_settlement_coverage_to_lcb on its lcb_by_direction, under the
          EXISTING q_lcb_settlement_coverage_gate_enabled flag (default FALSE). No
          duplicate helper. Flag OFF -> no-op; the wired call is present for when
          settled data accrues.

  ITEM 3: every replacement q_lcb decision logs claimed -> floored -> (coverage-shrunk)
          to a queryable surface so live before/after validation data accrues.
"""
from __future__ import annotations

import logging
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
    return SimpleNamespace(
        posterior_id=123,
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        q={"bin-28": 0.80},
        q_lcb=None,
        provenance_json={
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


def _setup(monkeypatch, *, coverage_flag: bool, floor_flag: bool):
    from src.config import settings
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import replacement_forecast_hook_factory as hook_factory

    feature_flags = dict(settings._data.get("feature_flags", {}))
    feature_flags["openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled"] = True
    monkeypatch.setitem(settings._data, "feature_flags", feature_flags)

    edli = dict(settings._data["edli_v1"])
    edli["q_lcb_settlement_coverage_gate_enabled"] = coverage_flag
    edli["replacement_qlcb_settlement_sigma_floor_enabled"] = floor_flag
    monkeypatch.setitem(settings._data, "edli_v1", edli)

    monkeypatch.setattr(hook_factory, "_latest_replacement_readiness", lambda *a, **k: object())
    monkeypatch.setattr(
        reader,
        "read_replacement_forecast_bundle",
        lambda *a, **k: SimpleNamespace(ok=True, bundle=_replacement_bundle(), reason_code="READY"),
    )
    monkeypatch.setattr(adapter, "settlement_sigma_floor", lambda *a, **k: 3.18)


def _run(monkeypatch):
    from tests.test_replacement_forecast_runtime_policy import (
        _capital_objective_evidence,
        _passing_evidence,
    )

    return adapter._replacement_authority_probability_and_fdr_proof(
        event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
        payload={},
        family=_family(),
        conn=object(),
        native_costs=_native_costs(),
        decision_time=datetime(2026, 6, 7, tzinfo=timezone.utc),
        promotion_evidence=_passing_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )


def test_replacement_path_calls_k3_coverage_helper(monkeypatch) -> None:
    """ITEM 2: the replacement path invokes the EXISTING coverage helper (same helper,
    no duplicate) on its lcb_by_direction, threading the forecast_conn."""
    _setup(monkeypatch, coverage_flag=False, floor_flag=False)
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


def test_replacement_path_coverage_flag_off_is_noop(monkeypatch) -> None:
    """ITEM 2: with the coverage flag OFF the wired K3 call is a no-op — the q_lcb is
    the (unfloored, floor flag also off) Wilson value, byte-identical to pre-wiring."""
    _setup(monkeypatch, coverage_flag=False, floor_flag=False)
    from src.calibration.qlcb_provenance import _qlcb_float

    _q, lcb, _p, _pf, _ev = _run(monkeypatch)
    wilson_28 = adapter._wilson_lower_bound(41.0, 51.0)
    assert _qlcb_float(lcb[("cond-28", "buy_yes")]) == pytest.approx(wilson_28)
    # No SETTLEMENT_ISOTONIC re-grounding when the gate is off.
    assert lcb[("cond-28", "buy_yes")].calibration_source != "SETTLEMENT_ISOTONIC"


def test_replacement_qlcb_shadow_log_emitted(monkeypatch, caplog) -> None:
    """ITEM 3: every replacement q_lcb decision emits a queryable claimed->floored
    shadow record (so live before/after data accrues from the next daemon run)."""
    _setup(monkeypatch, coverage_flag=False, floor_flag=True)
    with caplog.at_level(logging.INFO, logger="zeus.replacement_qlcb_shadow"):
        _run(monkeypatch)
    records = [r for r in caplog.records if r.name == "zeus.replacement_qlcb_shadow"]
    assert records, "expected a replacement q_lcb shadow log record"
    msg = records[0].getMessage()
    # The shadow record carries the city, the bin, claimed and floored values.
    assert "Testopolis" in msg
    assert "claimed" in msg and "floored" in msg
