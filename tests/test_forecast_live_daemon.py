# Created: 2026-07-30
# Last reused/audited: 2026-08-22
# Lifecycle: created=2026-07-30; last_reviewed=2026-08-22; last_reused=2026-08-22
# Authority basis: operator-directed held SELL terminal-wake hotfix.
"""Held SELL terminal-wake completion antibodies."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.main as main
from src.runtime import reactor_wake


def _request(
    *,
    position_id: str,
    schema_version: int = 3,
    generation: str = "generation-1",
    probability_content_identity: str = "q-current",
    held_best_bid: float = 0.22,
    book_state: str = "EXECUTABLE",
) -> reactor_wake.HeldSellReauctionRequest:
    return reactor_wake.make_held_sell_reauction_request(
        position_id=position_id,
        family=("Paris", "2026-07-30", "low"),
        probability_content_identity=probability_content_identity,
        held_token_id=f"token-{position_id}",
        held_best_bid=held_best_bid,
        bid_observed_at="2026-07-30T12:00:00+00:00",
        probability_observed_at="2026-07-30T12:00:00+00:00",
        schema_version=schema_version,
        generation=generation,
        book_state=book_state,
    )


def _terminal_receipt(
    request: reactor_wake.HeldSellReauctionRequest,
    *,
    phase: str = "settled",
    chain_state: str = "synced",
    chain_shares: float | None = 4.0,
    settled_at: str = "2026-07-30T13:00:00+00:00",
    reason: str | None = None,
) -> reactor_wake.HeldSellReauctionReceipt:
    receipt_reason = reason or reactor_wake.held_sell_no_longer_exposed_reason(
        lifecycle_phase=phase,
        chain_state=chain_state,
        chain_shares=chain_shares,
        settled_at=settled_at,
    )
    return reactor_wake.HeldSellReauctionReceipt(
        request_id=request.request_id,
        material_identity=request.material_identity,
        generation=request.generation,
        schema_version=request.schema_version,
        scope_identity=request.scope_identity,
        book_state=request.book_state,
        attempt_identity=request.attempt_identity,
        status=reactor_wake.POSITION_NO_LONGER_EXPOSED,
        reason=receipt_reason or "INVALID_NO_EXPOSURE_PROOF",
        lifecycle_phase=phase,
        chain_state=chain_state,
        chain_shares=chain_shares,
        settled_at=settled_at,
    )


@pytest.mark.parametrize("schema_version", (1, 2, 3))
def test_terminal_receipt_completes_each_supported_request_version(
    tmp_path: Path, schema_version: int
) -> None:
    request = _request(
        position_id=f"terminal-v{schema_version}", schema_version=schema_version
    )
    receipt = _terminal_receipt(
        request,
        phase="economically_closed",
        chain_state="chain_confirmed_zero",
        chain_shares=0.0,
        settled_at="",
    )

    assert reactor_wake.persist_held_sell_reauction_receipts(
        (receipt,), path=tmp_path / "wake.json"
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=tmp_path / "wake.json"
    )


def test_terminal_receipt_requires_explicit_canonical_terminal_phase(
    tmp_path: Path,
) -> None:
    request = _request(position_id="bad-terminal")
    invalid = _terminal_receipt(request, phase="active")
    actuated_without_v3_q = reactor_wake.HeldSellReauctionReceipt(
        request_id=request.request_id,
        material_identity=request.material_identity,
        generation=request.generation,
        schema_version=3,
        scope_identity=request.scope_identity,
        attempt_identity=request.attempt_identity,
        status="ACTUATED",
        reason="must_remain_strict",
        selection_epoch_identity="epoch",
        sell_book_witness_identity="book",
    )

    assert not reactor_wake.persist_held_sell_reauction_receipts(
        (invalid,), path=tmp_path / "wake.json"
    )
    assert not reactor_wake.persist_held_sell_reauction_receipts(
        (actuated_without_v3_q,), path=tmp_path / "wake.json"
    )


@pytest.mark.parametrize("schema_version", (1, 2, 3))
@pytest.mark.parametrize(
    ("phase", "chain_state", "chain_shares", "settled_at"),
    (
        ("economically_closed", "unknown", 0.0, ""),
        ("economically_closed", "synced", None, ""),
        ("economically_closed", "synced", 1e-12, ""),
        ("settled", "synced", 4.0, ""),
    ),
)
def test_terminal_receipt_rejects_incomplete_chain_first_proof_for_all_versions(
    tmp_path: Path,
    schema_version: int,
    phase: str,
    chain_state: str,
    chain_shares: float | None,
    settled_at: str,
) -> None:
    request = _request(
        position_id=f"negative-{schema_version}-{phase}-{chain_state}",
        schema_version=schema_version,
    )
    receipt = _terminal_receipt(
        request,
        phase=phase,
        chain_state=chain_state,
        chain_shares=chain_shares,
        settled_at=settled_at,
    )

    assert not reactor_wake.persist_held_sell_reauction_receipts(
        (receipt,), path=tmp_path / "wake.json"
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=tmp_path / "wake.json"
    )


def test_terminal_receipt_is_idempotent_and_cannot_cover_hash_drift(
    tmp_path: Path,
) -> None:
    old = _request(
        position_id="same-position",
        generation="stable-generation",
        probability_content_identity="q-old",
        held_best_bid=0.0,
        book_state="NO_EXECUTABLE_BOOK",
    )
    fresh = _request(
        position_id="same-position",
        generation="stable-generation",
        probability_content_identity="q-fresh",
        held_best_bid=0.23,
    )
    first = _terminal_receipt(old, chain_shares=7.0)
    rewritten = _terminal_receipt(old, chain_shares=0.0)

    assert old.request_id != fresh.request_id
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (first,), path=tmp_path / "wake.json"
    )
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (rewritten,), path=tmp_path / "wake.json"
    )
    assert (
        reactor_wake._read_held_sell_reauction_receipt(
            old.request_id, path=tmp_path / "wake.json"
        )
        == first
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (old,), path=tmp_path / "wake.json"
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (fresh,), path=tmp_path / "wake.json"
    )


def _install_trade_reader(
    monkeypatch, tmp_path: Path, rows: tuple[tuple[object, ...], ...]
):
    db_path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT NOT NULL,
                chain_state TEXT,
                chain_shares REAL,
                settled_at TEXT
            )
            """
        )
        conn.executemany("INSERT INTO position_current VALUES (?, ?, ?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()

    def _reader():
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    monkeypatch.setattr("src.state.db.get_trade_connection_read_only", _reader)


def test_canonical_terminal_query_drains_only_explicit_no_exposure_rows(
    monkeypatch, tmp_path: Path
) -> None:
    economically_closed = _request(position_id="economically-closed")
    settled_with_residual = _request(position_id="settled-residual")
    admin_closed = _request(position_id="admin-closed")
    voided = _request(position_id="voided")
    active = _request(position_id="active")
    day0_window = _request(position_id="day0-window")
    pending_exit = _request(position_id="pending-exit")
    missing = _request(position_id="missing")
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        (
            (
                "economically-closed",
                "economically_closed",
                "chain_confirmed_zero",
                0.0,
                None,
            ),
            (
                "settled-residual",
                "settled",
                "synced",
                12.5,
                "2026-07-30T13:00:00+00:00",
            ),
            (
                "admin-closed",
                "admin_closed",
                "chain_confirmed_zero",
                0.0,
                None,
            ),
            ("voided", "voided", "closed_exited", 0.0, None),
            ("active", "active", "synced", 3.0, None),
            ("day0-window", "day0_window", "synced", 3.0, None),
            ("pending-exit", "pending_exit", "exit_pending", 3.0, None),
        ),
    )

    receipts = main._terminal_held_sell_reauction_receipts(
        (
            economically_closed,
            settled_with_residual,
            admin_closed,
            voided,
            active,
            day0_window,
            pending_exit,
            missing,
        )
    )

    assert {receipt.request_id for receipt in receipts} == {
        economically_closed.request_id,
        settled_with_residual.request_id,
        admin_closed.request_id,
        voided.request_id,
    }
    settled = next(
        receipt
        for receipt in receipts
        if receipt.request_id == settled_with_residual.request_id
    )
    assert (
        settled.lifecycle_phase,
        settled.chain_state,
        settled.chain_shares,
        settled.settled_at,
    ) == (
        "settled",
        "synced",
        12.5,
        "2026-07-30T13:00:00+00:00",
    )
    assert (
        settled.reason
        == reactor_wake.SELL_OBLIGATION_ENDED_BY_SETTLEMENT_ONLY
    )
    assert "REDEEM" not in settled.reason


