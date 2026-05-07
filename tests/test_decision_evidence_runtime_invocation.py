# Created: 2026-04-23
# Last reused/audited: 2026-05-05
# Lifecycle: created=2026-04-23; last_reviewed=2026-05-05; last_reused=2026-05-05
# Authority basis: midstream verdict v2 2026-04-23; Phase 1A calibration-maturity gate 2026-04-29
# Purpose: Lock runtime construction of entry DecisionEvidence on evaluator accept paths.
# Reuse: Run for evaluator entry decision, day0 accept-path, or DecisionEvidence wiring changes.

"""T4.3b DecisionEvidence runtime-invocation antibody.

T4.1b wires ``DecisionEvidence(evidence_type="entry", ...)`` into the
evaluator accept path at ``src/engine/evaluator.py:L1700+``. T4.3
(AST-walk presence test) pins the source-code literal, but a static
presence check cannot detect a silent refactor that routes around the
accept path — a dead-code-path gap.

T4.3b closes that gap at runtime: wrap ``DecisionEvidence.__init__`` on
the live class via ``monkeypatch``, exercise one full
``evaluate_candidate`` cycle on a fixture DB that reaches the accept
path (min_order_usd dropped so sizing does not reject), and assert the
wrapped ``__init__`` was invoked with ``evidence_type="entry"`` at
least once. If a future refactor silently bypasses the T4.1b
construction, this test fires immediately.

Paired with:
- T4.1b static emission tests (``tests/test_decision_evidence_entry_emission.py``)
- T4.1a primitive round-trip tests (``tests/test_decision_evidence_persistence.py``)
- T4.2-Phase1 exit-side audit tests (``tests/test_exit_evidence_audit.py``)
"""

from __future__ import annotations

import types
from datetime import datetime, timezone

import numpy as np

import src.engine.evaluator as evaluator_module
from src.config import cities_by_name
from src.contracts.decision_evidence import DecisionEvidence
from src.state.db import get_connection, init_schema
from src.state.portfolio import PortfolioState
from src.strategy.risk_limits import RiskLimits
from src.types import BinEdge


class _FakeEns:
    """Stubbed EnsembleSignal — carries every surface that production
    evaluator.py reads off the instance during a day0_capture accept-path
    traversal. Field additions vs tests/test_fdr.py's original fixture:
    `bias_corrected` (bool, read at L991 / L1292), `member_extrema`
    (array, read at L1284), `p_raw_vector` method (fallback used in the
    non-day0 branch)."""

    def __init__(self, *args, **kwargs):
        self.member_maxes = np.array([70.0, 71.0, 72.0, 73.0])
        self.member_extrema = np.array([70.0, 71.0, 72.0, 73.0])
        self.bias_corrected = False

    def spread(self):
        return evaluator_module.TemperatureDelta(1.0, "F")

    def spread_float(self):
        return 1.0

    def is_bimodal(self):
        return False

    def p_raw_vector(self, bins):
        return np.array([0.1, 0.2, 0.5, 0.2])


class _FakeDay0Result:
    """Mock return value for Day0Router.route(...) — carries p_vector +
    forecast_context surface the evaluator consumes downstream. Returns
    a bins-sized array so downstream FDR family scan (which indexes
    p_cal / p_posterior / p_market by bin index) does not overflow."""

    def p_vector(self, bins, n_mc=None):
        n = len(bins)
        return np.full(n, 1.0 / n)

    def forecast_context(self):
        return {"day0_test": True}


class _FakeAnalysis:
    def __init__(self, **kwargs):
        self.selected_method = kwargs.get("selected_method", "test_fixture")
        self.bias_correction = kwargs.get("bias_correction", kwargs.get("bias_corrected", False))
        self.bins = kwargs["bins"]
        self.p_raw = kwargs["p_raw"]
        self.p_cal = kwargs["p_cal"]
        n_bins = len(self.bins)
        # Differentiate p_posterior and p_market so bin 0 carries a real
        # positive edge (p_posterior > p_market) — uniform equality would
        # zero out edge_yes and the BH-selected family would come back
        # empty, rejecting at FDR_FILTERED before the accept path.
        self.p_market = np.full(n_bins, 1.0 / n_bins)
        self.p_posterior = np.full(n_bins, 0.9 / n_bins)
        self.p_posterior[0] = 1.0 - self.p_posterior[1:].sum()

    def forecast_context(self):
        return {"uncertainty": {}, "location": {}}

    def find_edges(self, n_bootstrap=None):
        self.selected_method = getattr(self, "selected_method", "test_fixture")
        assert self.selected_method
        return [
            BinEdge(
                bin=self.bins[0],
                direction="buy_yes",
                edge=0.1,
                ci_lower=0.02,
                ci_upper=0.2,
                p_model=0.2,
                p_market=0.1,
                p_posterior=0.2,
                entry_price=0.1,
                p_value=0.001,
                vwmp=0.1,
                forward_edge=0.1,
                support_index=0,
            )
        ]

    def _bootstrap_bin(self, idx, n):
        return (0.02, 0.2, 0.001) if idx == 0 else (-0.1, 0.1, 0.5)

    def _bootstrap_bin_no(self, idx, n):
        return (-0.2, -0.01, 0.001) if idx == 0 else (-0.1, 0.1, 0.5)


