# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D2 shift-bin old-leg exit submission. Pins the venue-boundary logic
#   in src/engine/event_reactor_adapter._submit_shift_bin_old_leg_exit: the old leg is
#   sold reduce-only THROUGH the existing exit path (place_sell_order → execute_exit_
#   order), and the SHIFT_BIN lease advances by the exit OrderResult status —
#   EXIT_SUBMITTED on a placed/acked sell, EXIT_UNKNOWN on an unknown side effect,
#   ABORTED on a clean pre-venue rejection. NO counter-entry is ever built here.
"""ANTIBODY: the shift-bin old-leg exit reuses the existing exit path and the lease
state machine tracks the venue outcome HONESTLY (unknown → block; clean reject →
release; placed → EXIT_SUBMITTED blocking). Never a false exit, never a counter-entry
while the old leg is unresolved."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from src.engine import event_reactor_adapter as era
from src.state.schema.family_rebalance_intents_schema import ensure_table
from src.strategy import family_rebalance as fr


_POSITION_CURRENT_DDL = """
CREATE TABLE position_current (
    position_id TEXT, phase TEXT, token_id TEXT, no_token_id TEXT,
    bin_label TEXT, direction TEXT, condition_id TEXT, city TEXT,
    target_date TEXT, temperature_metric TEXT, p_posterior REAL,
    entry_ci_width REAL, cost_basis_usd REAL, chain_cost_basis_usd REAL,
    shares REAL, chain_shares REAL, size_usd REAL, updated_at TEXT
)
"""


@dataclass
class _FakeOrderResult:
    status: str
    order_id: str = ""
    external_order_id: str = ""
    reason: str = ""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_POSITION_CURRENT_DDL)
    ensure_table(conn)
    return conn


def _insert_old_leg(
    conn,
    *,
    token_id="tok-A",
    no_token_id="",
    direction="buy_yes",
    position_id="p-old",
    shares=10.0,
):
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, token_id, no_token_id, bin_label, direction,
            condition_id, city, target_date, temperature_metric, p_posterior,
            entry_ci_width, cost_basis_usd, chain_cost_basis_usd, shares, chain_shares,
            size_usd, updated_at
        ) VALUES (?, 'active', ?, ?, '60-61F', ?, 'cond-1', 'Tokyo',
                  '2026-06-23', 'high', 0.50, 0.20, 4.0, NULL, ?, ?, 4.0,
                  '2026-06-22T06:00:00')
        """,
        (position_id, token_id, no_token_id, direction, shares, shares),
    )


def _acquire_shift_lease(conn) -> str:
    intent = fr.acquire_rebalance_lease(
        conn, family_key="live|Tokyo|2026-06-23|high", operation="SHIFT_BIN",
        now_iso="t0", held_position_id="p-old", held_token_id="tok-A",
        held_bin_id="60-61F",
    )
    fr.advance_rebalance_lease(conn, intent, status="EXIT_SUBMITTED", now_iso="t0")
    return intent


def _payload(intent_id):
    return {
        "phase": "EXIT_OLD_LEG",
        "intent_id": intent_id,
        "old_position_id": "p-old",
        "old_token_id": "tok-A",
        "city": "Tokyo", "target_date": "2026-06-23", "metric": "high",
    }


def _stub_exit_inputs(monkeypatch, *, shares=10.0, price=0.42):
    """Stub the snapshot/shares read so the test exercises ONLY the lease-advance
    branching off the OrderResult — not the snapshot plumbing."""
    monkeypatch.setattr(
        era, "_read_old_leg_exit_inputs",
        lambda conn, *, old_token_id: (shares, price, price, {"executable_snapshot_id": "snap-1"}),
    )


def _now():
    return datetime(2026, 6, 22, 6, 40, tzinfo=timezone.utc)


def test_placed_sell_advances_lease_exit_submitted(monkeypatch):
    conn = _conn()
    _insert_old_leg(conn)
    intent = _acquire_shift_lease(conn)
    _stub_exit_inputs(monkeypatch)
    import src.execution.exit_lifecycle as xl
    monkeypatch.setattr(
        xl, "place_sell_order",
        lambda **kw: _FakeOrderResult(status="pending", order_id="exit-cmd-1"),
    )
    era._submit_shift_bin_old_leg_exit(conn, payload=_payload(intent), decision_time=_now())
    row = conn.execute(
        "SELECT status, old_exit_command_id FROM family_rebalance_intents WHERE intent_id=?",
        (intent,),
    ).fetchone()
    assert row["status"] == "EXIT_SUBMITTED"  # family stays BLOCKING; no counter-entry
    assert row["old_exit_command_id"] == "exit-cmd-1"


