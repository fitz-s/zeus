# Lifecycle: created=2026-07-04; last_reviewed=2026-07-15; last_reused=2026-07-15
# Purpose: Regression tests for the chain-mirror reconciler.
# Reuse: Run when position_current chain-mirror classification, the
#   scripts/reconcile_chain_mirror.py CLI, or the market-rule state model
#   for active/settled positions change.
# Authority basis: operator directive 2026-07-04 (root AGENTS.md §2
#   reconciliation order Chain > Chronicler > Portfolio).
from __future__ import annotations

import json
import sqlite3

import pytest

from src.contracts.semantic_types import ChainState
from src.state.chain_mirror_reconciler import (
    CLOSED_EXITED,
    CLOSED_REDEEMED,
    CLOSED_WORTHLESS,
    CONSISTENT,
    FOREIGN,
    MISSING_LOCAL_ROW,
    REDEEMABLE,
    REVIEW_OPEN_ABSENT,
    SIZE_CORRECTED,
    ChainPositionFact,
    LocalPositionRow,
    SettlementFact,
    classify_chain_only_asset,
    classify_local_position,
    grade_bin,
    load_chain_positions_by_asset,
    reconcile,
)
from src.state.db import init_schema, init_schema_trade_only


# -----------------------------------------------------------------------------
# ChainState enum round-trip (Position.__post_init__ coercion contract)
# -----------------------------------------------------------------------------


def test_closed_redeemed_and_worthless_are_registered_chain_states():
    assert ChainState("closed_redeemed") is ChainState.CLOSED_REDEEMED
    assert ChainState("closed_worthless") is ChainState.CLOSED_WORTHLESS


# -----------------------------------------------------------------------------
# Pure grading
# -----------------------------------------------------------------------------


def test_grade_bin_buy_yes_win():
    assert grade_bin("32°C", "buy_yes", "32°C") is True


def test_grade_bin_buy_yes_loss():
    assert grade_bin("32°C", "buy_yes", "31°C") is False


def test_grade_bin_buy_no_win_when_bin_did_not_happen():
    assert grade_bin("32°C", "buy_no", "31°C") is True


def test_grade_bin_buy_no_loss_when_bin_happened():
    assert grade_bin("32°C", "buy_no", "32°C") is False


def test_grade_bin_ungradeable_returns_none():
    assert grade_bin("not-a-temperature", "buy_yes", "also-not-one") is None


# -----------------------------------------------------------------------------
# Pure classification: local rows
# -----------------------------------------------------------------------------


def _row(**overrides) -> LocalPositionRow:
    base = dict(
        position_id="pos-1",
        phase="active",
        chain_state="synced",
        city="manila",
        target_date="2026-07-04",
        temperature_metric="high",
        bin_label="33°C",
        direction="buy_yes",
        token_id="tok-yes-1",
        no_token_id="tok-no-1",
        condition_id="cond-1",
        chain_shares=11.1,
        shares=11.1,
        strategy_key="edli",
    )
    base.update(overrides)
    return LocalPositionRow(**base)


def _settlement(**overrides) -> SettlementFact:
    base = dict(winning_bin="33°C", authority="VERIFIED", settlement_value=1.0)
    base.update(overrides)
    return SettlementFact(**base)


def test_absent_token_resolved_winner_classifies_closed_redeemed():
    row = _row(direction="buy_yes", bin_label="33°C")
    settlement = {("manila", "2026-07-04", "high"): _settlement(winning_bin="33°C")}
    finding = classify_local_position(row, chain_by_asset={}, settlement_by_key=settlement)
    assert finding.classification == CLOSED_REDEEMED
    assert finding.writes is True
    assert finding.details["won"] is True


def test_absent_token_resolved_loser_classifies_closed_worthless():
    row = _row(direction="buy_yes", bin_label="33°C")
    settlement = {("manila", "2026-07-04", "high"): _settlement(winning_bin="30°C")}
    finding = classify_local_position(row, chain_by_asset={}, settlement_by_key=settlement)
    assert finding.classification == CLOSED_WORTHLESS
    assert finding.writes is True
    assert finding.details["won"] is False


def test_absent_token_unresolved_open_phase_is_review_finding_no_write():
    """The Manila ce105753-e91 case: day0_window, chain-absent NO token, market not resolved."""
    row = _row(phase="day0_window", direction="buy_no", bin_label="33°C")
    finding = classify_local_position(row, chain_by_asset={}, settlement_by_key={})
    assert finding.classification == REVIEW_OPEN_ABSENT
    assert finding.writes is False


def test_absent_token_unresolved_already_closed_phase_is_consistent():
    row = _row(phase="economically_closed", direction="buy_yes")
    finding = classify_local_position(row, chain_by_asset={}, settlement_by_key={})
    assert finding.classification == CONSISTENT
    assert finding.writes is False


def test_size_mismatch_classifies_size_corrected():
    """The Dallas case: local chain_shares=1184.57 vs chain size=74.55."""
    row = _row(direction="buy_yes", token_id="tok-dallas", chain_shares=1184.57, shares=1184.57)
    chain = {"tok-dallas": ChainPositionFact(
        token_id="tok-dallas", condition_id="cond-1", size=74.55,
        redeemable=True, current_value=0.0, side="Yes",
    )}
    finding = classify_local_position(row, chain_by_asset=chain, settlement_by_key={})
    assert finding.classification == SIZE_CORRECTED
    assert finding.writes is True
    assert finding.details["chain_size"] == 74.55
    assert finding.details["local_shares"] == 1184.57


