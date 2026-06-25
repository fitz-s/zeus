import sqlite3

from src.execution import executor
from src.execution import command_recovery
from src.state import venue_command_repo


def test_submit_terminal_no_fill_can_release_entry_duplicate_lock() -> None:
    assert venue_command_repo._TRANSITIONS[("SUBMITTING", "EXPIRED")] == "EXPIRED"
    assert "EXPIRED" not in executor._ENTRY_DUPLICATE_OPEN_COMMAND_STATES


def test_review_required_still_blocks_ambiguous_entry_duplicate_lock() -> None:
    assert venue_command_repo._TRANSITIONS[("SUBMITTING", "REVIEW_REQUIRED")] == "REVIEW_REQUIRED"
    assert "REVIEW_REQUIRED" in executor._ENTRY_DUPLICATE_OPEN_COMMAND_STATES


def test_terminal_no_fill_requires_explicit_zero_matched_size() -> None:
    sqlite_conn = sqlite3.connect(":memory:")
    sqlite_conn.row_factory = sqlite3.Row
    proven, reason = command_recovery._terminal_point_order_zero_fill_proven(
        sqlite_conn,
        command_id="cmd1",
        point_order={"status": "CANCELLED", "matched_size": "0"},
    )
    assert proven is True
    assert reason == "terminal_zero_fill_proven"

    missing, missing_reason = command_recovery._terminal_point_order_zero_fill_proven(
        sqlite_conn,
        command_id="cmd1",
        point_order={"status": "CANCELLED"},
    )
    assert missing is False
    assert missing_reason == "terminal_matched_size_missing"

    positive, positive_reason = command_recovery._terminal_point_order_zero_fill_proven(
        sqlite_conn,
        command_id="cmd1",
        point_order={"status": "CANCELLED", "matched_size": "1.25"},
    )
    assert positive is False
    assert positive_reason == "terminal_matched_size_positive_or_invalid"

    sqlite_conn.execute(
        "CREATE TABLE venue_trade_facts (command_id TEXT, state TEXT, filled_size TEXT)"
    )
    sqlite_conn.execute(
        "INSERT INTO venue_trade_facts VALUES ('cmd1', 'MATCHED', '0.5')"
    )
    trade_fact, trade_fact_reason = command_recovery._terminal_point_order_zero_fill_proven(
        sqlite_conn,
        command_id="cmd1",
        point_order={"status": "CANCELLED", "matched_size": "0"},
    )
    assert trade_fact is False
    assert trade_fact_reason == "terminal_positive_trade_fact_exists"
