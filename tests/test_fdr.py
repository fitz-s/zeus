# Lifecycle: created=2026-03-30; last_reviewed=2026-04-29; last_reused=2026-04-29
# Purpose: Tests for FDR (Benjamini-Hochberg) filter + T4.3 selection-family
#          substrate boundary-gate + Day0 causal-day edge case coverage.
# Reuse: Referenced by regression suite; touched 2026-04-24 only for M3
#        CANONICAL_DATA_VERSIONS → CANONICAL_ENSEMBLE_DATA_VERSIONS comment
#        rename (no assertion change).
# Authority basis: midstream verdict v2 2026-04-23; Phase 1A calibration-maturity gate 2026-04-29
"""Tests for FDR (Benjamini-Hochberg) filter.

Covers:
1. Happy path: 10 edges with known p-values → correct filtering
2. Edge case: all p-values below threshold → all pass
3. Edge case: all p-values above threshold → none pass
4. Empty input → empty output
"""

import json
import types
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

import src.engine.evaluator as evaluator_module
from src.engine.evaluator import _record_selection_family_facts, _selected_edge_keys_from_full_family
from src.config import cities_by_name
from src.state.db import get_connection, init_schema
from src.state.portfolio import PortfolioState
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis, scan_full_hypothesis_family
from src.strategy.fdr_filter import fdr_filter
from src.strategy.risk_limits import RiskLimits
from src.strategy.selection_family import apply_familywise_fdr, benjamini_hochberg_mask, make_family_id, make_edge_family_id
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


