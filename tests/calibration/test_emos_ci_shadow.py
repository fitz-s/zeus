# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: EMOS-CI shadow extension spec (/tmp/design_emos_ci.md);
#   trade_score.py:68-71 (_robust_trade_score_receipt edge_bound); operator CI-honesty law.
"""RED→GREEN tests for the EMOS-CI shadow recording extension.

Invariants:
(a) robust_score_raw replication: compute_robust_edge matches trade_score.py:68-71
    on hand-computed fixtures (q-domain parity).
(b) emos_q_lcb = min(emos_q, bin_probability(mu, k*sigma, low, high))
    is never-optimistic: with k=1.0, emos_q_lcb == emos_q; with k>1.0,
    emos_q_lcb <= emos_q (widening σ can only lower in-bin mass for peaked bins).
(c) k_cov solve: picks the smallest k≥1 with cov90∈[0.86,0.94], clamps to 1
    when already-covering or over-dispersed; never k<1.
(d) scorer-path: _bin_prob_from_row at k_cov>1 gives lcb < emos_q for peaked bin;
    rescued-count SHRINKS at k_cov>1 vs k=1 on an under-covered fixture.
(e) cross-module parity: compute_robust_edge output equals edge_bound from
    trade_score._robust_trade_score_receipt (the live formula).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import norm

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# helpers shared across tests
# ---------------------------------------------------------------------------

def _robust_edge(q_posterior: float, q_5pct: float, cost: float, penalty: float = 0.01) -> float:
    """Mirror of trade_score.py:48-52 (p_fill_lcb removed; the >0 test uses the edge)."""
    return min(q_5pct - cost - penalty, q_posterior - cost - penalty)


# ---------------------------------------------------------------------------
# (a) robust_score_raw replication: must match trade_score.py:48-52
# ---------------------------------------------------------------------------

class TestRobustScoreRawReplication:
    """The recorded robust_score_raw_* must replicate the live formula exactly."""

    def test_formula_matches_trade_score_positive(self):
        """q_posterior=0.70, q_5pct=0.65, cost=0.55 → edge=0.65-0.55-0.01=0.09 > 0.
        Authority: trade_score.py:68-71.
        """
        from src.calibration.emos_ci_shadow import compute_robust_edge

        edge = compute_robust_edge(q_posterior=0.70, q_5pct=0.65, cost=0.55)
        expected = min(0.65 - 0.55 - 0.01, 0.70 - 0.55 - 0.01)  # min(0.09, 0.14) = 0.09
        assert abs(edge - expected) < 1e-12, f"edge={edge}, expected={expected}"
        assert edge > 0.0

    def test_formula_matches_trade_score_negative(self):
        """q_posterior=0.60, q_5pct=0.50, cost=0.55 → lcb side = 0.50-0.55-0.01=-0.06 < 0."""
        from src.calibration.emos_ci_shadow import compute_robust_edge

        edge = compute_robust_edge(q_posterior=0.60, q_5pct=0.50, cost=0.55)
        expected = min(0.50 - 0.55 - 0.01, 0.60 - 0.55 - 0.01)  # min(-0.06, 0.04) = -0.06
        assert abs(edge - expected) < 1e-12
        assert edge < 0.0

    def test_formula_controlled_by_lcb_side(self):
        """When q_5pct is the binding constraint: min is on lcb side."""
        from src.calibration.emos_ci_shadow import compute_robust_edge

        # q_5pct - cost - penalty = 0.59 - 0.55 - 0.01 = 0.03 (binding)
        # q_posterior - cost - penalty = 0.80 - 0.55 - 0.01 = 0.24
        edge = compute_robust_edge(q_posterior=0.80, q_5pct=0.59, cost=0.55)
        assert abs(edge - 0.03) < 1e-12

    def test_formula_controlled_by_posterior_side(self):
        """When q_posterior is the binding constraint (tight spread)."""
        from src.calibration.emos_ci_shadow import compute_robust_edge

        # q_5pct - cost - penalty = 0.80 - 0.55 - 0.01 = 0.24
        # q_posterior - cost - penalty = 0.57 - 0.55 - 0.01 = 0.01 (binding)
        edge = compute_robust_edge(q_posterior=0.57, q_5pct=0.80, cost=0.55)
        assert abs(edge - 0.01) < 1e-12

    def test_penalty_is_0_01(self):
        """Penalty literal must be 0.01 (not 0 or 0.05)."""
        from src.calibration.emos_ci_shadow import compute_robust_edge

        # With cost=0, we can read the penalty directly: edge = min(q_5pct - penalty, q - penalty)
        edge = compute_robust_edge(q_posterior=1.0, q_5pct=1.0, cost=0.0)
        # Should be min(1.0-0.01, 1.0-0.01) = 0.99
        assert abs(edge - 0.99) < 1e-12, f"Penalty is not 0.01: edge={edge}"


# ---------------------------------------------------------------------------
# (b) emos_q_lcb never-optimistic property
# ---------------------------------------------------------------------------

class TestEmosQLcbNeverOptimistic:
    """emos_q_lcb = min(emos_q, bin_prob(mu, k*sigma, lo, hi)) must never exceed emos_q."""

    def _emos_q_lcb(self, mu: float, sigma: float, k: float, lo, hi) -> float:
        from src.calibration.emos import bin_probability

        emos_q = bin_probability(mu, sigma, lo, hi)
        q_inflated = bin_probability(mu, k * sigma, lo, hi)
        return min(emos_q, q_inflated)

    def test_k1_lcb_equals_emos_q(self):
        """k=1.0: emos_q_lcb must equal emos_q (no haircut applied in shadow)."""
        from src.calibration.emos import bin_probability

        mu, sigma = 18.0, 3.0
        lo, hi = 15.0, 20.0
        emos_q = bin_probability(mu, sigma, lo, hi)
        lcb = self._emos_q_lcb(mu, sigma, 1.0, lo, hi)
        assert abs(lcb - emos_q) < 1e-12, f"k=1 lcb={lcb} != emos_q={emos_q}"

    def test_k_greater_than_1_lowers_peaked_bin(self):
        """k>1 (inflated sigma) lowers mass for a peaked in-distribution bin."""
        mu, sigma = 18.0, 2.0
        lo, hi = 16.0, 20.0  # bin straddling the mean — peaked => widening sigma lowers mass

        from src.calibration.emos import bin_probability

        emos_q = bin_probability(mu, sigma, lo, hi)
        lcb = self._emos_q_lcb(mu, sigma, 2.0, lo, hi)
        assert lcb <= emos_q + 1e-12, f"emos_q_lcb={lcb} exceeds emos_q={emos_q} for k=2 peaked bin"

    def test_min_ensures_never_optimistic(self):
        """For every k>=1 and every bin, min(emos_q, q(k*sigma)) <= emos_q always holds."""
        from src.calibration.emos import bin_probability

        mu, sigma = 20.0, 3.0
        bins = [(None, 15.0), (15.0, 20.0), (20.0, 25.0), (25.0, None)]
        for lo, hi in bins:
            emos_q = bin_probability(mu, sigma, lo, hi)
            for k in [1.0, 1.2, 1.5, 2.0, 3.0]:
                lcb = self._emos_q_lcb(mu, sigma, k, lo, hi)
                assert lcb <= emos_q + 1e-12, (
                    f"bin=({lo},{hi}) k={k}: emos_q_lcb={lcb} > emos_q={emos_q} — OPTIMISTIC"
                )

    def test_k_less_than_1_forbidden_via_clamp(self):
        """The k_cov solver must clamp to k>=1 so emos_q_lcb is never tightened."""
        from src.calibration.emos_ci_shadow import solve_k_cov

        # A fixture where EMOS already over-disperses (high cov90): k_cov must be 1.0
        # We'll use a synthetic PIT array that is uniform (perfect calibration) => k_cov=1
        rng = np.random.default_rng(42)
        # Perfect PIT: uniform in [0,1] => cov90 ≈ 0.90 => k_cov = 1.0
        pit = rng.uniform(0.0, 1.0, 200)
        k = solve_k_cov(pit)
        assert k >= 1.0, f"k_cov={k} < 1 — tightening sigma is forbidden"


# ---------------------------------------------------------------------------
# (c) k_cov solve correctness
# ---------------------------------------------------------------------------

class TestKCovSolve:
    """k_cov = smallest k>=1 such that inflated cov90 in [0.86, 0.94]; clamp=1 if already covers."""

    def _synthetic_under_covered_pit(self, n: int = 200, seed: int = 0) -> np.ndarray:
        """PIT from a too-tight σ: clustered near 0 and 1 (fat-tailed, under-covers central CI).

        A too-tight sigma pushes the true obs outside the CI → PIT values cluster near
        0 and 1 (obs falls near the tails of the predictive). cov90 = fraction in [0.05,0.95]
        will be low when many points are outside that range.
        """
        rng = np.random.default_rng(seed)
        # Mix: 40% near 0, 40% near 1, 20% uniform — simulates systematic under-coverage
        n_tail = n * 2 // 5
        n_mid = n - 2 * n_tail
        lo_tail = rng.uniform(0.0, 0.04, n_tail)
        hi_tail = rng.uniform(0.96, 1.0, n_tail)
        mid = rng.uniform(0.05, 0.95, n_mid)
        return np.concatenate([lo_tail, hi_tail, mid])

    def _synthetic_over_dispersed_pit(self, n: int = 200, seed: int = 1) -> np.ndarray:
        """PIT nearly uniform (well-covered): k_cov must be clamped to 1."""
        rng = np.random.default_rng(seed)
        return rng.uniform(0.0, 1.0, n)

    def test_under_covered_returns_k_greater_than_1(self):
        """Under-covered PIT (bunched) → k_cov > 1.0 to inflate sigma."""
        from src.calibration.emos_ci_shadow import solve_k_cov

        pit = self._synthetic_under_covered_pit()
        k = solve_k_cov(pit)
        # Under-covered means CI is too narrow; k must expand it
        assert k >= 1.0, f"k_cov={k} must be >=1"
        # With the bunched PIT, cov90 < 0.86, so k must be >1
        cov90_raw = float(np.mean((pit >= 0.05) & (pit <= 0.95)))
        if cov90_raw < 0.86:
            assert k > 1.0, f"Under-covered (cov90={cov90_raw:.3f}) but k={k} not >1"

    def test_over_dispersed_pit_clamps_to_1(self):
        """Over-dispersed PIT → k_cov clamped to 1.0."""
        from src.calibration.emos_ci_shadow import solve_k_cov

        pit = self._synthetic_over_dispersed_pit()
        k = solve_k_cov(pit)
        assert k == 1.0, f"Over-dispersed PIT but k_cov={k} != 1.0 — must clamp"

    def test_perfect_coverage_returns_1(self):
        """PIT exactly uniform in [0,1] (perfect EMOS) → k_cov = 1.0."""
        from src.calibration.emos_ci_shadow import solve_k_cov

        # Deterministic perfect PIT
        pit = np.linspace(0.0, 1.0, 201)[1:-1]  # 199 points in (0,1), uniform
        k = solve_k_cov(pit)
        assert k == 1.0, f"Perfect PIT but k_cov={k} != 1.0"

    def test_k_cov_solve_small_n_returns_1(self):
        """With n < MIN_N_FOR_VERDICT=20, k_cov should fall back to 1.0 (insufficient data)."""
        from src.calibration.emos_ci_shadow import solve_k_cov

        pit = np.array([0.6, 0.5, 0.4, 0.55, 0.45])  # n=5 < 20
        k = solve_k_cov(pit)
        assert k == 1.0, f"Insufficient n but k_cov={k} != 1.0"

    def test_solve_k_cov_result_achieves_target_coverage(self):
        """For a guaranteed under-covered input, k achieves cov90 ∈ [0.86, 0.94]."""
        from src.calibration.emos_ci_shadow import solve_k_cov, _coverage_at_k

        # Use the class fixture: fat-tail PIT → cov90 < 0.86
        pit_raw = self._synthetic_under_covered_pit(n=400, seed=0)
        cov90_raw = float(np.mean((pit_raw >= 0.05) & (pit_raw <= 0.95)))
        # Sanity: this fixture must be under-covered
        assert cov90_raw < 0.86, f"fixture cov90={cov90_raw:.3f} is not under-covered"

        k = solve_k_cov(pit_raw)
        assert k > 1.0, f"Under-covered fixture but k={k} not >1"
        cov90_k = _coverage_at_k(pit_raw, k)
        assert cov90_k >= 0.86 - 0.005, (
            f"k={k:.3f} does not achieve target: cov90@k={cov90_k:.3f}"
        )


# ---------------------------------------------------------------------------
# (d) Scorer-path: _bin_prob_from_row and rescued-count shrinkage
# ---------------------------------------------------------------------------

class TestScorerPathBinProbFromRow:
    """Tests for the load-bearing scorer path: _bin_prob_from_row + rescued shrinkage.

    These test the actual functions called by score_emos_forward.py §4ii Pass 2,
    NOT just the helper in emos_ci_shadow.py.
    """

    def _make_row(self, mu_c: float, sigma_c: float, bin_low, bin_high,
                  bin_unit: str = "C") -> dict:
        """Synthetic ledger row with EMOS mu/sigma fields.

        emos_q is pre-computed using bin_probability_settlement (the live formula),
        matching what _write_emos_shadow_ledger records in production.
        """
        from src.calibration.emos import bin_probability_settlement
        if bin_unit == "F":
            mu_native = mu_c * 9.0 / 5.0 + 32.0
            sigma_native = sigma_c * 9.0 / 5.0
        else:
            mu_native, sigma_native = mu_c, sigma_c
        emos_q = bin_probability_settlement(mu_native, sigma_native, bin_low, bin_high)
        return {
            "emos_mu_c": mu_c,
            "emos_sigma_c": sigma_c,
            "bin_unit": bin_unit,
            "bin_low": bin_low,
            "bin_high": bin_high,
            "emos_q": emos_q,
        }

    def test_bin_prob_from_row_k1_equals_emos_q(self):
        """At k=1, _bin_prob_from_row returns emos_q unchanged (no haircut)."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
        from score_emos_forward import _bin_prob_from_row

        row = self._make_row(mu_c=20.0, sigma_c=2.0, bin_low=18.0, bin_high=22.0)
        lcb_k1 = _bin_prob_from_row(row, k=1.0)
        assert lcb_k1 is not None
        assert abs(lcb_k1 - float(row["emos_q"])) < 1e-12, (
            f"k=1 lcb={lcb_k1} != emos_q={row['emos_q']}"
        )

    def test_bin_prob_from_row_k_gt1_lowers_peaked_bin(self):
        """k>1 lowers emos_q_lcb for a peaked bin centered on the mean.

        Inflating sigma spreads mass out from the bin center → lower in-bin probability.
        This is the never-optimistic invariant on the scorer path (not just the helper).
        """
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
        from score_emos_forward import _bin_prob_from_row

        # Peaked bin: mu=20, sigma=2, bin [19,21) — tight around mean
        row = self._make_row(mu_c=20.0, sigma_c=2.0, bin_low=19.0, bin_high=21.0)
        emos_q = float(row["emos_q"])
        lcb_k2 = _bin_prob_from_row(row, k=2.0)
        assert lcb_k2 is not None
        assert lcb_k2 < emos_q, (
            f"k=2 lcb={lcb_k2:.4f} should be < emos_q={emos_q:.4f} for peaked bin"
        )

    def test_rescued_count_shrinks_at_k_cov_gt1(self):
        """Rescued count at k_cov>1 MUST be <= rescued count at k=1.

        Under-covered city (k_cov>1): inflating sigma lowers emos_q_lcb for
        peaked bins → some rows that 'would_clear at k=1' no longer clear.
        The k_cov-anchored licensing count must be <= the k=1 optimistic count.
        """
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
        from score_emos_forward import _bin_prob_from_row, _robust_edge

        # Fixture: a batch of rows for an under-covered city (k_cov will be ~2.0)
        # Peaked bin, cost such that k=1 rescues but k=2 does not.
        # Settlement bins: mu=20, sigma=2, bin [19,19] (point bin, settlement interval [18.5,19.5))
        #   k=1: emos_q = bin_probability_settlement(20, 2, 19, 19)
        #              ≈ Φ(0.25) - Φ(-0.75) ≈ 0.1974 (mass in the point bin [18.5,19.5))
        #   With cost=0.10: edge≈0.1974-0.10-0.01=0.087 → clears at k=1
        #   k=2 sigma=4: Φ(0.125) - Φ(-0.375) ≈ 0.1463
        #              min(0.1974, 0.1463) = 0.1463, edge≈0.1463-0.10-0.01=0.036 → clears too
        # Use a narrower bin: bin [20,20] (point bin at mean), cost=0.18
        #   k=1: Φ(0.25) - Φ(-0.25) ≈ 0.1974, edge≈0.007 → barely clears
        #   k=2: Φ(0.125) - Φ(-0.125) ≈ 0.0997, edge≈0.0997-0.18-0.01 < 0 → does NOT clear
        from src.calibration.emos import bin_probability_settlement
        mu_c, sigma_c = 20.0, 2.0
        bin_low, bin_high = 20.0, 20.0  # point bin at mean
        cost = 0.18

        emos_q = bin_probability_settlement(mu_c, sigma_c, bin_low, bin_high)
        lcb_k1 = min(emos_q, bin_probability_settlement(mu_c, 1.0 * sigma_c, bin_low, bin_high))
        lcb_k2 = min(emos_q, bin_probability_settlement(mu_c, 2.0 * sigma_c, bin_low, bin_high))

        clears_k1 = _robust_edge(emos_q, lcb_k1, cost) > 0
        clears_k2 = _robust_edge(emos_q, lcb_k2, cost) > 0

        assert clears_k1, f"Fixture should clear at k=1: edge={_robust_edge(emos_q, lcb_k1, cost):.4f}"
        assert not clears_k2, (
            f"Fixture should NOT clear at k=2: edge={_robust_edge(emos_q, lcb_k2, cost):.4f}"
        )
        # This demonstrates the monotonicity: rescued count at k>1 <= rescued count at k=1
        # (a row that clears at k=1 may not clear at k_cov>1 → count shrinks or stays same)

    def test_bin_prob_from_row_missing_fields_returns_none(self):
        """Rows without mu_c/sigma_c (pre-CI-extension) return None gracefully."""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
        from score_emos_forward import _bin_prob_from_row

        row = {"city": "Chicago", "raw_q": 0.4, "bin_low": 18.0, "bin_high": 22.0}
        result = _bin_prob_from_row(row, k=1.5)
        assert result is None, f"Expected None for missing mu_c/sigma_c, got {result}"


