# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 7 LIVE half — when the sigma floor is enabled but a floor input is missing, the LIVE/authority path must block (not degrade to raw Wilson); fail-soft-to-raw is unsafe on the money path.
# Reuse: Run with pytest; update if q_lcb floor logic or LIVE/SHADOW mode-split in replacement_0_1 changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: PR#400 the_path audit BLOCKER 7 (q_lcb floor unsafe live fallback).
#   docs/the_path/QLCB_HONESTY.md FIX-C established the LIVE replacement_0_1 settlement
#   sigma-floor. The floor exists because the raw Wilson q_lcb over 51 AIFS votes ignores
#   the ~3.2x settlement underdispersion (overconfident lower bound). When the floor is
#   ENABLED but a floor input (anchor mu / sigma-floor cell / bin topology) is MISSING,
#   degrading to the RAW Wilson value re-emits the exact overconfident bound the floor
#   exists to fix. Fail-soft-to-raw is acceptable for SHADOW (observation only) but UNSAFE
#   for LIVE/authority/capital. On the live authority path a missing floor MUST BLOCK the
#   candidate (no submit), NEVER pass the raw bound to capital.
"""BLOCKER 7 — relationship test: LIVE replacement q_lcb path BLOCKS on a missing floor.

Relationship under test (cross-module boundary): the settlement sigma-floor producer
(_replacement_authority_probability_and_fdr_proof, the LIVE replacement_0_1 authority
builder) hands a per-bin q_lcb to the candidate-proof selector
(_generate_candidate_proofs) which sizes real capital. The invariant that must hold
across that boundary: when the floor is ENABLED, the q_lcb consumed by live selection is
EITHER the settlement-grounded floored value OR the candidate is blocked — it is NEVER the
raw (unfloored, overconfident) Wilson value. A missing floor input on the live path is a
hard block, not a silent raw fallback.

These tests fail (the live path returns the raw Wilson q_lcb) if the BLOCKER 7 fix is
reverted to the fail-soft-to-raw behavior.
"""
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
                condition_id="cond-28",
                yes_token_id="yes-28",
                no_token_id="no-28",
                bin=Bin(low=28.0, high=28.0, unit="C", label="28°C"),
            ),
        ),
    )


def _replacement_bundle(*, with_anchor: bool = True, with_topology: bool = True) -> SimpleNamespace:
    # bin-28 gets 41/51 members -> raw Wilson ~0.68 (the overconfident bound the floor
    # exists to cap). The floor at sigma=3.18C grounds that to ~0.12.
    provenance: dict = {
        "aifs_member_count": 51,
        "aifs_probabilities": {"bin-28": 41 / 51},
        # FIX 1 (2026-06-09): the live q-mode gate now runs BEFORE the q_lcb floor logic and
        # admits only the fused-Normal modes. These fixtures exercise the DOWNSTREAM q_lcb-floor
        # relationship, so they carry a live-eligible mode to reach it (the gate itself is covered
        # by tests/test_replacement_q_mode_authority.py).
        "replacement_q_mode": "FUSED_NORMAL_FULL",
        "q_shape": "fused_normal_direct",
    }
    if with_anchor:
        provenance["anchor_value_c"] = 28.0
    if with_topology:
        provenance["bin_topology"] = [{"bin_id": "bin-28", "lower_c": 28.0, "upper_c": 28.0}]
    return SimpleNamespace(
        posterior_id=123,
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        q={"bin-28": 0.80},
        q_lcb=None,
        provenance_json=provenance,
    )


def _native_costs() -> dict:
    return {
        ("cond-28", "buy_yes"): (None, ExecutionPrice(0.55, "ask", fee_deducted=True, currency="probability_units"), 0.55, None, None),
        ("cond-28", "buy_no"): (None, ExecutionPrice(0.45, "ask", fee_deducted=True, currency="probability_units"), 0.45, None, None),
    }


def _setup(monkeypatch, *, floor_flag: bool, bundle: SimpleNamespace, sigma_floor):
    from src.config import settings
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import replacement_forecast_hook_factory as hook_factory

    feature_flags = dict(settings._data.get("feature_flags", {}))
    feature_flags["openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled"] = True
    monkeypatch.setitem(settings._data, "feature_flags", feature_flags)

    edli = dict(settings._data["edli_v1"])
    edli["replacement_qlcb_settlement_sigma_floor_enabled"] = floor_flag
    edli["q_lcb_settlement_coverage_gate_enabled"] = False
    monkeypatch.setitem(settings._data, "edli_v1", edli)

    monkeypatch.setattr(hook_factory, "_latest_replacement_readiness", lambda *a, **k: object())
    monkeypatch.setattr(
        reader,
        "read_replacement_forecast_bundle",
        lambda *a, **k: SimpleNamespace(ok=True, bundle=bundle, reason_code="READY"),
    )
    monkeypatch.setattr(adapter, "settlement_sigma_floor", sigma_floor)


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


def _floor_missing_codes(exc: ValueError) -> bool:
    return "REPLACEMENT_0_1_LIVE_AUTHORITY_QLCB_FLOOR_MISSING" in str(exc)


