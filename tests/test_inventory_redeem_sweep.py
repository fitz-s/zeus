# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator redeem directive 2026-06-09 (auto-collect: the redeem
#   trigger is the Safe's actual chain inventory, never the portfolio ledger).
# Lifecycle: created=2026-06-09; last_reviewed=2026-06-09; last_reused=never
# Purpose: Antibody — sweep_chain_inventory_for_redeems enqueues redeem commands
#   from chain holdings with ZERO ledger involvement (the London-16 case), fails
#   closed on chain mismatch, and redeemed USDC.e proceeds auto-wrap via
#   enqueue_wrap_if_balance_above_threshold (no operator hands anywhere).
# Reuse: Run when modifying inventory_redeem_sweep.py, the balance probe, the
#   _redeem_submitter_cycle sweep wiring, or the wrap intent creator.
"""Antibody tests for the inventory-truth auto-collect sweep.

Root cause (2026-06-09): the harvester's redeem enqueue keyed off the internal
portfolio ledger. Observed failure modes: phantom enqueues (ledger held /
chain empty -> GS013 purgatory), missed real winners (pending_exit /
admin_closed phases the sweep never visits), and ledger-invisible holdings
(London-16C YES ~$798 with ZERO position_current rows). The antibody is a
chain-inventory sweep that never reads the ledger.

Contracts (sed-flip verifiable):
  S1 (London-16 case): a chain-verified redeemable winner with NO ledger rows
      anywhere still gets a settlement_commands row enqueued.
  S2: live balance 0 (already redeemed / API lag) -> NOT enqueued.
  S3: derived positionId != API asset id -> NOT enqueued (fail-closed).
  S4: zero-value (curPrice 0) and non-redeemable rows are filtered out.
  S5: re-sweep while a command is active returns the SAME command id (no dupes).
  W1 (auto-unwrap): redeemed USDC.e proceeds above threshold enqueue a
      WRAP_REQUESTED row with the full balance (no operator hands).
"""

from __future__ import annotations

import sqlite3

import pytest

from src.state.db import init_schema


_CID = "0x" + "ee" * 32
_ASSET = "81633771000021127658752261257414280805140874457786559998422070609600874978435"
_SAFE = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"


@pytest.fixture()
def trade_conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    yield db
    db.close()


class _ProbeAdapter:
    def __init__(self, *, balance_micro: int, position_id: str = _ASSET, ok: bool = True):
        self._balance = balance_micro
        self._pid = position_id
        self._ok = ok
        self.probe_calls = []

    def get_negrisk_winning_position_balance(self, condition_id, index_set, *, holder=None):
        self.probe_calls.append((condition_id, index_set))
        if not self._ok:
            return {"ok": False, "errorCode": "REDEEM_BALANCE_PROBE_FAILED"}
        return {
            "ok": True,
            "balance_micro": self._balance,
            "position_id": int(self._pid),
            "wcol": "0x" + "1" * 40,
            "holder": holder or _SAFE,
            "zeus_index_set": index_set,
            "ctf_index_set": 1 if index_set == 2 else 2,
        }


def _position(**over):
    base = {
        "conditionId": _CID,
        "asset": _ASSET,
        "outcome": "Yes",
        "outcomeIndex": 0,
        "size": 797.9768,
        "curPrice": 1,
        "redeemable": True,
        "negativeRisk": True,
        "title": "Will the highest temperature in London be 16°C on June 8?",
    }
    base.update(over)
    return base