# ---------------------------------------------------------------------------
# (e) Cross-module parity: compute_robust_edge vs live trade_score formula
# ---------------------------------------------------------------------------

class TestCrossModuleParityWithTradeScore:
    """compute_robust_edge must match the live _robust_trade_score_receipt edge_bound.

    This test calls BOTH the shadow helper AND the production formula and compares
    to within 1e-12.  It catches literal drift between modules.
    Authority: trade_score.py:68-71.
    """

    def _live_edge_bound(self, q_posterior, q_5pct, cost, penalty=0.01) -> float:
        """Call the live receipt and extract edge_bound directly."""
        from src.strategy.live_inference.trade_score import _robust_trade_score_receipt
        from src.contracts.execution_price import ExecutionPrice

        # Must pass assert_kelly_safe: fee_adjusted, fee_deducted=True, probability_units
        c = ExecutionPrice(
            value=cost,
            price_type="fee_adjusted",
            fee_deducted=True,
            currency="probability_units",
        )
        receipt = _robust_trade_score_receipt(
            trade_score_id="test",
            q_posterior=q_posterior,
            q_5pct=q_5pct,
            c_95pct=c,
            c_stress=c,
            p_fill_lcb=1.0,  # neutral; we only compare edge_bound
            penalty=penalty,
            stress_penalty=penalty,
        )
        return receipt.score  # = 1.0 * edge_bound since p_fill_lcb=1

    def _shadow_edge(self, q_posterior, q_5pct, cost, penalty=0.01) -> float:
        from src.calibration.emos_ci_shadow import compute_robust_edge
        return compute_robust_edge(q_posterior=q_posterior, q_5pct=q_5pct,
                                   cost=cost, penalty=penalty)

    def test_parity_positive_edge(self):
        """Positive edge: shadow == live to 1e-12."""
        live = self._live_edge_bound(q_posterior=0.70, q_5pct=0.65, cost=0.55)
        shadow = self._shadow_edge(q_posterior=0.70, q_5pct=0.65, cost=0.55)
        assert abs(live - shadow) < 1e-12, f"live={live}, shadow={shadow}"

    def test_parity_negative_edge(self):
        """Negative edge: both return the same negative value."""
        live = self._live_edge_bound(q_posterior=0.60, q_5pct=0.50, cost=0.55)
        shadow = self._shadow_edge(q_posterior=0.60, q_5pct=0.50, cost=0.55)
        assert abs(live - shadow) < 1e-12, f"live={live}, shadow={shadow}"

    def test_parity_lcb_binding(self):
        """LCB-binding case (q_5pct constrains): both agree."""
        live = self._live_edge_bound(q_posterior=0.80, q_5pct=0.59, cost=0.55)
        shadow = self._shadow_edge(q_posterior=0.80, q_5pct=0.59, cost=0.55)
        assert abs(live - shadow) < 1e-12, f"live={live}, shadow={shadow}"


