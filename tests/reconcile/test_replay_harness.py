# Created: 2026-07-08
"""Fixture-DB replay harness tests. Demonstrates replay_window against a
synthetic 24h window with both a match (legacy pass + diff engine agree
something drifted) and a mismatch (legacy pass appended a corrective event
the diff engine's current predicate table does not reproduce -- a FINDING,
not necessarily a failure, per replay_window's docstring)."""
from __future__ import annotations

from src.reconcile.replay import load_legacy_corrective_events, replay_window
from tests.reconcile.conftest import insert_position_current, insert_reservation, insert_trade_fact, insert_venue_command


def _insert_legacy_event(
    conn, *, event_id, position_id, command_id, source_module, occurred_at,
    event_type="ADMIN_VOIDED", sequence_no=1,
):
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, decision_id,
            snapshot_id, order_id, command_id, caused_by, idempotency_key,
            venue_status, source_module, env, payload_json
        ) VALUES (?, ?, 1, ?, ?, ?, 'active', 'voided', 'edli', 'dec-1',
                  'snap-1', NULL, ?, 'legacy_pass', ?, 'voided', ?, 'live', '{}')
        """,
        (event_id, position_id, sequence_no, event_type, occurred_at, command_id, event_id, source_module),
    )


def test_load_legacy_corrective_events_scopes_to_window_and_source(trades_conn):
    insert_position_current(trades_conn, position_id="pos-1")
    _insert_legacy_event(
        trades_conn, event_id="evt-1", position_id="pos-1", command_id="cmd-1",
        source_module="src.state.chain_mirror_reconciler", occurred_at="2026-07-04T12:00:00+00:00",
    )
    # Outside the window.
    _insert_legacy_event(
        trades_conn, event_id="evt-2", position_id="pos-1", command_id="cmd-2",
        source_module="src.state.chain_mirror_reconciler", occurred_at="2026-07-01T00:00:00+00:00",
        sequence_no=2,
    )
    # Not a legacy reconciler source_module.
    _insert_legacy_event(
        trades_conn, event_id="evt-3", position_id="pos-1", command_id="cmd-3",
        source_module="src.execution.exit_lifecycle", occurred_at="2026-07-04T13:00:00+00:00",
        sequence_no=3,
    )
    trades_conn.commit()

    rows = load_legacy_corrective_events(
        trades_conn, window_start="2026-07-04T00:00:00+00:00", window_end="2026-07-05T00:00:00+00:00"
    )

    assert [str(r["event_id"]) for r in rows] == ["evt-1"]


def test_replay_window_reports_match_when_diff_engine_agrees(trades_conn, forecasts_conn):
    # A legacy pass appended a corrective event for pos-1 in the window; the
    # diff engine, run over CURRENT state, ALSO flags pos-1 (cancel/match
    # race: cancelled command with a positive canonical fill) -- a match.
    insert_position_current(trades_conn, position_id="pos-1")
    insert_venue_command(trades_conn, command_id="cmd-1", position_id="pos-1", state="CANCELLED")
    insert_trade_fact(
        trades_conn, trade_fact_id=1, trade_id="t1", venue_order_id="vo-cmd-1",
        command_id="cmd-1", state="CONFIRMED", filled_size="5",
    )
    _insert_legacy_event(
        trades_conn, event_id="evt-1", position_id="pos-1", command_id="cmd-1",
        source_module="src.execution.exchange_reconcile", occurred_at="2026-07-04T12:00:00+00:00",
    )
    trades_conn.commit()

    report = replay_window(
        trades_conn, forecasts_conn, {},
        window_start="2026-07-04T00:00:00+00:00", window_end="2026-07-05T00:00:00+00:00",
    )

    assert report.legacy_event_count == 1
    assert report.matched_count == 1
    assert report.mismatched_count == 0
    assert report.comparisons[0].diff_engine_classifications


def test_replay_window_reports_mismatch_when_diff_engine_silent(trades_conn, forecasts_conn):
    # A legacy pass appended a corrective event for pos-2, but pos-2's
    # CURRENT state gives the diff engine no evidence to flag anything (e.g.
    # the drift was already fully resolved) -- a mismatch, reported as a
    # finding, not asserted to be a diff-engine bug (see replay_window docstring).
    insert_position_current(trades_conn, position_id="pos-2", shares=0.0)
    insert_venue_command(
        trades_conn, command_id="cmd-2", position_id="pos-2", state="FILLED", intent_kind="EXIT",
    )
    _insert_legacy_event(
        trades_conn, event_id="evt-2", position_id="pos-2", command_id="cmd-2",
        source_module="src.state.chain_mirror_reconciler", occurred_at="2026-07-04T12:00:00+00:00",
    )
    trades_conn.commit()

    report = replay_window(
        trades_conn, forecasts_conn, {},
        window_start="2026-07-04T00:00:00+00:00", window_end="2026-07-05T00:00:00+00:00",
    )

    assert report.legacy_event_count == 1
    assert report.matched_count == 0
    assert report.mismatched_count == 1


def test_replay_window_json_dict_is_serializable(trades_conn, forecasts_conn):
    import json

    insert_position_current(trades_conn, position_id="pos-1")
    trades_conn.commit()

    report = replay_window(
        trades_conn, forecasts_conn, {},
        window_start="2026-07-04T00:00:00+00:00", window_end="2026-07-05T00:00:00+00:00",
    )

    json.dumps(report.to_json_dict())  # must not raise