def test_matching_size_unresolved_market_is_consistent():
    row = _row(direction="buy_yes", token_id="tok-open", chain_shares=10.0, shares=10.0)
    chain = {"tok-open": ChainPositionFact(
        token_id="tok-open", condition_id="cond-1", size=10.0,
        redeemable=False, current_value=5.0, side="Yes",
    )}
    finding = classify_local_position(row, chain_by_asset=chain, settlement_by_key={})
    assert finding.classification == CONSISTENT
    assert finding.writes is False


def test_already_voided_duplicate_rows_on_same_token_get_no_size_correction():
    """Regression (found running the real dry-run against the live DB,
    2026-07-04): multiple already-voided position_current rows can share the
    SAME physical token (a pre-existing local duplicate-row condition — see
    src/state/position_duplicate_consolidator.py). Each must classify
    CONSISTENT / writes=False, never SIZE_CORRECTED — writing the chain's
    single wallet balance onto every duplicate row would be a multi-row
    over-attribution of one balance, the exact counting-error class this
    reconciler exists to eliminate.
    """
    chain = {"tok-seoul": ChainPositionFact(
        token_id="tok-seoul", condition_id="cond-seoul", size=184.13,
        redeemable=True, current_value=0.0, side="Yes",
    )}
    for position_id in ("dup-1", "dup-2", "dup-3", "dup-4"):
        row = _row(
            position_id=position_id, phase="voided", direction="buy_yes",
            token_id="tok-seoul", chain_shares=None, shares=0.0,
        )
        finding = classify_local_position(row, chain_by_asset=chain, settlement_by_key={})
        assert finding.classification == CONSISTENT, position_id
        assert finding.writes is False, position_id


def test_already_settled_row_with_size_mismatch_gets_no_correction():
    row = _row(phase="settled", direction="buy_yes", token_id="tok-closed", chain_shares=5.0, shares=5.0)
    chain = {"tok-closed": ChainPositionFact(
        token_id="tok-closed", condition_id="cond-1", size=999.0,
        redeemable=True, current_value=0.0, side="Yes",
    )}
    finding = classify_local_position(row, chain_by_asset=chain, settlement_by_key={})
    assert finding.classification == CONSISTENT
    assert finding.writes is False


def test_size_matches_market_resolved_winner_still_held_is_redeemable():
    row = _row(direction="buy_yes", token_id="tok-win", chain_shares=5.0, shares=5.0, bin_label="33°C")
    chain = {"tok-win": ChainPositionFact(
        token_id="tok-win", condition_id="cond-1", size=5.0,
        redeemable=True, current_value=2.0, side="Yes",
    )}
    settlement = {("manila", "2026-07-04", "high"): _settlement(winning_bin="33°C")}
    finding = classify_local_position(row, chain_by_asset=chain, settlement_by_key=settlement)
    assert finding.classification == REDEEMABLE
    assert finding.writes is True
    assert finding.details["chain_absent"] is False


def test_size_matches_market_resolved_loser_still_held_closes_worthless():
    row = _row(direction="buy_yes", token_id="tok-lose", chain_shares=5.0, shares=5.0, bin_label="33°C")
    chain = {"tok-lose": ChainPositionFact(
        token_id="tok-lose", condition_id="cond-1", size=5.0,
        redeemable=True, current_value=0.0, side="Yes",
    )}
    settlement = {("manila", "2026-07-04", "high"): _settlement(winning_bin="30°C")}
    finding = classify_local_position(row, chain_by_asset=chain, settlement_by_key=settlement)
    assert finding.classification == CLOSED_WORTHLESS
    assert finding.writes is True


# -----------------------------------------------------------------------------
# Pure classification: chain-only assets (no local row)
# -----------------------------------------------------------------------------


def test_chain_only_asset_with_zeus_origin_is_missing_local_row_finding():
    fact = ChainPositionFact(
        token_id="tok-orphan", condition_id="cond-2", size=3.0,
        redeemable=True, current_value=0.0, side="Yes",
    )
    finding = classify_chain_only_asset("tok-orphan", fact, matched_local_assets=set(), is_zeus_origin=True)
    assert finding is not None
    assert finding.classification == MISSING_LOCAL_ROW
    assert finding.writes is False


def test_chain_only_asset_without_zeus_origin_is_foreign_report_only():
    fact = ChainPositionFact(
        token_id="tok-foreign", condition_id="cond-3", size=48.52,
        redeemable=False, current_value=23.05, side="Yes", title="Will Anthropic's public ticker be $ANTH?",
    )
    finding = classify_chain_only_asset("tok-foreign", fact, matched_local_assets=set(), is_zeus_origin=False)
    assert finding is not None
    assert finding.classification == FOREIGN
    assert finding.writes is False


def test_chain_only_asset_already_matched_returns_none():
    fact = ChainPositionFact(
        token_id="tok-matched", condition_id="cond-4", size=1.0,
        redeemable=True, current_value=0.0, side="Yes",
    )
    finding = classify_chain_only_asset("tok-matched", fact, matched_local_assets={"tok-matched"}, is_zeus_origin=True)
    assert finding is None


# -----------------------------------------------------------------------------
# load_chain_positions_by_asset
# -----------------------------------------------------------------------------


