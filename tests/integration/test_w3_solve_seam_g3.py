# Created: 2026-07-03
# Last reused/audited: 2026-07-09
"""G3 harness for the W3 SOLVE promotion seam (qkernel_spine_bridge.py w3_solve_enabled flag).

Proves the promotion flag is a SAFE, reversible, single-point cutover before any live enablement:
  (a) absent-vs-OFF byte-identity — the flag key absent vs explicitly False produce identical
      SpineDecisionResults over a fixture corpus (the OFF path is a no-op);
  (b) single-divergence-point — `w3_solve_enabled` is consumed at EXACTLY one code site (the guard);
  (c) ON-mode integration — with the flag ON the shim runs and every decision passes
      validate_family_decision_contract (no getattr-default consumer field fired);
  (d) OFF-path import-isolation — a decide call with the flag OFF does not import src.solve.

Fixtures are reused from tests/integration/test_qkernel_spine_routing.py (the realistic family +
proofs the legacy spine path is tested against).
"""

from __future__ import annotations

import ast
import datetime as _dt
import subprocess
import sys
import textwrap
from decimal import Decimal

import pytest

import src.engine.qkernel_spine_bridge as bridge
import src.engine.event_reactor_adapter as era
from src.solve.solver import validate_family_decision_contract
from src.strategy import utility_ranker
from tests.integration import test_qkernel_spine_routing as R

_BRIDGE_PATH = bridge.__file__


def _drive(family, proofs, payload):
    """Drive decide_family_via_spine with a FIXED positive baseline so the fixture's wealth is
    deterministic (the module bankroll provider is not warm in-test); identical for OFF and ON."""
    return bridge.decide_family_via_spine(
        family=family, payload=payload, proofs=proofs,
        decision_time=_dt.datetime(2026, 6, 13, 12, 0, tzinfo=_dt.timezone.utc),
        native_side_candidate_from_proof=era._native_side_candidate_from_proof,
        candidate_bin_id=era._candidate_bin_id,
        payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
        exposure_builder=era._robust_marginal_utility_exposure,
        baseline_usd_provider=lambda: Decimal("1000"),
        per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
        extra_exposure_by_bin_id=None,
    )


@pytest.fixture(autouse=True)
def _fast_band_draws(monkeypatch):
    monkeypatch.setattr(bridge, "SPINE_BAND_DRAWS", 400, raising=False)


def _corpus():
    """A small (family, proofs, payload) corpus: a +edge trade and an overpriced no-trade."""
    fam_a, _ = R._three_bin_family()
    trade = (
        fam_a,
        R._proofs_for(
            fam_a, yes_asks=[0.05, 0.20, 0.20, 0.05], no_asks=[0.92, 0.75, 0.75, 0.92],
            q_by_bin=[0.05, 0.45, 0.40, 0.10], q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
        ),
        R._payload_with_spine_inputs(mu=20.4, sigma=1.2, members=[19.8, 20.1, 20.5, 21.0, 20.7]),
    )
    fam_b, _ = R._three_bin_family()
    no_trade = (
        fam_b,
        R._proofs_for(
            fam_b, yes_asks=[0.60, 0.60, 0.60, 0.60], no_asks=[0.60, 0.60, 0.60, 0.60],
            q_by_bin=[0.05, 0.45, 0.40, 0.10], q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
        ),
        R._payload_with_spine_inputs(mu=20.4, sigma=1.2, members=[19.8, 20.1, 20.5, 21.0, 20.7]),
    )
    return [trade, no_trade]