def test_canonical_terminal_query_rejects_ambiguous_or_incomplete_proof(
    monkeypatch, tmp_path: Path
) -> None:
    requests = tuple(
        _request(position_id=position_id)
        for position_id in (
            "economic-unknown",
            "economic-null",
            "economic-positive",
            "settled-missing-time",
        )
    )
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        (
            ("economic-unknown", "economically_closed", "unknown", 0.0, None),
            ("economic-null", "economically_closed", "synced", None, None),
            ("economic-positive", "economically_closed", "synced", 1e-12, None),
            ("settled-missing-time", "settled", "synced", 8.0, None),
        ),
    )

    assert main._terminal_held_sell_reauction_receipts(requests) == ()


def test_canonical_terminal_query_retains_wake_on_trade_read_failure(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "src.state.db.get_trade_connection_read_only",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
    )

    assert (
        main._terminal_held_sell_reauction_receipts(
            (_request(position_id="db-failure"),)
        )
        == ()
    )


def _install_structural_win_reader(
    monkeypatch,
    tmp_path: Path,
    request: reactor_wake.HeldSellReauctionRequest,
    *,
    command_state: str = "EXPIRED",
    debt_overrides: dict[str, object] | None = None,
    monitor_overrides: dict[str, object] | None = None,
    debt_sequence: int = 10,
    monitor_sequence: int = 11,
) -> Path:
    db_path = tmp_path / "structural-win-trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                chain_state TEXT,
                chain_shares REAL,
                settled_at TEXT,
                direction TEXT,
                token_id TEXT,
                no_token_id TEXT,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                bin_label TEXT,
                condition_id TEXT
            )
            """
        )
        direction = "buy_no"
        bin_label = "Will the lowest temperature in Paris be 18°C on July 30?"
        condition_id = "condition-structural-win"
        conn.execute(
            "INSERT INTO position_current VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request.position_id,
                "day0_window",
                "synced",
                7.0,
                None,
                direction,
                f"yes-{request.position_id}",
                request.held_token_id,
                request.family[0],
                request.family[1],
                request.family[2],
                bin_label,
                condition_id,
            ),
        )
        conn.execute(
            """
            CREATE TABLE position_events (
                event_id TEXT PRIMARY KEY,
                position_id TEXT,
                sequence_no INTEGER,
                event_type TEXT,
                occurred_at TEXT,
                payload_json TEXT
            )
            """
        )
        obligation = {
            "schema_version": 4,
            "request_id": request.request_id,
            "material_identity": request.material_identity,
            "scope_identity": request.scope_identity,
            "generation": request.generation,
            "attempt_identity": request.attempt_identity,
            "position_id": request.position_id,
            "held_token_id": request.held_token_id,
            "state": "ARMED",
        }
        if debt_overrides:
            obligation.update(debt_overrides)
        debt_payload = {
            "release_reason": "GLOBAL_SELL_SNAPSHOT_REAUCTION_REQUIRED",
            "status": "durable_wake_reserved",
            "held_sell_reauction_obligation": obligation,
        }
        monitor_payload = {
            "applied_validations": [
                "day0_absorbing_hard_fact",
                "day0_hard_fact_structural_win_hold",
            ],
            "bin_label": bin_label,
            "city": request.family[0],
            "condition_id": condition_id,
            "direction": direction,
            "exit_decision_selected_method": "day0_absorbing_hard_fact",
            "exit_decision_should_exit": False,
            "exit_decision_trigger": "DAY0_HARD_FACT_STRUCTURAL_WIN_HOLD",
            "last_monitor_prob": 1.0,
            "last_monitor_prob_is_fresh": True,
            "monitor_probability_receipt": {
                "hard_fact_evidence": {"source": "ogimet_metar_lfpg"}
            },
            "selected_method": "day0_absorbing_hard_fact",
            "target_date": request.family[1],
        }
        if monitor_overrides:
            monitor_payload.update(monitor_overrides)
        conn.executemany(
            "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                (
                    "debt-event",
                    request.position_id,
                    debt_sequence,
                    "EXIT_RETRY_RELEASED",
                    "2026-07-30T12:00:00+00:00",
                    json.dumps(debt_payload, sort_keys=True),
                ),
                (
                    f"{request.position_id}:monitor_refreshed:{monitor_sequence}",
                    request.position_id,
                    monitor_sequence,
                    "MONITOR_REFRESHED",
                    "2026-07-30T12:01:00+00:00",
                    json.dumps(monitor_payload, sort_keys=True),
                ),
            ),
        )
        conn.execute(
            """
            CREATE TABLE venue_commands (
                command_id TEXT PRIMARY KEY,
                state TEXT,
                venue_order_id TEXT,
                idempotency_key TEXT,
                position_id TEXT,
                token_id TEXT,
                side TEXT,
                intent_kind TEXT,
                updated_at TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE venue_command_events (
                event_id TEXT PRIMARY KEY,
                command_id TEXT,
                sequence_no INTEGER,
                event_type TEXT,
                payload_json TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO venue_commands VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "prior-exit-command",
                command_state,
                None,
                "prior-exit-key",
                request.position_id,
                request.held_token_id,
                "SELL",
                "EXIT",
                "2026-07-30T12:00:00+00:00",
                "2026-07-30T12:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO venue_command_events VALUES (?, ?, ?, ?, ?)",
            (
                "prior-exit-event",
                "prior-exit-command",
                1,
                command_state,
                "{}",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        "src.state.db.get_trade_connection_read_only",
        lambda: sqlite3.connect(f"file:{db_path}?mode=ro", uri=True),
    )
    return db_path


def test_structural_win_supersedes_exact_v4_debt_after_terminal_command(
    monkeypatch, tmp_path: Path
) -> None:
    request = _request(position_id="structural-win", schema_version=4)
    _install_structural_win_reader(monkeypatch, tmp_path, request)

    receipts = main._terminal_held_sell_reauction_receipts((request,))

    assert len(receipts) == 1
    receipt = receipts[0]
    assert (
        receipt.status,
        receipt.reason,
        receipt.position_id,
        receipt.held_token_id,
        receipt.debt_sequence_no,
        receipt.monitor_sequence_no,
    ) == (
        reactor_wake.SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN,
        reactor_wake.SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN,
        request.position_id,
        request.held_token_id,
        10,
        11,
    )
    wake_path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-structural-win",
        held_sell_reauction_requests=(request,),
    )
    assert reactor_wake.persist_held_sell_reauction_receipts(
        receipts, path=wake_path
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=wake_path
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (request,),
        path=wake_path,
        allow_structural_win_supersession=True,
    )


@pytest.mark.parametrize(
    "mutation",
    (
        {"monitor_payload_sha256": "0" * 63},
        {"monitor_sequence_no": 10},
        {"monitor_probability_is_fresh": False},
        {"held_token_id": "different-held-token"},
        {"hard_fact_source": ""},
        {"hard_fact_finality": ""},
        {"hard_fact_source": "wu_icao_history"},
        {"hard_fact_finality": "FINAL_DAILY_SETTLEMENT"},
    ),
)
def test_structural_win_receipt_rejects_invalid_proof_or_lineage(
    monkeypatch,
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    request = _request(position_id="invalid-structural-receipt", schema_version=4)
    _install_structural_win_reader(monkeypatch, tmp_path, request)
    receipt = main._terminal_held_sell_reauction_receipts((request,))[0]
    wake_path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-invalid-structural-receipt",
        held_sell_reauction_requests=(request,),
    )

    assert not reactor_wake.persist_held_sell_reauction_receipts(
        (replace(receipt, **mutation),), path=wake_path
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=wake_path
    )


@pytest.mark.parametrize(
    "command_state",
    (
        "REVIEW_REQUIRED",
        "UNKNOWN",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        "ACKED",
        "UNRECOGNIZED_STATE",
    ),
)
def test_structural_win_cannot_supersede_nonterminal_or_unknown_command(
    monkeypatch, tmp_path: Path, command_state: str
) -> None:
    request = _request(position_id=f"blocked-{command_state}", schema_version=4)
    _install_structural_win_reader(
        monkeypatch,
        tmp_path,
        request,
        command_state=command_state,
    )

    assert main._terminal_held_sell_reauction_receipts((request,)) == ()


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ("hko_daily_api", True),
        ("ogimet_metar_lfpg", True),
        ("hko_hourly_accumulator_v1", False),
        ("wu_icao_history", False),
    ),
)
def test_structural_win_requires_absorbing_source_finality(
    monkeypatch,
    tmp_path: Path,
    source: str,
    expected: bool,
) -> None:
    request = _request(
        position_id=f"structural-source-{source}",
        schema_version=4,
    )
    _install_structural_win_reader(
        monkeypatch,
        tmp_path,
        request,
        monitor_overrides={
            "monitor_probability_receipt": {
                "hard_fact_evidence": {"source": source}
            }
        },
    )

    assert bool(main._terminal_held_sell_reauction_receipts((request,))) is expected