def test_s1_ledger_invisible_winner_is_enqueued(trade_conn):
    """S1 (London-16 case): chain-verified winner with ZERO ledger rows is
    enqueued purely from chain inventory.

    The conn contains NO position rows of any kind — if the sweep consulted
    any ledger table, it would find nothing and skip. Sed-flip: make the sweep
    require a position_current row -> RED."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _ProbeAdapter(balance_micro=797_976_847)
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_position()],
    )
    assert len(cmds) == 1, "S1 FAIL: chain-verified winner was not enqueued."
    row = trade_conn.execute(
        "SELECT condition_id, state, winning_index_set, pusd_amount_micro, token_amounts_json "
        "FROM settlement_commands WHERE command_id = ?",
        (cmds[0],),
    ).fetchone()
    assert row is not None
    assert row["condition_id"] == _CID
    assert row["state"] == "REDEEM_INTENT_CREATED"
    assert row["winning_index_set"] == '["2"]', (
        f"S1 FAIL: YES winner must carry Zeus label [\"2\"], got {row['winning_index_set']!r}"
    )
    # Amount comes from CHAIN truth (live balance), not the API size snapshot.
    assert row["pusd_amount_micro"] == 797_976_847
    assert _ASSET in row["token_amounts_json"]
    # Probe was called with the Zeus YES label.
    assert adapter.probe_calls == [(_CID, 2)]


def test_s1b_no_winner_uses_zeus_label_1(trade_conn):
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _ProbeAdapter(balance_micro=15_000_000)
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE,
        positions=[_position(outcome="No", outcomeIndex=1, size=15.0)],
    )
    assert len(cmds) == 1
    row = trade_conn.execute(
        "SELECT winning_index_set FROM settlement_commands WHERE command_id = ?",
        (cmds[0],),
    ).fetchone()
    assert row["winning_index_set"] == '["1"]'
    assert adapter.probe_calls == [(_CID, 1)]


def test_s2_zero_live_balance_not_enqueued(trade_conn):
    """S2: API still lists the position but chain balance is 0 (already
    redeemed / API lag) -> no enqueue, no row churn."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _ProbeAdapter(balance_micro=0)
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_position()],
    )
    assert cmds == []
    n = trade_conn.execute("SELECT COUNT(*) FROM settlement_commands").fetchone()[0]
    assert n == 0, "S2 FAIL: zero-balance candidate created a command row."


def test_s3_position_id_mismatch_fails_closed(trade_conn):
    """S3: derived positionId != API asset -> fail-closed (the inverted
    index-set convention bug class; never enqueue on mismatched identity)."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _ProbeAdapter(balance_micro=797_976_847, position_id="12345")
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_position()],
    )
    assert cmds == []
    n = trade_conn.execute("SELECT COUNT(*) FROM settlement_commands").fetchone()[0]
    assert n == 0, "S3 FAIL: mismatched positionId still enqueued."


def test_s4_zero_value_and_non_redeemable_filtered(trade_conn):
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _ProbeAdapter(balance_micro=5_000_000)
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE,
        positions=[
            _position(curPrice=0),            # losing dust
            _position(redeemable=False),       # not yet resolved
            _position(curPrice=0.4),           # below winner floor
        ],
    )
    assert cmds == []
    assert adapter.probe_calls == [], "S4 FAIL: filtered candidates were probed."


def test_s5_active_command_dedupes(trade_conn):
    """S5: sweeping twice while the first command is still active returns the
    same command id (request_redeem active-row dedupe) — no duplicate intents."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _ProbeAdapter(balance_micro=15_000_000)
    first = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_position()],
    )
    second = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_position()],
    )
    assert first == second, "S5 FAIL: duplicate command for the same active condition."
    n = trade_conn.execute(
        "SELECT COUNT(*) FROM settlement_commands WHERE condition_id = ?", (_CID,)
    ).fetchone()[0]
    assert n == 1


def test_w1_redeemed_usdce_proceeds_auto_wrap():
    """W1 (auto-unwrap antibody): USDC.e proceeds at the Safe above threshold
    enqueue WRAP_REQUESTED with the full balance — the redeem->usable-collateral
    chain closes without operator hands.

    Sed-flip: break _read_usdce_balance wiring or the threshold gate -> RED."""
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        enqueue_wrap_if_balance_above_threshold,
    )

    proceeds_micro = 857_476_847  # the 2026-06-09 redemption proceeds

    class _FakeEth:
        def call(self, params, block_identifier="latest"):
            # web3 returns HexBytes (bytes subclass); _read_usdce_balance does
            # int.from_bytes on it.
            return proceeds_micro.to_bytes(32, "big")

    class _FakeW3:
        eth = _FakeEth()

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        cmd = enqueue_wrap_if_balance_above_threshold(_SAFE, _FakeW3(), conn)
        assert cmd is not None, "W1 FAIL: proceeds above threshold did not enqueue a wrap."
        row = conn.execute(
            "SELECT state, amount_micro FROM wrap_unwrap_commands WHERE command_id = ?",
            (cmd,),
        ).fetchone()
        assert row["state"] == WrapUnwrapState.WRAP_REQUESTED.value
        assert row["amount_micro"] == proceeds_micro
        # Idempotency: second call with a pending row is a no-op.
        assert enqueue_wrap_if_balance_above_threshold(_SAFE, _FakeW3(), conn) is None
    finally:
        conn.close()
