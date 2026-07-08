# Created: 2026-07-08
"""Predicate tests (synthetic venue-behavior fixtures) + classify/apply/reconcile
tests for src.reconcile.diff_engine."""
from __future__ import annotations

import pytest

from src.reconcile.chain_truth import load_chain_truth_snapshot
from src.reconcile.diff_engine import (
    LOCAL_STATE_IGNORES_CONCURRENT_FILL,
    PARTIAL_REMAINDER_TERMINAL_PROOF_AVAILABLE,
    POSITION_SHARES_EXCEED_CANONICAL_FILL,
    RESERVATION_ORPHANED_FILL_AFTER_RELEASE,
    WS_STATE_STALE_NEEDS_REST_TRUTH,
    apply_corrective_event,
    classify,
    reconcile,
)
from src.reconcile.local_truth import load_local_truth_snapshot
from tests.reconcile.conftest import (
    insert_order_fact,
    insert_position_current,
    insert_reservation,
    insert_trade_fact,
    insert_venue_command,
)


# -----------------------------------------------------------------------------
# Predicate 1: cancel_match_race
# -----------------------------------------------------------------------------


def test_cancel_match_race_flags_cancelled_command_with_positive_fill(trades_conn, forecasts_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", state="CANCELLED")
    insert_trade_fact(
        trades_conn, trade_fact_id=1, trade_id="t1", venue_order_id="vo-cmd-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="5",
    )
    trades_conn.commit()

    local = load_local_truth_snapshot(trades_conn)
    chain = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})
    findings = classify(local, chain)

    matches = [f for f in findings if f.classification == LOCAL_STATE_IGNORES_CONCURRENT_FILL]
    assert len(matches) == 1
    assert matches[0].command_id == "cmd-1"
    assert matches[0].writes is False


def test_cancel_match_race_silent_when_cancelled_with_zero_fill(trades_conn, forecasts_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", state="CANCELLED")
    trades_conn.commit()

    local = load_local_truth_snapshot(trades_conn)
    chain = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})
    findings = classify(local, chain)

    assert not [f for f in findings if f.classification == LOCAL_STATE_IGNORES_CONCURRENT_FILL]


# -----------------------------------------------------------------------------
# Predicate 2: ws_unreliable_rest_point_truth
# -----------------------------------------------------------------------------


def test_ws_unreliable_flags_open_command_with_heartbeat_cancel_suspected(trades_conn, forecasts_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", state="ACKED")
    insert_order_fact(
        trades_conn, fact_id=1, venue_order_id="vo-cmd-1", command_id="cmd-1",
        state="HEARTBEAT_CANCEL_SUSPECTED",
    )
    trades_conn.commit()

    local = load_local_truth_snapshot(trades_conn)
    chain = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})
    findings = classify(local, chain)

    matches = [f for f in findings if f.classification == WS_STATE_STALE_NEEDS_REST_TRUTH]
    assert len(matches) == 1


def test_ws_unreliable_silent_for_terminal_command(trades_conn, forecasts_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", state="FILLED")
    insert_order_fact(
        trades_conn, fact_id=1, venue_order_id="vo-cmd-1", command_id="cmd-1",
        state="HEARTBEAT_CANCEL_SUSPECTED",
    )
    trades_conn.commit()

    local = load_local_truth_snapshot(trades_conn)
    chain = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})
    findings = classify(local, chain)

    assert not [f for f in findings if f.classification == WS_STATE_STALE_NEEDS_REST_TRUTH]


# -----------------------------------------------------------------------------
# Predicate 3: partial_fill_disappearance
# -----------------------------------------------------------------------------


def test_partial_fill_disappearance_flags_gone_remainder_with_preserved_fill(trades_conn, forecasts_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", state="PARTIAL")
    insert_order_fact(
        trades_conn, fact_id=1, venue_order_id="vo-cmd-1", command_id="cmd-1",
        state="EXPIRED", source="REST",
    )
    insert_trade_fact(
        trades_conn, trade_fact_id=1, trade_id="t1", venue_order_id="vo-cmd-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="3",
    )
    trades_conn.commit()

    local = load_local_truth_snapshot(trades_conn)
    chain = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})
    findings = classify(local, chain)

    matches = [f for f in findings if f.classification == PARTIAL_REMAINDER_TERMINAL_PROOF_AVAILABLE]
    assert len(matches) == 1
    assert matches[0].details["canonical_filled_size"] == 3.0
    assert matches[0].details["preserve_exposure"] is True


