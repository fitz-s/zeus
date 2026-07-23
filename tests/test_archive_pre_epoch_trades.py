# Created: 2026-07-23
# Last audited: 2026-07-23
# Authority basis: operator directive 2026-07-2x "清空7月之前所有的交易记录作为archive
#   不要再分析"; companion to scripts/ops/archive_pre_epoch_trades.py.
"""Fixture-only tests for scripts/ops/archive_pre_epoch_trades.py.

Builds a real trade-DB schema via src.state.db.init_schema_trade_only, seeds
synthetic pre/post-epoch rows (including the abort case: an OPEN position with
a pre-epoch command), and exercises dry-run, precondition-abort, and the full
archive+delete+reconcile path. Never touches state/zeus_trades.db.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "archive_pre_epoch_trades.py"

_spec = importlib.util.spec_from_file_location("archive_pre_epoch_trades", SCRIPT)
apt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apt)

sys.path.insert(0, str(ROOT))
from src.state.db import init_schema_trade_only  # noqa: E402

EPOCH = "2026-07-01T00:00:00Z"
PRE = "2026-06-15T00:00:00Z"     # well before epoch
POST = "2026-07-10T00:00:00Z"    # well after epoch


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema_trade_only(conn)
    conn.commit()
    return conn


def _mk_position(conn, position_id: str, *, phase: str, updated_at: str) -> None:
    conn.execute(
        """
        INSERT INTO position_current
            (position_id, phase, strategy_key, updated_at, temperature_metric)
        VALUES (?, ?, 'strategy_a', ?, 'high')
        """,
        (position_id, phase, updated_at),
    )


def _mk_position_event(conn, event_id: str, position_id: str, seq: int, occurred_at: str) -> None:
    conn.execute(
        """
        INSERT INTO position_events
            (event_id, position_id, sequence_no, event_type, occurred_at,
             strategy_key, source_module, env, payload_json)
        VALUES (?, ?, ?, 'SETTLED', ?, 'strategy_a', 'test', 'test', '{}')
        """,
        (event_id, position_id, seq, occurred_at),
    )


def _mk_command(conn, command_id: str, position_id: str, *, created_at: str) -> None:
    conn.execute(
        """
        INSERT INTO venue_commands
            (command_id, snapshot_id, envelope_id, position_id, decision_id,
             idempotency_key, intent_kind, market_id, token_id, side, size,
             price, state, created_at, updated_at)
        VALUES (?, 'snap-1', 'env-1', ?, 'dec-1', ?, 'ENTRY', 'mkt-1', 'tok-1',
                'BUY', 10.0, 0.5, 'FILLED', ?, ?)
        """,
        (command_id, position_id, f"idem-{command_id}", created_at, created_at),
    )


def _mk_order_fact(conn, command_id: str, *, observed_at: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO venue_order_facts
            (venue_order_id, command_id, state, source, observed_at,
             local_sequence, raw_payload_hash)
        VALUES (?, ?, 'MATCHED', 'REST', ?, 1, 'hash1')
        """,
        (f"vo-{command_id}", command_id, observed_at),
    )
    return cur.lastrowid