def test_buy_no_shift_exit_sells_no_token(monkeypatch):
    conn = _conn()
    _insert_old_leg(
        conn,
        token_id="yes-tok-A",
        no_token_id="no-tok-A",
        direction="buy_no",
        shares=14.0,
    )
    intent = fr.acquire_rebalance_lease(
        conn, family_key="live|Tokyo|2026-06-23|high", operation="SHIFT_BIN",
        now_iso="t0", held_position_id="p-old", held_token_id="no-tok-A",
        held_bin_id="60-61F",
    )
    fr.advance_rebalance_lease(conn, intent, status="EXIT_SUBMITTED", now_iso="t0")

    def _inputs(conn, *, old_token_id):
        assert old_token_id == "no-tok-A"
        return (14.0, 0.42, 0.42, {"executable_snapshot_id": "snap-1"})

    monkeypatch.setattr(era, "_read_old_leg_exit_inputs", _inputs)
    captured = {}
    import src.execution.exit_lifecycle as xl
    def _placed(**kw):
        captured["kw"] = kw
        return _FakeOrderResult(status="pending", order_id="exit-cmd-1")

    monkeypatch.setattr(xl, "place_sell_order", _placed)
    payload = _payload(intent)
    payload["old_token_id"] = "no-tok-A"
    era._submit_shift_bin_old_leg_exit(conn, payload=payload, decision_time=_now())
    assert captured["kw"]["token_id"] == "no-tok-A"
    assert captured["kw"]["shares"] == pytest.approx(14.0)


def test_unknown_side_effect_blocks_family(monkeypatch):
    conn = _conn()
    _insert_old_leg(conn)
    intent = _acquire_shift_lease(conn)
    _stub_exit_inputs(monkeypatch)
    import src.execution.exit_lifecycle as xl
    monkeypatch.setattr(
        xl, "place_sell_order",
        lambda **kw: _FakeOrderResult(status="unknown_side_effect", order_id="exit-cmd-2"),
    )
    era._submit_shift_bin_old_leg_exit(conn, payload=_payload(intent), decision_time=_now())
    row = conn.execute(
        "SELECT status, abort_reason FROM family_rebalance_intents WHERE intent_id=?", (intent,),
    ).fetchone()
    assert row["status"] == "EXIT_UNKNOWN"  # block the family; reconciliation owns it
    assert str(row["abort_reason"]).startswith("SHIFT_BIN_EXIT_UNKNOWN:")


def test_clean_rejection_releases_lease(monkeypatch):
    conn = _conn()
    _insert_old_leg(conn)
    intent = _acquire_shift_lease(conn)
    _stub_exit_inputs(monkeypatch)
    import src.execution.exit_lifecycle as xl
    monkeypatch.setattr(
        xl, "place_sell_order",
        lambda **kw: _FakeOrderResult(status="rejected", reason="exit_mutex_held"),
    )
    era._submit_shift_bin_old_leg_exit(conn, payload=_payload(intent), decision_time=_now())
    row = conn.execute(
        "SELECT status, abort_reason FROM family_rebalance_intents WHERE intent_id=?",
        (intent,),
    ).fetchone()
    # Clean pre-venue rejection: no side effect → release the family for a legit retry.
    assert row["status"] == "ABORTED"
    assert "exit_mutex_held" in (row["abort_reason"] or "")


def test_auth_signature_rejection_keeps_shift_exit_retry_active(monkeypatch):
    conn = _conn()
    _insert_old_leg(conn)
    intent = _acquire_shift_lease(conn)
    _stub_exit_inputs(monkeypatch)
    import src.execution.exit_lifecycle as xl
    monkeypatch.setattr(
        xl,
        "place_sell_order",
        lambda **kw: _FakeOrderResult(
            status="rejected",
            order_id="exit-cmd-auth",
            reason=(
                "venue_rejected_400: PolyApiException[status_code=400, "
                "error_message={'error': 'invalid POLY_GNOSIS_SAFE signature'}]"
            ),
        ),
    )
    era._submit_shift_bin_old_leg_exit(conn, payload=_payload(intent), decision_time=_now())
    row = conn.execute(
        "SELECT status, old_exit_command_id, abort_reason "
        "FROM family_rebalance_intents WHERE intent_id=?",
        (intent,),
    ).fetchone()
    assert row["status"] == "EXIT_UNKNOWN"
    assert row["old_exit_command_id"] == "exit-cmd-auth"
    assert str(row["abort_reason"]).startswith("SHIFT_BIN_EXIT_RETRYABLE_REJECTED:")


def test_exit_raises_without_durable_command_releases_for_retry(monkeypatch):
    conn = _conn()
    _insert_old_leg(conn)
    intent = _acquire_shift_lease(conn)
    _stub_exit_inputs(monkeypatch)
    import src.execution.exit_lifecycle as xl

    def _boom(**kw):
        raise RuntimeError("network blip at venue boundary")

    monkeypatch.setattr(xl, "place_sell_order", _boom)
    era._submit_shift_bin_old_leg_exit(conn, payload=_payload(intent), decision_time=_now())
    row = conn.execute(
        "SELECT status, abort_reason FROM family_rebalance_intents WHERE intent_id=?", (intent,),
    ).fetchone()
    assert row["status"] == "ABORTED"
    assert "NO_DURABLE_COMMAND" in (row["abort_reason"] or "")
    assert "network blip at venue boundary" in (row["abort_reason"] or "")