def test_load_chain_positions_by_asset_keys_on_token_id():
    raw = [
        {"token_id": "a1", "condition_id": "c1", "size": 10.0, "redeemable": True,
         "current_value": 0.0, "side": "Yes", "title": "t1"},
        {"token_id": "", "condition_id": "c2", "size": 1.0, "redeemable": True,
         "current_value": 0.0, "side": "No", "title": "skip-me-no-token"},
    ]
    out = load_chain_positions_by_asset(raw)
    assert set(out.keys()) == {"a1"}
    assert out["a1"].size == 10.0


# -----------------------------------------------------------------------------
# End-to-end DB tests: real position_current + position_events via a real
# in-memory trade schema (init_schema + init_schema_trade_only, matching
# tests/test_executable_market_snapshot.py's fixture pattern).
# -----------------------------------------------------------------------------


@pytest.fixture
def trades_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_trade_only(conn)
    yield conn
    conn.close()


@pytest.fixture
def forecasts_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            winning_bin TEXT, authority TEXT, settlement_value REAL,
            settlement_source TEXT, market_slug TEXT
        )
        """
    )
    yield conn
    conn.close()


def _insert_position_current(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    phase: str = "active",
    chain_state: str = "synced",
    city: str = "manila",
    target_date: str = "2026-07-04",
    temperature_metric: str = "high",
    bin_label: str = "33°C",
    direction: str = "buy_yes",
    token_id: str = "",
    no_token_id: str = "",
    condition_id: str = "cond-1",
    chain_shares: float | None = None,
    shares: float | None = None,
    cost_basis_usd: float | None = None,
    strategy_key: str = "edli",
    updated_at: str = "2026-07-04T00:00:00+00:00",
) -> None:
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, city, target_date, bin_label,
            direction, chain_state, token_id, no_token_id, condition_id,
            chain_shares, shares, cost_basis_usd, strategy_key, updated_at, temperature_metric
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            position_id, phase, position_id, city, target_date, bin_label,
            direction, chain_state, token_id, no_token_id, condition_id,
            chain_shares, shares, cost_basis_usd, strategy_key, updated_at, temperature_metric,
        ),
    )
    conn.commit()


def _insert_settlement(conn: sqlite3.Connection, **overrides) -> None:
    base = dict(
        city="manila", target_date="2026-07-04", temperature_metric="high",
        winning_bin="33°C", authority="VERIFIED", settlement_value=1.0,
        settlement_source="test", market_slug="manila-2026-07-04",
    )
    base.update(overrides)
    conn.execute(
        """
        INSERT INTO settlement_outcomes (
            city, target_date, temperature_metric, winning_bin, authority,
            settlement_value, settlement_source, market_slug
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(base[k] for k in (
            "city", "target_date", "temperature_metric", "winning_bin",
            "authority", "settlement_value", "settlement_source", "market_slug",
        )),
    )
    conn.commit()


def test_apply_closes_absent_winner_to_settled_closed_redeemed(trades_conn, forecasts_conn):
    _insert_position_current(
        trades_conn, position_id="pos-win", phase="active",
        city="milan", target_date="2026-06-23", bin_label="40°C",
        direction="buy_yes", token_id="tok-milan-yes",
    )
    _insert_settlement(forecasts_conn, city="milan", target_date="2026-06-23", winning_bin="40°C")

    report = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    assert report.applied == 1
    row = trades_conn.execute(
        "SELECT phase, chain_state FROM position_current WHERE position_id='pos-win'"
    ).fetchone()
    assert row["phase"] == "settled"
    assert row["chain_state"] == CLOSED_REDEEMED
    events = trades_conn.execute(
        "SELECT event_type, phase_after FROM position_events WHERE position_id='pos-win'"
    ).fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == "SETTLED"
    assert events[0]["phase_after"] == "settled"


def test_apply_closes_absent_loser_to_settled_closed_worthless(trades_conn, forecasts_conn):
    _insert_position_current(
        trades_conn, position_id="pos-lose", phase="active",
        city="seoul", target_date="2026-06-21", bin_label="24°C",
        direction="buy_yes", token_id="tok-seoul-yes",
    )
    _insert_settlement(forecasts_conn, city="seoul", target_date="2026-06-21", winning_bin="20°C")

    report = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    assert report.applied == 1
    row = trades_conn.execute(
        "SELECT phase, chain_state FROM position_current WHERE position_id='pos-lose'"
    ).fetchone()
    assert row["phase"] == "settled"
    assert row["chain_state"] == CLOSED_WORTHLESS


def test_apply_buy_no_win_emits_distinct_market_and_position_results(
    trades_conn, forecasts_conn
):
    _insert_position_current(
        trades_conn, position_id="pos-no-win", phase="active",
        city="milan", target_date="2026-06-23", bin_label="40°C",
        direction="buy_no", no_token_id="tok-milan-no", chain_state="synced",
    )
    _insert_settlement(
        forecasts_conn, city="milan", target_date="2026-06-23",
        winning_bin="39°C",
    )

    report = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    assert report.applied == 1
    row = trades_conn.execute(
        "SELECT payload_json FROM position_events WHERE position_id='pos-no-win'"
    ).fetchone()
    payload = json.loads(row["payload_json"])
    assert payload["won"] is True
    assert payload["market_bin_won"] is False
    assert payload["position_won"] is True
    assert payload["outcome"] == 1


