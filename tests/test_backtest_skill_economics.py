# Lifecycle: created=2026-04-28; last_reviewed=2026-05-01; last_reused=2026-05-01
# Created: 2026-04-28
# Last reused/audited: 2026-05-01
# Authority basis: docs/operations/task_2026-04-27_backtest_first_principles_review/01_backtest_upgrade_design.md
# Purpose: Guard backtest economics tombstone behavior and report/replay cohort gates.
# Reuse: Run after backtest purpose, economics readiness, or report/replay cohort changes.
"""Antibodies for S4 (economics tombstone) and S2 (skill purpose enforcement).

Verifies that:
- ECONOMICS purpose refuses to run until upstream data unblocks (tombstone).
- SKILL orchestrator rejects mismatched PurposeContracts.
- SKILL orchestrator does not leak ECONOMICS-shaped output fields.
"""

import pytest
import sqlite3

from src.backtest.economics import check_economics_readiness, run_economics
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
from scripts.equity_curve import _single_exit_economics_cohort
from scripts.profit_validation_replay import (
    CORRECTED_ECONOMICS_COHORT,
    LEGACY_DIAGNOSTIC_COHORT,
    require_single_exit_economics_cohort,
)


def _corrected_exit_row(**overrides):
    row = {
        "pricing_semantics_version": CORRECTED_ECONOMICS_COHORT,
        "corrected_executable_economics_eligible": True,
        "entry_economics_authority": "avg_fill_price",
        "fill_authority": "venue_confirmed_full",
        "shares_filled": 10.0,
        "filled_cost_basis_usd": 5.0,
        "entry_cost_basis_hash": "a" * 64,
        "execution_cost_basis_version": "cost_basis:test",
    }
    row.update(overrides)
    return row


def test_profit_replay_hard_fails_mixed_pricing_semantics_cohorts():
    legacy = {"entry_price": 0.5, "size_usd": 5.0}
    corrected = _corrected_exit_row()

    with pytest.raises(ValueError, match="mixed pricing semantics cohorts"):
        require_single_exit_economics_cohort([legacy, corrected])


def test_profit_replay_rejects_incomplete_corrected_economics_row():
    incomplete = _corrected_exit_row(filled_cost_basis_usd=0.0)

    with pytest.raises(ValueError, match="missing fill/cost-basis authority"):
        require_single_exit_economics_cohort([incomplete])


def test_equity_curve_reports_single_corrected_cohort():
    cohort, counts = _single_exit_economics_cohort([
        _corrected_exit_row(),
        _corrected_exit_row(shares_filled=5.0, filled_cost_basis_usd=2.5),
    ])

    assert cohort == CORRECTED_ECONOMICS_COHORT
    assert counts == {CORRECTED_ECONOMICS_COHORT: 2}
    assert require_single_exit_economics_cohort([]) == LEGACY_DIAGNOSTIC_COHORT


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


def test_economics_readiness_reports_missing_connection():
    readiness = check_economics_readiness(None)

    assert readiness.ready is False
    assert readiness.blockers == ("missing_connection",)
    assert readiness.table_counts == ()


def test_economics_readiness_reports_missing_and_empty_substrate():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE market_events_v2 (id INTEGER PRIMARY KEY, outcome TEXT)")
    conn.execute("CREATE TABLE venue_trade_facts (state TEXT)")
    conn.execute("INSERT INTO venue_trade_facts (state) VALUES ('MATCHED')")

    readiness = check_economics_readiness(conn)
    conn.close()

    assert readiness.ready is False
    assert readiness.count_for("market_events_v2") == 0
    assert readiness.count_for("market_price_history") is None
    assert "empty_table:market_events_v2" in readiness.blockers
    assert "missing_table:market_price_history" in readiness.blockers
    assert "no_confirmed_venue_trade_facts" in readiness.blockers
    assert "economics_engine_not_implemented" in readiness.blockers