def _serialize(result) -> str:
    """Canonical serialization of a SpineDecisionResult for byte-identity comparison."""
    d = result.decision
    sel = getattr(d, "selected", None) if d is not None else None
    parts = [
        f"decided_by_spine={getattr(result, 'decided_by_spine', None)}",
        f"no_trade_reason={result.no_trade_reason!r}",
        f"selected_proof={getattr(getattr(result, 'selected_proof', None), 'token_id', None)!r}",
    ]
    if d is not None:
        parts += [
            f"decision_id={d.decision_id!r}", f"receipt_hash={d.receipt_hash!r}",
            f"no_trade={d.no_trade_reason!r}", f"n_candidates={len(d.candidates)}",
            f"n_candidate_decisions={len(d.candidate_decisions)}",
        ]
    if sel is not None:
        parts += [
            f"sel_route={sel.route_id!r}", f"sel_stake={sel.optimal_stake_usd}",
            f"sel_du={sel.optimal_delta_u!r}",
        ]
    return "|".join(parts)


def _set_flag(value):
    """Set the flag dict entry (None => absent). Returns a restore callable."""
    from src.config import settings

    ff = settings["feature_flags"]
    had = "w3_solve_enabled" in ff
    prev = ff.get("w3_solve_enabled")
    if value is None:
        ff.pop("w3_solve_enabled", None)
    else:
        ff["w3_solve_enabled"] = value

    def _restore():
        if had:
            ff["w3_solve_enabled"] = prev
        else:
            ff.pop("w3_solve_enabled", None)

    return _restore


# --- (a) absent-vs-OFF byte-identity ----------------------------------------

def test_g3_absent_vs_off_byte_identical():
    corpus = _corpus()
    restore = _set_flag(None)  # absent
    try:
        assert bridge.w3_solve_enabled() is False
        absent = [_serialize(_drive(f, p, pl)) for f, p, pl in corpus]
    finally:
        restore()
    restore = _set_flag(False)  # explicit OFF
    try:
        assert bridge.w3_solve_enabled() is False
        off = [_serialize(_drive(f, p, pl)) for f, p, pl in corpus]
    finally:
        restore()
    assert absent == off, f"absent vs OFF diverged:\n absent={absent}\n off={off}"
    # the corpus must run the real pipeline (a FamilyDecision produced), not a trivial input-fault
    assert any("decision_id=" in s for s in off), "corpus did not exercise the engine pipeline"


# --- (b) single divergence point --------------------------------------------

def test_g3_flag_consumed_at_exactly_one_site():
    tree = ast.parse(open(_BRIDGE_PATH).read())
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "w3_solve_enabled"
    ]
    wraps = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_wrap_engine_with_solve_shim"
    ]
    assert len(calls) == 1, f"w3_solve_enabled() must be consumed at EXACTLY one site, found {len(calls)}"
    assert len(wraps) == 1, f"_wrap_engine_with_solve_shim must be called exactly once, found {len(wraps)}"


# --- (c) ON-mode integration ------------------------------------------------

_SOLVER_ORIGIN_REASONS = (
    "NO_IMPROVING_DISCRETE_PLAN", "NO_EXECUTABLE_MENU_ITEMS", "UNSAFE_PREFIX_DECOMPOSITION",
    "BUDGET_EXCEEDED", "PHASE1_PRIMARY_LEG",
)


def test_g3_on_mode_shim_runs_and_is_contract_valid():
    corpus = _corpus()
    restore = _set_flag(True)
    try:
        assert bridge.w3_solve_enabled() is True
        ran_solver = False
        for f, p, pl in corpus:
            result = _drive(f, p, pl)
            if result.decision is None:
                continue
            # every emitted FamilyDecision satisfies the frozen consumer contract (no getattr
            # default would fire in the facts writer / overlay)
            validate_family_decision_contract(result.decision)
            if result.decision.selected is not None:
                ran_solver = True
                # projection stamped: selected carries the standalone ΔU value
                assert result.decision.selected.optimal_delta_u is not None
            elif result.no_trade_reason and any(k in result.no_trade_reason for k in _SOLVER_ORIGIN_REASONS):
                ran_solver = True  # a solver-origin no-trade proves the solver selection path ran
        # the ON branch physically imported + executed the solver
        assert "src.solve.solver" in sys.modules
        assert ran_solver, "ON-mode did not exercise the solver selection path"
    finally:
        restore()


