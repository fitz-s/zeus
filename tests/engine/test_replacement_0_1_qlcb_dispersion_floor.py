# Created: 2026-06-07
# Last reused or audited: 2026-06-07
# Authority basis: docs/the_path/QLCB_HONESTY.md FIX-C (floor the LIVE replacement_0_1
#   q_lcb at the realized-settlement residual) + OBSERVE_BASELINE.md (the -6.6% /
#   optimism baseline). Iron rule #6: q_lcb MUST be a conservative lower bound; the
#   fix must be ONLY-LOWERS (widening sigma lowers q_lcb on overconfident bins) and
#   flag-OFF must be byte-identical to today.
"""TDD for the q_lcb honesty fix on the LIVE replacement_0_1 authority path.

Three properties, written BEFORE the implementation:

  (a) ONLY-LOWERS — over a grid of (mu, sigma_model, floor) including floor>sigma_model,
      the floored q_lcb is always <= the raw Wilson/bundle q_lcb (never raises it).

  (b) FLOOR-USES-REALIZED-RESIDUAL — with sigma_model < settlement_sigma_floor the
      effective sigma is the FLOOR (not the ~0.67C member spread), so a tight cluster
      of members (e.g. 41/51) cannot manufacture a q_lcb above the settlement-grounded
      ceiling implied by the 3.18C residual.

  (c) FLAG-OFF BYTE-IDENTICAL — with replacement_qlcb_settlement_sigma_floor_enabled
      FALSE (the default) the replacement q_lcb is byte-identical to the pre-fix Wilson
      value: the floor is never consulted.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.calibration.emos import bin_probability_settlement
from src.calibration.qlcb_provenance import _qlcb_float
from src.contracts.execution_price import ExecutionPrice
from src.engine import event_reactor_adapter as adapter
from src.types.market import Bin


# ---------------------------------------------------------------------------
# (a) ONLY-LOWERS — the pure-function property over a grid
# ---------------------------------------------------------------------------
def test_settlement_grounded_floor_only_lowers_over_grid() -> None:
    """For every (mu, sigma_model, floor) — INCLUDING floor > sigma_model — the
    floored q_lcb is <= the raw Wilson/bundle q_lcb. The floor can ONLY lower it."""
    # Raw Wilson q_lcb for a range of member-vote counts on 51 trials (the optimistic
    # branch the live path takes today). A 1C point bin centred on mu.
    member_count = 51.0
    lower_c, upper_c = 28.0, 28.0  # degenerate point bin -> WMO preimage [27.5, 28.5)
    moved_down = 0
    for successes in (10.0, 20.0, 30.0, 35.0, 40.0, 45.0, 51.0):
        raw_wilson = adapter._wilson_lower_bound(successes, member_count)
        for mu in (26.0, 27.5, 28.0, 28.5, 30.0):
            for sigma_model in (0.4, 0.67, 1.0, 3.0):
                for floor in (0.95, 3.18, 7.40):  # min / median / max of the 232-cell table
                    grounded = adapter._replacement_settlement_grounded_lcb(
                        mu_c=mu,
                        sigma_floor_c=floor,
                        sigma_model_c=sigma_model,
                        lower_c=lower_c,
                        upper_c=upper_c,
                    )
                    floored = min(raw_wilson, grounded)
                    # ONLY-LOWERS: never above the raw value.
                    assert floored <= raw_wilson + 1e-12
                    if floored < raw_wilson - 1e-12:
                        moved_down += 1
    # The fix must actually bite somewhere on this overconfident grid (else it is inert).
    assert moved_down > 0


def test_settlement_grounded_floor_uses_floor_not_member_spread() -> None:
    """floor > sigma_model => effective sigma is the FLOOR. The grounded q_lcb must
    equal the bin mass under N(mu, floor), NOT the (much tighter) N(mu, sigma_model)."""
    mu, sigma_model, floor = 28.0, 0.67, 3.18
    lower_c, upper_c = 28.0, 28.0
    grounded = adapter._replacement_settlement_grounded_lcb(
        mu_c=mu, sigma_floor_c=floor, sigma_model_c=sigma_model,
        lower_c=lower_c, upper_c=upper_c,
    )
    expected_floor = bin_probability_settlement(mu, floor, lower_c, upper_c)
    expected_member = bin_probability_settlement(mu, sigma_model, lower_c, upper_c)
    assert grounded == pytest.approx(expected_floor)
    # And it is strictly BELOW the member-spread mass (the 3.2x underdispersion gap).
    assert grounded < expected_member
    # The honest ceiling for a 1C bin at sigma=3.18C is ~0.125 — far below the 0.47-0.79
    # Wilson values the live path emits today (QLCB_HONESTY.md §2 Construction B).
    assert grounded < 0.15


def test_settlement_grounded_floor_takes_max_when_model_wider() -> None:
    """sigma_model > floor => effective sigma is sigma_model (max(), never narrower)."""
    mu, sigma_model, floor = 28.0, 5.0, 3.18
    lower_c, upper_c = 28.0, 28.0
    grounded = adapter._replacement_settlement_grounded_lcb(
        mu_c=mu, sigma_floor_c=floor, sigma_model_c=sigma_model,
        lower_c=lower_c, upper_c=upper_c,
    )
    expected = bin_probability_settlement(mu, max(sigma_model, floor), lower_c, upper_c)
    assert grounded == pytest.approx(expected)


def test_settlement_grounded_floor_handles_missing_member_sigma() -> None:
    """sigma_model None (not carried in provenance) => effective sigma is the floor."""
    mu, floor = 28.0, 3.18
    lower_c, upper_c = 28.0, 28.0
    grounded = adapter._replacement_settlement_grounded_lcb(
        mu_c=mu, sigma_floor_c=floor, sigma_model_c=None,
        lower_c=lower_c, upper_c=upper_c,
    )
    expected = bin_probability_settlement(mu, floor, lower_c, upper_c)
    assert grounded == pytest.approx(expected)


# ---------------------------------------------------------------------------
# (b) / (c) end-to-end on the live replacement bundle, flag OFF vs ON
# ---------------------------------------------------------------------------
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
    # mu (anchor_value_c) = 28.0C; bin-28 gets 41/51 members -> Wilson ~0.68 (overconfident).
    return SimpleNamespace(
        posterior_id=123,
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        q={"bin-27": 0.20, "bin-28": 0.80},
        q_lcb=None,
        provenance_json={
            # FIX 1 (2026-06-09): live-eligible q-mode so this fixture reaches the q_lcb-floor
            # dispersion relationship it tests (the gate itself is covered separately).
            "replacement_q_mode": "FUSED_NORMAL_FULL",
            "q_shape": "fused_normal_direct",
            "anchor_value_c": 28.0,
            "aifs_member_count": 51,
            "aifs_probabilities": {"bin-27": 10 / 51, "bin-28": 41 / 51},
            "bin_topology": [
                {"bin_id": "bin-27", "lower_c": 27.0, "upper_c": 27.0},
                {"bin_id": "bin-28", "lower_c": 28.0, "upper_c": 28.0},
            ],
        },
    )


def _native_costs() -> dict:
    return {
        ("cond-27", "buy_yes"): (None, ExecutionPrice(0.30, "ask", fee_deducted=True, currency="probability_units"), 0.30, None, None),
        ("cond-28", "buy_yes"): (None, ExecutionPrice(0.55, "ask", fee_deducted=True, currency="probability_units"), 0.55, None, None),
        ("cond-27", "buy_no"): (None, ExecutionPrice(0.70, "ask", fee_deducted=True, currency="probability_units"), 0.70, None, None),
        ("cond-28", "buy_no"): (None, ExecutionPrice(0.45, "ask", fee_deducted=True, currency="probability_units"), 0.45, None, None),
    }


def _run_replacement(monkeypatch, *, floor_flag: bool):
    from src.config import settings
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import replacement_forecast_hook_factory as hook_factory
    from tests.test_replacement_forecast_runtime_policy import (
        _capital_objective_evidence,
        _passing_evidence,
    )

    feature_flags = dict(settings._data.get("feature_flags", {}))
    feature_flags["openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled"] = True
    monkeypatch.setitem(settings._data, "feature_flags", feature_flags)

    edli = dict(settings._data["edli_v1"])
    edli["replacement_qlcb_settlement_sigma_floor_enabled"] = floor_flag
    # Keep the K3 coverage gate OFF for this test (it is a separate item).
    edli["q_lcb_settlement_coverage_gate_enabled"] = False
    monkeypatch.setitem(settings._data, "edli_v1", edli)

    monkeypatch.setattr(hook_factory, "_latest_replacement_readiness", lambda *a, **k: object())
    monkeypatch.setattr(
        reader,
        "read_replacement_forecast_bundle",
        lambda *a, **k: SimpleNamespace(ok=True, bundle=_replacement_bundle(), reason_code="READY"),
    )
    # Floor table lookup -> a known cell value so the test is deterministic and does not
    # depend on the on-disk 232-cell artifact. Effective floor sigma = 3.18C.
    monkeypatch.setattr(adapter, "settlement_sigma_floor", lambda *a, **k: 3.18)

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


def test_flag_off_replacement_qlcb_is_byte_identical_to_wilson(monkeypatch) -> None:
    """FLAG OFF (default): the replacement q_lcb is the raw Wilson value, byte-identical
    to pre-fix. bin-28 = 41/51 -> Wilson lower bound, which is > 0.55 (the cost)."""
    _q, lcb_by_direction, _p, _pf, _ev = _run_replacement(monkeypatch, floor_flag=False)
    wilson_28 = adapter._wilson_lower_bound(41.0, 51.0)
    got = _qlcb_float(lcb_by_direction[("cond-28", "buy_yes")])
    assert got == pytest.approx(wilson_28)
    # Sanity: this is the overconfident value the baseline emits (>0.55), unfloored.
    assert got > 0.55


def test_flag_on_replacement_qlcb_floored_to_settlement_residual(monkeypatch) -> None:
    """FLAG ON: bin-28's Wilson ~0.68 is floored to the settlement-grounded ceiling
    under N(mu=28, sigma=3.18) ~0.12 — ONLY-LOWERS, never raised."""
    _q, lcb_off, _p, _pf, _ev = _run_replacement(monkeypatch, floor_flag=False)
    _q2, lcb_on, _p2, _pf2, _ev2 = _run_replacement(monkeypatch, floor_flag=True)

    off_28 = _qlcb_float(lcb_off[("cond-28", "buy_yes")])
    on_28 = _qlcb_float(lcb_on[("cond-28", "buy_yes")])

    grounded = bin_probability_settlement(28.0, 3.18, 28.0, 28.0)
    assert on_28 == pytest.approx(min(off_28, grounded))
    # ONLY-LOWERS on this overconfident bin.
    assert on_28 < off_28
    assert on_28 == pytest.approx(grounded)
    assert grounded < 0.15