def test_economics_readiness_requires_neg_risk_snapshot_fact():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE market_events_v2 (id INTEGER PRIMARY KEY, outcome TEXT)")
    conn.execute("CREATE TABLE market_price_history (id INTEGER PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE executable_market_snapshots ("
        "min_tick_size TEXT, min_order_size TEXT, fee_details_json TEXT, raw_orderbook_hash TEXT)"
    )
    conn.execute("CREATE TABLE venue_trade_facts (state TEXT)")
    conn.execute("CREATE TABLE position_lots (state TEXT)")
    conn.execute("CREATE TABLE probability_trace_fact (decision_snapshot_id TEXT)")
    conn.execute("CREATE TABLE trade_decisions (decision_snapshot_id TEXT)")
    conn.execute("CREATE TABLE selection_family_fact (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE selection_hypothesis_fact (selected_post_fdr INTEGER)")
    conn.execute("CREATE TABLE settlements_v2 (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE outcome_fact (decision_snapshot_id TEXT, outcome INTEGER)")
    conn.execute("INSERT INTO market_events_v2 (outcome) VALUES ('YES')")
    conn.execute("INSERT INTO market_price_history DEFAULT VALUES")
    conn.execute(
        "INSERT INTO executable_market_snapshots "
        "(min_tick_size, min_order_size, fee_details_json, raw_orderbook_hash) "
        "VALUES ('0.01', '5', '{}', 'hash-orderbook')"
    )
    conn.execute("INSERT INTO venue_trade_facts (state) VALUES ('CONFIRMED')")
    conn.execute("INSERT INTO position_lots (state) VALUES ('CONFIRMED_EXPOSURE')")
    conn.execute("INSERT INTO probability_trace_fact (decision_snapshot_id) VALUES ('snap-1')")
    conn.execute("INSERT INTO trade_decisions (decision_snapshot_id) VALUES ('snap-1')")
    conn.execute("INSERT INTO selection_family_fact DEFAULT VALUES")
    conn.execute("INSERT INTO selection_hypothesis_fact (selected_post_fdr) VALUES (1)")
    conn.execute("INSERT INTO settlements_v2 DEFAULT VALUES")
    conn.execute("INSERT INTO outcome_fact (decision_snapshot_id, outcome) VALUES ('snap-1', 1)")

    readiness = check_economics_readiness(conn)
    conn.close()

    assert readiness.ready is False
    assert "invalid_schema:executable_market_snapshots.fee_tick_min_order_neg_risk_orderbook" in readiness.blockers


def test_economics_readiness_rejects_gamma_price_only_history():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE market_price_history (market_slug TEXT, token_id TEXT, price REAL, recorded_at TEXT)")
    conn.execute(
        "INSERT INTO market_price_history (market_slug, token_id, price, recorded_at) "
        "VALUES ('market-slug', 'yes-token', 0.42, '2026-04-30T12:00:00+00:00')"
    )

    readiness = check_economics_readiness(conn)
    conn.close()

    assert readiness.ready is False
    assert readiness.count_for("market_price_history") == 1
    assert "market_price_history_lacks_full_linkage_contract" in readiness.blockers


def test_economics_readiness_full_substrate_still_blocks_until_engine_implemented():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE market_events_v2 (id INTEGER PRIMARY KEY, outcome TEXT)")
    conn.execute(
        "CREATE TABLE market_price_history ("
        "id INTEGER PRIMARY KEY, market_price_linkage TEXT, source TEXT, "
        "best_bid REAL, best_ask REAL, raw_orderbook_hash TEXT)"
    )
    conn.execute(
        "CREATE TABLE executable_market_snapshots ("
        "min_tick_size TEXT, min_order_size TEXT, fee_details_json TEXT, neg_risk INTEGER, raw_orderbook_hash TEXT)"
    )
    conn.execute("CREATE TABLE venue_trade_facts (state TEXT)")
    conn.execute("CREATE TABLE position_lots (state TEXT)")
    conn.execute("CREATE TABLE probability_trace_fact (decision_snapshot_id TEXT)")
    conn.execute("CREATE TABLE trade_decisions (decision_snapshot_id TEXT)")
    conn.execute("CREATE TABLE selection_family_fact (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE selection_hypothesis_fact (selected_post_fdr INTEGER)")
    conn.execute("CREATE TABLE settlements_v2 (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE outcome_fact (decision_snapshot_id TEXT, outcome INTEGER)")
    conn.execute("INSERT INTO market_events_v2 (outcome) VALUES ('YES')")
    conn.execute(
        "INSERT INTO market_price_history "
        "(market_price_linkage, source, best_bid, best_ask, raw_orderbook_hash) "
        "VALUES ('full', 'CLOB_WS_MARKET', 0.42, 0.44, 'hash-orderbook')"
    )
    conn.execute(
        "INSERT INTO executable_market_snapshots "
        "(min_tick_size, min_order_size, fee_details_json, neg_risk, raw_orderbook_hash) "
        "VALUES ('0.01', '5', '{}', 0, 'hash-orderbook')"
    )
    conn.execute("INSERT INTO venue_trade_facts (state) VALUES ('CONFIRMED')")
    conn.execute("INSERT INTO position_lots (state) VALUES ('CONFIRMED_EXPOSURE')")
    conn.execute("INSERT INTO probability_trace_fact (decision_snapshot_id) VALUES ('snap-1')")
    conn.execute("INSERT INTO trade_decisions (decision_snapshot_id) VALUES ('snap-1')")
    conn.execute("INSERT INTO selection_family_fact DEFAULT VALUES")
    conn.execute("INSERT INTO selection_hypothesis_fact (selected_post_fdr) VALUES (1)")
    conn.execute("INSERT INTO settlements_v2 DEFAULT VALUES")
    conn.execute("INSERT INTO outcome_fact (decision_snapshot_id, outcome) VALUES ('snap-1', 1)")

    readiness = check_economics_readiness(conn)

    assert readiness.ready is False
    assert readiness.blockers == ("economics_engine_not_implemented",)
    with pytest.raises(PurposeContractViolation) as excinfo:
        run_economics(conn=conn)
    conn.close()
    assert "ECONOMICS purpose is tombstoned" in str(excinfo.value)
    assert "economics_engine_not_implemented" in str(excinfo.value)


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
