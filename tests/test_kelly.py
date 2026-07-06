"""Tests for Kelly sizing and risk limits."""

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.strategy.kelly import kelly_size, dynamic_kelly_mult, strategy_kelly_multiplier
from src.strategy.risk_limits import RiskLimits, check_position_allowed


def _ep(value: float) -> ExecutionPrice:
    """Build a valid Kelly-safe ExecutionPrice for test use."""
    return ExecutionPrice(
        value=value,
        price_type="fee_adjusted",
        fee_deducted=True,
        currency="probability_units",
    )


class TestKellySize:
    def test_positive_edge(self):
        """p_posterior > entry → positive size."""
        size = kelly_size(0.60, _ep(0.40), 100.0, kelly_mult=0.25)
        assert size > 0

    def test_no_edge(self):
        """p_posterior <= entry → zero."""
        assert kelly_size(0.40, _ep(0.50), 100.0) == 0.0
        assert kelly_size(0.50, _ep(0.50), 100.0) == 0.0

    def test_formula_correctness(self):
        """f* = (0.6 - 0.4) / (1 - 0.4) = 0.333. Size = 0.333 × 0.25 × 100 = 8.33"""
        size = kelly_size(0.60, _ep(0.40), 100.0, kelly_mult=0.25)
        expected = (0.60 - 0.40) / (1.0 - 0.40) * 0.25 * 100.0
        assert size == pytest.approx(expected)

    def test_entry_at_one(self):
        """entry_price = 1.0 → kelly_size returns 0.0 (price >= 1.0 guard)."""
        # value=1.0 is valid at construction (boundary of probability_units range)
        # kelly_size short-circuits to 0.0 when price_value >= 1.0
        assert kelly_size(0.99, _ep(1.0), 100.0) == 0.0

    def test_small_edge_small_size(self):
        """Small edge → small position."""
        size = kelly_size(0.11, _ep(0.10), 100.0, kelly_mult=0.25)
        assert 0 < size < 5  # Small size for small edge

    def test_no_per_trade_safety_cap_parameter(self):
        """Antibody for the 2026-05-04 cap removal.

        The per-trade hard ceiling was deleted from
        ``src/strategy/kelly.py::kelly_size``. Per-cycle exposure
        discipline now lives in posture / RiskGuard / max-exposure
        gates only (see ``config/settings.json::_bankroll_doctrine_2026_05_04``).

        This test fails if anyone re-introduces a ``safety_cap_usd``
        parameter without operator authorization.
        """

        import inspect

        sig = inspect.signature(kelly_size)
        assert "safety_cap_usd" not in sig.parameters, (
            "kelly_size must NOT accept safety_cap_usd; the per-trade cap "
            "was removed 2026-05-04. Re-introducing the parameter would "
            "resurrect dead code that masks the bankroll truth chain."
        )


class TestKellyMultiplierRetuneJuly2026:
    """Pins the 2026-07-06 operator retune: kelly_multiplier 0.02 -> 0.03125 (1/32).

    Regression for the throughput increase past the proving phase (see
    ``config/settings.json::sizing._kelly_multiplier_note`` 2026-07-06 entry).
    Confirms (a) the live config value loads at the new rung and stays inside
    the provenance cascade_bound [0.01, 1.0] (``config/provenance_registry.yaml``
    ::kelly_mult) and the boot-guard correlated ceiling (kelly_multiplier <=
    max_correlated_pct); and (b) the live stake formula
    (kelly_size == bankroll x kelly_multiplier x f*) lands the expected stake
    at a representative f*=0.40.
    """

    def test_live_config_kelly_multiplier_is_1_over_32_within_bounds(self):
        import json
        from pathlib import Path

        settings_path = Path(__file__).resolve().parents[1] / "config/settings.json"
        cfg = json.loads(settings_path.read_text())
        mult = cfg["sizing"]["kelly_multiplier"]

        assert mult == pytest.approx(1.0 / 32.0)
        assert mult == pytest.approx(0.03125)

        # cascade_bound [0.01, 1.0] -- config/provenance_registry.yaml::kelly_mult
        assert 0.01 <= mult <= 1.0

        # boot-guard ceiling -- src/main.py::assert_kelly_multiplier_within_correlated_ceiling
        max_corr = cfg["sizing"]["max_correlated_pct"]
        assert mult <= max_corr

    def test_live_formula_stake_at_bankroll_1269_f_star_040(self):
        """f* = (0.64 - 0.40) / (1 - 0.40) = 0.40 exactly.

        stake = bankroll x kelly_multiplier x f* = 1269.0 x 0.03125 x 0.40
              = 15.8625
        """
        bankroll = 1269.0
        mult = 0.03125
        size = kelly_size(0.64, _ep(0.40), bankroll, kelly_mult=mult)

        assert size == pytest.approx(bankroll * mult * 0.40, rel=1e-9)
        assert size == pytest.approx(15.86, abs=0.01)