# -----------------------------------------------------------------------------
# Predicate 4: fill_dedup_ordering_drift (position-scoped)
# -----------------------------------------------------------------------------


def test_position_shares_exceed_canonical_fill_flags_overcounted_shares(trades_conn, forecasts_conn):
    insert_position_current(trades_conn, position_id="pos-1", shares=50.0)
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", intent_kind="ENTRY")
    insert_trade_fact(
        trades_conn, trade_fact_id=1, trade_id="t1", venue_order_id="vo-cmd-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="10",
    )
    trades_conn.commit()

    local = load_local_truth_snapshot(trades_conn)
    chain = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})
    findings = classify(local, chain)

    matches = [f for f in findings if f.classification == POSITION_SHARES_EXCEED_CANONICAL_FILL]
    assert len(matches) == 1
    assert matches[0].details["local_shares"] == 50.0
    assert matches[0].details["canonical_filled_size_total"] == 10.0


def test_position_shares_within_tolerance_is_silent(trades_conn, forecasts_conn):
    insert_position_current(trades_conn, position_id="pos-1", shares=10.0)
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", intent_kind="ENTRY")
    insert_trade_fact(
        trades_conn, trade_fact_id=1, trade_id="t1", venue_order_id="vo-cmd-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="10",
    )
    trades_conn.commit()

    local = load_local_truth_snapshot(trades_conn)
    chain = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})
    findings = classify(local, chain)

    assert not [f for f in findings if f.classification == POSITION_SHARES_EXCEED_CANONICAL_FILL]


# -----------------------------------------------------------------------------
# Predicate 5: reservation_orphan_fill_after_release (writes=True; the one
# hole-closure predicate with a real apply_corrective_event body)
# -----------------------------------------------------------------------------


def test_reservation_orphan_detected_when_fill_lands_after_release(trades_conn, forecasts_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", state="EXPIRED")
    insert_reservation(
        trades_conn, command_id="cmd-1", amount=5_000_000,
        released_at="2026-07-04T00:01:00+00:00", release_reason="EXPIRED", converted_amount=0,
    )
    # Fill fact lands AFTER the reservation was released -- the orphan window.
    insert_trade_fact(
        trades_conn, trade_fact_id=1, trade_id="t1", venue_order_id="vo-cmd-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="10",
        observed_at="2026-07-04T00:05:00+00:00",
    )
    trades_conn.commit()

    local = load_local_truth_snapshot(trades_conn)
    chain = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})
    findings = classify(local, chain)

    matches = [f for f in findings if f.classification == RESERVATION_ORPHANED_FILL_AFTER_RELEASE]
    assert len(matches) == 1
    assert matches[0].writes is True
    assert matches[0].position_id == "pos-1"


def test_reservation_orphan_silent_when_fill_precedes_release(trades_conn, forecasts_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", state="FILLED")
    insert_reservation(
        trades_conn, command_id="cmd-1", amount=5_000_000,
        released_at="2026-07-04T00:05:00+00:00", release_reason="CONVERTED_ON_FILL", converted_amount=5_000_000,
    )
    insert_trade_fact(
        trades_conn, trade_fact_id=1, trade_id="t1", venue_order_id="vo-cmd-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="10",
        observed_at="2026-07-04T00:00:10+00:00",
    )
    trades_conn.commit()

    local = load_local_truth_snapshot(trades_conn)
    chain = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})
    findings = classify(local, chain)

    assert not [f for f in findings if f.classification == RESERVATION_ORPHANED_FILL_AFTER_RELEASE]


