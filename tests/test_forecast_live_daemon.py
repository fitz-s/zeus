# Created: 2026-07-30
# Last reused/audited: 2026-07-30
# Lifecycle: created=2026-07-30; last_reviewed=2026-07-30; last_reused=2026-07-30
# Authority basis: operator-directed held SELL terminal-wake hotfix.
"""Held SELL terminal-wake completion antibodies."""

from __future__ import annotations

import sqlite3
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
    monkeypatch.setattr(main, "_edli_last_reactor_wake_id", None)
    exact_cut_requests: list[tuple[str, ...]] = []

    def _exact_cut(**kwargs):
        requests = kwargs["producer_held_sell_reauction_requests"]
        exact_cut_requests.append(tuple(request.position_id for request in requests))
        return False

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
    active_queue_file = reactor_wake._wake_queue_target(
        active_wake, path=wake_path
    )
    active_bytes = active_queue_file.read_bytes()

    for _ in range(3):
        assert main._edli_reactor_wake_poll_once() is False

    remaining = reactor_wake.coalescible_reactor_wakes(
        reactor_wake.read_reactor_wake(path=wake_path),
        path=wake_path,
        max_wakes=100,
    )
    assert tuple(wake.wake_id for wake in remaining) == ("wake-active",)
    assert active_queue_file.read_bytes() == active_bytes
    assert exact_cut_requests == [("oldest-active",)] * 3
    assert all(
        reactor_wake.held_sell_reauction_requests_completed(
            (request,), path=wake_path
        )
        for request in terminals
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (active,), path=wake_path
    )
