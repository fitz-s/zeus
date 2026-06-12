# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator redeem directive 2026-06-10 ($19 stuck — harvester
#   logged "Skipping settlement close for 3a6f0728-c50: winning position has no
#   condition_id for redeem command" because the position carried token_ids but a
#   NULL condition_id; settlement close + redeem never proceeded). Confirmed
#   read-only: position_current trade_id=3a6f0728-c50 had token_id/no_token_id set
#   and condition_id IS NULL; executable_market_snapshots maps that yes_token_id
#   -> 0xddb5c82d…4df4d.
# Lifecycle: created=2026-06-10; last_reviewed=2026-06-10; last_reused=never
# Purpose: Relationship antibody — when a winning position lacks condition_id, the
#   harvester backfills it from the token->market mapping
#   (executable_market_snapshots.yes_token_id/no_token_id -> condition_id) so
#   settlement close + redeem enqueue proceed; when no mapping exists the loud
#   skip is preserved (fail-closed, never guesses a condition_id).
# Reuse: Run when modifying _resolve_condition_id_from_token_map or the
#   condition_id skip block in _settle_positions.
"""Relationship antibodies for harvester condition_id backfill (Defect 2)."""

from __future__ import annotations

import sqlite3

import pytest


_YES_TOKEN = "113959433546428599583458171463964346033318046435676830124564125503733330054946"
_MAPPED_CONDITION = "0xddb5c82d33579fbd3d47600a89438a1c6af5b1ac7ba48ed3a4099c6070c4df4d"