def _mk_trade_fact(conn, command_id: str, *, observed_at: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO venue_trade_facts
            (trade_id, venue_order_id, command_id, state, filled_size,
             fill_price, source, observed_at, local_sequence, raw_payload_hash)
        VALUES (?, ?, ?, 'MATCHED', '10', '0.5', 'REST', ?, 1, 'hash2')
        """,
        (f"trade-{command_id}", f"vo-{command_id}", command_id, observed_at),
    )
    return cur.lastrowid


def _mk_position_lot(conn, position_id: str, command_id: str, trade_fact_id: int, *, observed_at: str) -> None:
    # state='SETTLED' bypasses the OPTIMISTIC/CONFIRMED trade-authority triggers.
    conn.execute(
        """
        INSERT INTO position_lots
            (position_id, state, shares, entry_price_avg, source_command_id,
             source_trade_fact_id, captured_at, state_changed_at, source,
             observed_at, local_sequence, raw_payload_hash)
        VALUES (?, 'SETTLED', '10', '0.5', ?, ?, ?, ?, 'REST', ?, 1, 'hash3')
        """,
        (position_id, command_id, trade_fact_id, observed_at, observed_at, observed_at),
    )


def _mk_command_event(conn, command_id: str, *, occurred_at: str) -> None:
    conn.execute(
        """
        INSERT INTO venue_command_events
            (event_id, command_id, sequence_no, event_type, occurred_at, state_after)
        VALUES (?, ?, 1, 'FILLED', ?, 'FILLED')
        """,
        (f"cmdevt-{command_id}", command_id, occurred_at),
    )


def _mk_exit_mutex(conn, command_id: str, *, acquired_at: str) -> None:
    conn.execute(
        """
        INSERT INTO exit_mutex_holdings (mutex_key, command_id, acquired_at, released_at)
        VALUES (?, ?, ?, ?)
        """,
        (f"mutex-{command_id}", command_id, acquired_at, acquired_at),
    )


def _mk_trade_decision(conn, *, timestamp: str) -> None:
    conn.execute(
        """
        INSERT INTO trade_decisions
            (market_id, bin_label, direction, size_usd, price, timestamp,
             p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction)
        VALUES ('mkt-1', 'bin-1', 'buy_yes', 10.0, 0.5, ?, 0.5, 0.5, 0.1, 0.4, 0.6, 0.1)
        """,
        (timestamp,),
    )


def _mk_settlement_command(conn, command_id: str, *, requested_at: str, state: str = "REDEEM_CONFIRMED") -> None:
    conn.execute(
        """
        INSERT INTO settlement_commands
            (command_id, state, condition_id, market_id, payout_asset, requested_at)
        VALUES (?, ?, 'cond-1', 'mkt-1', 'pUSD', ?)
        """,
        (command_id, state, requested_at),
    )


def _mk_settlement_event(conn, command_id: str) -> None:
    conn.execute(
        """
        INSERT INTO settlement_command_events (command_id, event_type, payload_hash)
        VALUES (?, 'REDEEM_INTENT_CREATED', 'h1')
        """,
        (command_id,),
    )


def _mk_wallet_fill_observation(conn, *, observed_at: str) -> None:
    conn.execute(
        """
        INSERT INTO wallet_fill_observations
            (trade_id, observed_at, raw_payload_hash, disposition)
        VALUES ('wf-1', ?, 'wfhash', 'ZEUS_ATTRIBUTED')
        """,
        (observed_at,),
    )


def _mk_payout_observation(conn, *, observed_at: str) -> None:
    conn.execute(
        """
        INSERT INTO payout_observations
            (condition_id, outcome_index, state, observed_at)
        VALUES ('cond-1', 0, 'UNRESOLVED', ?)
        """,
        (observed_at,),
    )


def _seed_full_pre_epoch_position(conn, *, position_id="pos-archived-1", command_id="cmd-1") -> None:
    """One fully-closed, pre-epoch position with the whole FK chain, all rows
    strictly before epoch — the primary case the archive should sweep."""
    _mk_position(conn, position_id, phase="settled", updated_at=PRE)
    _mk_position_event(conn, f"evt-{position_id}-1", position_id, 1, PRE)
    _mk_command(conn, command_id, position_id, created_at=PRE)
    _mk_order_fact(conn, command_id, observed_at=PRE)
    trade_fact_id = _mk_trade_fact(conn, command_id, observed_at=PRE)
    _mk_position_lot(conn, position_id, command_id, trade_fact_id, observed_at=PRE)
    _mk_command_event(conn, command_id, occurred_at=PRE)
    _mk_exit_mutex(conn, command_id, acquired_at=PRE)


def _seed_post_epoch_control_position(conn, *, position_id="pos-post-1", command_id="cmd-post-1") -> None:
    """A structurally-identical position/command chain, entirely post-epoch —
    must survive untouched. Proves the script doesn't over-delete."""
    _mk_position(conn, position_id, phase="settled", updated_at=POST)
    _mk_position_event(conn, f"evt-{position_id}-1", position_id, 1, POST)
    _mk_command(conn, command_id, position_id, created_at=POST)
    _mk_order_fact(conn, command_id, observed_at=POST)
    trade_fact_id = _mk_trade_fact(conn, command_id, observed_at=POST)
    _mk_position_lot(conn, position_id, command_id, trade_fact_id, observed_at=POST)
    _mk_command_event(conn, command_id, occurred_at=POST)
    _mk_exit_mutex(conn, command_id, acquired_at=POST)


def _table_count(conn, table: str) -> int:
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def _all_trade_class_tables(conn):
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path, capsys):
    db_path = tmp_path / "zeus_trades.db"
    conn = _connect(db_path)
    _seed_full_pre_epoch_position(conn)
    _seed_post_epoch_control_position(conn)
    _mk_trade_decision(conn, timestamp=PRE)
    conn.commit()
    conn.close()

    before = {t: _table_count(sqlite3.connect(str(db_path)), t) for t in ("position_current", "position_events", "venue_commands")}

    code = apt.main(["--db", str(db_path), "--epoch", EPOCH])
    assert code == 0

    after_conn = sqlite3.connect(str(db_path))
    for t in ("position_current", "position_events", "venue_commands"):
        assert _table_count(after_conn, t) == before[t], f"{t} row count changed on dry-run"
    assert not (tmp_path / "archive").exists()

    out = capsys.readouterr().out
    assert "position_current: 1" in out
    assert "Precondition gate: PASS" in out