class TestDynamicKellyMult:
    def test_base_unchanged(self):
        """Default params → returns base."""
        m = dynamic_kelly_mult(base=0.25)
        assert m == 0.25

    def test_wide_ci_reduces(self):
        """ci_width > 0.15 → aggressive reduction."""
        m = dynamic_kelly_mult(base=0.25, ci_width=0.20)
        assert m < 0.25 * 0.7 * 0.5 + 0.01

    def test_long_lead_reduces(self):
        m_short = dynamic_kelly_mult(base=0.25, lead_days=1.0)
        m_long = dynamic_kelly_mult(base=0.25, lead_days=6.0)
        assert m_long < m_short

    def test_heat_reduces(self):
        # rolling_win_rate_20/drawdown_pct/max_drawdown deleted Wave 3 (zero live callers).
        # portfolio_heat is the surviving concentration haircut.
        m_cool = dynamic_kelly_mult(base=0.25, portfolio_heat=0.10)
        m_hot = dynamic_kelly_mult(base=0.25, portfolio_heat=0.80)
        assert m_hot < m_cool

    def test_nan_input_floors_at_minimum(self):
        """NaN from upstream must raise, not produce a floor."""
        with pytest.raises(ValueError, match="NaN"):
            dynamic_kelly_mult(base=float("nan"))

    def test_strategy_multiplier_table(self):
        expected = {
            "settlement_capture": 1.0,
            "center_buy": 1.0,
            "opening_inertia": 0.5,
            "shoulder_sell": 0.0,
            "shoulder_buy": 0.0,
            "center_sell": 0.0,
            "unknown": 0.0,
        }
        for strategy_key, multiplier in expected.items():
            assert strategy_kelly_multiplier(strategy_key) == multiplier

    def test_dynamic_kelly_applies_strategy_multiplier(self):
        assert dynamic_kelly_mult(base=0.25, strategy_key="center_buy") == pytest.approx(0.25)
        assert dynamic_kelly_mult(base=0.25, strategy_key="opening_inertia") == pytest.approx(0.125)
        assert dynamic_kelly_mult(base=0.25, strategy_key="shoulder_sell") == 0.0
        assert dynamic_kelly_mult(base=0.25, strategy_key="center_sell") == 0.0
        assert dynamic_kelly_mult(base=0.25, strategy_key="unknown") == 0.0


class TestRiskLimits:
    def test_allowed(self):
        ok, reason = check_position_allowed(
            size_usd=5.0, bankroll=100.0,
            city="NYC",
            current_city_exposure=0.0,
            current_portfolio_heat=0.0, limits=RiskLimits(),
        )
        assert ok is True

    def test_below_minimum(self):
        ok, reason = check_position_allowed(
            size_usd=0.50, bankroll=100.0,
            city="NYC",
            current_city_exposure=0.0,
            current_portfolio_heat=0.0, limits=RiskLimits(),
        )
        assert ok is False
        assert "minimum" in reason

    def test_single_position_pct_is_not_hard_block(self):
        ok, reason = check_position_allowed(
            size_usd=15.0, bankroll=100.0,
            city="NYC",
            current_city_exposure=0.0,
            current_portfolio_heat=0.0, limits=RiskLimits(),
        )
        assert ok is True
        assert reason == "OK"

    def test_portfolio_heat_is_not_hard_block(self):
        ok, reason = check_position_allowed(
            size_usd=5.0, bankroll=100.0,
            city="NYC",
            current_city_exposure=0.0,
            current_portfolio_heat=0.48, limits=RiskLimits(),
        )
        assert ok is True
        assert reason == "OK"

    def test_city_exposure_is_not_hard_block(self):
        ok, reason = check_position_allowed(
            size_usd=5.0, bankroll=100.0,
            city="NYC",
            current_city_exposure=0.18,
            current_portfolio_heat=0.0, limits=RiskLimits(),
        )
        assert ok is True
        assert reason == "OK"