@pytest.mark.parametrize(
    ("monitor_overrides", "debt_overrides", "debt_sequence", "monitor_sequence"),
    (
        ({"last_monitor_prob": 0.99}, None, 10, 11),
        ({"last_monitor_prob_is_fresh": False}, None, 10, 11),
        ({"selected_method": "model_only_v1"}, None, 10, 11),
        ({"exit_decision_should_exit": True}, None, 10, 11),
        ({"exit_decision_trigger": "SELL_REVERSAL"}, None, 10, 11),
        ({"monitor_probability_receipt": {}}, None, 10, 11),
        (
            {
                "monitor_probability_receipt": {
                    "hard_fact_evidence": {"source": "wu_icao_history"}
                }
            },
            None,
            10,
            11,
        ),
        (None, {"attempt_identity": "stale-attempt"}, 10, 11),
        (None, {"schema_version": 3}, 10, 11),
        (None, {"schema_version": None}, 10, 11),
        (None, None, 11, 11),
    ),
)
def test_structural_win_supersession_rejects_stale_or_mismatched_proof(
    monkeypatch,
    tmp_path: Path,
    monitor_overrides: dict[str, object] | None,
    debt_overrides: dict[str, object] | None,
    debt_sequence: int,
    monitor_sequence: int,
) -> None:
    request = _request(position_id="mismatched-proof", schema_version=4)
    _install_structural_win_reader(
        monkeypatch,
        tmp_path,
        request,
        monitor_overrides=monitor_overrides,
        debt_overrides=debt_overrides,
        debt_sequence=debt_sequence,
        monitor_sequence=monitor_sequence,
    )

    assert main._terminal_held_sell_reauction_receipts((request,)) == ()


def test_structural_win_supersession_is_exact_and_leaves_sibling_bytes_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    matched = _request(position_id="matched-structural-win", schema_version=4)
    sibling = _request(position_id="sibling-still-pending", schema_version=4)
    _install_structural_win_reader(monkeypatch, tmp_path, matched)
    wake_path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-matched",
        held_sell_reauction_requests=(matched,),
    )
    sibling_wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-sibling",
        held_sell_reauction_requests=(sibling,),
    )
    sibling_file = reactor_wake._wake_queue_target(sibling_wake, path=wake_path)
    sibling_bytes = sibling_file.read_bytes()

    receipts = main._terminal_held_sell_reauction_receipts((matched, sibling))

    assert tuple(receipt.position_id for receipt in receipts) == (
        matched.position_id,
    )
    assert reactor_wake.persist_held_sell_reauction_receipts(
        receipts, path=wake_path
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (matched,), path=wake_path
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (matched,),
        path=wake_path,
        allow_structural_win_supersession=True,
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (sibling,), path=wake_path
    )
    assert sibling_file.read_bytes() == sibling_bytes


def _structural_win_coordinator(db_path: Path):
    from src.state.write_coordinator import DBIdentity, WriteCoordinator

    return WriteCoordinator({DBIdentity.TRADE: db_path})


def test_structural_win_atomic_revalidation_persists_and_acks_under_writer_lock(
    monkeypatch, tmp_path: Path
) -> None:
    request = _request(position_id="atomic-structural-win", schema_version=4)
    db_path = _install_structural_win_reader(monkeypatch, tmp_path, request)
    wake_path = tmp_path / "wake.json"
    wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-atomic-structural-win",
        held_sell_reauction_requests=(request,),
    )
    queue_file = reactor_wake._wake_queue_target(wake, path=wake_path)

    completed, failed = main._atomically_ack_structural_win_wakes(
        (wake,),
        wake_path=wake_path,
        coordinator=_structural_win_coordinator(db_path),
    )

    assert failed is False
    assert completed == (wake,)
    assert not queue_file.exists()
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=wake_path
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (request,),
        path=wake_path,
        allow_structural_win_supersession=True,
    )


@pytest.mark.parametrize("race", ("unknown_command", "newer_monitor_reversal"))
def test_structural_win_atomic_revalidation_closes_snapshot_to_ack_race(
    monkeypatch,
    tmp_path: Path,
    race: str,
) -> None:
    request = _request(position_id=f"race-{race}", schema_version=4)
    db_path = _install_structural_win_reader(monkeypatch, tmp_path, request)
    wake_path = tmp_path / "wake.json"
    wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id=f"wake-race-{race}",
        held_sell_reauction_requests=(request,),
    )
    queue_file = reactor_wake._wake_queue_target(wake, path=wake_path)
    queue_bytes = queue_file.read_bytes()
    assert main._terminal_held_sell_reauction_receipts((request,))

    conn = sqlite3.connect(db_path)
    try:
        if race == "unknown_command":
            conn.execute(
                "INSERT INTO venue_commands VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "racing-unknown-command",
                    "UNKNOWN",
                    None,
                    "racing-unknown-key",
                    request.position_id,
                    request.held_token_id,
                    "SELL",
                    "EXIT",
                    "2026-07-30T12:02:00+00:00",
                    "2026-07-30T12:02:00+00:00",
                ),
            )
            conn.execute(
                "INSERT INTO venue_command_events VALUES (?, ?, ?, ?, ?)",
                (
                    "racing-unknown-event",
                    "racing-unknown-command",
                    1,
                    "SUBMIT_UNKNOWN_SIDE_EFFECT",
                    "{}",
                ),
            )
        else:
            prior = conn.execute(
                """
                SELECT payload_json FROM position_events
                 WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
                """,
                (request.position_id,),
            ).fetchone()
            payload = json.loads(prior[0])
            payload.update(
                {
                    "last_monitor_prob": 0.4,
                    "selected_method": "model_only_v1",
                    "exit_decision_selected_method": "model_only_v1",
                    "exit_decision_should_exit": True,
                    "exit_decision_trigger": "SELL_REVERSAL",
                }
            )
            conn.execute(
                "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"{request.position_id}:monitor_refreshed:12",
                    request.position_id,
                    12,
                    "MONITOR_REFRESHED",
                    "2026-07-30T12:02:00+00:00",
                    json.dumps(payload, sort_keys=True),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    completed, failed = main._atomically_ack_structural_win_wakes(
        (wake,),
        wake_path=wake_path,
        coordinator=_structural_win_coordinator(db_path),
    )

    assert failed is True
    assert completed == ()
    assert queue_file.read_bytes() == queue_bytes
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=wake_path
    )