# -----------------------------------------------------------------------------
# Bug B antibody (2026-07-07): _apply_settlement_finding computes _pnl for the
# position_settled.v1 audit payload but never overwrites realized_pnl_usd /
# exit_price on `projection` -- those two columns keep whatever pre-transition
# value position_current already had (NULL for a position settling for the
# first time), so a chain-mirror-graded close is invisible to Zeus's own P&L
# accounting even though _pnl was computed correctly.
# -----------------------------------------------------------------------------


def test_apply_closes_absent_winner_projects_realized_pnl(trades_conn, forecasts_conn):
    _insert_position_current(
        trades_conn, position_id="pos-win-pnl", phase="active",
        city="milan", target_date="2026-06-23", bin_label="40°C",
        direction="buy_yes", token_id="tok-milan-yes-pnl",
        chain_shares=10.0, shares=10.0, cost_basis_usd=4.0,
    )
    _insert_settlement(forecasts_conn, city="milan", target_date="2026-06-23", winning_bin="40°C")

    report = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    assert report.applied == 1
    row = trades_conn.execute(
        "SELECT realized_pnl_usd, exit_price FROM position_current WHERE position_id='pos-win-pnl'"
    ).fetchone()
    # won: _pnl = shares - cost_basis_usd = 10.0 - 4.0 = 6.0
    assert row["realized_pnl_usd"] is not None, (
        "BUG B: chain-mirror settlement must project realized_pnl_usd, got NULL"
    )
    assert row["realized_pnl_usd"] == pytest.approx(6.0), (
        f"expected realized_pnl_usd 6.0, got {row['realized_pnl_usd']}"
    )
    assert row["exit_price"] == pytest.approx(1.0), (
        f"expected exit_price 1.0 for a winning settlement, got {row['exit_price']}"
    )


def test_apply_closes_absent_loser_projects_negative_realized_pnl(trades_conn, forecasts_conn):
    _insert_position_current(
        trades_conn, position_id="pos-lose-pnl", phase="active",
        city="seoul", target_date="2026-06-21", bin_label="24°C",
        direction="buy_yes", token_id="tok-seoul-yes-pnl",
        chain_shares=8.0, shares=8.0, cost_basis_usd=3.2,
    )
    _insert_settlement(forecasts_conn, city="seoul", target_date="2026-06-21", winning_bin="20°C")

    report = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    assert report.applied == 1
    row = trades_conn.execute(
        "SELECT realized_pnl_usd, exit_price FROM position_current WHERE position_id='pos-lose-pnl'"
    ).fetchone()
    # lost: _pnl = -cost_basis_usd = -3.2
    assert row["realized_pnl_usd"] is not None, (
        "BUG B: chain-mirror settlement must project realized_pnl_usd, got NULL"
    )
    assert row["realized_pnl_usd"] == pytest.approx(-3.2), (
        f"expected realized_pnl_usd -3.2, got {row['realized_pnl_usd']}"
    )
    assert row["exit_price"] == pytest.approx(0.0), (
        f"expected exit_price 0.0 for a losing settlement, got {row['exit_price']}"
    )


def test_apply_corrects_size_mismatch(trades_conn, forecasts_conn):
    _insert_position_current(
        trades_conn, position_id="pos-dallas", phase="active",
        city="dallas", target_date="2026-06-20", bin_label="100-101°F",
        direction="buy_yes", token_id="tok-dallas-yes",
        chain_shares=1184.57, shares=1184.57,
    )
    chain = {"tok-dallas-yes": ChainPositionFact(
        token_id="tok-dallas-yes", condition_id="cond-1", size=74.55,
        redeemable=True, current_value=0.0, side="Yes",
    )}

    report = reconcile(trades_conn, forecasts_conn, chain_by_asset=chain, apply=True)

    assert report.applied == 1
    row = trades_conn.execute(
        "SELECT chain_shares FROM position_current WHERE position_id='pos-dallas'"
    ).fetchone()
    assert row["chain_shares"] == 74.55
    events = trades_conn.execute(
        "SELECT event_type FROM position_events WHERE position_id='pos-dallas'"
    ).fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == "CHAIN_SIZE_CORRECTED"


def test_open_phase_absent_token_unresolved_market_first_run_marks_review_no_close(
    trades_conn, forecasts_conn
):
    """The Manila ce105753-e91 case must not be auto-closed on a single read.

    P0b (2026-07-04): a single absent read is ambiguous (data-api lag) and
    must NOT force-close. It DOES persist a durable, phase-preserving
    REVIEW_REQUIRED marker event (the bookkeeping half of the two-
    consecutive-mirror-runs threshold — see
    _has_prior_review_open_absent_marker) — this is evidence, not a
    lifecycle transition (phase_before == phase_after).
    """
    _insert_position_current(
        trades_conn, position_id="pos-manila", phase="day0_window",
        city="manila", target_date="2026-07-04", bin_label="33°C",
        direction="buy_no", no_token_id="tok-manila-no", chain_state="synced",
        chain_shares=11.1, shares=11.1,
    )

    report = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    row = trades_conn.execute(
        "SELECT phase, chain_state FROM position_current WHERE position_id='pos-manila'"
    ).fetchone()
    assert row["phase"] == "day0_window"
    assert row["chain_state"] == "synced"
    events = trades_conn.execute(
        "SELECT event_type, phase_before, phase_after FROM position_events WHERE position_id='pos-manila'"
    ).fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == "REVIEW_REQUIRED"
    assert events[0]["phase_before"] == "day0_window"
    assert events[0]["phase_after"] == "day0_window"
    review_findings = [f for f in report.findings if f.classification == REVIEW_OPEN_ABSENT]
    assert len(review_findings) == 1