def test_open_position_with_pre_epoch_command_aborts(tmp_path, capsys):
    db_path = tmp_path / "zeus_trades.db"
    conn = _connect(db_path)
    _mk_position(conn, "pos-open-1", phase="active", updated_at=PRE)
    _mk_command(conn, "cmd-open-1", "pos-open-1", created_at=PRE)
    conn.commit()
    conn.close()

    code = apt.main(["--db", str(db_path), "--epoch", EPOCH])
    assert code == 1
    out = capsys.readouterr().out
    assert "PRECONDITION VIOLATION" in out
    assert "pos-open-1" in out

    # --execute must also abort, with an ack supplied, and touch nothing.
    before = _table_count(sqlite3.connect(str(db_path)), "venue_commands")
    code = apt.main(["--db", str(db_path), "--epoch", EPOCH, "--execute", "--i-have-a-backup"])
    assert code == 1
    after = _table_count(sqlite3.connect(str(db_path)), "venue_commands")
    assert after == before
    assert not (tmp_path / "archive").exists()


def test_execute_requires_backup_ack(tmp_path):
    db_path = tmp_path / "zeus_trades.db"
    conn = _connect(db_path)
    conn.commit()
    conn.close()

    with pytest.raises(SystemExit):
        apt.main(["--db", str(db_path), "--epoch", EPOCH, "--execute"])