class _FakeClob:
    def get_best_bid_ask(self, token_id):
        return (0.1, 0.2, 10.0, 10.0)


def _install_common_mocks(monkeypatch, now: datetime) -> None:
    """Shared fixture setup — same surface area as
    tests/test_fdr.py::test_evaluate_candidate_materializes_selection_facts
    so behavior diverges only at the sizing gate (which we flip to
    accept here)."""
    monkeypatch.setattr(
        evaluator_module,
        "fetch_ensemble",
        lambda city, forecast_days, **kwargs: {
            "members_hourly": np.ones((51, 2)) * 72.0,
            "times": [now, now],
            "fetch_time": now,
            "available_at": now,
            "issue_time": now,
            "first_valid_time": now,
            "model": "ecmwf_ifs025",
            "source_id": "tigge",
            "raw_payload_hash": "a" * 64,
            "authority_tier": "FORECAST",
            "degradation_level": "OK",
            "forecast_source_role": kwargs.get("role", "entry_primary"),
            "n_members": 51,
        },
    )
    monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda *args, **kwargs: True)
    monkeypatch.setattr(evaluator_module, "EnsembleSignal", _FakeEns)
    # Day0Signal was refactored to Day0Router — patch Day0Router.route at
    # the source module so the evaluator's day0 branch receives our
    # stubbed routing result.
    monkeypatch.setattr(
        "src.signal.day0_router.Day0Router.route",
        staticmethod(lambda inputs: _FakeDay0Result()),
    )
    # DT#7 boundary gate: evaluator metadata reads now happen after
    # snapshot persistence and require the exact decision_snapshot_id. This
    # test focuses on DecisionEvidence construction, so keep v2 metadata
    # neutral and independent of fixture DB state.
    monkeypatch.setattr(
        evaluator_module,
        "_read_v2_snapshot_metadata",
        lambda *args, **kwargs: {},
    )
    from src.signal.day0_extrema import RemainingMemberExtrema as _REM
    monkeypatch.setattr(
        evaluator_module,
        "remaining_member_extrema_for_day0",
        lambda *args, **kwargs: (_REM(maxes=np.array([70.0, 71.0, 72.0, 73.0]), mins=None), 2.0),
    )
    monkeypatch.setattr(
        evaluator_module,
        "_get_day0_temporal_context",
        lambda *args, **kwargs: types.SimpleNamespace(current_utc_timestamp=now),
    )
    monkeypatch.setattr(evaluator_module, "MarketAnalysis", _FakeAnalysis)
    monkeypatch.setattr(evaluator_module, "edge_n_bootstrap", lambda: 2)
    # ENS snapshot persistence is a write-side concern orthogonal to the
    # T4.3b accept-path claim — stub both so _FakeEns does not need the
    # full production ENS attribute surface (bias_corrected, member_extrema,
    # etc.).
    monkeypatch.setattr(
        evaluator_module, "_store_ens_snapshot",
        lambda conn, city, target_date, ens, ens_result: "snap-runtime-test",
    )
    monkeypatch.setattr(
        evaluator_module, "_store_snapshot_p_raw",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (object(), 1))
    monkeypatch.setattr(
        evaluator_module,
        "calibrate_and_normalize",
        lambda p_raw, *args, **kwargs: np.array(p_raw, dtype=float).copy(),
    )
    monkeypatch.setattr(
        evaluator_module,
        "resolve_strategy_policy",
        lambda conn, strategy_key, now: evaluator_module.StrategyPolicy(
            strategy_key=strategy_key,
            gated=False,
            allocation_multiplier=1.0,
            threshold_multiplier=1.0,
            exit_only=False,
            sources=[],
        ),
    )
    monkeypatch.setattr(
        evaluator_module,
        "_strict_feature_flag",
        lambda name, default=True: False if name == "ddd_v2_enabled" else default,
    )