def test_multi_wake_ack_restores_all_visible_bytes_when_staging_fails(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / "wake.json"
    first = reactor_wake.publish_reactor_wake(
        source="test",
        reason="market_price_advanced",
        path=wake_path,
        wake_id="wake-stage-first",
    )
    second = reactor_wake.publish_reactor_wake(
        source="test",
        reason="forecast_posterior_advanced",
        path=wake_path,
        wake_id="wake-stage-second",
    )
    first_file = reactor_wake._wake_queue_target(first, path=wake_path)
    second_file = reactor_wake._wake_queue_target(second, path=wake_path)
    before = (first_file.read_bytes(), second_file.read_bytes())
    real_replace = reactor_wake.os.replace
    stage_count = 0

    def _fail_second_stage(source, destination):
        nonlocal stage_count
        if str(destination).endswith(".ack-stage"):
            stage_count += 1
            if stage_count == 2:
                raise OSError("injected second-stage failure")
        return real_replace(source, destination)

    monkeypatch.setattr(reactor_wake.os, "replace", _fail_second_stage)

    assert not reactor_wake.acknowledge_reactor_wakes(
        (first, second), path=wake_path
    )
    assert (first_file.read_bytes(), second_file.read_bytes()) == before


def test_multi_wake_ack_reports_success_after_all_are_staged(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / "wake.json"
    wakes = tuple(
        reactor_wake.publish_reactor_wake(
            source="test",
            reason=reason,
            path=wake_path,
            wake_id=f"wake-cleanup-{index}",
        )
        for index, reason in enumerate(
            ("market_price_advanced", "forecast_posterior_advanced")
        )
    )
    queue_files = tuple(
        reactor_wake._wake_queue_target(wake, path=wake_path) for wake in wakes
    )
    real_unlink = Path.unlink
    stage_cleanup_count = 0

    def _fail_second_hidden_cleanup(target, *args, **kwargs):
        nonlocal stage_cleanup_count
        if str(target).endswith(".ack-stage"):
            stage_cleanup_count += 1
            if stage_cleanup_count == 2:
                raise OSError("injected hidden cleanup failure")
        return real_unlink(target, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _fail_second_hidden_cleanup)

    assert reactor_wake.acknowledge_reactor_wakes(wakes, path=wake_path)
    assert all(not queue_file.exists() for queue_file in queue_files)


def test_listener_ack_staging_failure_never_runs_stale_structural_global_cut(
    monkeypatch, tmp_path: Path
) -> None:
    request = _request(position_id="listener-stage-failure", schema_version=4)
    db_path = _install_structural_win_reader(monkeypatch, tmp_path, request)
    wake_path = tmp_path / "wake.json"
    monkeypatch.setattr("src.config.state_path", lambda _filename: wake_path)
    wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-listener-stage-failure",
        held_sell_reauction_requests=(request,),
    )
    queue_file = reactor_wake._wake_queue_target(wake, path=wake_path)
    queue_bytes = queue_file.read_bytes()
    if not wake_path.exists():
        wake_path.write_bytes(queue_bytes)
    legacy_bytes = wake_path.read_bytes()
    actual_acknowledge_many = reactor_wake.acknowledge_reactor_wakes
    order: list[str] = []
    _install_listener_dependencies(monkeypatch, wake, order)
    monkeypatch.setattr(
        reactor_wake,
        "acknowledge_reactor_wakes",
        actual_acknowledge_many,
    )
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        "src.state.write_coordinator.default_runtime_write_coordinator",
        lambda: _structural_win_coordinator(db_path),
    )
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **_kwargs: order.append("global_cut") or True,
    )
    real_replace = reactor_wake.os.replace
    stage_count = 0

    def _fail_second_stage(source, destination):
        nonlocal stage_count
        if str(destination).endswith(".ack-stage"):
            stage_count += 1
            if stage_count == 2:
                raise OSError("injected listener stage failure")
        return real_replace(source, destination)

    monkeypatch.setattr(reactor_wake.os, "replace", _fail_second_stage)

    assert main._edli_reactor_wake_poll_once() is False
    assert order == []
    assert queue_file.read_bytes() == queue_bytes
    assert wake_path.read_bytes() == legacy_bytes


def _wake(*requests: reactor_wake.HeldSellReauctionRequest) -> reactor_wake.ReactorWake:
    return reactor_wake.ReactorWake(
        wake_id="wake-terminal",
        published_at=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc).isoformat(),
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        held_sell_reauction_requests=requests,
    )


def _install_listener_dependencies(monkeypatch, wake, order: list[str]) -> None:
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_edli_last_reactor_wake_id", None)
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    monkeypatch.setattr(
        main,
        "_edli_day0_post_monitor_yield",
        main._OneTurnWakeExclusion(),
    )
    monkeypatch.setattr(reactor_wake, "read_reactor_wake", lambda **_kwargs: wake)
    monkeypatch.setattr(
        reactor_wake, "coalescible_reactor_wakes", lambda _wake: (wake,)
    )
    monkeypatch.setattr(
        reactor_wake,
        "acknowledge_reactor_wake",
        lambda _wake: order.append("ack") or True,
    )
    monkeypatch.setattr(
        reactor_wake,
        "acknowledge_reactor_wakes",
        lambda _wakes: order.append("ack") or True,
    )


