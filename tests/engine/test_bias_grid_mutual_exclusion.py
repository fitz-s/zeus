# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: double-count structural antibody. In _market_analysis_from_event_snapshot
#   the bias correction (_maybe_apply_edli_bias_correction) and the grid-representativeness
#   correction (_maybe_apply_grid_representativeness_correction) are composed UNCONDITIONALLY.
#   Both subtract a per-city MEAN temperature residual (E[forecast-observed] and
#   E[grid-point offset]). If BOTH flags are ever ON they subtract F = E[r_bias] + E[r_grid]
#   -> the warm-shift is applied ~twice (over-correction). Today bias=ON, grid=OFF, so this
#   is inert — but the guard makes the wrong code UNCONSTRUCTABLE (Fitz: kill the category,
#   not the instance). At most ONE temperature-domain mean correction may apply; both -> raise.
"""Relationship test (cross-module invariant) — bias/grid mutual exclusion.

Cross-module invariant under test:
    At most ONE temperature-domain mean correction (bias OR grid) may be applied to the
    member array before p_raw. If a candidate would be BOTH bias-corrected AND grid-corrected
    the adapter must FAIL CLOSED (raise) rather than silently subtract two mean residuals.

Tests:
    (a) bias-only applies (grid returns applied=False) -> members shifted once, no raise.
    (b) grid-only applies (bias returns applied=False) -> members shifted once, no raise.
    (c) BOTH applied on the same members -> guard fires (raises), no 2x subtraction.
    (d) RED-first witness: with the guard removed, BOTH-enabled silently double-subtracts.
        Asserted here by directly exercising the guard helper so the structural intent is
        pinned even if the adapter wiring is refactored.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

from src.contracts.execution_price import ExecutionPrice as EP
from src.engine.event_reactor_adapter import _market_analysis_from_event_snapshot
from src.types.market import Bin

RAW_MEAN = 24.5
N_MEMBERS = 51
EFF_BIAS_C = -3.447   # warming shift (bias)
GRID_OFFSET_C = -1.0  # warming shift (grid)


def _raw_members(mean=RAW_MEAN, spread=1.6, n=N_MEMBERS, seed=7):
    rng = np.random.default_rng(seed)
    return rng.normal(mean, spread, n).astype(float)


def _two_bins():
    return [
        Bin(23, 23, "C", "23°C"),
        Bin(24, None, "C", "24°C or higher"),
    ]


def _family(bins, city="Tokyo", metric="high"):
    candidates = [
        SimpleNamespace(condition_id=f"cond-{i}", bin=b,
                        yes_token_id=f"yes-{i}", no_token_id=f"no-{i}")
        for i, b in enumerate(bins)
    ]
    return SimpleNamespace(
        city=city, metric=metric, target_date="2026-06-03",
        event_type="FORECAST_SNAPSHOT_READY", bins=bins, candidates=candidates,
        yes_token_ids=[f"yes-{i}" for i in range(len(bins))],
        no_token_ids=[f"no-{i}" for i in range(len(bins))], family_id="test-fam",
    )


def _snapshot(members, metric="high"):
    return {
        "settlement_unit": "C", "temperature_metric": metric,
        "members_json": json.dumps(members.tolist()), "members_precision": 1.0,
        "source_id": "ecmwf_open_data", "issue_time": "2026-06-01T00:00:00+00:00",
        "dataset_id": "test_v1", "data_version": "test_v1",
    }


def _costs(bins, no_price=0.75, yes_price=0.25):
    costs = {}
    for i, _ in enumerate(bins):
        cid = f"cond-{i}"
        costs[(cid, "buy_yes")] = (None, EP(yes_price, "ask", fee_deducted=True, currency="probability_units"), yes_price, None, None)
        costs[(cid, "buy_no")] = (None, EP(no_price, "ask", fee_deducted=True, currency="probability_units"), no_price, None, None)
    return costs


def _run_market_analysis(*, bias_applied: bool, grid_applied: bool, raw_members: np.ndarray):
    """Drive _market_analysis_from_event_snapshot with deterministic bias/grid mocks.

    Each mock subtracts its constant shift and reports applied=<flag>. This isolates the
    composition/guard logic from any DB / flag state.
    """
    from src.config import runtime_cities_by_name
    if runtime_cities_by_name().get("Tokyo") is None:
        pytest.skip("Tokyo city config missing")

    bins = _two_bins()
    snapshot = _snapshot(raw_members)
    family = _family(bins)
    native_costs = _costs(bins)
    payload: dict = {}
    cal = sqlite3.connect(":memory:")

    def _fake_bias(members, *, snapshot, family, city, payload):
        if bias_applied:
            return np.asarray(members, dtype=float) - EFF_BIAS_C, True
        return np.asarray(members, dtype=float), False

    def _fake_grid(members, *, snapshot, family, city, payload):
        if grid_applied:
            payload["_edli_grid_corrected"] = True
            return np.asarray(members, dtype=float) - GRID_OFFSET_C, True
        return np.asarray(members, dtype=float), False

    with mock.patch(
        "src.engine.event_reactor_adapter._maybe_apply_edli_bias_correction",
        side_effect=_fake_bias,
    ), mock.patch(
        "src.engine.event_reactor_adapter._maybe_apply_grid_representativeness_correction",
        side_effect=_fake_grid,
    ):
        return _market_analysis_from_event_snapshot(
            calibration_conn=cal, snapshot=snapshot, family=family,
            native_costs=native_costs, payload=payload, decision_time=None,
        )


# ---------------------------------------------------------------------------
# (a) bias-only applies
# ---------------------------------------------------------------------------

def test_a_bias_only_applies_single_shift():
    raw = _raw_members()
    analysis = _run_market_analysis(bias_applied=True, grid_applied=False, raw_members=raw)
    # one warming shift of -EFF_BIAS_C (=+3.447)
    assert abs(analysis._member_maxes.mean() - (raw.mean() - EFF_BIAS_C)) < 1e-9


# ---------------------------------------------------------------------------
# (b) grid-only applies
# ---------------------------------------------------------------------------

def test_b_grid_only_applies_single_shift():
    raw = _raw_members()
    analysis = _run_market_analysis(bias_applied=False, grid_applied=True, raw_members=raw)
    assert abs(analysis._member_maxes.mean() - (raw.mean() - GRID_OFFSET_C)) < 1e-9


# ---------------------------------------------------------------------------
# (c) BOTH applied -> guard fires (no 2x subtraction)
# ---------------------------------------------------------------------------

def test_c_both_applied_guard_fires():
    raw = _raw_members()
    with pytest.raises(Exception) as exc:
        _run_market_analysis(bias_applied=True, grid_applied=True, raw_members=raw)
    msg = str(exc.value).lower()
    assert "double" in msg or "mutual" in msg or "both" in msg or "exclus" in msg, (
        f"guard must raise a double-count/mutual-exclusion error, got: {exc.value!r}"
    )


# ---------------------------------------------------------------------------
# (d) Direct guard-helper unit: the structural intent, refactor-proof
# ---------------------------------------------------------------------------

def test_d_guard_helper_rejects_both_temperature_mean_corrections():
    """Pin the guard at the helper level so the antibody survives adapter refactors.

    With BOTH temperature-domain mean corrections applied the helper must raise; with at
    most one applied it must be a no-op. RED-first witness: if the helper is a pass-through,
    test_c can never fire because the composition would silently double-subtract.
    """
    from src.engine.event_reactor_adapter import _assert_single_temperature_mean_correction

    # at most one -> no raise
    _assert_single_temperature_mean_correction(bias_applied=True, grid_applied=False)
    _assert_single_temperature_mean_correction(bias_applied=False, grid_applied=True)
    _assert_single_temperature_mean_correction(bias_applied=False, grid_applied=False)
    # both -> raise
    with pytest.raises(Exception):
        _assert_single_temperature_mean_correction(bias_applied=True, grid_applied=True)