def test_open_phase_absent_token_second_consecutive_run_force_closes(trades_conn, forecasts_conn):
    """A projection with no fill proof may be voided after two absent reads."""
    _insert_position_current(
        trades_conn, position_id="pos-manila-2", phase="day0_window",
        city="manila", target_date="2026-07-04", bin_label="33°C",
        direction="buy_no", no_token_id="tok-manila-no-2", chain_state="synced",
        chain_shares=11.1, shares=11.1,
    )

    first = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)
    assert any(f.classification == REVIEW_OPEN_ABSENT for f in first.findings)
    row = trades_conn.execute(
        "SELECT phase FROM position_current WHERE position_id='pos-manila-2'"
    ).fetchone()
    assert row["phase"] == "day0_window"

    second = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    closed_findings = [f for f in second.findings if f.classification == CLOSED_EXITED]
    assert len(closed_findings) == 1
    row = trades_conn.execute(
        "SELECT phase, chain_state FROM position_current WHERE position_id='pos-manila-2'"
    ).fetchone()
    assert row["phase"] == "voided"
    assert row["chain_state"] == CLOSED_EXITED
    events = trades_conn.execute(
        "SELECT event_type, phase_before, phase_after FROM position_events "
        "WHERE position_id='pos-manila-2' ORDER BY sequence_no"
    ).fetchall()
    assert len(events) == 2
    assert events[0]["event_type"] == "REVIEW_REQUIRED"
    assert events[1]["event_type"] == "ADMIN_VOIDED"
    assert events[1]["phase_before"] == "day0_window"
    assert events[1]["phase_after"] == "voided"


def test_confirmed_fill_absence_never_force_voids_without_economic_close_proof(
    trades_conn, forecasts_conn
):
    """A real fill stays open for review until exit, redeem, or settlement evidence."""
    position_id = "pos-confirmed-fill-absent"
    _insert_position_current(
        trades_conn, position_id=position_id, phase="day0_window",
        city="seoul", target_date="2026-07-15", bin_label="29°C",
        direction="buy_no", no_token_id="tok-confirmed-no", chain_state="synced",
        chain_shares=15.0, shares=15.0,
    )
    trades_conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, source_module,
            env, payload_json
        ) VALUES (?, ?, 1, 1, 'ENTRY_ORDER_FILLED', ?, 'pending_entry',
                  'day0_window', 'edli', 'test', 'live', '{}')
        """,
        (
            f"{position_id}:entry-filled",
            position_id,
            "2026-07-15T06:02:00+00:00",
        ),
    )

    first = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)
    second = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    assert any(f.classification == REVIEW_OPEN_ABSENT for f in first.findings)
    assert any(f.classification == REVIEW_OPEN_ABSENT for f in second.findings)
    assert not any(f.classification == CLOSED_EXITED for f in second.findings)
    row = trades_conn.execute(
        "SELECT phase, chain_state FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    assert (row["phase"], row["chain_state"]) == ("day0_window", "synced")
    events = trades_conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ? ORDER BY sequence_no",
        (position_id,),
    ).fetchall()
    assert [event["event_type"] for event in events] == [
        "ENTRY_ORDER_FILLED",
        "REVIEW_REQUIRED",
    ]


def test_monitor_refresh_between_absent_reads_does_not_reset_chain_evidence(
    trades_conn, forecasts_conn
):
    """Monitor observations are not Chain/CLOB evidence.

    A monitor event between two independent absent chain snapshots must not
    turn the second snapshot back into a first absence forever.
    """
    position_id = "pos-manila-monitor-noise"
    _insert_position_current(
        trades_conn, position_id=position_id, phase="day0_window",
        city="manila", target_date="2026-07-04", bin_label="33°C",
        direction="buy_no", no_token_id="tok-manila-monitor-noise",
        chain_state="synced", chain_shares=11.1, shares=11.1,
    )

    first = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)
    assert any(f.classification == REVIEW_OPEN_ABSENT for f in first.findings)

    trades_conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, caused_by,
            source_module, env, payload_json
        ) VALUES (?, ?, 1, 2, 'MONITOR_REFRESHED', ?, 'day0_window',
                  'day0_window', 'edli', 'monitor_refresh',
                  'src.engine.cycle_runtime', 'live', '{}')
        """,
        (
            f"{position_id}:monitor_refreshed:2",
            position_id,
            "2026-07-04T00:05:00+00:00",
        ),
    )
    trades_conn.commit()

    second = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    assert [f.classification for f in second.findings if f.position_id == position_id] == [
        CLOSED_EXITED
    ]
    row = trades_conn.execute(
        "SELECT phase, chain_state FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    assert (row["phase"], row["chain_state"]) == ("voided", CLOSED_EXITED)
    events = trades_conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ? ORDER BY sequence_no",
        (position_id,),
    ).fetchall()
    assert [event["event_type"] for event in events] == [
        "REVIEW_REQUIRED",
        "MONITOR_REFRESHED",
        "ADMIN_VOIDED",
    ]