def _install_position_fill_scope_readers(
    monkeypatch,
    tmp_path: Path,
    *,
    event_rows: tuple[tuple[str, str, str], ...],
    position_rows: tuple[tuple[object, ...], ...],
) -> None:
    world_path = tmp_path / "zeus-world.db"
    world = sqlite3.connect(world_path)
    try:
        world.execute(
            "CREATE TABLE opportunity_events "
            "(event_id TEXT PRIMARY KEY, event_type TEXT, payload_json TEXT)"
        )
        world.executemany(
            "INSERT INTO opportunity_events VALUES (?, ?, ?)", event_rows
        )
        world.commit()
    finally:
        world.close()

    trade_path = tmp_path / "zeus_trades.db"
    trade = sqlite3.connect(trade_path)
    try:
        trade.execute(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                shares REAL,
                cost_basis_usd REAL,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT
            )
            """
        )
        trade.executemany(
            "INSERT INTO position_current VALUES (?, ?, ?, ?, ?, ?, ?)",
            position_rows,
        )
        trade.commit()
    finally:
        trade.close()

    def _reader(path: Path):
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    monkeypatch.setattr(
        main,
        "get_world_connection_read_only",
        lambda: _reader(world_path),
    )
    monkeypatch.setattr(
        "src.state.db.get_trade_connection_read_only",
        lambda: _reader(trade_path),
    )


def _position_fill_wake(*, wake_id: str = "wake-position-fill"):
    return reactor_wake.ReactorWake(
        wake_id=wake_id,
        published_at="2026-08-08T12:00:00+00:00",
        source="fill_tracker",
        reason="position_fill_projected",
        event_ids=("event-position-fill",),
    )


def _position_fill_event_payload(*position_ids: str) -> str:
    return json.dumps(
        {
            "redecision_origin": "position_fill",
            "position_fill_position_ids": list(position_ids),
        }
    )


def test_position_fill_scope_uses_current_local_only_position_family(
    monkeypatch, tmp_path: Path
) -> None:
    _install_position_fill_scope_readers(
        monkeypatch,
        tmp_path,
        event_rows=(
            (
                "event-position-fill",
                "EDLI_REDECISION_PENDING",
                _position_fill_event_payload("local-only"),
            ),
        ),
        position_rows=(
            (
                "local-only",
                "active",
                2.0,
                0.40,
                "Paris",
                "2026-08-08",
                "low",
            ),
        ),
    )

    assert main._position_fill_wake_held_families(("event-position-fill",)) == frozenset(
        {("Paris", "2026-08-08", "low")}
    )


def test_position_fill_scope_missing_current_identity_requires_full_book(
    monkeypatch, tmp_path: Path
) -> None:
    _install_position_fill_scope_readers(
        monkeypatch,
        tmp_path,
        event_rows=(
            (
                "event-position-fill",
                "EDLI_REDECISION_PENDING",
                _position_fill_event_payload("present", "missing"),
            ),
        ),
        position_rows=(
            (
                "present",
                "active",
                2.0,
                0.40,
                "Paris",
                "2026-08-08",
                "low",
            ),
        ),
    )

    assert main._position_fill_wake_held_families(("event-position-fill",)) is None


def test_finished_position_fill_wake_monitors_before_reactor_and_ack(
    monkeypatch, tmp_path: Path
) -> None:
    wake = _position_fill_wake()
    _install_position_fill_scope_readers(
        monkeypatch,
        tmp_path,
        event_rows=(
            (
                "event-position-fill",
                "EDLI_REDECISION_PENDING",
                _position_fill_event_payload("local-only"),
            ),
        ),
        position_rows=(
            (
                "local-only",
                "day0_window",
                2.0,
                0.40,
                "Paris",
                "2026-08-08",
                "low",
            ),
        ),
    )
    order: list[str] = []
    _install_listener_dependencies(monkeypatch, wake, order)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=False,
            finished=True,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: True)

    def _dispatch(wake_ids, target_families):
        assert wake_ids == (wake.wake_id,)
        assert target_families == frozenset({("Paris", "2026-08-08", "low")})
        order.append("monitor")
        with main._forecast_exit_monitor_attempts_lock:
            for wake_id in wake_ids:
                main._forecast_exit_monitor_attempts[wake_id] = True
        return True

    monkeypatch.setattr(main, "_dispatch_forecast_exit_monitor", _dispatch)
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **_kwargs: order.append("cycle") or True,
    )
    main._forecast_exit_monitor_attempts.clear()

    try:
        assert main._edli_reactor_wake_poll_once() is True
        assert order == ["monitor", "cycle", "ack"]
    finally:
        main._forecast_exit_monitor_attempts.clear()


def test_uncertain_position_fill_scope_uses_full_book_monitor(
    monkeypatch, tmp_path: Path
) -> None:
    wake = _position_fill_wake(wake_id="wake-position-fill-uncertain")
    _install_position_fill_scope_readers(
        monkeypatch,
        tmp_path,
        event_rows=(
            (
                "event-position-fill",
                "EDLI_REDECISION_PENDING",
                json.dumps({"redecision_origin": "position_fill"}),
            ),
        ),
        position_rows=(),
    )
    order: list[str] = []
    _install_listener_dependencies(monkeypatch, wake, order)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=False,
            finished=True,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: True)

    def _dispatch(wake_ids, target_families):
        assert target_families is None
        order.append("monitor")
        with main._forecast_exit_monitor_attempts_lock:
            for wake_id in wake_ids:
                main._forecast_exit_monitor_attempts[wake_id] = True
        return True

    monkeypatch.setattr(main, "_dispatch_forecast_exit_monitor", _dispatch)
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **_kwargs: order.append("cycle") or True,
    )
    main._forecast_exit_monitor_attempts.clear()

    try:
        assert main._edli_reactor_wake_poll_once() is True
        assert order == ["monitor", "cycle", "ack"]
    finally:
        main._forecast_exit_monitor_attempts.clear()


def test_position_fill_monitor_failure_retains_wake_for_retry(
    monkeypatch, tmp_path: Path
) -> None:
    wake = _position_fill_wake(wake_id="wake-position-fill-retry")
    _install_position_fill_scope_readers(
        monkeypatch,
        tmp_path,
        event_rows=(
            (
                "event-position-fill",
                "EDLI_REDECISION_PENDING",
                _position_fill_event_payload("local-only"),
            ),
        ),
        position_rows=(
            (
                "local-only",
                "pending_exit",
                2.0,
                0.40,
                "Paris",
                "2026-08-08",
                "low",
            ),
        ),
    )
    order: list[str] = []
    _install_listener_dependencies(monkeypatch, wake, order)
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=False,
            finished=True,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: True)
    attempts = iter((False, True))

    def _allow_retry() -> frozenset[str]:
        with main._forecast_exit_monitor_attempts_lock:
            main._forecast_exit_monitor_attempts.pop(wake.wake_id, None)
        return frozenset()

    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", _allow_retry)

    def _dispatch(wake_ids, _target_families):
        order.append("monitor")
        succeeded = next(attempts)
        with main._forecast_exit_monitor_attempts_lock:
            for wake_id in wake_ids:
                main._forecast_exit_monitor_attempts[wake_id] = succeeded
        return True

    monkeypatch.setattr(main, "_dispatch_forecast_exit_monitor", _dispatch)
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **_kwargs: order.append("cycle") or True,
    )
    main._forecast_exit_monitor_attempts.clear()

    try:
        assert main._edli_reactor_wake_poll_once() is False
        assert order == ["monitor"]
        assert main._edli_reactor_wake_poll_once() is True
        assert order == ["monitor", "monitor", "cycle", "ack"]
    finally:
        main._forecast_exit_monitor_attempts.clear()


def test_listener_persists_terminal_before_exact_cut_and_acks_mixed_batch(
    monkeypatch,
) -> None:
    terminal = _request(position_id="terminal", schema_version=1)
    active = _request(position_id="active", schema_version=1)
    wake = _wake(terminal, active)
    order: list[str] = []
    _install_listener_dependencies(monkeypatch, wake, order)
    monkeypatch.setattr(
        main,
        "_terminal_held_sell_reauction_receipts",
        lambda _requests: (_terminal_receipt(terminal),),
    )
    monkeypatch.setattr(
        reactor_wake,
        "persist_held_sell_reauction_receipts",
        lambda _receipts: order.append("persist") or True,
    )
    cycle_finished = False

    def _completed(requests):
        request_ids = {request.request_id for request in requests}
        if request_ids == {terminal.request_id}:
            return True
        if request_ids == {active.request_id}:
            return cycle_finished
        return cycle_finished

    def _cycle(**kwargs):
        nonlocal cycle_finished
        assert kwargs["producer_held_sell_reauction_requests"] == (active,)
        order.append("cycle")
        cycle_finished = True
        return True

    monkeypatch.setattr(
        reactor_wake, "held_sell_reauction_requests_completed", _completed
    )
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _cycle)

    assert main._edli_reactor_wake_poll_once() is True
    assert order == ["persist", "cycle", "ack"]


def test_listener_acks_old_terminal_wake_but_never_short_circuits_active(
    monkeypatch,
) -> None:
    terminal = _request(position_id="terminal-only", schema_version=1)
    terminal_wake = _wake(terminal)
    terminal_order: list[str] = []
    _install_listener_dependencies(monkeypatch, terminal_wake, terminal_order)
    monkeypatch.setattr(
        main,
        "_terminal_held_sell_reauction_receipts",
        lambda _requests: (_terminal_receipt(terminal),),
    )
    monkeypatch.setattr(
        reactor_wake,
        "persist_held_sell_reauction_receipts",
        lambda _receipts: terminal_order.append("persist") or True,
    )
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: terminal_order.append("complete") or True,
    )
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **_kwargs: pytest.fail("terminal wake must not run an exact cut"),
    )

    assert main._edli_reactor_wake_poll_once() is True
    assert terminal_order == ["persist", "complete", "ack"]

    active = _request(position_id="active-only", schema_version=1)
    active_wake = _wake(active)
    active_order: list[str] = []
    _install_listener_dependencies(monkeypatch, active_wake, active_order)
    monkeypatch.setattr(main, "_terminal_held_sell_reauction_receipts", lambda _r: ())
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: False,
    )
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **_kwargs: False)

    assert main._edli_reactor_wake_poll_once() is False
    assert active_order == []


def test_oldest_active_wake_cannot_starve_later_terminal_queue_files(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    active = _request(position_id="oldest-active", schema_version=3)
    terminals = tuple(
        _request(position_id=f"terminal-{index:02d}", schema_version=3)
        for index in range(31)
    )
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        (
            ("oldest-active", "active", "synced", 5.0, None),
            *(
                (
                    request.position_id,
                    "settled",
                    "synced",
                    float(index + 1),
                    "2026-07-30T13:00:00+00:00",
                )
                for index, request in enumerate(terminals)
            ),
        ),
    )
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_position_fill_wake_held_families",
        lambda _event_ids: frozenset(),
    )
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    selected_reasons: list[str] = []

    def _exact_cut(**kwargs):
        reason = kwargs["producer_wake_reason"]
        selected_reasons.append(reason)
        if reason == reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON:
            requests = kwargs["producer_held_sell_reauction_requests"]
            assert tuple(request.position_id for request in requests) == (
                "oldest-active",
            )
            return False
        return True

    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _exact_cut)
    published_at = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    active_wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-active",
        published_at=published_at,
        held_sell_reauction_requests=(active,),
    )
    for index, request in enumerate(terminals, start=1):
        reactor_wake.publish_reactor_wake(
            source="held_position_monitor",
            reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            path=wake_path,
            wake_id=f"wake-terminal-{index:02d}",
            published_at=published_at + timedelta(microseconds=index),
            held_sell_reauction_requests=(request,),
        )
    other_wakes = tuple(
        reactor_wake.publish_reactor_wake(
            source="test_producer",
            reason=reason,
            path=wake_path,
            wake_id=f"wake-{reason}",
            published_at=published_at + timedelta(microseconds=40 + index),
        )
        for index, reason in enumerate(
            (
                "position_fill_projected",
                "market_price_advanced",
                "forecast_posterior_advanced",
            )
        )
    )
    active_queue_file = reactor_wake._wake_queue_target(
        active_wake, path=wake_path
    )
    active_bytes = active_queue_file.read_bytes()

    results = tuple(main._edli_reactor_wake_poll_once() for _ in range(8))

    remaining = reactor_wake.coalescible_reactor_wakes(
        reactor_wake.read_reactor_wake(path=wake_path),
        path=wake_path,
        max_wakes=100,
    )
    assert tuple(wake.wake_id for wake in remaining) == ("wake-active",)
    assert active_queue_file.read_bytes() == active_bytes
    assert results == (False, True, False, True, False, True, False, False)
    assert selected_reasons == [
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        "position_fill_projected",
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        "market_price_advanced",
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        "forecast_posterior_advanced",
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
    ]
    assert all(
        not reactor_wake._wake_queue_target(wake, path=wake_path).exists()
        for wake in other_wakes
    )
    assert all(
        reactor_wake.held_sell_reauction_requests_completed(
            (request,), path=wake_path
        )
        for request in terminals
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (active,), path=wake_path
    )


def test_global_completion_yield_preserves_day0_and_resets_without_work_or_restart(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    active = _request(position_id="active-only-yield", schema_version=3)
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        (("active-only-yield", "active", "synced", 2.0, None),),
    )
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    exact_cut_count = 0
    selected_reasons: list[str] = []

    def _incomplete_exact_cut(**kwargs):
        nonlocal exact_cut_count
        reason = kwargs["producer_wake_reason"]
        selected_reasons.append(reason)
        if reason == "day0_extreme_event_committed":
            return True
        assert reason == reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON
        exact_cut_count += 1
        return False

    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _incomplete_exact_cut)
    monkeypatch.setattr(main, "_day0_wake_requires_exit_monitor", lambda _scope: False)
    monkeypatch.setattr(
        main, "_pending_held_day0_wake_families", lambda: frozenset()
    )
    wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-active-only-yield",
        published_at=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        held_sell_reauction_requests=(active,),
    )
    queue_file = reactor_wake._wake_queue_target(wake, path=wake_path)
    queue_bytes = queue_file.read_bytes()

    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 1
    day0_wake = reactor_wake.publish_reactor_wake(
        source="day0_test_producer",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id="wake-day0-during-global-yield",
        published_at=datetime(2026, 7, 30, 12, 1, tzinfo=timezone.utc),
    )
    day0_queue_file = reactor_wake._wake_queue_target(
        day0_wake, path=wake_path
    )

    assert main._edli_reactor_wake_poll_once() is True
    assert exact_cut_count == 1
    assert not day0_queue_file.exists()
    assert queue_file.read_bytes() == queue_bytes
    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 2
    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 2
    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 3

    main._edli_initialize_reactor_wake_cursor()
    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 4
    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 4
    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 5
    assert selected_reasons[:3] == [
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        "day0_extreme_event_committed",
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
    ]
    assert queue_file.read_bytes() == queue_bytes
    assert reactor_wake.read_reactor_wake(path=wake_path) == wake


def test_executable_v4_exact_debt_does_not_yield_its_book_window(monkeypatch):
    request = _request(position_id="executable-v4", schema_version=4)
    wake = _wake(request)
    exclusion = main._OneTurnWakeExclusion()
    monkeypatch.setattr(main, "_edli_global_completion_yield", exclusion)

    main._yield_incomplete_global_completion_once(
        wake,
        (request,),
        wake_ids=(wake.wake_id,),
    )

    assert exclusion.consume() == frozenset()


def test_unfinished_day0_monitor_cannot_starve_exact_held_sell_debt(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    request = _request(position_id="capital-debt-behind-unfinished-day0")
    monkeypatch.setattr("src.config.state_path", lambda filename: tmp_path / filename)
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main, "_edli_global_completion_yield", main._OneTurnWakeExclusion()
    )
    monkeypatch.setattr(
        main, "_edli_day0_post_monitor_yield", main._OneTurnWakeExclusion()
    )
    main._edli_initialize_reactor_wake_cursor()
    monkeypatch.setattr(main, "_day0_wake_requires_exit_monitor", lambda _scope: True)
    monkeypatch.setattr(
        main,
        "_day0_exit_monitor_attempt_state",
        lambda _wake_id: (False, None),
    )
    monkeypatch.setattr(
        main,
        "_dispatch_day0_exit_monitor",
        lambda _wake_id, _families: None,
    )
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: False,
    )
    selected_reasons: list[str] = []
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **kwargs: selected_reasons.append(kwargs["producer_wake_reason"])
        or False,
    )
    published_at = datetime(2026, 8, 1, 11, 0, tzinfo=timezone.utc)
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-capital-debt-unfinished-day0",
        published_at=published_at,
        held_sell_reauction_requests=(request,),
    )
    reactor_wake.publish_reactor_wake(
        source="day0_test_producer",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id="wake-day0-monitor-unfinished",
        published_at=published_at + timedelta(seconds=1),
        event_ids=("event-day0-unfinished",),
        forecast_families=(request.family,),
    )

    assert main._edli_reactor_wake_poll_once() is False
    assert main._edli_reactor_wake_poll_once() is False
    assert selected_reasons == [reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON]
    main._edli_initialize_reactor_wake_cursor()


def test_completed_day0_monitor_yields_one_turn_to_exact_held_sell_debt(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    request = _request(position_id="capital-debt-behind-day0", schema_version=3)
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    monkeypatch.setattr(
        main,
        "_edli_day0_post_monitor_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    selected_reasons: list[str] = []

    monkeypatch.setattr(main, "_day0_wake_requires_exit_monitor", lambda _scope: True)
    monkeypatch.setattr(
        main,
        "_day0_exit_monitor_attempt_state",
        lambda _wake_id: (True, True),
    )
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=True,
            finished=False,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: False)
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: False,
    )

    def _cycle(**kwargs):
        selected_reasons.append(kwargs["producer_wake_reason"])
        if (
            kwargs["producer_wake_reason"]
            == reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON
        ):
            assert kwargs["producer_held_sell_reauction_requests"] == (request,)
        return False

    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _cycle)
    published_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    held_wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-capital-debt",
        published_at=published_at,
        held_sell_reauction_requests=(request,),
    )
    day0_wake = reactor_wake.publish_reactor_wake(
        source="day0_test_producer",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id="wake-day0-monitor-complete",
        published_at=published_at + timedelta(seconds=1),
        event_ids=("event-day0-incomplete",),
        forecast_families=(request.family,),
    )
    other_day0_wake = reactor_wake.publish_reactor_wake(
        source="day0_test_producer",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id="wake-day0-also-queued",
        published_at=published_at + timedelta(milliseconds=500),
        event_ids=("event-day0-also-incomplete",),
        forecast_families=(request.family,),
    )
    held_bytes = reactor_wake._wake_queue_target(
        held_wake, path=wake_path
    ).read_bytes()
    day0_bytes = reactor_wake._wake_queue_target(
        day0_wake, path=wake_path
    ).read_bytes()
    other_day0_bytes = reactor_wake._wake_queue_target(
        other_day0_wake, path=wake_path
    ).read_bytes()

    assert main._edli_reactor_wake_poll_once() is False
    assert main._edli_reactor_wake_poll_once() is False
    assert selected_reasons == [
        "day0_extreme_event_committed",
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
    ]
    assert (
        reactor_wake._wake_queue_target(held_wake, path=wake_path).read_bytes()
        == held_bytes
    )
    assert (
        reactor_wake._wake_queue_target(day0_wake, path=wake_path).read_bytes()
        == day0_bytes
    )
    assert (
        reactor_wake._wake_queue_target(
            other_day0_wake, path=wake_path
        ).read_bytes()
        == other_day0_bytes
    )


def test_day0_monitor_ack_failure_still_yields_exact_held_sell_turn(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    request = _request(position_id="capital-debt-after-ack-failure")
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    monkeypatch.setattr(
        main,
        "_edli_day0_post_monitor_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    monkeypatch.setattr(main, "_day0_wake_requires_exit_monitor", lambda _scope: True)
    monkeypatch.setattr(
        main,
        "_day0_exit_monitor_attempt_state",
        lambda _wake_id: (True, True),
    )
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=True,
            finished=True,
        ),
    )
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: False,
    )
    acknowledgements: list[str] = []
    monkeypatch.setattr(
        main,
        "_acknowledge_edli_reactor_wake_batch",
        lambda wake, *_args, **_kwargs: acknowledgements.append(wake.wake_id)
        or False,
    )
    selected_reasons: list[str] = []
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **kwargs: selected_reasons.append(kwargs["producer_wake_reason"])
        or False,
    )
    published_at = datetime(2026, 8, 1, 13, 0, tzinfo=timezone.utc)
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-capital-debt-after-ack-failure",
        published_at=published_at,
        held_sell_reauction_requests=(request,),
    )
    reactor_wake.publish_reactor_wake(
        source="day0_test_producer",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id="wake-day0-ack-failure",
        published_at=published_at + timedelta(seconds=1),
        event_ids=("event-day0-terminal",),
        forecast_families=(request.family,),
    )

    assert main._edli_reactor_wake_poll_once() is False
    assert acknowledgements == ["wake-day0-ack-failure"]
    assert main._edli_reactor_wake_poll_once() is False
    assert selected_reasons == [
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON
    ]


def test_incomplete_exact_shards_yield_one_price_and_keep_all_debt(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    requests = tuple(
        _request(position_id=f"incomplete-shard-{index}", schema_version=3)
        for index in range(3)
    )
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        tuple(
            (request.position_id, "active", "synced", 2.0, None)
            for request in requests
        ),
    )
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    selected: list[tuple[str, tuple[str, ...]]] = []

    def _cycle(**kwargs):
        selected.append(
            (
                kwargs["producer_wake_reason"],
                tuple(
                    request.position_id
                    for request in kwargs.get("producer_held_sell_reauction_requests", ())
                ),
            )
        )
        return kwargs["producer_wake_reason"] != (
            reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON
        )

    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _cycle)
    published_at = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    exact_wakes = tuple(
        reactor_wake.publish_reactor_wake(
            source="held_position_monitor",
            reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            path=wake_path,
            wake_id=f"wake-incomplete-{index}",
            published_at=published_at + timedelta(microseconds=index),
            held_sell_reauction_requests=(request,),
        )
        for index, request in enumerate(requests)
    )
    price_wake = reactor_wake.publish_reactor_wake(
        source="price_channel",
        reason="market_price_advanced",
        path=wake_path,
        wake_id="wake-price-after-exact",
        published_at=published_at + timedelta(seconds=1),
    )

    assert main._edli_reactor_wake_poll_once() is False
    assert selected == [
        (
            reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            tuple(request.position_id for request in requests),
        )
    ]
    assert reactor_wake.exact_held_sell_completion_wake_ids(path=wake_path) == {
        wake.wake_id for wake in exact_wakes
    }
    assert all(
        reactor_wake._wake_queue_target(wake, path=wake_path).exists()
        for wake in exact_wakes
    )
    assert all(
        not reactor_wake.held_sell_reauction_requests_completed(
            (request,), path=wake_path
        )
        for request in requests
    )

    assert main._edli_reactor_wake_poll_once() is True
    assert selected[-1] == ("market_price_advanced", ())
    assert not reactor_wake._wake_queue_target(price_wake, path=wake_path).exists()
    assert all(
        reactor_wake._wake_queue_target(wake, path=wake_path).exists()
        for wake in exact_wakes
    )

    assert main._edli_reactor_wake_poll_once() is False
    assert selected[-1] == (
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        tuple(request.position_id for request in requests),
    )


def test_exact_wake_snapshot_does_not_exclude_new_exact_debt(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    first = _request(position_id="snapshot-first", schema_version=3)
    newer = _request(position_id="snapshot-newer", schema_version=3)
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        (
            (first.position_id, "active", "synced", 2.0, None),
            (newer.position_id, "active", "synced", 2.0, None),
        ),
    )
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    selected: list[str] = []
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **kwargs: selected.append(kwargs["producer_wake_reason"]) or False,
    )
    published_at = datetime(2026, 8, 2, 13, 0, tzinfo=timezone.utc)
    first_wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-snapshot-first",
        published_at=published_at,
        held_sell_reauction_requests=(first,),
    )
    reactor_wake.publish_reactor_wake(
        source="price_channel",
        reason="market_price_advanced",
        path=wake_path,
        wake_id="wake-snapshot-price",
        published_at=published_at + timedelta(seconds=1),
    )

    assert main._edli_reactor_wake_poll_once() is False
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-snapshot-newer",
        published_at=published_at + timedelta(seconds=2),
        held_sell_reauction_requests=(newer,),
    )

    assert main._edli_reactor_wake_poll_once() is False
    assert selected == [
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
    ]
    assert reactor_wake._wake_queue_target(first_wake, path=wake_path).exists()
    assert reactor_wake.exact_held_sell_completion_wake_ids(path=wake_path) == {
        "wake-snapshot-first",
        "wake-snapshot-newer",
    }


def test_exact_fairness_keeps_price_and_forecast_batch_coalescing(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    requests = tuple(
        _request(position_id=f"batch-exact-{index}", schema_version=3)
        for index in range(3)
    )
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        tuple(
            (request.position_id, "active", "synced", 2.0, None)
            for request in requests
        ),
    )
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    selected: list[tuple[str, tuple[str, ...]]] = []

    def _cycle(**kwargs):
        selected.append(
            (
                kwargs["producer_wake_reason"],
                tuple(kwargs["producer_wake_ids"]),
            )
        )
        return kwargs["producer_wake_reason"] != (
            reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON
        )

    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _cycle)
    published_at = datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc)
    for index, request in enumerate(requests):
        reactor_wake.publish_reactor_wake(
            source="held_position_monitor",
            reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            path=wake_path,
            wake_id=f"wake-batch-exact-{index}",
            published_at=published_at + timedelta(microseconds=index),
            held_sell_reauction_requests=(request,),
        )
    for index in range(2):
        reactor_wake.publish_reactor_wake(
            source="price_channel",
            reason="market_price_advanced",
            path=wake_path,
            wake_id=f"wake-batch-price-{index}",
            published_at=published_at + timedelta(seconds=1, microseconds=index),
        )
    for index in range(2):
        reactor_wake.publish_reactor_wake(
            source="forecast_producer",
            reason="forecast_posterior_advanced",
            path=wake_path,
            wake_id=f"wake-batch-forecast-{index}",
            published_at=published_at + timedelta(seconds=2, microseconds=index),
        )

    assert main._edli_reactor_wake_poll_once() is False
    assert main._edli_reactor_wake_poll_once() is True
    assert main._edli_reactor_wake_poll_once() is False
    assert main._edli_reactor_wake_poll_once() is True
    assert selected[:3] == [
        (
            reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            tuple(f"wake-batch-exact-{index}" for index in range(3)),
        ),
        ("market_price_advanced", ("wake-batch-price-0", "wake-batch-price-1")),
        (
            reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            tuple(f"wake-batch-exact-{index}" for index in range(3)),
        ),
    ]
    assert selected[3][0] == "forecast_posterior_advanced"
    assert set(selected[3][1]) == {
        "wake-batch-forecast-0",
        "wake-batch-forecast-1",
    }
    assert reactor_wake.exact_held_sell_completion_wake_ids(path=wake_path) == {
        f"wake-batch-exact-{index}" for index in range(3)
    }


def test_price_wake_dispatches_held_monitor_before_reactor(monkeypatch, tmp_path: Path) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    family = ("Paris", "2026-07-30", "low")
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(main, "_price_wake_target_families", lambda _event_ids: frozenset({family}))
    monkeypatch.setattr(main, "_forecast_wake_held_families", lambda _families: frozenset({family}))
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(ready=True, finished=False),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _event_ids: True)
    main._edli_initialize_reactor_wake_cursor()
    main._forecast_exit_monitor_attempts.clear()
    monitored = []
    dispatched = []

    def _dispatch(wake_ids, target_families, *, urgent_price=False):
        monitored.append((wake_ids, target_families, urgent_price))
        with main._forecast_exit_monitor_attempts_lock:
            for wake_id in wake_ids:
                main._forecast_exit_monitor_attempts[wake_id] = True
        return True

    monkeypatch.setattr(main, "_dispatch_forecast_exit_monitor", _dispatch)
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **kwargs: dispatched.append(kwargs["producer_wake_reason"]) or True,
    )
    wake = reactor_wake.publish_reactor_wake(
        source="price_channel",
        reason="market_price_advanced",
        path=wake_path,
        wake_id="wake-price-held-monitor",
        event_ids=("price-event",),
    )

    try:
        assert main._edli_reactor_wake_poll_once() is True
        assert monitored == [((wake.wake_id,), frozenset({family}), True)]
        assert dispatched == ["market_price_advanced"]
    finally:
        main._forecast_exit_monitor_attempts.clear()


def test_degraded_price_wake_retires_hint_after_held_monitor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Non-GREEN BUY debt stays durable without replaying one monitor hint forever."""

    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel

    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    family = ("Paris", "2026-07-30", "low")
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_price_wake_target_families",
        lambda _event_ids: frozenset({family}),
    )
    monkeypatch.setattr(
        main,
        "_forecast_wake_held_families",
        lambda _families: frozenset({family}),
    )
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=True,
            finished=False,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: False)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.DATA_DEGRADED)
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **_kwargs: True)
    main._edli_initialize_reactor_wake_cursor()
    main._forecast_exit_monitor_attempts.clear()

    def _dispatch(wake_ids, _target_families, *, urgent_price=False):
        assert urgent_price is True
        with main._forecast_exit_monitor_attempts_lock:
            for wake_id in wake_ids:
                main._forecast_exit_monitor_attempts[wake_id] = True
        return True

    monkeypatch.setattr(main, "_dispatch_forecast_exit_monitor", _dispatch)
    wake = reactor_wake.publish_reactor_wake(
        source="price_channel",
        reason="market_price_advanced",
        path=wake_path,
        wake_id="wake-price-degraded",
        event_ids=("durable-price-event",),
    )

    try:
        assert main._edli_reactor_wake_poll_once() is True
        assert not reactor_wake._wake_queue_target(wake, path=wake_path).exists()
        assert main._edli_reactor_wake_poll_once() is False
    finally:
        main._forecast_exit_monitor_attempts.clear()


