# Created: 2026-10-06
# Last reused or audited: 2026-10-06
# Lifecycle: created=2026-10-06; last_reviewed=2026-10-06; last_reused=never
# Purpose: Antibody for scripts/repair_historical_position_economics_2026_10_06.py.
# Reuse: pytest tests/test_repair_historical_position_economics_2026_10_06.py;
#   the fixture schema is the live trade-DB DDL captured read-only 2026-10-06.
# Authority basis: AGENTS.md section 2 lifecycle law; src/state/close_economics.py;
#   src/state/ledger.append_many_and_project (INV-04 / INV-08).
"""Covers: exact booked values, idempotent rerun, original events never mutated,
event+projection atomicity, and exclusion when native evidence is missing."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import repair_historical_position_economics_2026_10_06 as repair
from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS

SCHEMA = Path(__file__).parent / "fixtures" / "zeus_trades_live_position_schema_2026_10_06.sql"
NOW = "2026-10-06T00:00:00+00:00"
POS = "39db44dc-144"
COND = "0xb34566938bb7f587230ccf45f52083b4b7ac80638e36b0368980884a04d1074e"
YES = "115637747034373108298961628394099431465596677942035548840983120699796371387364"
NO = "28413108201997962838017038553539941646049981149641410084885525670342175758704"
ORDER = "0x32c5c93628c7e9c2ae592c4b9d85a4b3bff7e2f337830e2a1f456f2dc114de86"
FILL_TX = "0x8c0d182e3e4f1f4d86446c17dbea1e73beebc2fb4f86bffcd3c6146ad619e323"
SNAP = "ems2-83df3b96cb8cc5817ac894e8de2225b7b6e9a158"
TARGET = {POS: repair.TARGETS[POS]}


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _seed(conn: sqlite3.Connection, *, cash_fact: bool = True, payout_won: bool = True) -> None:
    pc = {c: None for c in CANONICAL_POSITION_CURRENT_COLUMNS} | {
        "position_id": POS, "phase": "voided", "trade_id": POS, "market_id": COND, "city": "Seoul",
        "cluster": "Seoul", "target_date": "2026-06-23",
        "bin_label": "Will the highest temperature in Seoul be 28°C on June 23?", "direction": "buy_no",
        "unit": "C", "size_usd": 16.895, "shares": 0.0, "cost_basis_usd": 0.0, "entry_price": 0.0,
        "p_posterior": 0.8767, "decision_snapshot_id": SNAP, "strategy_key": "opening_inertia",
        "chain_state": "local_only", "token_id": YES, "no_token_id": NO, "condition_id": COND,
        "order_id": ORDER, "order_status": "canceled", "updated_at": "2026-06-21T10:39:36.369196+00:00",
        "temperature_metric": "high", "entry_ci_width": 0.0, "exit_retry_count": 0,
    }
    conn.execute(
        f"INSERT INTO position_current ({', '.join(pc)}) VALUES ({', '.join('?' for _ in pc)})", tuple(pc.values())
    )
    for seq, etype, before, after in (
        (1, "POSITION_OPEN_INTENT", None, "pending_entry"),
        (2, "ENTRY_ORDER_POSTED", "pending_entry", "pending_entry"),
        (3, "ENTRY_ORDER_VOIDED", "pending_entry", "voided"),
    ):
        conn.execute(
            "INSERT INTO position_events (event_id, position_id, event_version, sequence_no, event_type, occurred_at,"
            " phase_before, phase_after, strategy_key, idempotency_key, source_module, env, payload_json)"
            " VALUES (?, ?, 1, ?, ?, ?, ?, ?, 'opening_inertia', ?, 'src.execution.command_recovery', 'live', '{}')",
            (f"{POS}:{seq}", POS, seq, etype, f"2026-06-21T10:3{seq}:00+00:00", before, after, f"{POS}:{seq}"),
        )
    conn.execute(
        "INSERT INTO venue_commands (command_id, snapshot_id, envelope_id, position_id, decision_id, idempotency_key,"
        " intent_kind, market_id, token_id, side, size, price, venue_order_id, state, created_at, updated_at)"
        " VALUES ('37c227a8a0f24596', ?, 'env', ?, 'dec', 'idem', 'ENTRY', '2625814', ?, 'BUY', 27.25, 0.62, ?,"
        " 'CANCELLED', '2026-06-21T10:33:06+00:00', '2026-06-21T10:39:36+00:00')",
        (SNAP, POS, NO, ORDER),
    )
    for seq, state in ((1, "MATCHED"), (2, "CONFIRMED")):
        conn.execute(
            "INSERT INTO venue_trade_facts (trade_id, venue_order_id, command_id, state, filled_size, fill_price,"
            " tx_hash, source, observed_at, local_sequence, raw_payload_hash, raw_payload_json)"
            " VALUES ('6deac112', ?, '37c227a8a0f24596', ?, '5', '0.62', ?, 'REST', ?, ?, 'h', '{}')",
            (ORDER, state, FILL_TX, f"2026-06-2{seq}T10:39:35+00:00", seq),
        )
    if cash_fact:
        proof = {"decoded": {"events": [{
            "order_hash": ORDER, "token_id": NO, "side": "BUY", "shares_atoms": 5_000_000,
            "principal_atoms": 3_100_000, "fee_atoms": 0,
        }]}}
        conn.execute(
            "INSERT INTO venue_fill_cash_facts (chain_id, tx_hash, wallet, status, reason, block_number, block_hash,"
            " finalized_number, finalized_hash, observed_at, proof_hash, proof_json)"
            " VALUES (137, ?, ?, 'PROVEN', 'ok', 88888826, ?, 88888900, ?, '2026-09-11T00:00:00+00:00', 'p', ?)",
            (FILL_TX, repair.WALLET, "0x" + "a" * 64, "0x" + "b" * 64, json.dumps(proof)),
        )
    conn.execute(
        "INSERT INTO executable_market_snapshots (snapshot_id, gamma_market_id, event_id, event_slug, condition_id,"
        " question_id, yes_token_id, no_token_id, enable_orderbook, active, closed, min_tick_size, min_order_size,"
        " fee_details_json, token_map_json, neg_risk, orderbook_top_bid, orderbook_top_ask, orderbook_depth_json,"
        " raw_gamma_payload_hash, raw_clob_market_info_hash, raw_orderbook_hash, authority_tier, captured_at,"
        " freshness_deadline) VALUES (?, '2625814', 'ev', 'highest-temperature-in-seoul-on-june-23-2026', ?, 'q',"
        " ?, ?, 1, 1, 0, '0.01', '5', '{}', '{}', 1, '0.6', '0.62', '{}', 'h', 'h', 'h', 'CLOB',"
        " '2026-06-21T10:33:00+00:00', '2026-06-21T10:34:00+00:00')",
        (SNAP, COND, YES, NO),
    )
    numerators = (0, 1) if payout_won else (1, 0)
    for index, numerator in enumerate(numerators):
        conn.execute(
            "INSERT INTO payout_observations (condition_id, outcome_index, payout_numerator, payout_denominator,"
            " state, block_number, block_hash, observed_at, source) VALUES (?, ?, ?, 1, ?, 90569669, ?,"
            " '2026-07-20T14:58:22+00:00', 'chain_rpc_finalized_v1')",
            (COND, index, numerator, "RESOLVED_NONZERO" if numerator else "RESOLVED_ZERO", "0x" + "c" * 64),
        )
    conn.commit()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "zeus_trades.db"
    conn = _connect(path)
    conn.executescript(SCHEMA.read_text())
    conn.close()
    return path


def _seeded(db: Path, **kwargs) -> Path:
    conn = _connect(db)
    _seed(conn, **kwargs)
    conn.close()
    return db


def _plan(db: Path) -> list[repair.Item]:
    conn = _connect(db)
    try:
        return repair.plan(conn, now=NOW, targets=TARGET)
    finally:
        conn.close()


def _events(db: Path) -> list[dict]:
    conn = _connect(db)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM position_events ORDER BY position_id, sequence_no")]
    finally:
        conn.close()


def _current(db: Path) -> dict:
    conn = _connect(db)
    try:
        return dict(conn.execute("SELECT * FROM position_current WHERE position_id = ?", (POS,)).fetchone())
    finally:
        conn.close()


def test_books_redeemed_winner_at_fill_derived_values(db: Path) -> None:
    _seeded(db)
    [item] = _plan(db)
    assert item.status == "BOOK", item.reasons
    repair.apply(db, [item])

    row = _current(db)
    assert row["phase"] == "settled"
    assert row["realized_pnl_usd"] == pytest.approx(1.90)
    assert (row["shares"], row["cost_basis_usd"], row["entry_price"]) == pytest.approx((5.0, 3.10, 0.62))
    assert (row["exit_price"], row["settlement_price"]) == (1.0, 1.0)
    assert row["settled_at"] == repair.TARGETS[POS].redeemed_at
    assert row["fill_authority"] == "cancelled_remainder"
    assert row["chain_state"] == "closed_redeemed"

    new = [e for e in _events(db) if e["source_module"] == repair.SOURCE_MODULE]
    assert [(e["sequence_no"], e["event_type"], e["phase_before"], e["phase_after"]) for e in new] == [
        (4, "REVIEW_REQUIRED", "voided", "active"),
        (5, "SETTLED", "active", "settled"),
    ]
    settled = json.loads(new[1]["payload_json"])
    assert settled["pnl"] == pytest.approx(1.90)
    assert settled["position_won"] is True and settled["outcome"] == 1
    assert settled["evidence_refs"]["redemption"]["payout_micro"] == 5_000_000
    assert all(e["occurred_at"] == repair.TARGETS[POS].redeemed_at for e in new)


def test_rerun_after_apply_is_noop(db: Path) -> None:
    _seeded(db)
    repair.apply(db, _plan(db))
    events, current = _events(db), _current(db)

    [again] = _plan(db)
    assert again.status == "NOOP"
    repair.apply(db, [again])
    assert _events(db) == events
    assert _current(db) == current


def test_original_events_are_never_mutated(db: Path) -> None:
    _seeded(db)
    before = _events(db)
    repair.apply(db, _plan(db))
    after = _events(db)
    assert after[: len(before)] == before
    assert len(after) == len(before) + 2


def test_event_and_projection_commit_or_roll_back_together(db: Path) -> None:
    _seeded(db)
    [item] = _plan(db)
    # Passes pre-validation, so both events are inserted before the projection
    # upsert rejects a non-binary payout: the event rows must roll back too.
    item.after["settlement_price"] = 0.5
    before_events, before_current = _events(db), _current(db)
    with pytest.raises(ValueError, match="InvalidSettlementPrice"):
        repair.apply(db, [item])
    assert _events(db) == before_events
    assert _current(db) == before_current


def test_stale_plan_is_refused(db: Path) -> None:
    _seeded(db)
    [item] = _plan(db)
    conn = _connect(db)
    conn.execute("UPDATE position_current SET updated_at = 'drifted' WHERE position_id = ?", (POS,))
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="stale"):
        repair.apply(db, [item])
    assert not [e for e in _events(db) if e["source_module"] == repair.SOURCE_MODULE]


@pytest.mark.parametrize(
    ("seed_kwargs", "targets", "reason"),
    [
        ({"cash_fact": False}, TARGET, "no PROVEN venue_fill_cash_facts"),
        ({"payout_won": False}, TARGET, "held token lost"),
        ({}, {POS: None}, "no verified hold-to-redemption"),
    ],
)
def test_missing_native_evidence_excludes(db: Path, seed_kwargs, targets, reason) -> None:
    _seeded(db, **seed_kwargs)
    conn = _connect(db)
    [item] = repair.plan(conn, now=NOW, targets=targets)
    conn.close()
    assert item.status == "EXCLUDE"
    assert any(reason in r for r in item.reasons), item.reasons
    before = _events(db)
    repair.apply(db, [item])
    assert _events(db) == before


def test_sold_token_is_excluded_not_settled(db: Path) -> None:
    _seeded(db)
    conn = _connect(db)
    conn.execute(
        "INSERT INTO venue_commands (command_id, snapshot_id, envelope_id, position_id, decision_id, idempotency_key,"
        " intent_kind, market_id, token_id, side, size, price, venue_order_id, state, created_at, updated_at)"
        " VALUES ('exit1', ?, 'env2', ?, 'exit', 'idem2', 'EXIT', ?, ?, 'SELL', 5, 0.8, '0xsell', 'FILLED',"
        " '2026-06-22T00:00:00+00:00', '2026-06-22T00:00:00+00:00')",
        (SNAP, POS, NO, NO),
    )
    conn.execute(
        "INSERT INTO venue_trade_facts (trade_id, venue_order_id, command_id, state, filled_size, fill_price, tx_hash,"
        " source, observed_at, local_sequence, raw_payload_hash) VALUES ('sell1', '0xsell', 'exit1', 'CONFIRMED',"
        " '5', '0.8', '0xselltx', 'REST', '2026-06-22T00:00:00+00:00', 1, 'h')"
    )
    conn.commit()
    conn.close()
    [item] = _plan(db)
    assert item.status == "EXCLUDE"
    assert any("SOLD" in r for r in item.reasons), item.reasons


def test_sibling_already_booked_is_noop(db: Path) -> None:
    _seeded(db)
    conn = _connect(db)
    sibling = _current(db) | {
        "position_id": "chain-only-x", "trade_id": "chain-only-x", "phase": "settled", "shares": 5.0,
        "realized_pnl_usd": 1.90, "strategy_key": "chain_only_reconciliation",
    }
    conn.execute(
        f"INSERT INTO position_current ({', '.join(sibling)}) VALUES ({', '.join('?' for _ in sibling)})",
        tuple(sibling.values()),
    )
    conn.commit()
    conn.close()
    [item] = _plan(db)
    assert item.status == "NOOP"
    assert "double-count" in item.reasons[0]


def test_dry_run_main_writes_nothing(db: Path, capsys) -> None:
    _seeded(db)
    before = (_events(db), _current(db))
    assert repair.main(["--trades-db", str(db)]) == 0
    assert (_events(db), _current(db)) == before
    assert "DRY-RUN: nothing written" in capsys.readouterr().out