def test_live_missing_sigma_floor_cell_blocks(monkeypatch) -> None:
    """Floor ENABLED but the per-(city,season,metric) sigma-floor cell is missing
    (settlement_sigma_floor returns None). The live path must BLOCK — it must NOT emit the
    raw Wilson ~0.68 q_lcb to capital."""
    _setup(
        monkeypatch,
        floor_flag=True,
        bundle=_replacement_bundle(),
        sigma_floor=lambda *a, **k: None,
    )
    with pytest.raises(ValueError) as excinfo:
        _run(monkeypatch)
    assert _floor_missing_codes(excinfo.value)


def test_live_missing_anchor_mu_blocks(monkeypatch) -> None:
    """Floor ENABLED but the anchor mu (anchor_value_c) is absent from provenance. The
    live path must BLOCK rather than silently keep the raw overconfident bound."""
    _setup(
        monkeypatch,
        floor_flag=True,
        bundle=_replacement_bundle(with_anchor=False),
        sigma_floor=lambda *a, **k: 3.18,
    )
    with pytest.raises(ValueError) as excinfo:
        _run(monkeypatch)
    assert _floor_missing_codes(excinfo.value)


def test_live_missing_bin_topology_blocks(monkeypatch) -> None:
    """Floor ENABLED but the bin topology is absent. The candidate must BLOCK (never emit the
    raw overconfident bound to capital). A fully-absent topology is caught by the pre-existing
    bin-binding guard (it cannot bind the candidate to a bin_id), so the block surfaces as the
    bin-binding code; a per-bin floor-input loss (anchor/sigma) surfaces as the floor-missing
    code. The relationship invariant is the SAME either way: live + floor-ON + a missing floor
    input => BLOCK, never raw. Assert the block (either honest blocking code), not the literal."""
    _setup(
        monkeypatch,
        floor_flag=True,
        bundle=_replacement_bundle(with_topology=False),
        sigma_floor=lambda *a, **k: 3.18,
    )
    with pytest.raises(ValueError) as excinfo:
        _run(monkeypatch)
    msg = str(excinfo.value)
    assert (
        "REPLACEMENT_0_1_LIVE_AUTHORITY_QLCB_FLOOR_MISSING" in msg
        or "REPLACEMENT_0_1_LIVE_AUTHORITY_BIN_BINDING_MISSING" in msg
    ), msg


def test_live_sigma_floor_setup_exception_blocks(monkeypatch) -> None:
    """Floor ENABLED but the floor SETUP raises (e.g. season/city resolution failure).
    The live path must BLOCK — the family-level fail-soft-to-raw is unsafe for capital."""
    def _raise(*a, **k):
        raise RuntimeError("sigma floor table unavailable")

    _setup(
        monkeypatch,
        floor_flag=True,
        bundle=_replacement_bundle(),
        sigma_floor=_raise,
    )
    with pytest.raises(ValueError) as excinfo:
        _run(monkeypatch)
    assert _floor_missing_codes(excinfo.value)


def test_live_floor_present_does_not_block_and_floors(monkeypatch) -> None:
    """Control: with all floor inputs present the live path does NOT block; it floors the
    overconfident bin to the settlement-grounded ceiling (ONLY-LOWERS, the FIX-C behavior).
    This proves the block is specific to the MISSING-floor case, not a blanket veto."""
    from src.calibration.emos import bin_probability_settlement

    _setup(
        monkeypatch,
        floor_flag=True,
        bundle=_replacement_bundle(),
        sigma_floor=lambda *a, **k: 3.18,
    )
    _q, lcb, _p, _pf, _ev = _run(monkeypatch)
    grounded = bin_probability_settlement(28.0, 3.18, 28.0, 28.0)
    got = _qlcb_float(lcb[("cond-28", "buy_yes")])
    # Floored to the settlement-grounded ceiling, far below the raw Wilson ~0.68.
    assert got == pytest.approx(grounded)
    assert got < 0.15


def test_live_flag_off_keeps_raw_even_when_floor_inputs_missing(monkeypatch) -> None:
    """Control: with the floor flag OFF the floor is NEVER consulted, so a missing floor
    input is irrelevant — the path emits the raw Wilson value, byte-identical to pre-FIX-C.
    The block is gated strictly on (flag ON AND input missing), not on input presence.

    Topology is kept present (the candidate must still bind to a bin_id — that guard is
    flag-independent); the floor-specific input (anchor μ) AND the σ-floor cell are absent.
    With the flag OFF the resolver is not called at all, so the absent anchor/σ never blocks."""
    _setup(
        monkeypatch,
        floor_flag=False,
        bundle=_replacement_bundle(with_anchor=False),
        sigma_floor=lambda *a, **k: None,
    )
    _q, lcb, _p, _pf, _ev = _run(monkeypatch)
    wilson_28 = adapter._wilson_lower_bound(41.0, 51.0)
    assert _qlcb_float(lcb[("cond-28", "buy_yes")]) == pytest.approx(wilson_28)