def test_archive_and_delete_reconciliation(tmp_path, capsys):
    db_path = tmp_path / "zeus_trades.db"
    conn = _connect(db_path)
    _seed_full_pre_epoch_position(conn, position_id="pos-archived-1", command_id="cmd-1")
    _seed_post_epoch_control_position(conn, position_id="pos-post-1", command_id="cmd-post-1")
    _mk_trade_decision(conn, timestamp=PRE)
    _mk_trade_decision(conn, timestamp=POST)
    _mk_settlement_command(conn, "settle-1", requested_at=PRE)
    _mk_settlement_event(conn, "settle-1")
    _mk_settlement_command(conn, "settle-post-1", requested_at=POST)
    _mk_wallet_fill_observation(conn, observed_at=PRE)
    _mk_payout_observation(conn, observed_at=PRE)
    conn.commit()
    conn.close()

    guarded_triggers = [
        "trg_position_events_no_delete",
        "position_lots_no_delete",
        "venue_order_facts_no_delete",
        "venue_trade_facts_no_delete",
    ]

    code = apt.main(["--db", str(db_path), "--epoch", EPOCH, "--execute", "--i-have-a-backup"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "OK: archive complete, all tables reconciled." in out

    live = sqlite3.connect(str(db_path))
    live.row_factory = sqlite3.Row

    # pre-epoch chain fully removed from the live DB
    assert _table_count(live, "position_current") == 1  # only the post-epoch control row remains
    assert live.execute("SELECT 1 FROM position_current WHERE position_id='pos-archived-1'").fetchone() is None
    assert live.execute("SELECT 1 FROM position_current WHERE position_id='pos-post-1'").fetchone() is not None
    assert live.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 1
    assert live.execute("SELECT 1 FROM venue_commands WHERE command_id='cmd-post-1'").fetchone() is not None
    assert live.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 1
    assert live.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 1
    assert live.execute("SELECT COUNT(*) FROM venue_order_facts").fetchone()[0] == 1
    assert live.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 1
    assert live.execute("SELECT COUNT(*) FROM exit_mutex_holdings").fetchone()[0] == 1
    assert live.execute("SELECT COUNT(*) FROM trade_decisions").fetchone()[0] == 1
    assert live.execute("SELECT COUNT(*) FROM settlement_commands").fetchone()[0] == 1
    assert live.execute("SELECT COUNT(*) FROM settlement_command_events").fetchone()[0] == 0

    # excluded/append-only tables are completely untouched
    assert _table_count(live, "wallet_fill_observations") == 1
    assert _table_count(live, "payout_observations") == 1

    # guarded triggers were restored, not left dropped
    trig_names = {
        r["name"]
        for r in live.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
    }
    for t in guarded_triggers:
        assert t in trig_names, f"{t} was not restored after archive"

    # the append-only guard is actually live again: a stray DELETE now aborts
    with pytest.raises(sqlite3.IntegrityError):
        live.execute("DELETE FROM position_events")
    live.close()

    # archive DB has exactly the archived rows
    archive_path = tmp_path / "archive" / f"zeus_trades_pre{EPOCH[:10]}.db"
    assert archive_path.exists()
    arc = sqlite3.connect(str(archive_path))
    assert arc.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 1
    assert arc.execute("SELECT 1 FROM position_current WHERE position_id='pos-archived-1'").fetchone() is not None
    assert arc.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 1
    assert arc.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 1
    assert arc.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 1
    assert arc.execute("SELECT COUNT(*) FROM venue_order_facts").fetchone()[0] == 1
    assert arc.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 1
    assert arc.execute("SELECT COUNT(*) FROM trade_decisions").fetchone()[0] == 1
    assert arc.execute("SELECT COUNT(*) FROM settlement_commands").fetchone()[0] == 1
    assert arc.execute("SELECT COUNT(*) FROM settlement_command_events").fetchone()[0] == 1
    arc.close()


def test_orphan_residue_positions_swept(tmp_path, capsys):
    """A position with rows in position_events/position_lots but NO
    position_current row at all (already-absent) is archived if every row is
    pre-epoch; left alone if any row is post-epoch."""
    db_path = tmp_path / "zeus_trades.db"
    conn = _connect(db_path)

    # orphan-safe: all rows pre-epoch, no position_current row
    _mk_position_event(conn, "evt-orphan-1", "pos-orphan-1", 1, PRE)
    cmd_o1 = "cmd-orphan-1"
    _mk_command(conn, cmd_o1, "pos-orphan-1", created_at=PRE)
    tf = _mk_trade_fact(conn, cmd_o1, observed_at=PRE)
    _mk_position_lot(conn, "pos-orphan-1", cmd_o1, tf, observed_at=PRE)

    # orphan-unsafe: one row is post-epoch -> must NOT be swept
    _mk_position_event(conn, "evt-orphan-2-a", "pos-orphan-2", 1, PRE)
    _mk_position_event(conn, "evt-orphan-2-b", "pos-orphan-2", 2, POST)
    conn.commit()
    conn.close()

    code = apt.main(["--db", str(db_path), "--epoch", EPOCH, "--execute", "--i-have-a-backup"])
    assert code == 0

    live = sqlite3.connect(str(db_path))
    assert live.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id='pos-orphan-1'"
    ).fetchone()[0] == 0
    assert live.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id='pos-orphan-2'"
    ).fetchone()[0] == 2, "position with a post-epoch row must not be swept as orphan residue"
    live.close()


def test_fk_delete_order_children_before_parents():
    """DELETE_ORDER must place every declared-FK child before its parent."""
    order = list(apt.DELETE_ORDER)
    for child, parent in apt._KNOWN_CHILD_EDGES:
        assert order.index(child) < order.index(parent), f"{child} must precede {parent} in DELETE_ORDER"


def test_no_unhandled_fk_edges_on_real_schema(tmp_path):
    """Runtime FK discovery against the REAL init_schema_trade_only schema
    must find nothing beyond what this script already declares handling for."""
    db_path = tmp_path / "zeus_trades.db"
    conn = _connect(db_path)
    apt._assert_no_unhandled_fk_edges(conn)  # must not raise
    conn.close()