def test_semantic_monitor_event_between_absent_reads_resets_chain_evidence(
    trades_conn, forecasts_conn
):
    """A partial-fill monitor subtype is order evidence, not monitor noise."""
    position_id = "pos-manila-partial-fill-reset"
    _insert_position_current(
        trades_conn, position_id=position_id, phase="day0_window",
        city="manila", target_date="2026-07-04", bin_label="33°C",
        direction="buy_no", no_token_id="tok-manila-partial-fill-reset",
        chain_state="synced", chain_shares=11.1, shares=11.1,
    )

    reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)
    trades_conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, caused_by,
            source_module, env, payload_json
        ) VALUES (?, ?, 1, 2, 'MONITOR_REFRESHED', ?, 'day0_window',
                  'day0_window', 'edli', 'partial_exit_fill',
                  'src.execution.exit_lifecycle', 'live', ?)
        """,
        (
            f"{position_id}:partial_exit_fill:2",
            position_id,
            "2026-07-04T00:05:00+00:00",
            '{"semantic_event":"PARTIAL_FILL_OBSERVED",'
            '"filled_shares":2.0,"remaining_shares":9.1}',
        ),
    )
    trades_conn.commit()

    second = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    assert not any(f.classification == CLOSED_EXITED for f in second.findings)
    assert [f.classification for f in second.findings if f.position_id == position_id] == [
        REVIEW_OPEN_ABSENT
    ]
    row = trades_conn.execute(
        "SELECT phase, chain_state FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    assert (row["phase"], row["chain_state"]) == ("day0_window", "synced")
    events = trades_conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ? ORDER BY sequence_no",
        (position_id,),
    ).fetchall()
    assert [event["event_type"] for event in events] == [
        "REVIEW_REQUIRED",
        "MONITOR_REFRESHED",
        "REVIEW_REQUIRED",
    ]


def test_exact_size_reappearance_resets_chain_evidence_before_next_absence(
    trades_conn, forecasts_conn
):
    """Exact-size token presence must durably break an absence streak."""
    position_id = "pos-manila-chain-reset"
    token_id = "tok-manila-chain-reset"
    _insert_position_current(
        trades_conn, position_id=position_id, phase="day0_window",
        city="manila", target_date="2026-07-04", bin_label="33°C",
        direction="buy_no", no_token_id=token_id,
        chain_state="synced", chain_shares=11.1, shares=11.1,
    )

    reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)
    present = reconcile(
        trades_conn,
        forecasts_conn,
        chain_by_asset={token_id: ChainPositionFact(
            token_id=token_id, condition_id="cond-1", size=11.1,
            redeemable=False, current_value=0.0, side="No",
        )},
        apply=True,
    )
    assert [f.classification for f in present.findings if f.position_id == position_id] == [
        SIZE_CORRECTED
    ]

    present_again = reconcile(
        trades_conn,
        forecasts_conn,
        chain_by_asset={token_id: ChainPositionFact(
            token_id=token_id, condition_id="cond-1", size=11.1,
            redeemable=False, current_value=0.0, side="No",
        )},
        apply=True,
    )
    assert [
        f.classification for f in present_again.findings if f.position_id == position_id
    ] == [CONSISTENT]

    absent_again = reconcile(
        trades_conn, forecasts_conn, chain_by_asset={}, apply=True
    )

    assert not any(f.classification == CLOSED_EXITED for f in absent_again.findings)
    assert [
        f.classification for f in absent_again.findings if f.position_id == position_id
    ] == [REVIEW_OPEN_ABSENT]
    row = trades_conn.execute(
        "SELECT phase, chain_state, chain_seen_at FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    assert (row["phase"], row["chain_state"]) == ("day0_window", "synced")
    assert row["chain_seen_at"]
    events = trades_conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ? ORDER BY sequence_no",
        (position_id,),
    ).fetchall()
    assert [event["event_type"] for event in events] == [
        "REVIEW_REQUIRED",
        "CHAIN_SIZE_CORRECTED",
        "REVIEW_REQUIRED",
    ]


def test_open_phase_absent_token_reappears_between_runs_no_close(trades_conn, forecasts_conn):
    """Token reappears on the second read (present + matching size) — must
    record a no-delta chain observation and never force-close."""
    _insert_position_current(
        trades_conn, position_id="pos-manila-3", phase="day0_window",
        city="manila", target_date="2026-07-04", bin_label="33°C",
        direction="buy_no", no_token_id="tok-manila-no-3", chain_state="synced",
        chain_shares=11.1, shares=11.1,
    )

    first = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)
    assert any(f.classification == REVIEW_OPEN_ABSENT for f in first.findings)

    chain = {"tok-manila-no-3": ChainPositionFact(
        token_id="tok-manila-no-3", condition_id="cond-1", size=11.1,
        redeemable=False, current_value=0.0, side="No",
    )}
    second = reconcile(trades_conn, forecasts_conn, chain_by_asset=chain, apply=True)

    assert not any(f.classification == CLOSED_EXITED for f in second.findings)
    reappeared = [f for f in second.findings if f.position_id == "pos-manila-3"]
    assert len(reappeared) == 1
    assert reappeared[0].classification == SIZE_CORRECTED
    row = trades_conn.execute(
        "SELECT phase, chain_state, chain_seen_at FROM position_current "
        "WHERE position_id='pos-manila-3'"
    ).fetchone()
    assert row["phase"] == "day0_window"
    assert row["chain_state"] == "synced"
    assert row["chain_seen_at"]


def test_open_phase_absent_token_second_run_with_open_order_does_not_close(
    trades_conn, forecasts_conn
):
    """A second consecutive absent read must NOT force-close if an order is
    still open/in-flight for this position — never void out from under a
    live order."""
    _insert_position_current(
        trades_conn, position_id="pos-manila-4", phase="day0_window",
        city="manila", target_date="2026-07-04", bin_label="33°C",
        direction="buy_no", no_token_id="tok-manila-no-4", chain_state="synced",
        chain_shares=11.1, shares=11.1,
    )
    trades_conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, state, created_at, updated_at
        ) VALUES (
            'cmd-1', 'snap-1', 'env-1', 'pos-manila-4', 'dec-1', 'idem-1',
            'EXIT', 'market-1', 'tok-manila-no-4', 'SELL', 11.1, 0.5,
            'ACKED', ?, ?
        )
        """,
        ("2026-07-04T00:00:00+00:00", "2026-07-04T00:00:00+00:00"),
    )
    trades_conn.commit()

    reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)
    second = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    assert not any(f.classification == CLOSED_EXITED for f in second.findings)
    review_findings = [f for f in second.findings if f.classification == REVIEW_OPEN_ABSENT]
    assert len(review_findings) == 1
    row = trades_conn.execute(
        "SELECT phase FROM position_current WHERE position_id='pos-manila-4'"
    ).fetchone()
    assert row["phase"] == "day0_window"