def test_green_price_wake_keeps_hint_until_entry_event_finishes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Normal BUY-capable operation retains the exact event-completion fence."""

    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel

    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_price_wake_target_families",
        lambda _event_ids: frozenset({("Paris", "2026-07-30", "low")}),
    )
    monkeypatch.setattr(
        main,
        "_forecast_wake_held_families",
        lambda _families: frozenset(),
    )
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=True,
            finished=False,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: False)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **_kwargs: True)
    main._edli_initialize_reactor_wake_cursor()
    wake = reactor_wake.publish_reactor_wake(
        source="price_channel",
        reason="market_price_advanced",
        path=wake_path,
        wake_id="wake-price-green",
        event_ids=("unfinished-price-event",),
    )

    assert main._edli_reactor_wake_poll_once() is False
    assert reactor_wake._wake_queue_target(wake, path=wake_path).exists()


def test_degraded_day0_wake_retires_hint_after_held_monitor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A completed hard-fact monitor must not replay behind blocked BUY work."""

    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel

    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    family = ("Paris", "2026-07-30", "high")
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(main, "_day0_wake_requires_exit_monitor", lambda _scope: True)
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=True,
            finished=False,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: False)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.YELLOW)
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **_kwargs: True)
    main._edli_initialize_reactor_wake_cursor()
    main._day0_exit_monitor_attempts.clear()

    def _dispatch(wake_id, target_families):
        assert target_families == frozenset({family})
        with main._day0_exit_monitor_attempts_lock:
            main._day0_exit_monitor_attempts[wake_id] = True
        return True

    monkeypatch.setattr(main, "_dispatch_day0_exit_monitor", _dispatch)
    wake = reactor_wake.publish_reactor_wake(
        source="day0",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id="wake-day0-degraded",
        event_ids=("durable-day0-event",),
        forecast_families=(family,),
    )

    try:
        assert main._edli_reactor_wake_poll_once() is True
        assert not reactor_wake._wake_queue_target(wake, path=wake_path).exists()
        assert main._edli_reactor_wake_poll_once() is False
    finally:
        main._day0_exit_monitor_attempts.clear()
