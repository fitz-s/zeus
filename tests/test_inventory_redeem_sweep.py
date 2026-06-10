# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator redeem directive 2026-06-09 (auto-collect: the redeem
#   trigger is the Safe's actual chain inventory, never the portfolio ledger).
#   2026-06-09 P1 follow-up: curPrice is a mutable data-api field, NOT a veto on
#   chain truth — structural prefilter only; CHAIN balance is the sole veto.
# Lifecycle: created=2026-06-09; last_reviewed=2026-06-09; last_reused=2026-06-09
# Purpose: Antibody — sweep_chain_inventory_for_redeems enqueues redeem commands
#   from chain holdings with ZERO ledger involvement (the London-16 case), fails
#   closed on chain mismatch, NEVER lets a data-api curPrice skip the chain probe
#   (the $857 stranded-winner category), and redeemed USDC.e proceeds auto-wrap
#   via enqueue_wrap_if_balance_above_threshold (no operator hands anywhere).
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
  S4: non-redeemable rows are structurally filtered (never probed); but a
      low/zero curPrice is NOT a structural veto — see C1/C2/C3.
  S5: re-sweep while a command is active returns the SAME command id (no dupes).
  W1 (auto-unwrap): redeemed USDC.e proceeds above threshold enqueue a
      WRAP_REQUESTED row with the full balance (no operator hands).

curPrice-truth antibodies (2026-06-09 P1 — the $857 stranded-winner category):
  C1 (JACKPOT): redeemable=True, curPrice=0.0, size>0, correct asset, chain
      balance>0 -> ENQUEUED. A mutable data-api price MUST NOT skip the chain
      probe. This is the load-bearing antibody.
  C2 (CHAIN VETO BOTH WAYS): curPrice=0.0 but chain balance 0 -> NOT enqueued.
  C3 (DATA-API LIES, TELEMETRY): below-threshold curPrice + chain balance>0 ->
      WARNING logged AND enqueued.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.state.db import init_schema


_CID = "0x" + "ee" * 32
_ASSET = "81633771000021127658752261257414280805140874457786559998422070609600874978435"
_SAFE = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"


@pytest.fixture(autouse=True)
def _reset_rotation():
    """The per-Safe probe-rotation cursor is module state; isolate each test."""
    from src.execution.inventory_redeem_sweep import reset_rotation_for_tests

    reset_rotation_for_tests()
    yield
    reset_rotation_for_tests()


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


def test_s4_non_redeemable_filtered_structurally(trade_conn):
    """S4: non-redeemable / zero-size / no-identity rows are STRUCTURALLY
    filtered (never probed). curPrice is NOT a structural veto, so a low/zero
    curPrice row is NOT filtered here — it flows to the chain probe (see C1).

    Sed-flip: re-add `cur_price > min_cur_price` to the prefilter -> the
    curPrice=0 row below would be filtered and C1 would RED."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    # Chain says these are all LOSERS (balance 0): the chain veto suppresses
    # them, NOT a price prefilter. Probe IS called for the structurally-eligible
    # rows (curPrice 0 and 0.4); only the non-redeemable row is never probed.
    adapter = _ProbeAdapter(balance_micro=0)
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE,
        positions=[
            _position(curPrice=0),            # eligible -> probed -> chain veto (bal 0)
            _position(redeemable=False),       # NOT eligible -> never probed
            _position(curPrice=0.4),           # eligible -> probed -> chain veto (bal 0)
        ],
    )
    assert cmds == []
    # The non-redeemable row is structurally filtered (never probed); the two
    # low/zero-curPrice rows ARE probed — curPrice never short-circuits chain.
    assert len(adapter.probe_calls) == 2, (
        "S4 FAIL: a low/zero curPrice must NOT skip the chain probe; only the "
        f"non-redeemable row should be filtered. probe_calls={adapter.probe_calls!r}"
    )


def test_c1_zero_curprice_winner_is_the_jackpot_and_is_enqueued(trade_conn):
    """C1 (load-bearing antibody, the $857 category): a redeemable row whose
    MUTABLE data-api curPrice is 0.0 but whose CHAIN balance is large is the
    jackpot case — it MUST be probed and enqueued. A data-api price never vetoes
    chain truth.

    Sed-flip: restore `cur_price > min_cur_price` to the prefilter -> RED."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _ProbeAdapter(balance_micro=797_976_847)
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_position(curPrice=0.0)],
    )
    assert len(cmds) == 1, "C1 FAIL: curPrice=0 chain-winner was stranded (not enqueued)."
    assert adapter.probe_calls == [(_CID, 2)], "C1 FAIL: chain probe was skipped on curPrice=0."
    row = trade_conn.execute(
        "SELECT pusd_amount_micro FROM settlement_commands WHERE command_id = ?",
        (cmds[0],),
    ).fetchone()
    assert row["pusd_amount_micro"] == 797_976_847, "C1 FAIL: amount must come from CHAIN balance."


def test_c2_zero_curprice_with_zero_chain_balance_not_enqueued(trade_conn):
    """C2 (chain veto both ways): curPrice=0 AND chain balance 0 -> the chain
    veto suppresses it. The row IS probed (no price short-circuit), but chain
    truth says nothing to claim."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _ProbeAdapter(balance_micro=0)
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_position(curPrice=0.0)],
    )
    assert cmds == []
    assert adapter.probe_calls == [(_CID, 2)], "C2 FAIL: row must still be probed (chain is the veto)."
    n = trade_conn.execute("SELECT COUNT(*) FROM settlement_commands").fetchone()[0]
    assert n == 0


def test_c3_below_threshold_curprice_with_chain_balance_warns_and_enqueues(trade_conn, caplog):
    """C3 (data-api lies, telemetry): a below-threshold curPrice on a
    chain-CONFIRMED winner means the mutable data-api price is stale/wrong. We
    WARN (telemetry of the lie) AND enqueue on chain truth."""
    import logging

    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _ProbeAdapter(balance_micro=120_000_000)
    with caplog.at_level(logging.WARNING, logger="src.execution.inventory_redeem_sweep"):
        cmds = sweep_chain_inventory_for_redeems(
            trade_conn, adapter, _SAFE, positions=[_position(curPrice=0.3)],
        )
    assert len(cmds) == 1, "C3 FAIL: below-threshold-but-chain-confirmed winner was not enqueued."
    assert any(
        "DATA_API_PRICE_DISAGREES" in r.message for r in caplog.records
    ), "C3 FAIL: data-api price disagreement was not logged as telemetry."


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
