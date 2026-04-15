"""Tests for FDR (Benjamini-Hochberg) filter.

Covers:
1. Happy path: 10 edges with known p-values → correct filtering
2. Edge case: all p-values below threshold → all pass
3. Edge case: all p-values above threshold → none pass
4. Empty input → empty output
"""

import json
import types
from datetime import datetime, timezone

import numpy as np
import pytest

import src.engine.evaluator as evaluator_module
from src.engine.evaluator import _record_selection_family_facts, _selected_edge_keys_from_full_family
from src.config import cities_by_name
from src.state.db import get_connection, init_schema
from src.state.portfolio import PortfolioState
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis
from src.strategy.fdr_filter import fdr_filter
from src.strategy.risk_limits import RiskLimits
from src.strategy.selection_family import apply_familywise_fdr, benjamini_hochberg_mask, make_family_id
from src.types import Bin, BinEdge


def _make_edge(p_value: float, *, low: float = 40, high: float = 41) -> BinEdge:
    """Helper: create a BinEdge with given p_value."""
    return BinEdge(
        bin=Bin(low=low, high=high, unit="F", label=f"{int(low)}-{int(high)}°F"),
        direction="buy_yes",
        edge=0.05,
        ci_lower=0.01,
        ci_upper=0.10,
        p_model=0.15,
        p_market=0.10,
        p_posterior=0.15,
        entry_price=0.10,
        p_value=p_value,
        vwmp=0.10,
    )


class TestFDRFilter:
    def test_known_p_values(self):
        """Standard BH test with 10 edges at known p-values.

        fdr_alpha=0.10, m=10.
        BH thresholds: k/10 * 0.10 = [0.01, 0.02, 0.03, ..., 0.10]
        """
        p_values = [0.005, 0.01, 0.025, 0.05, 0.08, 0.12, 0.15, 0.30, 0.50, 0.90]
        edges = [_make_edge(p) for p in p_values]

        result = fdr_filter(edges, fdr_alpha=0.10)

        # k=1: p=0.005 <= 0.01 ✓
        # k=2: p=0.01  <= 0.02 ✓
        # k=3: p=0.025 <= 0.03 ✓
        # k=4: p=0.05  <= 0.04 ✗
        # So threshold_k = 3
        assert len(result) == 3
        # Results should be sorted by p-value
        assert result[0].p_value <= result[1].p_value <= result[2].p_value

    def test_all_significant(self):
        """All p-values very low → all pass."""
        edges = [_make_edge(0.001) for _ in range(5)]
        result = fdr_filter(edges, fdr_alpha=0.10)
        assert len(result) == 5

    def test_none_significant(self):
        """All p-values high → none pass."""
        edges = [_make_edge(0.50) for _ in range(5)]
        result = fdr_filter(edges, fdr_alpha=0.10)
        assert len(result) == 0

    def test_empty_input(self):
        assert fdr_filter([], fdr_alpha=0.10) == []

    def test_single_edge_passes(self):
        """Single edge with p=0.05, fdr=0.10 → passes (0.05 <= 0.10 * 1/1)."""
        result = fdr_filter([_make_edge(0.05)], fdr_alpha=0.10)
        assert len(result) == 1

    def test_single_edge_fails(self):
        """Single edge with p=0.15, fdr=0.10 → fails (0.15 > 0.10)."""
        result = fdr_filter([_make_edge(0.15)], fdr_alpha=0.10)
        assert len(result) == 0