def test_absent_pending_exit_with_confirmed_exit_fill_skips_review_marker(
    trades_conn, forecasts_conn
):
    """A just-filled exit can remove the held token before the fill projector
    folds position_current. Chain-mirror must not append a stale REVIEW marker
    in that window."""
    _insert_position_current(
        trades_conn, position_id="pos-milan-exit-fill", phase="pending_exit",
        city="milan", target_date="2026-07-08", bin_label="36°C",
        direction="buy_no", no_token_id="tok-milan-no", chain_state="synced",
        chain_shares=26.79, shares=26.79,
    )
    trades_conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at
        ) VALUES (
            'cmd-exit-fill', 'snap-exit-fill', 'env-exit-fill',
            'pos-milan-exit-fill', 'dec-exit-fill', 'idem-exit-fill',
            'EXIT', 'market-milan', 'tok-milan-no', 'SELL', 26.79, 0.26,
            'order-exit-fill', 'FILLED', ?, ?
        )
        """,
        ("2026-07-08T14:07:10+00:00", "2026-07-08T14:07:21+00:00"),
    )
    trades_conn.execute(
        """
        INSERT INTO venue_order_facts (
            venue_order_id, command_id, state, remaining_size, matched_size,
            source, observed_at, local_sequence, raw_payload_hash, raw_payload_json
        ) VALUES (
            'order-exit-fill', 'cmd-exit-fill', 'MATCHED', '0', '26.79',
            'REST', '2026-07-08T14:07:21+00:00', 1, 'hash-exit-fill', '{}'
        )
        """
    )
    trades_conn.commit()

    report = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    findings = [f for f in report.findings if f.position_id == "pos-milan-exit-fill"]
    assert len(findings) == 1
    assert findings[0].classification == CONSISTENT
    assert findings[0].details["reason"] == "confirmed_exit_fill_fact_pending_projection"
    assert report.applied == 0
    events = trades_conn.execute(
        "SELECT COUNT(*) AS n FROM position_events WHERE position_id='pos-milan-exit-fill'"
    ).fetchone()
    assert events["n"] == 0


def test_pending_entry_open_order_without_fill_skips_review_marker(
    trades_conn, forecasts_conn
):
    """A live maker entry order is not a held position until venue fill facts exist."""
    _insert_position_current(
        trades_conn, position_id="pos-wuhan-entry-live", phase="pending_entry",
        city="wuhan", target_date="2026-07-10",
        bin_label="Will the highest temperature in Wuhan be 36°C on July 10?",
        direction="buy_no", no_token_id="tok-wuhan-no", chain_state="local_only",
        shares=0.0, chain_shares=None,
    )
    trades_conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at
        ) VALUES (
            'cmd-entry-live', 'snap-entry-live', 'env-entry-live',
            'pos-wuhan-entry-live', 'dec-entry-live', 'idem-entry-live',
            'ENTRY', 'market-wuhan', 'tok-wuhan-no', 'BUY', 18.02, 0.62,
            'order-entry-live', 'ACKED', ?, ?
        )
        """,
        ("2026-07-08T14:27:44+00:00", "2026-07-08T14:27:47+00:00"),
    )
    trades_conn.execute(
        """
        INSERT INTO venue_order_facts (
            venue_order_id, command_id, state, remaining_size, matched_size,
            source, observed_at, local_sequence, raw_payload_hash, raw_payload_json
        ) VALUES (
            'order-entry-live', 'cmd-entry-live', 'LIVE', '18.02', '0',
            'REST', '2026-07-08T14:27:47+00:00', 1, 'hash-entry-live', '{}'
        )
        """
    )
    trades_conn.commit()

    report = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    findings = [f for f in report.findings if f.position_id == "pos-wuhan-entry-live"]
    assert len(findings) == 1
    assert findings[0].classification == CONSISTENT
    assert (
        findings[0].details["reason"]
        == "open_entry_order_without_fill_pending_position"
    )
    assert report.applied == 0
    events = trades_conn.execute(
        "SELECT COUNT(*) AS n FROM position_events WHERE position_id='pos-wuhan-entry-live'"
    ).fetchone()
    assert events["n"] == 0