def _seed_ensemble_snapshots_v2_row(
    conn, *, city: str, target_date: str, metric: str = "high",
    boundary_ambiguous: int = 0, causality_status: str = "OK",
) -> None:
    """S1.3 / T2.g helper: INSERT a minimal valid ensemble_snapshots_v2 row.

    Closes the "natural-empty-v2 bypass" in T2.d/e/f tests — when v2 is
    empty, `_read_v2_snapshot_metadata` returns {} and
    `boundary_ambiguous_refuses_signal({})` returns False (no refusal).
    The DT7 gate passes but via dormant path. Post-T2.g, the gate runs
    against a REAL populated row and its boundary_ambiguous=0 is
    explicitly read — exercising the production schema path.

    The row uses canonical HIGH-track identity (temperature_metric='high',
    physical_quantity='mx2t6_local_calendar_day_max',
    observation_field='high_temp',
    data_version='tigge_mx2t6_local_calendar_day_max_v1') per INV-14
    identity spine + CANONICAL_ENSEMBLE_DATA_VERSIONS allowlist at
    src/contracts/ensemble_snapshot_provenance.py. Members are a plain
    4-member array matching the test's FakeEns.member_maxes shape.
    """
    conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, physical_quantity,
            observation_field, available_at, fetch_time, lead_hours,
            members_json, model_version, data_version,
            boundary_ambiguous, causality_status
        ) VALUES (
            ?, ?, ?, 'mx2t6_local_calendar_day_max',
            'high_temp', '2026-04-12T06:00:00Z', '2026-04-12T06:00:00Z', 12.0,
            '[70.0, 71.0, 72.0, 73.0]', 'test_model_v1',
            'tigge_mx2t6_local_calendar_day_max_v1',
            ?, ?
        )
        """,
        (city, target_date, metric, boundary_ambiguous, causality_status),
    )
    conn.commit()


def _patch_mature_calibration(monkeypatch) -> None:
    """Keep non-calibration evaluator tests on the mature-calibration path."""
    monkeypatch.setattr(evaluator_module, "get_calibrator", lambda *args, **kwargs: (object(), 1))
    monkeypatch.setattr(
        evaluator_module,
        "calibrate_and_normalize",
        lambda p_raw, *args, **kwargs: np.array(p_raw, dtype=float).copy(),
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


class TestDT7ScemaPathActuallyRuns:
    """S1.3 / T2.g antibody — the DT7 gate `boundary_ambiguous_refuses_signal`
    actually fires against REAL ensemble_snapshots_v2 rows, not just via the
    trivial empty-table short-circuit.

    Without these two tests, a future refactor that silently breaks
    `_read_v2_snapshot_metadata` (wrong column name, wrong WHERE clause,
    swapped temperature_metric comparison) would leave the TestSelection-
    FamilySubstrate suite green because they all pass via the legacy
    empty-v2 bypass. These tests exercise the real schema path end-to-end
    and will fail-fast on any upstream schema/query drift."""

    def test_T2g_read_metadata_returns_boundary_ambiguous_zero_from_real_row(self, tmp_path):
        """Seed a row with boundary_ambiguous=0 and verify
        `_read_v2_snapshot_metadata` returns the correct dict against the
        real schema path (not via the fall-through empty-row branch)."""
        from src.engine.evaluator import _read_v2_snapshot_metadata
        conn = get_connection(tmp_path / "t2g_bambig_zero.db")
        init_schema(conn)
        _seed_ensemble_snapshots_v2_row(
            conn, city="Dallas", target_date="2026-04-12", metric="high",
            boundary_ambiguous=0,
        )
        meta = _read_v2_snapshot_metadata(conn, "Dallas", "2026-04-12", "high")
        assert meta, "meta must be non-empty — schema path must read the real row"
        assert meta.get("boundary_ambiguous") == 0
        assert meta.get("causality_status") == "OK"
        from src.contracts.boundary_policy import boundary_ambiguous_refuses_signal
        assert boundary_ambiguous_refuses_signal(meta) is False, (
            "boundary_ambiguous=0 must NOT refuse signal"
        )

    def test_T2g_read_metadata_returns_boundary_ambiguous_one_and_gate_fires(self, tmp_path):
        """Seed a row with boundary_ambiguous=1 (and causality_status=
        REJECTED_BOUNDARY_AMBIGUOUS per the CHECK constraint on that column)
        and verify the refusal-gate actually fires. This is the positive
        proof that DT7 is wired — not just dormant."""
        from src.engine.evaluator import _read_v2_snapshot_metadata
        conn = get_connection(tmp_path / "t2g_bambig_one.db")
        init_schema(conn)
        _seed_ensemble_snapshots_v2_row(
            conn, city="Dallas", target_date="2026-04-12", metric="high",
            boundary_ambiguous=1, causality_status="REJECTED_BOUNDARY_AMBIGUOUS",
        )
        meta = _read_v2_snapshot_metadata(conn, "Dallas", "2026-04-12", "high")
        assert meta.get("boundary_ambiguous") == 1
        from src.contracts.boundary_policy import boundary_ambiguous_refuses_signal
        assert boundary_ambiguous_refuses_signal(meta) is True, (
            "boundary_ambiguous=1 MUST refuse signal — DT7 gate is the core "
            "of the boundary-day-leakage invariant"
        )


class TestSelectionFamilySubstrate:
    def test_family_id_is_stable(self):
        # Phase 1 (2026-04-16): migrated from make_family_id to make_edge_family_id.
        # The canonical ID now carries the "edge|" scope prefix.
        # S4 R9 P10B: temperature_metric added as required kwarg; tuple shape updated.
        assert make_edge_family_id(
            cycle_mode="opening_hunt",
            city="NYC",
            target_date="2026-04-01",
            temperature_metric="high",
            strategy_key="center_buy",
            discovery_mode="opening_hunt",
            decision_snapshot_id="snap-1",
        ) == "edge|opening_hunt|NYC|2026-04-01|high|center_buy|opening_hunt|snap-1"

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
            temperature_metric="high",
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
        # Phase 1 (2026-04-16): hypothesis-scope IDs now carry "hyp|" prefix.
        # S4 R9 P10B: temperature_metric inserted after target_date.
        assert family["family_id"] == "hyp|opening_hunt|NYC|2026-04-01|high|opening_hunt|snap-1"
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
            temperature_metric="high",
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

    def test_multi_bin_full_family_scan_excludes_unexecutable_buy_no(self):
        class FakeAnalysis:
            bins = [
                Bin(low=None, high=38, unit="F", label="38°F or below"),
                Bin(low=39, high=40, unit="F", label="39-40°F"),
                Bin(low=41, high=None, unit="F", label="41°F or higher"),
            ]
            p_cal = np.array([0.25, 0.50, 0.25])
            p_market = np.array([0.20, 0.60, 0.20])
            p_posterior = np.array([0.30, 0.45, 0.25])

            def supports_buy_no_edges(self):
                return False

            def _bootstrap_bin(self, idx, n):
                return (0.01, 0.05, 0.001)

            def _bootstrap_bin_no(self, idx, n):
                raise AssertionError("multi-bin NO hypotheses require native NO-token economics")

        hypotheses = scan_full_hypothesis_family(FakeAnalysis(), n_bootstrap=2)

        assert len(hypotheses) == 3
        assert {hypothesis.direction for hypothesis in hypotheses} == {"buy_yes"}
        assert [hypothesis.range_label for hypothesis in hypotheses] == [
            "38°F or below",
            "39-40°F",
            "41°F or higher",
        ]

    def test_binary_full_family_scan_keeps_executable_buy_no(self):
        class FakeAnalysis:
            bins = [
                Bin(low=None, high=40, unit="F", label="40°F or below"),
                Bin(low=41, high=None, unit="F", label="41°F or higher"),
            ]
            p_cal = np.array([0.45, 0.55])
            p_market = np.array([0.55, 0.45])
            p_posterior = np.array([0.40, 0.60])

            def supports_buy_no_edges(self):
                return True

            def _bootstrap_bin(self, idx, n):
                return (0.01, 0.05, 0.001)

            def _bootstrap_bin_no(self, idx, n):
                return (0.01, 0.05, 0.001)

        hypotheses = scan_full_hypothesis_family(FakeAnalysis(), n_bootstrap=2)

        assert len(hypotheses) == 4
        assert [hypothesis.direction for hypothesis in hypotheses] == [
            "buy_yes",
            "buy_no",
            "buy_yes",
            "buy_no",
        ]

    def test_full_family_selection_uses_one_candidate_family_across_strategies(self, tmp_path):
        conn = get_connection(tmp_path / "selection_one_family.db")
        init_schema(conn)
        candidate = types.SimpleNamespace(
            city=types.SimpleNamespace(name="NYC"),
            target_date="2026-04-01",
            temperature_metric="high",
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
        # Phase 1 (2026-04-16): hypothesis-scope IDs now carry "hyp|" prefix.
        # S4 R9 P10B: temperature_metric inserted after target_date.
        assert family_ids == ["hyp|update_reaction|NYC|2026-04-01|high|update_reaction|snap-1"]
        assert {meta["hypothesis_strategy_key"] for meta in hypothesis_meta} == {
            "center_buy",
            "shoulder_sell",
        }

    def test_evaluate_candidate_materializes_selection_facts(self, tmp_path, monkeypatch):
        # T2.g CLOSED 2026-04-24: explicit ensemble_snapshots_v2 fixture row
        # with boundary_ambiguous=0 replaces the prior natural-empty-v2
        # bypass. DT7 gate now runs against the real populated schema path
        # (reads the row, sees boundary_ambiguous=0, correctly returns False
        # — no refusal). Proves the code actually executes under production-
        # shaped state, not just via a trivial short-circuit on empty table.
        conn = get_connection(tmp_path / "selection_eval_path.db")
        init_schema(conn)
        _seed_ensemble_snapshots_v2_row(
            conn, city="Dallas", target_date="2026-04-12", metric="high",
        )
        now = datetime.now(timezone.utc)
        _patch_mature_calibration(monkeypatch)

        class FakeEns:
            def __init__(self, *args, **kwargs):
                self.member_maxes = np.array([70.0, 71.0, 72.0, 73.0])
                # T2.d.1 2026-04-24: production evaluator reads
                # `ens.member_extrema` AND `ens.bias_corrected` at FOUR
                # sites — evaluator.py:991 (_store_snapshot_p_raw kwarg),
                # :1284 (MarketAnalysis construction kwarg member_maxes),
                # :1292 (MarketAnalysis construction kwarg bias_corrected),
                # :1842 (_store_ens_snapshot body .tolist()). Attr access
                # happens at KWARG-EVALUATION TIME, before the stubbed
                # callees take over, so FakeEns must carry these attrs
                # regardless of what the stubs do with them. MarketAnalysis
                # is monkeypatched to FakeAnalysis which drops both kwargs,
                # but Python still evaluates them to build the call.
                self.member_extrema = np.array([70.0, 71.0, 72.0, 73.0])
                self.bias_corrected = False

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
                # T2.d.1 2026-04-24: return bins-sized uniform vector so
                # downstream FDR family scan can index by bin without
                # out-of-bounds (was hardcoded size 3; now dynamic).
                _n = len(bins)
                return np.full(_n, 1.0 / _n)

            def forecast_context(self):
                return {"day0_test": True}

        class FakeAnalysis:
            def __init__(self, **kwargs):
                self.selected_method = kwargs.get("selected_method", "test_fixture")
                self.bias_correction = kwargs.get("bias_correction", kwargs.get("bias_corrected", False))
                # NOTE: production kwargs p_raw / p_cal / p_market / alpha /
                # calibrator / lead_days / etc. are intentionally dropped.
                # We pin bin topology from the candidate and fabricate a
                # 1-bin-positive-edge layout so FDR + BH exercise a
                # deterministic selection (bin 0 YES only).
                self.bins = kwargs["bins"]
                self.p_raw = kwargs["p_raw"]
                self.p_cal = kwargs["p_cal"]
                # T2.d.1 2026-04-24: scale p_market/p_posterior to n_bins
                # (now 4 with left-shoulder). Bin 0 keeps the positive edge
                # (p_posterior > p_market) so exactly one hypothesis passes
                # BH + prefilter; bins 1-3 fail at edge<=0 prefilter.
                _n_bins = len(self.bins)
                self.p_market = np.full(_n_bins, 0.2)
                self.p_market[0] = 0.1
                self.p_posterior = np.full(_n_bins, 0.15)
                self.p_posterior[0] = 1.0 - float(self.p_posterior[1:].sum())

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
        monkeypatch.setattr(evaluator_module, "EnsembleSignal", FakeEns)
        # T2.d.1 2026-04-24: Day0Signal symbol was refactored to
        # Day0Router.route in a prior session — target the current class
        # method (HIGH-only fixture; FakeDay0Signal is HIGH-shaped).
        # TODO(T2.g): if this test is ever parameterized for LOW, update
        # FakeDay0Signal and wrap the route lambda to dispatch on
        # inputs.temperature_metric.is_low().
        #
        # DT7 gate: init_schema(conn) calls apply_v2_schema which creates
        # empty ensemble_snapshots_v2; _read_v2_snapshot_metadata on empty
        # table naturally returns {} → boundary_ambiguous_refuses_signal
        # returns False → gate passes WITHOUT stubbing. T2.g (plan row)
        # WILL replace this natural bypass with a real v2 fixture row
        # (boundary_ambiguous=0) to exercise DT7 on fixture DB.
        #
        # ENS snapshot persistence is stubbed so FakeEns does not need
        # the full production attribute surface at the PERSISTENCE seam;
        # FakeEns also needs member_extrema + bias_corrected attrs because
        # evaluator.py:991 + :1284 + :1292 read them as KEYWORD ARGS
        # (evaluated at call-time, before the stubbed callee receives
        # them).
        monkeypatch.setattr(
            "src.signal.day0_router.Day0Router.route",
            staticmethod(lambda inputs: FakeDay0Signal()),
        )
        monkeypatch.setattr(
            evaluator_module,
            "_store_ens_snapshot",
            lambda conn, city, target_date, ens, ens_result: "snap-t2d1-test",
        )
        monkeypatch.setattr(
            evaluator_module,
            "_store_snapshot_p_raw",
            lambda *args, **kwargs: None,
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
                # T2.d.1 2026-04-24: left-shoulder added so validate_bin_topology
                # passes (leftmost must have range_low=None per open-shoulder
                # contract — see src/types/market.py::validate_bin_topology).
                {"title": "67°F or lower", "range_low": None, "range_high": 67, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0"},
                {"title": "68-69°F", "range_low": 68, "range_high": 69, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
                {"title": "70-71°F", "range_low": 70, "range_high": 71, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
                {"title": "72°F or higher", "range_low": 72, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3"},
            ],
            hours_since_open=2.0,
            hours_to_resolution=12.0,
            event_id="event-test",
            discovery_mode="day0_capture",
            observation=types.SimpleNamespace(
                high_so_far=70,
                low_so_far=None,
                current_temp=69,
                source="test",
                observation_time=now.isoformat(),
            ),
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

        # Phase 4 2026-04-29: multi-bin NO hypotheses are not executable with
        # YES-only VWMP, so the tested FDR family is the executable YES side.
        # Bin 0 is the only one with positive edge + CI_lower > 0; others fail
        # at prefilter. active_fdr_selected remains 1.
        assert decisions[0].rejection_stage == "SIZING_TOO_SMALL"
        assert family_count == 1
        assert hypothesis_count == 4
        assert meta["tested_hypotheses"] == 4
        assert meta["active_fdr_selected"] == 1

    def test_evaluate_candidate_fails_closed_when_full_family_scan_unavailable(self, tmp_path, monkeypatch):
        # T2.g CLOSED 2026-04-24: explicit v2 fixture row with
        # boundary_ambiguous=0 (see _seed_ensemble_snapshots_v2_row helper).
        conn = get_connection(tmp_path / "selection_fail_closed.db")
        init_schema(conn)
        _seed_ensemble_snapshots_v2_row(
            conn, city="Dallas", target_date="2026-04-12", metric="high",
        )
        now = datetime.now(timezone.utc)
        _patch_mature_calibration(monkeypatch)

        class FakeEns:
            def __init__(self, *args, **kwargs):
                self.member_maxes = np.array([70.0, 71.0, 72.0, 73.0])
                # T2.d.1 2026-04-24: production evaluator reads
                # `ens.member_extrema` AND `ens.bias_corrected` at FOUR
                # sites — evaluator.py:991 (_store_snapshot_p_raw kwarg),
                # :1284 (MarketAnalysis construction kwarg member_maxes),
                # :1292 (MarketAnalysis construction kwarg bias_corrected),
                # :1842 (_store_ens_snapshot body .tolist()). Attr access
                # happens at KWARG-EVALUATION TIME, before the stubbed
                # callees take over, so FakeEns must carry these attrs
                # regardless of what the stubs do with them. MarketAnalysis
                # is monkeypatched to FakeAnalysis which drops both kwargs,
                # but Python still evaluates them to build the call.
                self.member_extrema = np.array([70.0, 71.0, 72.0, 73.0])
                self.bias_corrected = False

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
                # T2.d.1 2026-04-24: return bins-sized uniform vector so
                # downstream FDR family scan can index by bin without
                # out-of-bounds (was hardcoded size 3; now dynamic).
                _n = len(bins)
                return np.full(_n, 1.0 / _n)

            def forecast_context(self):
                return {"day0_test": True}

        class FakeAnalysis:
            def __init__(self, **kwargs):
                self.selected_method = kwargs.get("selected_method", "test_fixture")
                self.bias_correction = kwargs.get("bias_correction", kwargs.get("bias_corrected", False))
                # NOTE: production kwargs p_raw / p_cal / p_market / alpha /
                # calibrator / lead_days / etc. are intentionally dropped.
                # We pin bin topology from the candidate and fabricate a
                # 1-bin-positive-edge layout so FDR + BH exercise a
                # deterministic selection (bin 0 YES only).
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
        monkeypatch.setattr(evaluator_module, "EnsembleSignal", FakeEns)
        # T2.d.1 2026-04-24: Day0Signal symbol was refactored to
        # Day0Router.route in a prior session — target the current class
        # method (HIGH-only fixture; FakeDay0Signal is HIGH-shaped).
        # TODO(T2.g): if this test is ever parameterized for LOW, update
        # FakeDay0Signal and wrap the route lambda to dispatch on
        # inputs.temperature_metric.is_low().
        #
        # DT7 gate: init_schema(conn) calls apply_v2_schema which creates
        # empty ensemble_snapshots_v2; _read_v2_snapshot_metadata on empty
        # table naturally returns {} → boundary_ambiguous_refuses_signal
        # returns False → gate passes WITHOUT stubbing. T2.g (plan row)
        # WILL replace this natural bypass with a real v2 fixture row
        # (boundary_ambiguous=0) to exercise DT7 on fixture DB.
        #
        # ENS snapshot persistence is stubbed so FakeEns does not need
        # the full production attribute surface at the PERSISTENCE seam;
        # FakeEns also needs member_extrema + bias_corrected attrs because
        # evaluator.py:991 + :1284 + :1292 read them as KEYWORD ARGS
        # (evaluated at call-time, before the stubbed callee receives
        # them).
        monkeypatch.setattr(
            "src.signal.day0_router.Day0Router.route",
            staticmethod(lambda inputs: FakeDay0Signal()),
        )
        monkeypatch.setattr(
            evaluator_module,
            "_store_ens_snapshot",
            lambda conn, city, target_date, ens, ens_result: "snap-t2d1-test",
        )
        monkeypatch.setattr(
            evaluator_module,
            "_store_snapshot_p_raw",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            evaluator_module,
            "_get_day0_temporal_context",
            lambda *args, **kwargs: types.SimpleNamespace(current_utc_timestamp=now),
        )
        from src.signal.day0_extrema import RemainingMemberExtrema as _REM
        monkeypatch.setattr(
            evaluator_module,
            "remaining_member_extrema_for_day0",
            lambda *args, **kwargs: (_REM(maxes=np.array([70.0, 71.0, 72.0, 73.0]), mins=None), 2.0),
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
                # T2.d.1 2026-04-24: left-shoulder added so validate_bin_topology
                # passes (leftmost must have range_low=None per open-shoulder
                # contract — see src/types/market.py::validate_bin_topology).
                {"title": "67°F or lower", "range_low": None, "range_high": 67, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0"},
                {"title": "68-69°F", "range_low": 68, "range_high": 69, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
                {"title": "70-71°F", "range_low": 70, "range_high": 71, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
                {"title": "72°F or higher", "range_low": 72, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3"},
            ],
            hours_since_open=2.0,
            hours_to_resolution=12.0,
            event_id="event-test",
            discovery_mode="day0_capture",
            observation=types.SimpleNamespace(
                high_so_far=70,
                low_so_far=None,
                current_temp=69,
                source="test",
                observation_time=now.isoformat(),
            ),
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
        # T2.g CLOSED 2026-04-24: explicit v2 fixture row with
        # boundary_ambiguous=0 (see _seed_ensemble_snapshots_v2_row helper).
        conn = get_connection(tmp_path / "selection_empty_family.db")
        init_schema(conn)
        _seed_ensemble_snapshots_v2_row(
            conn, city="Dallas", target_date="2026-04-12", metric="high",
        )
        now = datetime.now(timezone.utc)
        _patch_mature_calibration(monkeypatch)

        class FakeEns:
            def __init__(self, *args, **kwargs):
                self.member_maxes = np.array([70.0, 71.0, 72.0, 73.0])
                # T2.d.1 2026-04-24: production evaluator reads
                # `ens.member_extrema` AND `ens.bias_corrected` at FOUR
                # sites — evaluator.py:991 (_store_snapshot_p_raw kwarg),
                # :1284 (MarketAnalysis construction kwarg member_maxes),
                # :1292 (MarketAnalysis construction kwarg bias_corrected),
                # :1842 (_store_ens_snapshot body .tolist()). Attr access
                # happens at KWARG-EVALUATION TIME, before the stubbed
                # callees take over, so FakeEns must carry these attrs
                # regardless of what the stubs do with them. MarketAnalysis
                # is monkeypatched to FakeAnalysis which drops both kwargs,
                # but Python still evaluates them to build the call.
                self.member_extrema = np.array([70.0, 71.0, 72.0, 73.0])
                self.bias_corrected = False

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
                # T2.d.1 2026-04-24: return bins-sized uniform vector so
                # downstream FDR family scan can index by bin without
                # out-of-bounds (was hardcoded size 3; now dynamic).
                _n = len(bins)
                return np.full(_n, 1.0 / _n)

            def forecast_context(self):
                return {"day0_test": True}

        class FakeAnalysis:
            def __init__(self, **kwargs):
                self.selected_method = kwargs.get("selected_method", "test_fixture")
                self.bias_correction = kwargs.get("bias_correction", kwargs.get("bias_corrected", False))
                # NOTE: production kwargs p_raw / p_cal / p_market / alpha /
                # calibrator / lead_days / etc. are intentionally dropped.
                # We pin bin topology from the candidate and fabricate a
                # 1-bin-positive-edge layout so FDR + BH exercise a
                # deterministic selection (bin 0 YES only).
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
        monkeypatch.setattr(evaluator_module, "EnsembleSignal", FakeEns)
        # T2.d.1 2026-04-24: Day0Signal symbol was refactored to
        # Day0Router.route in a prior session — target the current class
        # method (HIGH-only fixture; FakeDay0Signal is HIGH-shaped).
        # TODO(T2.g): if this test is ever parameterized for LOW, update
        # FakeDay0Signal and wrap the route lambda to dispatch on
        # inputs.temperature_metric.is_low().
        #
        # DT7 gate: init_schema(conn) calls apply_v2_schema which creates
        # empty ensemble_snapshots_v2; _read_v2_snapshot_metadata on empty
        # table naturally returns {} → boundary_ambiguous_refuses_signal
        # returns False → gate passes WITHOUT stubbing. T2.g (plan row)
        # WILL replace this natural bypass with a real v2 fixture row
        # (boundary_ambiguous=0) to exercise DT7 on fixture DB.
        #
        # ENS snapshot persistence is stubbed so FakeEns does not need
        # the full production attribute surface at the PERSISTENCE seam;
        # FakeEns also needs member_extrema + bias_corrected attrs because
        # evaluator.py:991 + :1284 + :1292 read them as KEYWORD ARGS
        # (evaluated at call-time, before the stubbed callee receives
        # them).
        monkeypatch.setattr(
            "src.signal.day0_router.Day0Router.route",
            staticmethod(lambda inputs: FakeDay0Signal()),
        )
        monkeypatch.setattr(
            evaluator_module,
            "_store_ens_snapshot",
            lambda conn, city, target_date, ens, ens_result: "snap-t2d1-test",
        )
        monkeypatch.setattr(
            evaluator_module,
            "_store_snapshot_p_raw",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            evaluator_module,
            "_get_day0_temporal_context",
            lambda *args, **kwargs: types.SimpleNamespace(current_utc_timestamp=now),
        )
        from src.signal.day0_extrema import RemainingMemberExtrema as _REM
        monkeypatch.setattr(
            evaluator_module,
            "remaining_member_extrema_for_day0",
            lambda *args, **kwargs: (_REM(maxes=np.array([70.0, 71.0, 72.0, 73.0]), mins=None), 2.0),
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
                # T2.d.1 2026-04-24: left-shoulder added so validate_bin_topology
                # passes (leftmost must have range_low=None per open-shoulder
                # contract — see src/types/market.py::validate_bin_topology).
                {"title": "67°F or lower", "range_low": None, "range_high": 67, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0"},
                {"title": "68-69°F", "range_low": 68, "range_high": 69, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
                {"title": "70-71°F", "range_low": 70, "range_high": 71, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
                {"title": "72°F or higher", "range_low": 72, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3"},
            ],
            hours_since_open=2.0,
            hours_to_resolution=12.0,
            event_id="event-test",
            discovery_mode="day0_capture",
            observation=types.SimpleNamespace(
                high_so_far=70,
                low_so_far=None,
                current_temp=69,
                source="test",
                observation_time=now.isoformat(),
            ),
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

    def test_evaluate_candidate_exercises_real_day0_router_on_fixture_db(self, tmp_path, monkeypatch):
        """T2.g (W4 closure, 2026-04-24): runs evaluate_candidate WITHOUT the
        Day0Router.route monkeypatch, exercising the real Day0Router dispatch
        path against a full Day0TemporalContext fixture. Previously xfail
        (strict=False) pending fixture-builder work; closure commit replaces
        the SimpleNamespace stub with a real Day0TemporalContext + SolarDay
        built via _build_day0_temporal_context_fixture() so Day0HighSignal
        p_vector can compute over the fixture bins without TypeError.

        Regression signal: if a future refactor drops a required field on
        Day0TemporalContext, this test fails loudly (not xfail).
        """
        conn = get_connection(tmp_path / "t2g_real_day0.db")
        init_schema(conn)
        now = datetime.now(timezone.utc)
        _patch_mature_calibration(monkeypatch)

        class FakeEns:
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

        class FakeAnalysis:
            def __init__(self, **kwargs):
                self.selected_method = kwargs.get("selected_method", "test_fixture")
                self.bias_correction = kwargs.get("bias_correction", kwargs.get("bias_corrected", False))
                # Kwargs from production dropped; fabricate 1-bin-positive-edge.
                self.bins = kwargs["bins"]
                self.p_raw = kwargs["p_raw"]
                self.p_cal = kwargs["p_cal"]
                n_bins = len(self.bins)
                self.p_market = np.full(n_bins, 0.2)
                self.p_market[0] = 0.1
                self.p_posterior = np.full(n_bins, 0.15)
                self.p_posterior[0] = 1.0 - float(self.p_posterior[1:].sum())

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
        monkeypatch.setattr(evaluator_module, "EnsembleSignal", FakeEns)
        # T2.g: Day0Router.route NOT monkeypatched — real dispatch path.
        monkeypatch.setattr(evaluator_module, "MarketAnalysis", FakeAnalysis)
        monkeypatch.setattr(evaluator_module, "edge_n_bootstrap", lambda: 2)
        monkeypatch.setattr(
            evaluator_module,
            "_store_ens_snapshot",
            lambda conn, city, target_date, ens, ens_result: "snap-t2g-test",
        )
        monkeypatch.setattr(
            evaluator_module,
            "_store_snapshot_p_raw",
            lambda *args, **kwargs: None,
        )
        # T2.g W4 closure (2026-04-24): replace the SimpleNamespace stub
        # with a real Day0TemporalContext fixture so Day0HighSignal.p_vector
        # (delegating to Day0Signal) can traverse solar_day /
        # peak_hour / daylight_progress without TypeError. Dallas /
        # 2026-04-12 / CDT (UTC-5, DST active).
        from src.types import Day0TemporalContext, SolarDay

        _t2g_target_date = date(2026, 4, 12)
        _t2g_local_noon = datetime(2026, 4, 12, 17, 0, tzinfo=timezone.utc)  # ≈ 12:00 CDT
        _t2g_sunrise_utc = datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc)  # ≈ 07:00 CDT
        _t2g_sunset_utc = datetime(2026, 4, 13, 0, 45, tzinfo=timezone.utc)  # ≈ 19:45 CDT
        _t2g_solar_day = SolarDay(
            city="Dallas",
            target_date=_t2g_target_date,
            timezone="America/Chicago",
            sunrise_local=_t2g_sunrise_utc,
            sunset_local=_t2g_sunset_utc,
            sunrise_utc=_t2g_sunrise_utc,
            sunset_utc=_t2g_sunset_utc,
            utc_offset_minutes=-300,
            dst_active=True,
        )
        _t2g_temporal_context = Day0TemporalContext(
            city="Dallas",
            target_date=_t2g_target_date,
            timezone="America/Chicago",
            current_local_timestamp=_t2g_local_noon,
            current_utc_timestamp=_t2g_local_noon,
            current_local_hour=12.0,
            solar_day=_t2g_solar_day,
            observation_instant=None,
            peak_hour=15,
            post_peak_confidence=0.4,
            daylight_progress=0.5,
            utc_offset_minutes=-300,
            dst_active=True,
            time_basis="fixture_t2g",
            confidence_source="test_fixture",
        )
        monkeypatch.setattr(
            evaluator_module,
            "_get_day0_temporal_context",
            lambda *args, **kwargs: _t2g_temporal_context,
        )
        from src.signal.day0_extrema import RemainingMemberExtrema as _REM
        monkeypatch.setattr(
            evaluator_module,
            "remaining_member_extrema_for_day0",
            lambda *args, **kwargs: (_REM(maxes=np.array([70.0, 71.0, 72.0, 73.0]), mins=None), 2.0),
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

        candidate = evaluator_module.MarketCandidate(
            city=cities_by_name["Dallas"],
            target_date="2026-04-12",
            outcomes=[
                {"title": "67°F or lower", "range_low": None, "range_high": 67, "token_id": "yes0", "no_token_id": "no0", "market_id": "m0"},
                {"title": "68-69°F", "range_low": 68, "range_high": 69, "token_id": "yes1", "no_token_id": "no1", "market_id": "m1"},
                {"title": "70-71°F", "range_low": 70, "range_high": 71, "token_id": "yes2", "no_token_id": "no2", "market_id": "m2"},
                {"title": "72°F or higher", "range_low": 72, "range_high": None, "token_id": "yes3", "no_token_id": "no3", "market_id": "m3"},
            ],
            hours_since_open=2.0,
            hours_to_resolution=12.0,
            event_id="event-t2g",
            discovery_mode="day0_capture",
            observation=types.SimpleNamespace(
                high_so_far=70,
                low_so_far=None,
                current_temp=69,
                source="test",
                observation_time=now.isoformat(),
            ),
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
        conn.close()

        # If the real Day0Router reaches this assertion, the fixture is
        # complete enough for real integration. SIZING_TOO_SMALL is the
        # expected terminal rejection (same as T2.d monkeypatched variant).
        assert decisions[0].rejection_stage == "SIZING_TOO_SMALL"