# ---------------------------------------------------------------------------
# (f) forecast-consistency gate: stale rows excluded, consistent rows kept
# ---------------------------------------------------------------------------

class TestForecastConsistencyGate:
    """_build_stale_set excludes rows whose raw_mu_c deviates > STALE_MU_TOL_C
    from the causal snapshot mean, and keeps consistent rows.

    Uses an in-memory SQLite DB that mirrors the ensemble_snapshots schema.
    """

    @staticmethod
    def _make_forecasts_db(tmp_path, rows):
        """Create a temp zeus-forecasts.db with ensemble_snapshots rows.

        rows: list of dicts with keys:
            city, target_date, temperature_metric, available_at,
            lead_hours, members_json, members_unit
        """
        import json
        import sqlite3
        db_path = tmp_path / "zeus-forecasts.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE ensemble_snapshots (
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                available_at TEXT,
                lead_hours INTEGER,
                members_json TEXT,
                members_unit TEXT
            )
        """)
        for r in rows:
            conn.execute(
                "INSERT INTO ensemble_snapshots VALUES (?,?,?,?,?,?,?)",
                (r["city"], r["target_date"], r["temperature_metric"],
                 r["available_at"], r["lead_hours"],
                 r["members_json"], r["members_unit"]),
            )
        conn.commit()
        conn.close()
        return str(db_path)

    def test_stale_row_excluded(self, tmp_path):
        """Row with |raw_mu_c − causal_mean| > 1.0°C is excluded."""
        import json
        # Causal snapshot: 51 members all 82.4°F = 28.0°C mean
        members = [82.4] * 51
        db_path = self._make_forecasts_db(tmp_path, [{
            "city": "Chicago",
            "target_date": "2026-05-25",
            "temperature_metric": "high",
            "available_at": "2026-05-24T00:00:00+00:00",
            "lead_hours": 24,
            "members_json": json.dumps(members),
            "members_unit": "degF",
        }])
        # Ledger row with raw_mu_c=21.5°C → delta=6.5°C > 1.0°C → STALE
        row = {
            "city": "Chicago",
            "target_date": "2026-05-25",
            "ts": "2026-05-24T08:00:00+00:00",
            "raw_mu_c": 21.5,
            "metric": "high",
        }
        import scripts.score_emos_forward as scorer
        orig_forecasts = scorer.FORECASTS
        try:
            scorer.FORECASTS = db_path
            stale_ids, reasons = scorer._build_stale_set([row])
        finally:
            scorer.FORECASTS = orig_forecasts

        assert id(row) in stale_ids, "stale row must be in stale_ids"
        assert len(stale_ids) == 1

    def test_consistent_row_kept(self, tmp_path):
        """Row with |raw_mu_c − causal_mean| <= 1.0°C is NOT excluded."""
        import json
        # Causal snapshot: 51 members all 82.4°F = 28.0°C mean
        members = [82.4] * 51
        db_path = self._make_forecasts_db(tmp_path, [{
            "city": "Chicago",
            "target_date": "2026-05-25",
            "temperature_metric": "high",
            "available_at": "2026-05-24T00:00:00+00:00",
            "lead_hours": 24,
            "members_json": json.dumps(members),
            "members_unit": "degF",
        }])
        # Ledger row with raw_mu_c=28.3°C → delta=0.3°C < 1.0°C → CONSISTENT
        row = {
            "city": "Chicago",
            "target_date": "2026-05-25",
            "ts": "2026-05-24T08:00:00+00:00",
            "raw_mu_c": 28.3,
            "metric": "high",
        }
        import scripts.score_emos_forward as scorer
        orig_forecasts = scorer.FORECASTS
        try:
            scorer.FORECASTS = db_path
            stale_ids, reasons = scorer._build_stale_set([row])
        finally:
            scorer.FORECASTS = orig_forecasts

        assert id(row) not in stale_ids, "consistent row must NOT be in stale_ids"
        assert len(stale_ids) == 0

    def test_no_causal_snapshot_excludes_row(self, tmp_path):
        """Row with no causally-available snapshot (available_at > ts) is excluded."""
        import json
        # Snapshot only available AFTER decision time
        members = [82.4] * 51
        db_path = self._make_forecasts_db(tmp_path, [{
            "city": "Chicago",
            "target_date": "2026-05-25",
            "temperature_metric": "high",
            "available_at": "2026-05-25T12:00:00+00:00",  # after ts
            "lead_hours": 6,
            "members_json": json.dumps(members),
            "members_unit": "degF",
        }])
        row = {
            "city": "Chicago",
            "target_date": "2026-05-25",
            "ts": "2026-05-24T08:00:00+00:00",  # before snapshot
            "raw_mu_c": 28.0,
            "metric": "high",
        }
        import scripts.score_emos_forward as scorer
        orig_forecasts = scorer.FORECASTS
        try:
            scorer.FORECASTS = db_path
            stale_ids, _ = scorer._build_stale_set([row])
        finally:
            scorer.FORECASTS = orig_forecasts

        assert id(row) in stale_ids, "row with no causal snapshot must be excluded"

    def test_mixed_stale_and_consistent(self, tmp_path):
        """Two rows: one stale, one consistent — only stale is excluded."""
        import json
        members = [82.4] * 51  # 28.0°C mean
        db_path = self._make_forecasts_db(tmp_path, [{
            "city": "Chicago",
            "target_date": "2026-05-25",
            "temperature_metric": "high",
            "available_at": "2026-05-24T00:00:00+00:00",
            "lead_hours": 24,
            "members_json": json.dumps(members),
            "members_unit": "degF",
        }])
        stale_row = {
            "city": "Chicago", "target_date": "2026-05-25",
            "ts": "2026-05-24T08:00:00+00:00", "raw_mu_c": 21.5, "metric": "high",
        }
        good_row = {
            "city": "Chicago", "target_date": "2026-05-25",
            "ts": "2026-05-24T08:00:00+00:00", "raw_mu_c": 27.8, "metric": "high",
        }
        import scripts.score_emos_forward as scorer
        orig_forecasts = scorer.FORECASTS
        try:
            scorer.FORECASTS = db_path
            stale_ids, _ = scorer._build_stale_set([stale_row, good_row])
        finally:
            scorer.FORECASTS = orig_forecasts

        assert id(stale_row) in stale_ids
        assert id(good_row) not in stale_ids
        assert len(stale_ids) == 1