def test_foreign_and_missing_local_row_findings_never_create_a_local_row(trades_conn, forecasts_conn):
    chain = {
        "tok-foreign": ChainPositionFact(
            token_id="tok-foreign", condition_id="cond-x", size=48.52,
            redeemable=False, current_value=23.05, side="Yes", title="Will Anthropic's public ticker be $ANTH?",
        ),
    }
    report = reconcile(trades_conn, forecasts_conn, chain_by_asset=chain, apply=True)
    foreign = [f for f in report.findings if f.classification == FOREIGN]
    assert len(foreign) == 1
    count = trades_conn.execute("SELECT COUNT(*) AS n FROM position_current").fetchone()
    assert count["n"] == 0


def test_dry_run_writes_nothing(trades_conn, forecasts_conn):
    _insert_position_current(
        trades_conn, position_id="pos-dry", phase="active",
        city="milan", target_date="2026-06-23", bin_label="40°C",
        direction="buy_yes", token_id="tok-milan-yes-dry",
    )
    _insert_settlement(forecasts_conn, city="milan", target_date="2026-06-23", winning_bin="40°C")

    report = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=False)

    assert report.dry_run is True
    assert report.applied == 0
    row = trades_conn.execute(
        "SELECT phase FROM position_current WHERE position_id='pos-dry'"
    ).fetchone()
    assert row["phase"] == "active"
    events = trades_conn.execute(
        "SELECT COUNT(*) AS n FROM position_events WHERE position_id='pos-dry'"
    ).fetchone()
    assert events["n"] == 0
    # Findings are still computed even though nothing is written.
    assert any(f.classification == CLOSED_REDEEMED for f in report.findings)


def test_second_run_is_idempotent_no_duplicate_events(trades_conn, forecasts_conn):
    _insert_position_current(
        trades_conn, position_id="pos-idem", phase="active",
        city="milan", target_date="2026-06-23", bin_label="40°C",
        direction="buy_yes", token_id="tok-milan-yes-idem",
    )
    _insert_settlement(forecasts_conn, city="milan", target_date="2026-06-23", winning_bin="40°C")

    first = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)
    assert first.applied == 1

    second = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)
    assert second.applied == 0
    consistent = [f for f in second.findings if f.position_id == "pos-idem"]
    assert len(consistent) == 1
    assert consistent[0].classification == CONSISTENT

    events = trades_conn.execute(
        "SELECT COUNT(*) AS n FROM position_events WHERE position_id='pos-idem'"
    ).fetchone()
    assert events["n"] == 1


def test_position_events_table_is_append_only_trigger_enforced(trades_conn, forecasts_conn):
    """The reconciler's provenance relies on position_events being physically
    append-only: even a direct UPDATE/DELETE against a reconciler-written row
    must be rejected by the schema's own trigger, independent of application code."""
    _insert_position_current(
        trades_conn, position_id="pos-append-only", phase="active",
        city="milan", target_date="2026-06-23", bin_label="40°C",
        direction="buy_yes", token_id="tok-milan-yes-append-only",
    )
    _insert_settlement(forecasts_conn, city="milan", target_date="2026-06-23", winning_bin="40°C")
    reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    with pytest.raises(sqlite3.IntegrityError):
        trades_conn.execute(
            "UPDATE position_events SET event_type='ADMIN_VOIDED' WHERE position_id='pos-append-only'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        trades_conn.execute(
            "DELETE FROM position_events WHERE position_id='pos-append-only'"
        )


# -----------------------------------------------------------------------------
# R2-core hole closure (b): per-row isolation (R0 verifier finding -- this
# loop previously had no per-row try/except, so one raising row aborted the
# whole pass).
# -----------------------------------------------------------------------------


def test_reconcile_isolates_a_raising_position_and_continues(
    trades_conn, forecasts_conn, monkeypatch
):
    _insert_position_current(
        trades_conn, position_id="pos-raises", phase="active",
        city="milan", target_date="2026-06-23", bin_label="40°C",
        direction="buy_yes", token_id="tok-milan-yes-raises",
    )
    _insert_position_current(
        trades_conn, position_id="pos-ok", phase="active",
        city="milan", target_date="2026-06-23", bin_label="40°C",
        direction="buy_yes", token_id="tok-milan-yes-ok",
    )
    _insert_settlement(forecasts_conn, city="milan", target_date="2026-06-23", winning_bin="40°C")

    import src.state.chain_mirror_reconciler as chain_mirror_module

    original = chain_mirror_module.classify_local_position

    def _raising_classify(row, *args, **kwargs):
        if row.position_id == "pos-raises":
            raise RuntimeError("synthetic classify failure")
        return original(row, *args, **kwargs)

    monkeypatch.setattr(chain_mirror_module, "classify_local_position", _raising_classify)

    report = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    assert len(report.errors) == 1
    assert report.errors[0]["position_id"] == "pos-raises"
    ok_findings = [f for f in report.findings if f.position_id == "pos-ok"]
    assert len(ok_findings) == 1
    assert ok_findings[0].classification == CLOSED_REDEEMED
    # The raising row's own write never happened; the OTHER row's did.
    raises_row = trades_conn.execute(
        "SELECT phase FROM position_current WHERE position_id='pos-raises'"
    ).fetchone()
    assert raises_row["phase"] == "active"
    ok_row = trades_conn.execute(
        "SELECT phase FROM position_current WHERE position_id='pos-ok'"
    ).fetchone()
    assert ok_row["phase"] == "settled"