def test_apply_corrective_event_appends_review_required_marker(trades_conn, forecasts_conn):
    from datetime import datetime, timezone

    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", state="EXPIRED")
    insert_reservation(
        trades_conn, command_id="cmd-1", amount=5_000_000,
        released_at="2026-07-04T00:01:00+00:00", release_reason="EXPIRED", converted_amount=0,
    )
    insert_trade_fact(
        trades_conn, trade_fact_id=1, trade_id="t1", venue_order_id="vo-cmd-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="10",
        observed_at="2026-07-04T00:05:00+00:00",
    )
    trades_conn.commit()

    local = load_local_truth_snapshot(trades_conn)
    chain = load_chain_truth_snapshot(trades_conn, forecasts_conn, {})
    finding = next(f for f in classify(local, chain) if f.classification == RESERVATION_ORPHANED_FILL_AFTER_RELEASE)

    applied = apply_corrective_event(trades_conn, finding, now=datetime(2026, 7, 4, 1, 0, tzinfo=timezone.utc))
    assert applied is True

    row = trades_conn.execute(
        "SELECT event_type, phase_before, phase_after, command_id FROM position_events WHERE position_id = 'pos-1'"
    ).fetchone()
    assert row["event_type"] == "REVIEW_REQUIRED"
    assert row["phase_before"] is None
    assert row["phase_after"] is None
    assert row["command_id"] == "cmd-1"

    # position_current is NOT mutated -- this is a durable evidence marker,
    # not a balance/phase mutation (see apply_corrective_event's docstring).
    position_row = trades_conn.execute(
        "SELECT phase FROM position_current WHERE position_id = 'pos-1'"
    ).fetchone()
    assert position_row["phase"] == "active"


def test_apply_corrective_event_raises_for_unimplemented_classification(trades_conn):
    from src.reconcile.diff_engine import DiffFinding, LOCAL_STATE_IGNORES_CONCURRENT_FILL
    from datetime import datetime, timezone

    finding = DiffFinding(
        classification=LOCAL_STATE_IGNORES_CONCURRENT_FILL,
        command_id="cmd-1", position_id="pos-1", writes=False, details={},
    )
    with pytest.raises(NotImplementedError):
        apply_corrective_event(trades_conn, finding, now=datetime(2026, 7, 4, tzinfo=timezone.utc))


# -----------------------------------------------------------------------------
# reconcile() runner: per-row isolation (R0 verifier hole closure b -- "the
# diff engine must have this from birth too")
# -----------------------------------------------------------------------------


def test_reconcile_isolates_a_raising_command_and_continues(trades_conn, forecasts_conn, monkeypatch):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", state="CANCELLED")
    insert_trade_fact(
        trades_conn, trade_fact_id=1, trade_id="t1", venue_order_id="vo-cmd-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="5",
    )
    insert_position_current(trades_conn, position_id="pos-2")
    insert_venue_command(trades_conn, command_id="cmd-2", position_id="pos-2", state="CANCELLED")
    insert_trade_fact(
        trades_conn, trade_fact_id=2, trade_id="t2", venue_order_id="vo-cmd-2",
        command_id="cmd-2", state="CONFIRMED", filled_size="7",
    )
    trades_conn.commit()

    import src.reconcile.diff_engine as diff_engine_module

    original = diff_engine_module._predicate_cancel_match_race

    def _raising_predicate(cmd, facts):
        if cmd.command_id == "cmd-1":
            raise RuntimeError("synthetic predicate failure")
        return original(cmd, facts)

    monkeypatch.setattr(
        diff_engine_module,
        "PREDICATE_TABLE",
        tuple(
            diff_engine_module.Predicate(p.name, p.venue_behavior, _raising_predicate)
            if p.name == "cancel_match_race"
            else p
            for p in diff_engine_module.PREDICATE_TABLE
        ),
    )

    report = reconcile(trades_conn, forecasts_conn, {}, apply=False)

    assert len(report.errors) == 1
    assert report.errors[0]["command_id"] == "cmd-1"
    matches = [f for f in report.findings if f.classification == LOCAL_STATE_IGNORES_CONCURRENT_FILL]
    assert [f.command_id for f in matches] == ["cmd-2"]


def test_reconcile_apply_true_writes_corrective_event(trades_conn, forecasts_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", state="EXPIRED")
    insert_reservation(
        trades_conn, command_id="cmd-1", amount=5_000_000,
        released_at="2026-07-04T00:01:00+00:00", release_reason="EXPIRED", converted_amount=0,
    )
    insert_trade_fact(
        trades_conn, trade_fact_id=1, trade_id="t1", venue_order_id="vo-cmd-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="10",
        observed_at="2026-07-04T00:05:00+00:00",
    )
    trades_conn.commit()

    report = reconcile(trades_conn, forecasts_conn, {}, apply=True)

    assert report.applied == 1
    assert not report.errors