def _conn_with_snapshot(*, seed_mapping: bool) -> sqlite3.Connection:
    """In-memory conn with the tables _settle_positions touches, plus a minimal
    executable_market_snapshots carrying (or not) the token->condition mapping."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE position_current (trade_id TEXT PRIMARY KEY, city TEXT, "
        "target_date TEXT, phase TEXT)"
    )
    conn.execute(
        "CREATE TABLE executable_market_snapshots ("
        "snapshot_id TEXT PRIMARY KEY, condition_id TEXT, yes_token_id TEXT, "
        "no_token_id TEXT, captured_at TEXT)"
    )
    if seed_mapping:
        conn.execute(
            "INSERT INTO executable_market_snapshots "
            "(snapshot_id, condition_id, yes_token_id, no_token_id, captured_at) "
            "VALUES ('s1', ?, ?, 'no-tok', '2026-05-19T00:00:00Z')",
            (_MAPPED_CONDITION, _YES_TOKEN),
        )
    conn.commit()
    return conn


def _make_portfolio(*, condition_id):
    from unittest.mock import MagicMock

    pos = MagicMock()
    pos.trade_id = "3a6f0728-c50"
    pos.city = "London"
    pos.target_date = "2026-05-19"
    pos.direction = "buy_yes"
    pos.condition_id = condition_id  # None / "" simulates the gap
    pos.token_id = _YES_TOKEN
    pos.no_token_id = "42940306646602607001227280047215234576706914345564465344564453597218971262437"
    pos.market_id = ""
    pos.entry_price = 0.6
    pos.shares = 10.0
    pos.p_posterior = 0.7
    pos.bin_label = "16-17°C"
    pos.exit_price = None
    pos.entry_method = "model"
    pos.selected_method = "model"
    pos.decision_snapshot_id = ""
    pos.edge_source = "model"
    pos.strategy = "default"
    pos.last_exit_at = "2026-05-19T18:00:00Z"
    pos.state = "active"
    pos.exit_state = ""
    pos.chain_state = ""
    pos.temperature_metric = "high"

    portfolio = MagicMock()
    portfolio.positions = [pos]
    portfolio.ignored_tokens = []
    return portfolio, pos


def _run_settle(monkeypatch, conn, portfolio, pos):
    """Drive _settle_positions with the winning bin == position bin (so the
    position is the winner: exit_price > 0 triggers the redeem path)."""
    import src.execution.harvester as hv
    import src.execution.exit_lifecycle as el
    from unittest.mock import MagicMock

    enqueue_calls = []

    def fake_enqueue(c, *, condition_id, payout_asset, market_id, pusd_amount_micro,
                     token_amounts, trade_id, winning_index_set=None):
        enqueue_calls.append({"condition_id": condition_id, "trade_id": trade_id})
        return {"status": "queued", "command_id": "cmd-bf", "reason": None}

    monkeypatch.setattr(hv, "enqueue_redeem_command", fake_enqueue)
    monkeypatch.setattr(hv, "_get_canonical_exit_flag", lambda: True)
    monkeypatch.setattr(hv, "log_event", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "log_settlement_event", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "_dual_write_canonical_settlement_if_available",
                        lambda *a, **kw: False)
    monkeypatch.setattr(hv, "record_token_suppression",
                        lambda *a, **kw: {"status": "written"})
    monkeypatch.setattr(hv, "_settlement_economics_for_position",
                        lambda p: (p.shares, p.entry_price * p.shares))

    closed = MagicMock()
    closed.trade_id = pos.trade_id
    closed.pnl = 4.0
    closed.bin_label = pos.bin_label
    closed.direction = pos.direction
    closed.p_posterior = pos.p_posterior
    closed.decision_snapshot_id = ""
    closed.edge_source = "model"
    closed.strategy = "default"
    closed.last_exit_at = pos.last_exit_at
    closed.exit_price = 1.0
    monkeypatch.setattr(el, "mark_settled", lambda *a, **kw: closed)

    settled = hv._settle_positions(
        conn, portfolio,
        city="London", target_date="2026-05-19",
        winning_label=pos.bin_label, settlement_records=[],
    )
    return settled, enqueue_calls


def test_missing_condition_id_backfilled_then_close_proceeds(monkeypatch, caplog):
    """B1: condition_id NULL + token->market mapping present -> condition_id
    resolved, redeem enqueued, settlement counted.

    Sed-flip: delete the backfill call -> the redeem is skipped, enqueue_calls
    empty, settled==0 -> RED."""
    import logging

    conn = _conn_with_snapshot(seed_mapping=True)
    portfolio, pos = _make_portfolio(condition_id=None)
    with caplog.at_level(logging.INFO, logger="src.execution.harvester"):
        settled, enqueue_calls = _run_settle(monkeypatch, conn, portfolio, pos)
    assert settled == 1, "B1 FAIL: backfilled winner was not settled."
    assert len(enqueue_calls) == 1, "B1 FAIL: redeem was not enqueued after backfill."
    assert enqueue_calls[0]["condition_id"] == _MAPPED_CONDITION
    assert pos.condition_id == _MAPPED_CONDITION, "B1 FAIL: condition_id not backfilled onto pos."
    conn.close()


def test_unresolvable_condition_id_keeps_loud_skip(monkeypatch, caplog):
    """B2: condition_id NULL + NO token->market mapping -> redeem NOT enqueued,
    settlement NOT counted, and the loud error log is preserved (fail-closed)."""
    import logging

    conn = _conn_with_snapshot(seed_mapping=False)
    portfolio, pos = _make_portfolio(condition_id=None)
    with caplog.at_level(logging.ERROR, logger="src.execution.harvester"):
        settled, enqueue_calls = _run_settle(monkeypatch, conn, portfolio, pos)
    assert settled == 0, "B2 FAIL: unresolvable winner was wrongly settled."
    assert enqueue_calls == [], "B2 FAIL: redeem enqueued without a condition_id."
    assert any(
        "no condition_id for redeem command" in r.message for r in caplog.records
    ), "B2 FAIL: the loud skip log was lost."
    conn.close()


def test_present_condition_id_does_not_query_token_map(monkeypatch):
    """B3: a position that already has condition_id never triggers the backfill
    query (no behavior change for the healthy path)."""
    import src.execution.harvester as hv

    conn = _conn_with_snapshot(seed_mapping=True)
    portfolio, pos = _make_portfolio(condition_id="0x" + "ee" * 32)

    called = {"n": 0}
    real = hv._resolve_condition_id_from_token_map

    def spy(c, p):
        called["n"] += 1
        return real(c, p)

    monkeypatch.setattr(hv, "_resolve_condition_id_from_token_map", spy)
    settled, enqueue_calls = _run_settle(monkeypatch, conn, portfolio, pos)
    assert settled == 1
    assert called["n"] == 0, "B3 FAIL: backfill ran even though condition_id was present."
    assert enqueue_calls[0]["condition_id"] == "0x" + "ee" * 32
    conn.close()