class TestSelectionFamilySubstrate:
    def test_family_id_is_stable(self):
        assert make_family_id(
            cycle_mode="opening_hunt",
            city="NYC",
            target_date="2026-04-01",
            strategy_key="center_buy",
            discovery_mode="opening_hunt",
            decision_snapshot_id="snap-1",
        ) == "opening_hunt|NYC|2026-04-01|center_buy|opening_hunt|snap-1"

    def test_bh_mask_uses_full_tested_family(self):
        mask = benjamini_hochberg_mask([0.001, 0.020, 0.080, 0.500], q=0.10)
        assert mask == [True, True, False, False]

    def test_apply_familywise_fdr_separates_families(self):
        rows = [
            {"family_id": "fam-a", "hypothesis_id": "a1", "p_value": 0.001, "tested": True},
            {"family_id": "fam-a", "hypothesis_id": "a2", "p_value": 0.500, "tested": True},
            {"family_id": "fam-b", "hypothesis_id": "b1", "p_value": 0.080, "tested": True},
            {"family_id": "fam-b", "hypothesis_id": "b2", "p_value": 0.900, "tested": False},
        ]

        out = apply_familywise_fdr(rows, q=0.10)

        assert [row["selected_post_fdr"] for row in out] == [1, 0, 1, 0]
        assert out[0]["q_value"] <= 0.10
        assert out[1]["q_value"] > 0.10
        assert out[2]["q_value"] <= 0.10
        assert out[3]["q_value"] is None

    def test_candidate_snapshot_families_are_independent_not_cycle_wide(self):
        rows = [
            {"family_id": "nyc|2026-04-01|snap-1", "hypothesis_id": "nyc-1", "p_value": 0.049, "tested": True},
            {"family_id": "dal|2026-04-01|snap-2", "hypothesis_id": "dal-1", "p_value": 0.200, "tested": True},
            {"family_id": "dal|2026-04-01|snap-2", "hypothesis_id": "dal-2", "p_value": 0.200, "tested": True},
            {"family_id": "dal|2026-04-01|snap-2", "hypothesis_id": "dal-3", "p_value": 0.200, "tested": True},
        ]

        out = apply_familywise_fdr(rows, q=0.10)

        assert out[0]["selected_post_fdr"] == 1
        assert [row["selected_post_fdr"] for row in out[1:]] == [0, 0, 0]

    def test_selection_family_facts_record_all_tested_hypotheses(self, tmp_path):
        conn = get_connection(tmp_path / "selection_facts.db")
        init_schema(conn)
        candidate = types.SimpleNamespace(
            city=types.SimpleNamespace(name="NYC"),
            target_date="2026-04-01",
            discovery_mode="opening_hunt",
            event_id="event-1",
            slug="",
        )
        edges = [_make_edge(0.001), _make_edge(0.500, low=42, high=43)]
        hypotheses = [
            FullFamilyHypothesis(
                index=0,
                range_label="40-41°F",
                direction="buy_yes",
                edge=0.05,
                ci_lower=0.01,
                ci_upper=0.10,
                p_value=0.001,
                p_model=0.15,
                p_market=0.10,
                p_posterior=0.15,
                entry_price=0.10,
                is_shoulder=False,
                passed_prefilter=True,
            ),
            FullFamilyHypothesis(
                index=0,
                range_label="40-41°F",
                direction="buy_no",
                edge=-0.05,
                ci_lower=-0.10,
                ci_upper=-0.01,
                p_value=0.001,
                p_model=0.85,
                p_market=0.90,
                p_posterior=0.85,
                entry_price=0.90,
                is_shoulder=False,
                passed_prefilter=False,
            ),
            FullFamilyHypothesis(
                index=1,
                range_label="42-43°F",
                direction="buy_yes",
                edge=0.05,
                ci_lower=0.01,
                ci_upper=0.10,
                p_value=0.500,
                p_model=0.15,
                p_market=0.10,
                p_posterior=0.15,
                entry_price=0.10,
                is_shoulder=False,
                passed_prefilter=True,
            ),
        ]

        result = _record_selection_family_facts(
            conn,
            candidate=candidate,
            edges=edges,
            filtered=[edges[0]],
            hypotheses=hypotheses,
            decision_snapshot_id="snap-1",
            selected_method="ens_member_counting",
            recorded_at="2026-04-01T00:00:00Z",
        )
        family = conn.execute("SELECT * FROM selection_family_fact").fetchone()
        hypotheses = conn.execute(
            """
            SELECT range_label, direction, tested, passed_prefilter, selected_post_fdr, q_value, meta_json
            FROM selection_hypothesis_fact
            ORDER BY p_value
            """
        ).fetchall()
        conn.close()

        assert result == {"status": "written", "families": 1, "hypotheses": 3}
        assert family["family_id"] == "opening_hunt|NYC|2026-04-01||opening_hunt|snap-1"
        family_meta = json.loads(family["meta_json"])
        assert family_meta["active_fdr_selected"] == 1
        assert family_meta["passed_prefilter"] == 2
        assert family_meta["selected_method"] == "ens_member_counting"
        assert family_meta["selected_post_fdr"] == 1
        assert family_meta["tested_hypotheses"] == 3
        assert [row["tested"] for row in hypotheses] == [1, 1, 1]
        by_direction = {row["direction"]: row for row in hypotheses if row["range_label"] == "40-41°F"}
        assert by_direction["buy_no"]["passed_prefilter"] == 0
        assert by_direction["buy_no"]["selected_post_fdr"] == 0
        assert by_direction["buy_yes"]["passed_prefilter"] == 1
        assert by_direction["buy_yes"]["selected_post_fdr"] == 1
        assert by_direction["buy_yes"]["q_value"] <= 0.10
        assert json.loads(by_direction["buy_yes"]["meta_json"])["active_fdr_selected"] is True
        assert json.loads(hypotheses[2]["meta_json"])["active_fdr_selected"] is False

    def test_full_family_selection_requires_prefilter_pass(self):
        candidate = types.SimpleNamespace(
            city=types.SimpleNamespace(name="NYC"),
            target_date="2026-04-01",
            discovery_mode="opening_hunt",
            event_id="event-1",
            slug="",
            outcomes=[{"range_low": 40, "range_high": 41}],
        )
        hypotheses = [
            FullFamilyHypothesis(
                index=0,
                range_label="40-41°F",
                direction="buy_yes",
                edge=-0.01,
                ci_lower=-0.05,
                ci_upper=0.01,
                p_value=0.001,
                p_model=0.49,
                p_market=0.50,
                p_posterior=0.49,
                entry_price=0.50,
                is_shoulder=False,
                passed_prefilter=False,
            )
        ]

        selected = _selected_edge_keys_from_full_family(
            candidate,
            hypotheses,
            decision_snapshot_id="snap-1",
        )

        assert selected == set()

    def test_full_family_selection_uses_one_candidate_family_across_strategies(self, tmp_path):
        conn = get_connection(tmp_path / "selection_one_family.db")
        init_schema(conn)
        candidate = types.SimpleNamespace(
            city=types.SimpleNamespace(name="NYC"),
            target_date="2026-04-01",
            discovery_mode="update_reaction",
            event_id="event-1",
            slug="",
            outcomes=[],
        )
        edges = [
            _make_edge(0.001),
            _make_edge(0.001, low=32, high=33),
        ]
        hypotheses = [
            FullFamilyHypothesis(
                index=0,
                range_label="40-41°F",
                direction="buy_yes",
                edge=0.05,
                ci_lower=0.01,
                ci_upper=0.10,
                p_value=0.001,
                p_model=0.15,
                p_market=0.10,
                p_posterior=0.15,
                entry_price=0.10,
                is_shoulder=False,
                passed_prefilter=True,
            ),
            FullFamilyHypothesis(
                index=1,
                range_label="32°F or below",
                direction="buy_no",
                edge=0.04,
                ci_lower=0.01,
                ci_upper=0.08,
                p_value=0.001,
                p_model=0.15,
                p_market=0.10,
                p_posterior=0.15,
                entry_price=0.90,
                is_shoulder=True,
                passed_prefilter=True,
            ),
        ]

        result = _record_selection_family_facts(
            conn,
            candidate=candidate,
            edges=edges,
            filtered=edges,
            hypotheses=hypotheses,
            decision_snapshot_id="snap-1",
            selected_method="ens_member_counting",
            recorded_at="2026-04-01T00:00:00Z",
        )
        family_ids = [
            row["family_id"]
            for row in conn.execute("SELECT family_id FROM selection_family_fact").fetchall()
        ]
        hypothesis_meta = [
            json.loads(row["meta_json"])
            for row in conn.execute("SELECT meta_json FROM selection_hypothesis_fact").fetchall()
        ]
        conn.close()

        assert result == {"status": "written", "families": 1, "hypotheses": 2}
        assert family_ids == ["update_reaction|NYC|2026-04-01||update_reaction|snap-1"]
        assert {meta["hypothesis_strategy_key"] for meta in hypothesis_meta} == {
            "center_buy",
            "shoulder_sell",
        }

    def test_evaluate_candidate_materializes_selection_facts(self, tmp_path, monkeypatch):
        conn = get_connection(tmp_path / "selection_eval_path.db")
        init_schema(conn)
        now = datetime.now(timezone.utc)

        class FakeEns:
            def __init__(self, *args, **kwargs):
                self.member_maxes = np.array([70.0, 71.0, 72.0, 73.0])

            def spread(self):
                return evaluator_module.TemperatureDelta(1.0, "F")

            def spread_float(self):
                return 1.0

            def is_bimodal(self):
                return False

        class FakeDay0Signal:
            def __init__(self, *args, **kwargs):
                pass

            def p_vector(self, bins):
                return np.array([0.2, 0.5, 0.3])

            def forecast_context(self):
                return {"day0_test": True}

        class FakeAnalysis:
            def __init__(self, **kwargs):
                self.bins = kwargs["bins"]
                self.p_raw = kwargs["p_raw"]
                self.p_cal = kwargs["p_cal"]
                self.p_market = np.array([0.1, 0.2, 0.7])
                self.p_posterior = np.array([0.2, 0.3, 0.5])

            def forecast_context(self):
                return {"uncertainty": {}, "location": {}}

            def find_edges(self, n_bootstrap=None):
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
                    )
                ]

            def _bootstrap_bin(self, idx, n):
                return (0.02, 0.2, 0.001) if idx == 0 else (-0.1, 0.1, 0.5)

            def _bootstrap_bin_no(self, idx, n):
                return (-0.2, -0.01, 0.001) if idx == 0 else (-0.1, 0.1, 0.5)

        class FakeClob:
            paper_mode = True

            def get_best_bid_ask(self, token_id):
                return (0.1, 0.2, 10.0, 10.0)

        monkeypatch.setattr(
            evaluator_module,
            "fetch_ensemble",
            lambda city, forecast_days, **kwargs: {
                "members_hourly": np.ones((51, 2)) * 72.0,
                "times": [now, now],
                "fetch_time": now,
                "issue_time": now,
                "first_valid_time": now,
                "model": "ecmwf_ifs025",
            },
        )
        monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda *args, **kwargs: True)
        monkeypatch.setattr(evaluator_module, "EnsembleSignal", FakeEns)
        monkeypatch.setattr(evaluator_module, "Day0Signal", FakeDay0Signal)
        monkeypatch.setattr(
            evaluator_module,
            "remaining_member_maxes_for_day0",
            lambda *args, **kwargs: (np.array([70.0, 71.0, 72.0, 73.0]), 2.0),
        )
        monkeypatch.setattr(
            evaluator_module,
            "_get_day0_temporal_context",
            lambda *args, **kwargs: types.SimpleNamespace(current_utc_timestamp=now),
        )
        monkeypatch.setattr(evaluator_module, "MarketAnalysis", FakeAnalysis)
        monkeypatch.setattr(evaluator_module, "edge_n_bootstrap", lambda: 2)
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

        candidate = evaluator_module.MarketCandidate(
            city=cities_by_name["Dallas"],
            target_date="2026-04-12",
            outcomes=[
                {"title": "68-69°F", "range_low": 68, "range_high": 69, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
                {"title": "70-71°F", "range_low": 70, "range_high": 71, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
                {"title": "72°F or higher", "range_low": 72, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3"},
            ],
            hours_since_open=2.0,
            hours_to_resolution=12.0,
            event_id="event-test",
            discovery_mode="day0_capture",
            observation={"high_so_far": 70, "current_temp": 69, "source": "test", "observation_time": now.isoformat()},
        )

        decisions = evaluator_module.evaluate_candidate(
            candidate,
            conn,
            PortfolioState(bankroll=150.0, positions=[]),
            FakeClob(),
            RiskLimits(min_order_usd=999999.0),
            entry_bankroll=150.0,
            decision_time=now,
        )
        family_count = conn.execute("SELECT COUNT(*) FROM selection_family_fact").fetchone()[0]
        hypothesis_count = conn.execute("SELECT COUNT(*) FROM selection_hypothesis_fact").fetchone()[0]
        meta = json.loads(conn.execute("SELECT meta_json FROM selection_family_fact").fetchone()["meta_json"])
        conn.close()

        assert decisions[0].rejection_stage == "SIZING_TOO_SMALL"
        assert family_count == 1
        assert hypothesis_count == 6
        assert meta["tested_hypotheses"] == 6
        assert meta["active_fdr_selected"] == 1

    def test_evaluate_candidate_fails_closed_when_full_family_scan_unavailable(self, tmp_path, monkeypatch):
        conn = get_connection(tmp_path / "selection_fail_closed.db")
        init_schema(conn)
        now = datetime.now(timezone.utc)

        class FakeEns:
            def __init__(self, *args, **kwargs):
                self.member_maxes = np.array([70.0, 71.0, 72.0, 73.0])

            def spread(self):
                return evaluator_module.TemperatureDelta(1.0, "F")

            def spread_float(self):
                return 1.0

            def is_bimodal(self):
                return False

        class FakeDay0Signal:
            def __init__(self, *args, **kwargs):
                pass

            def p_vector(self, bins):
                return np.array([0.2, 0.5, 0.3])

            def forecast_context(self):
                return {"day0_test": True}

        class FakeAnalysis:
            def __init__(self, **kwargs):
                self.bins = kwargs["bins"]
                self.p_raw = kwargs["p_raw"]
                self.p_cal = kwargs["p_cal"]
                self.p_market = np.array([0.1, 0.2, 0.7])
                self.p_posterior = np.array([0.2, 0.3, 0.5])

            def forecast_context(self):
                return {"uncertainty": {}, "location": {}}

            def find_edges(self, n_bootstrap=None):
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
                    )
                ]

        class FakeClob:
            paper_mode = True

            def get_best_bid_ask(self, token_id):
                return (0.1, 0.2, 10.0, 10.0)

        monkeypatch.setattr(
            evaluator_module,
            "fetch_ensemble",
            lambda city, forecast_days, **kwargs: {
                "members_hourly": np.ones((51, 2)) * 72.0,
                "times": [now, now],
                "fetch_time": now,
                "issue_time": now,
                "first_valid_time": now,
                "model": "ecmwf_ifs025",
            },
        )
        monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda *args, **kwargs: True)
        monkeypatch.setattr(evaluator_module, "EnsembleSignal", FakeEns)
        monkeypatch.setattr(evaluator_module, "Day0Signal", FakeDay0Signal)
        monkeypatch.setattr(
            evaluator_module,
            "_get_day0_temporal_context",
            lambda *args, **kwargs: types.SimpleNamespace(current_utc_timestamp=now),
        )
        monkeypatch.setattr(
            evaluator_module,
            "remaining_member_maxes_for_day0",
            lambda *args, **kwargs: (np.array([70.0, 71.0, 72.0, 73.0]), 2.0),
        )
        monkeypatch.setattr(evaluator_module, "MarketAnalysis", FakeAnalysis)
        monkeypatch.setattr(evaluator_module, "edge_n_bootstrap", lambda: 2)
        monkeypatch.setattr(evaluator_module, "scan_full_hypothesis_family", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("family scan down")))
        monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: list(edges))
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

        candidate = evaluator_module.MarketCandidate(
            city=cities_by_name["Dallas"],
            target_date="2026-04-12",
            outcomes=[
                {"title": "68-69°F", "range_low": 68, "range_high": 69, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
                {"title": "70-71°F", "range_low": 70, "range_high": 71, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
                {"title": "72°F or higher", "range_low": 72, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3"},
            ],
            hours_since_open=2.0,
            hours_to_resolution=12.0,
            event_id="event-test",
            discovery_mode="day0_capture",
            observation={"high_so_far": 70, "current_temp": 69, "source": "test", "observation_time": now.isoformat()},
        )

        decisions = evaluator_module.evaluate_candidate(
            candidate,
            conn,
            PortfolioState(bankroll=150.0, positions=[]),
            FakeClob(),
            RiskLimits(min_order_usd=1.0),
            entry_bankroll=150.0,
            decision_time=now,
        )
        conn.close()

        assert len(decisions) == 1
        assert decisions[0].should_trade is False
        assert decisions[0].rejection_stage == "FDR_FAMILY_SCAN_UNAVAILABLE"
        assert decisions[0].fdr_fallback_fired is True
        assert decisions[0].fdr_family_size == 0
        assert decisions[0].n_edges_found == 1
        assert decisions[0].n_edges_after_fdr == 0

    def test_evaluate_candidate_fails_closed_when_full_family_returns_empty(self, tmp_path, monkeypatch):
        """Case 3: scan succeeds but returns [] — anomalous (any market has bins).

        The evaluator must NOT fall back to legacy fdr_filter; it must fail closed
        and set fdr_fallback_fired=True so observability surfaces the anomaly.
        """
        conn = get_connection(tmp_path / "selection_empty_family.db")
        init_schema(conn)
        now = datetime.now(timezone.utc)

        class FakeEns:
            def __init__(self, *args, **kwargs):
                self.member_maxes = np.array([70.0, 71.0, 72.0, 73.0])

            def spread(self):
                return evaluator_module.TemperatureDelta(1.0, "F")

            def spread_float(self):
                return 1.0

            def is_bimodal(self):
                return False

        class FakeDay0Signal:
            def __init__(self, *args, **kwargs):
                pass

            def p_vector(self, bins):
                return np.array([0.2, 0.5, 0.3])

            def forecast_context(self):
                return {"day0_test": True}

        class FakeAnalysis:
            def __init__(self, **kwargs):
                self.bins = kwargs["bins"]
                self.p_raw = kwargs["p_raw"]
                self.p_cal = kwargs["p_cal"]
                self.p_market = np.array([0.1, 0.2, 0.7])
                self.p_posterior = np.array([0.2, 0.3, 0.5])

            def forecast_context(self):
                return {"uncertainty": {}, "location": {}}

            def find_edges(self, n_bootstrap=None):
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
                    )
                ]

        class FakeClob:
            paper_mode = True

            def get_best_bid_ask(self, token_id):
                return (0.1, 0.2, 10.0, 10.0)

        monkeypatch.setattr(
            evaluator_module,
            "fetch_ensemble",
            lambda city, forecast_days, **kwargs: {
                "members_hourly": np.ones((51, 2)) * 72.0,
                "times": [now, now],
                "fetch_time": now,
                "issue_time": now,
                "first_valid_time": now,
                "model": "ecmwf_ifs025",
            },
        )
        monkeypatch.setattr(evaluator_module, "validate_ensemble", lambda *args, **kwargs: True)
        monkeypatch.setattr(evaluator_module, "EnsembleSignal", FakeEns)
        monkeypatch.setattr(evaluator_module, "Day0Signal", FakeDay0Signal)
        monkeypatch.setattr(
            evaluator_module,
            "_get_day0_temporal_context",
            lambda *args, **kwargs: types.SimpleNamespace(current_utc_timestamp=now),
        )
        monkeypatch.setattr(
            evaluator_module,
            "remaining_member_maxes_for_day0",
            lambda *args, **kwargs: (np.array([70.0, 71.0, 72.0, 73.0]), 2.0),
        )
        monkeypatch.setattr(evaluator_module, "MarketAnalysis", FakeAnalysis)
        monkeypatch.setattr(evaluator_module, "edge_n_bootstrap", lambda: 2)
        # Return empty list (not raise) — anomalous but not exception
        monkeypatch.setattr(evaluator_module, "scan_full_hypothesis_family", lambda *args, **kwargs: [])
        # Legacy filter WOULD pass the edge — but must NOT be used
        monkeypatch.setattr(evaluator_module, "fdr_filter", lambda edges, fdr_alpha=0.10: list(edges))
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

        candidate = evaluator_module.MarketCandidate(
            city=cities_by_name["Dallas"],
            target_date="2026-04-12",
            outcomes=[
                {"title": "68-69°F", "range_low": 68, "range_high": 69, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
                {"title": "70-71°F", "range_low": 70, "range_high": 71, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
                {"title": "72°F or higher", "range_low": 72, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3"},
            ],
            hours_since_open=2.0,
            hours_to_resolution=12.0,
            event_id="event-test",
            discovery_mode="day0_capture",
            observation={"high_so_far": 70, "current_temp": 69, "source": "test", "observation_time": now.isoformat()},
        )

        decisions = evaluator_module.evaluate_candidate(
            candidate,
            conn,
            PortfolioState(bankroll=150.0, positions=[]),
            FakeClob(),
            RiskLimits(min_order_usd=1.0),
            entry_bankroll=150.0,
            decision_time=now,
        )
        conn.close()

        assert len(decisions) == 1
        assert decisions[0].should_trade is False
        assert decisions[0].rejection_stage == "FDR_FAMILY_SCAN_UNAVAILABLE"
        assert decisions[0].fdr_fallback_fired is True
        assert decisions[0].fdr_family_size == 0
        assert decisions[0].n_edges_found == 1
        assert decisions[0].n_edges_after_fdr == 0
