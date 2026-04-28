# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-27_backtest_first_principles_review/01_backtest_upgrade_design.md
"""Antibodies for S4 (economics tombstone) and S2 (skill purpose enforcement).

Verifies that:
- ECONOMICS purpose refuses to run until upstream data unblocks (tombstone).
- SKILL orchestrator rejects mismatched PurposeContracts.
- SKILL orchestrator does not leak ECONOMICS-shaped output fields.
"""

import pytest

from src.backtest.economics import run_economics
from src.backtest.purpose import (
    BacktestPurpose,
    DIAGNOSTIC_CONTRACT,
    ECONOMICS_CONTRACT,
    PurposeContract,
    PurposeContractViolation,
    SKILL_CONTRACT,
    SKILL_PARITY,
)
from src.backtest.skill import run_skill, _economics_fields_in_limitations


def test_economics_tombstone_raises():
    with pytest.raises(PurposeContractViolation) as excinfo:
        run_economics("2026-04-01", "2026-04-27")
    assert "ECONOMICS purpose is tombstoned" in str(excinfo.value)
    assert "market_events_v2" in str(excinfo.value)
    assert "02_blocker_handling_plan.md" in str(excinfo.value)


def test_economics_tombstone_ignores_args():
    """Even with arbitrary kwargs, the tombstone refuses."""
    with pytest.raises(PurposeContractViolation):
        run_economics("2026-04-01", "2026-04-27", contract=ECONOMICS_CONTRACT)


def test_run_skill_rejects_economics_contract():
    with pytest.raises(PurposeContractViolation) as excinfo:
        run_skill("2026-04-01", "2026-04-27", contract=ECONOMICS_CONTRACT)
    assert "purpose=SKILL" in str(excinfo.value)


def test_run_skill_rejects_diagnostic_contract():
    with pytest.raises(PurposeContractViolation) as excinfo:
        run_skill("2026-04-01", "2026-04-27", contract=DIAGNOSTIC_CONTRACT)
    assert "purpose=SKILL" in str(excinfo.value)


def test_run_skill_rejects_promotion_authority_skill_contract():
    """SKILL with promotion_authority=True is structurally invalid."""
    bad = PurposeContract(
        purpose=BacktestPurpose.SKILL,
        permitted_outputs=SKILL_CONTRACT.permitted_outputs,
        parity=SKILL_PARITY,
        promotion_authority=True,
    )
    with pytest.raises(PurposeContractViolation) as excinfo:
        run_skill("2026-04-01", "2026-04-27", contract=bad)
    assert "promotion_authority" in str(excinfo.value)


def test_economics_field_leak_detector_clean():
    """A limitations dict with only declarative absence flags is NOT a leak."""
    clean = {
        "pnl_available": False,
        "pnl_unavailable_reason": "no_market_price_linkage",
        "authority_scope": "diagnostic_non_promotion",
        "uses_stored_winning_bin_as_truth": False,
    }
    assert _economics_fields_in_limitations(clean) == set()


def test_economics_field_leak_detector_catches_realized_pnl():
    """If a SKILL summary somehow stamps `realized_pnl` into limitations,
    the detector must catch it."""
    leaked = {
        "pnl_available": False,
        "realized_pnl": 123.45,  # would be illegal in SKILL
    }
    assert _economics_fields_in_limitations(leaked) == {"realized_pnl"}


def test_economics_field_leak_detector_catches_sharpe_max_drawdown():
    leaked = {
        "sharpe": 1.2,
        "max_drawdown": -50.0,
    }
    found = _economics_fields_in_limitations(leaked)
    assert "sharpe" in found
    assert "max_drawdown" in found