def _make_candidate(now: datetime):
    return evaluator_module.MarketCandidate(
        city=cities_by_name["Dallas"],
        target_date="2026-04-12",
        outcomes=[
            {"title": "67°F or lower", "range_low": None, "range_high": 67,
             "token_id": "yes0", "no_token_id": "no0", "market_id": "m0"},
            {"title": "68-69°F", "range_low": 68, "range_high": 69,
             "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
            {"title": "70-71°F", "range_low": 70, "range_high": 71,
             "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
            {"title": "72°F or higher", "range_low": 72, "range_high": None,
             "token_id": "yes3", "no_token_id": "no3", "market_id": "m3"},
        ],
        hours_since_open=2.0,
        hours_to_resolution=12.0,
        event_id="event-test-t4_3b",
        discovery_mode="day0_capture",
        observation=types.SimpleNamespace(
            high_so_far=70,
            low_so_far=None,
            current_temp=69,
            source="wu_api",
            observation_time=now.isoformat(),
        ),
    )


class TestDecisionEvidenceRuntimeInvocation:
    """Runtime mock antibody against silent refactor of the T4.1b
    entry-path construction."""

    def test_accept_path_constructs_entry_evidence_at_runtime(self, tmp_path, monkeypatch):
        conn = get_connection(tmp_path / "runtime_invocation.db")
        init_schema(conn)
        now = datetime.now(timezone.utc)
        _install_common_mocks(monkeypatch, now)

        # Wrap DecisionEvidence.__init__ so we observe every construction.
        # Must delegate back to the original to preserve field assignment +
        # __post_init__ validation (frozen=True dataclass: the generated
        # __init__ sets attributes via object.__setattr__ and invokes
        # __post_init__ at the end — both must run for the instance to be
        # valid).
        original_init = DecisionEvidence.__init__
        init_calls: list[dict] = []

        def _tracked_init(self, *args, **kwargs):
            # Capture both positional and kw form of the call.
            init_calls.append({"args": args, "kwargs": dict(kwargs)})
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(DecisionEvidence, "__init__", _tracked_init)

        candidate = _make_candidate(now)
        decisions = evaluator_module.evaluate_candidate(
            candidate,
            conn,
            PortfolioState(bankroll=211.37, positions=[]),
            _FakeClob(),
            # Drop min_order_usd low enough that the sizing gate ADMITS
            # the edge rather than rejecting it — the critical flip vs
            # tests/test_fdr.py::test_evaluate_candidate_materializes_selection_facts
            # which uses 999999 to force rejection.
            RiskLimits(min_order_usd=0.01),
            entry_bankroll=211.37,
            decision_time=now,
        )
        conn.close()

        # At least one accept decision, and its evidence must be the one
        # constructed during this call.
        accept_decisions = [d for d in decisions if d.should_trade]
        assert len(accept_decisions) >= 1, (
            "T4.3b fixture must reach the accept path; "
            f"got {len(decisions)} decisions, all rejected: "
            f"{[(d.rejection_stage, d.rejection_reasons) for d in decisions]}"
        )

        # Find the accept-path DecisionEvidence construction. The tracked
        # __init__ wraps EVERY DecisionEvidence instantiation in this
        # process; we filter to entry-type constructions.
        def _evidence_type(call: dict) -> str:
            if call["kwargs"].get("evidence_type"):
                return str(call["kwargs"]["evidence_type"])
            # Positional: evidence_type is the first dataclass field.
            if call["args"]:
                return str(call["args"][0])
            return ""

        entry_constructions = [c for c in init_calls if _evidence_type(c) == "entry"]
        assert len(entry_constructions) >= 1, (
            "T4.1b wiring must construct DecisionEvidence(evidence_type='entry') "
            "at least once on the accept path. If this assertion fails, a "
            "refactor silently bypassed src/engine/evaluator.py:L1700+. "
            f"All DecisionEvidence constructions observed: "
            f"{[_evidence_type(c) for c in init_calls]}"
        )

        # The accept EdgeDecision's decision_evidence must be populated
        # and reflect the entry-side statistical method.
        for d in accept_decisions:
            assert d.decision_evidence is not None, (
                "accept EdgeDecision.decision_evidence must be populated per T4.1b contract"
            )
            assert d.decision_evidence.evidence_type == "entry"
            assert d.decision_evidence.statistical_method == "bootstrap_ci_bh_fdr"
            assert d.decision_evidence.fdr_corrected is True
            # sample_size sources from evaluator_module.edge_n_bootstrap,
            # which the fixture monkeypatches to 2.
            assert d.decision_evidence.sample_size == 2
