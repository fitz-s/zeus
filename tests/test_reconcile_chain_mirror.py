# Lifecycle: created=2026-07-04; last_reviewed=2026-07-04; last_reused=never
# Purpose: Regression tests for the chain-mirror reconciler (design doc
#   docs/rebuild/chain_mirror_state_model_2026-07-04.md).
# Reuse: Run when position_current chain-mirror classification, the
#   scripts/reconcile_chain_mirror.py CLI, or the market-rule state model
#   for quarantined/settled positions change.
# Authority basis: operator directive 2026-07-04 (root AGENTS.md §2
#   reconciliation order Chain > Chronicler > Portfolio).
from __future__ import annotations

import sqlite3

import pytest

from src.contracts.semantic_types import ChainState
from src.state.chain_mirror_reconciler import (
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
        phase="quarantined",
        chain_state="entry_authority_quarantined",
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
    phase: str = "quarantined",
    chain_state: str = "entry_authority_quarantined",
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
    strategy_key: str = "edli",
    updated_at: str = "2026-07-04T00:00:00+00:00",
) -> None:
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, city, target_date, bin_label,
            direction, chain_state, token_id, no_token_id, condition_id,
            chain_shares, shares, strategy_key, updated_at, temperature_metric
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            position_id, phase, position_id, city, target_date, bin_label,
            direction, chain_state, token_id, no_token_id, condition_id,
            chain_shares, shares, strategy_key, updated_at, temperature_metric,
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
        trades_conn, position_id="pos-win", phase="quarantined",
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
        trades_conn, position_id="pos-lose", phase="quarantined",
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


def test_apply_corrects_size_mismatch(trades_conn, forecasts_conn):
    _insert_position_current(
        trades_conn, position_id="pos-dallas", phase="quarantined",
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


def test_open_phase_absent_token_unresolved_market_never_writes(trades_conn, forecasts_conn):
    """The Manila ce105753-e91 case must not be auto-closed."""
    _insert_position_current(
        trades_conn, position_id="pos-manila", phase="day0_window",
        city="manila", target_date="2026-07-04", bin_label="33°C",
        direction="buy_no", no_token_id="tok-manila-no", chain_state="synced",
        chain_shares=11.1, shares=11.1,
    )

    report = reconcile(trades_conn, forecasts_conn, chain_by_asset={}, apply=True)

    assert report.applied == 0
    row = trades_conn.execute(
        "SELECT phase FROM position_current WHERE position_id='pos-manila'"
    ).fetchone()
    assert row["phase"] == "day0_window"
    events = trades_conn.execute(
        "SELECT COUNT(*) AS n FROM position_events WHERE position_id='pos-manila'"
    ).fetchone()
    assert events["n"] == 0
    review_findings = [f for f in report.findings if f.classification == REVIEW_OPEN_ABSENT]
    assert len(review_findings) == 1


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
        trades_conn, position_id="pos-dry", phase="quarantined",
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
    assert row["phase"] == "quarantined"
    events = trades_conn.execute(
        "SELECT COUNT(*) AS n FROM position_events WHERE position_id='pos-dry'"
    ).fetchone()
    assert events["n"] == 0
    # Findings are still computed even though nothing is written.
    assert any(f.classification == CLOSED_REDEEMED for f in report.findings)


def test_second_run_is_idempotent_no_duplicate_events(trades_conn, forecasts_conn):
    _insert_position_current(
        trades_conn, position_id="pos-idem", phase="quarantined",
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
        trades_conn, position_id="pos-append-only", phase="quarantined",
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