def test_ctf_available_zero_completes_old_leg_exit_without_retry(monkeypatch):
    conn = _conn()
    _insert_old_leg(conn)
    intent = _acquire_shift_lease(conn)
    _stub_exit_inputs(monkeypatch, shares=12.03)
    import src.execution.exit_lifecycle as xl
    from src.state.collateral_ledger import CollateralInsufficient

    def _closed_on_chain(**kw):
        raise CollateralInsufficient(
            "ctf_tokens_insufficient: token_id=tok-A required=12030000 available=0"
        )

    monkeypatch.setattr(xl, "place_sell_order", _closed_on_chain)
    era._submit_shift_bin_old_leg_exit(conn, payload=_payload(intent), decision_time=_now())
    row = conn.execute(
        "SELECT status, abort_reason FROM family_rebalance_intents WHERE intent_id=?",
        (intent,),
    ).fetchone()
    assert row["status"] == "EXIT_ONLY_COMPLETE"
    assert str(row["abort_reason"]).startswith("SHIFT_BIN_OLD_LEG_CHAIN_ZERO_COLLATERAL:")


def test_ctf_available_positive_shortfall_stays_blocking(monkeypatch):
    conn = _conn()
    _insert_old_leg(conn)
    intent = _acquire_shift_lease(conn)
    _stub_exit_inputs(monkeypatch, shares=12.03)
    import src.execution.exit_lifecycle as xl
    from src.state.collateral_ledger import CollateralInsufficient

    def _partial_shortfall(**kw):
        raise CollateralInsufficient(
            "ctf_tokens_insufficient: token_id=tok-A required=12030000 available=100"
        )

    monkeypatch.setattr(xl, "place_sell_order", _partial_shortfall)
    era._submit_shift_bin_old_leg_exit(conn, payload=_payload(intent), decision_time=_now())
    row = conn.execute(
        "SELECT status, abort_reason FROM family_rebalance_intents WHERE intent_id=?",
        (intent,),
    ).fetchone()
    assert row["status"] == "ABORTED"
    assert "NO_DURABLE_COMMAND" in (row["abort_reason"] or "")


def test_old_leg_already_closed_leaves_lease_blocking(monkeypatch):
    """Old leg already gone (no inputs) → no exit submit, lease stays EXIT_SUBMITTED so
    the next redecision cycle re-evaluates (and will admit the counter-entry)."""
    conn = _conn()
    intent = _acquire_shift_lease(conn)
    monkeypatch.setattr(era, "_read_old_leg_exit_inputs", lambda conn, *, old_token_id: None)
    era._submit_shift_bin_old_leg_exit(conn, payload=_payload(intent), decision_time=_now())
    row = conn.execute(
        "SELECT status FROM family_rebalance_intents WHERE intent_id=?", (intent,),
    ).fetchone()
    assert row["status"] == "EXIT_SUBMITTED"  # unchanged — blocking, no counter-entry


# --- accessor regression: the old-leg exit SEED price must read the REAL snapshot
# attribute (orderbook_top_bid). The earlier (best_bid/selected_outcome_best_bid)
# names do not exist on the snapshot object, so the seed stayed 0.0 and the old-leg
# exit DEFERRED every cycle (shift-bin silently inert). This drives the REAL
# _read_old_leg_exit_inputs (not the stub) to lock the fix. (2026-06-22)

def test_old_leg_exit_seed_reads_orderbook_top_bid(monkeypatch):
    import sqlite3
    import src.engine.event_reactor_adapter as era

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE position_current (token_id TEXT, no_token_id TEXT, shares REAL, "
        "chain_shares REAL, phase TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO position_current (token_id, shares, phase, updated_at) "
        "VALUES ('old-tok', 10.0, 'active', '2026-06-22T07:00:00')"
    )

    monkeypatch.setattr(era, "_latest_exit_snapshot_context", None, raising=False)
    # The helpers are imported INSIDE the function from exit_lifecycle; patch there.
    import src.execution.exit_lifecycle as xl
    monkeypatch.setattr(xl, "_latest_exit_snapshot_context",
                        lambda conn, tok: {"executable_snapshot_id": "snap-1"}, raising=False)
    monkeypatch.setattr(xl, "_latest_exit_snapshot_identity_seed",
                        lambda conn, tok: {"executable_snapshot_id": "snap-1"}, raising=False)

    class _Snap:  # real snapshot dataclass exposes orderbook_top_bid (NOT best_bid)
        orderbook_top_bid = 0.42

    import src.state.snapshot_repo as sr
    monkeypatch.setattr(sr, "get_snapshot", lambda conn, sid: _Snap(), raising=False)

    out = era._read_old_leg_exit_inputs(conn, old_token_id="old-tok")
    assert out is not None, "real accessor must produce a usable seed from orderbook_top_bid"
    shares, current_price, best_bid, _identity = out
    assert shares == 10.0
    assert current_price == 0.42  # seeded from orderbook_top_bid, NOT 0.0/deferred
    assert best_bid == 0.42