def test_g3_on_mode_selection_diverges_from_off():
    # The whole point of the seam: ON runs a DIFFERENT selector. Same fixture, same inputs — the
    # ON no-trade reason is the solver's, the OFF reason is the legacy picker's.
    trade = _corpus()[0]
    restore = _set_flag(None)
    try:
        off = _drive(*trade)
    finally:
        restore()
    restore = _set_flag(True)
    try:
        on = _drive(*trade)
    finally:
        restore()
    assert off.decision is not None and on.decision is not None
    # both no-trade on this realistic (robust-band-wide) fixture, but for DIFFERENT reasons —
    # legacy NO_POSITIVE_EDGE_CANDIDATE vs solver NO_IMPROVING_DISCRETE_PLAN
    assert off.no_trade_reason != on.no_trade_reason, (
        f"selection did not diverge: off={off.no_trade_reason} on={on.no_trade_reason}"
    )
    assert any(k in (on.no_trade_reason or "") for k in _SOLVER_ORIGIN_REASONS) or on.decision.selected is not None


def test_g3_on_mode_never_reads_historical_decision_guards(monkeypatch):
    from src.decision.family_decision_engine import FamilyDecisionEngine

    def _history_read_forbidden(*args, **kwargs):
        raise AssertionError("W3_CURRENT_STATE_SOLVE_MUST_NOT_READ_HISTORICAL_GUARDS")

    monkeypatch.setattr(
        FamilyDecisionEngine,
        "_apply_qlcb_reliability_guard",
        _history_read_forbidden,
    )
    monkeypatch.setattr(
        FamilyDecisionEngine,
        "_apply_selection_calibrator_guard",
        _history_read_forbidden,
    )
    restore = _set_flag(True)
    try:
        result = _drive(*_corpus()[0])
    finally:
        restore()

    assert result.decision is not None
    validate_family_decision_contract(result.decision)


# --- (d) OFF-path import-isolation (subprocess) -----------------------------

def test_g3_off_path_does_not_import_src_solve():
    script = textwrap.dedent(
        """
        import sys, datetime
        from decimal import Decimal
        from src.config import settings
        settings["feature_flags"].pop("w3_solve_enabled", None)  # OFF/absent
        import src.engine.qkernel_spine_bridge as bridge
        import src.engine.event_reactor_adapter as era
        from src.strategy import utility_ranker
        bridge.SPINE_BAND_DRAWS = 400
        from tests.integration import test_qkernel_spine_routing as R
        fam, _ = R._three_bin_family()
        proofs = R._proofs_for(fam, yes_asks=[0.05,0.20,0.20,0.05], no_asks=[0.92,0.75,0.75,0.92],
                               q_by_bin=[0.05,0.45,0.40,0.10], q_lcb_by_bin=[0.02,0.32,0.28,0.05])
        payload = R._payload_with_spine_inputs(mu=20.4, sigma=1.2, members=[19.8,20.1,20.5,21.0,20.7])
        assert bridge.w3_solve_enabled() is False
        _ = bridge.decide_family_via_spine(  # a full decide with the flag OFF
            family=fam, payload=payload, proofs=proofs,
            decision_time=datetime.datetime(2026,6,13,12,0,tzinfo=datetime.timezone.utc),
            native_side_candidate_from_proof=era._native_side_candidate_from_proof,
            candidate_bin_id=era._candidate_bin_id,
            payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
            exposure_builder=era._robust_marginal_utility_exposure,
            baseline_usd_provider=lambda: Decimal("1000"),
            per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs), extra_exposure_by_bin_id=None,
        )
        leaked = [m for m in sys.modules if m.startswith('src.solve')]
        assert not leaked, f'OFF path imported src.solve: {leaked}'
        print('ISOLATION_OK')
        """
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=".")
    assert "ISOLATION_OK" in proc.stdout, f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
