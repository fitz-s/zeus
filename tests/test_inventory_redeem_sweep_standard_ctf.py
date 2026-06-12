# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator redeem directive 2026-06-10 ($19 stuck — a standard-CTF
#   (non-negRisk) winning position the negRisk-only sweep detected, logged
#   [INVENTORY_SWEEP_NON_NEGRISK_WINNER], and skipped forever).
# Lifecycle: created=2026-06-10; last_reviewed=2026-06-10; last_reused=never
# Purpose: Relationship antibody — when the data-api lists a chain-confirmed
#   non-negRisk redeemable winner, the sweep enqueues a standard-CTF redeem
#   command EXACTLY ONCE (idempotent on re-sweep), routes through the standard-CTF
#   probe (NOT the negRisk probe), and the negRisk lane is unchanged. The
#   chain-truth veto (derived positionId == data-api asset AND balance>0) is
#   preserved for both lanes, so unconfirmed / foreign positions are never swept.
# Reuse: Run when modifying inventory_redeem_sweep.py probe routing, the
#   standard-CTF balance probe, or the request_redeem enqueue.
"""Relationship antibodies for the standard-CTF lane of the inventory sweep.

Cross-module invariant under test (Module A = data-api positions list, Module B =
settlement_commands ledger): a non-negRisk redeemable winner whose CHAIN balance
is confirmed positive AND whose derived standard-CTF positionId equals the
data-api asset id flows into the ledger as exactly one REDEEM_INTENT_CREATED row,
using the Zeus winning_index_set the redeem lane expects. The negRisk lane is
proven untouched (it still uses get_negrisk_winning_position_balance).
"""

from __future__ import annotations

import sqlite3

import pytest

from src.state.db import init_schema


_CID = "0x" + "ab" * 32
# Standard-CTF asset id (the data-api `asset` IS the CTF positionId for non-negRisk).
_ASSET = "58247836098992232373879939396068240624111886040586191377868376850782672360377"
_SAFE = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"


@pytest.fixture(autouse=True)
def _reset_rotation():
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


class _DualProbeAdapter:
    """Adapter exposing BOTH probes; records which lane each call hit.

    The negRisk probe asserts it is NEVER called for a non-negRisk candidate
    (lane isolation); the standard-CTF probe returns the configured balance.
    """

    def __init__(self, *, balance_micro: int, position_id: str = _ASSET, ok: bool = True):
        self._balance = balance_micro
        self._pid = position_id
        self._ok = ok
        self.standard_calls: list[tuple[str, int]] = []
        self.negrisk_calls: list[tuple[str, int]] = []

    def get_standard_ctf_winning_position_balance(self, condition_id, index_set, *, holder=None):
        self.standard_calls.append((condition_id, index_set))
        if not self._ok:
            return {"ok": False, "errorCode": "REDEEM_BALANCE_PROBE_FAILED"}
        return {
            "ok": True,
            "balance_micro": self._balance,
            "position_id": int(self._pid),
            "collateral": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            "holder": holder or _SAFE,
            "zeus_index_set": index_set,
            "ctf_index_set": 1 if index_set == 2 else 2,
        }

    def get_negrisk_winning_position_balance(self, condition_id, index_set, *, holder=None):
        self.negrisk_calls.append((condition_id, index_set))
        return {"ok": True, "balance_micro": self._balance, "position_id": int(self._pid)}


def _std_position(**over):
    base = {
        "conditionId": _CID,
        "asset": _ASSET,
        "outcome": "No",
        "outcomeIndex": 1,
        "size": 10.0,
        "curPrice": 0,            # the $19 case: data-api price is 0 (mutable; not a veto)
        "redeemable": True,
        "negativeRisk": False,    # STANDARD CTF
        "title": "Will a Claude Mythos model be released by June 9, 2026?",
    }
    base.update(over)
    return base


def test_standard_ctf_winner_enqueued_via_standard_probe(trade_conn):
    """R1: a chain-confirmed non-negRisk NO winner is enqueued exactly once via
    the standard-CTF probe; the negRisk probe is never touched.

    Sed-flip: route non-negRisk through the negRisk probe -> negrisk_calls
    non-empty / positionId mismatch -> RED."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _DualProbeAdapter(balance_micro=10_000_000)
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_std_position()],
    )
    assert len(cmds) == 1, "R1 FAIL: standard-CTF winner was not enqueued."
    assert adapter.standard_calls == [(_CID, 1)], (
        "R1 FAIL: NO winner must probe the standard-CTF lane with Zeus label 1."
    )
    assert adapter.negrisk_calls == [], "R1 FAIL: negRisk probe was wrongly used."
    row = trade_conn.execute(
        "SELECT condition_id, state, winning_index_set, pusd_amount_micro, token_amounts_json "
        "FROM settlement_commands WHERE command_id = ?",
        (cmds[0],),
    ).fetchone()
    assert row["condition_id"] == _CID
    assert row["state"] == "REDEEM_INTENT_CREATED"
    # NO winner carries Zeus label ["1"]; the standard-CTF calldata builder
    # translates this to the on-chain CTF bitmask (2) at submit time.
    assert row["winning_index_set"] == '["1"]'
    # Amount comes from CHAIN balance, not the data-api size snapshot.
    assert row["pusd_amount_micro"] == 10_000_000
    assert _ASSET in row["token_amounts_json"]


def test_standard_ctf_resweep_is_idempotent(trade_conn):
    """R1 (idempotency): re-sweeping while the command is active returns the SAME
    command id — no duplicate intents (request_redeem active-row dedupe)."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _DualProbeAdapter(balance_micro=10_000_000)
    first = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_std_position()],
    )
    second = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_std_position()],
    )
    assert first == second and len(first) == 1, "idempotency FAIL: duplicate command."
    n = trade_conn.execute(
        "SELECT COUNT(*) FROM settlement_commands WHERE condition_id = ?", (_CID,)
    ).fetchone()[0]
    assert n == 1


def test_standard_ctf_zero_balance_not_enqueued(trade_conn):
    """R3: chain balance 0 (already redeemed / foreign / API lag) -> NOT enqueued.
    The chain veto strictness is identical to the negRisk lane."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _DualProbeAdapter(balance_micro=0)
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_std_position()],
    )
    assert cmds == []
    assert adapter.standard_calls == [(_CID, 1)], "row must be probed (chain is the veto)."
    assert trade_conn.execute("SELECT COUNT(*) FROM settlement_commands").fetchone()[0] == 0


def test_standard_ctf_position_id_mismatch_fails_closed(trade_conn):
    """R3: derived positionId != data-api asset -> fail-closed (never enqueue on
    mismatched identity). This is the foreign/wrong-token guard."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _DualProbeAdapter(balance_micro=10_000_000, position_id="999")
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_std_position()],
    )
    assert cmds == []
    assert trade_conn.execute("SELECT COUNT(*) FROM settlement_commands").fetchone()[0] == 0


def test_negrisk_lane_unchanged_uses_negrisk_probe(trade_conn):
    """R2: a negRisk winner still routes through the negRisk probe — the
    standard-CTF addition did not change the existing lane."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    adapter = _DualProbeAdapter(balance_micro=15_000_000, position_id=_ASSET)
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE,
        positions=[_std_position(negativeRisk=True, outcome="Yes", outcomeIndex=0)],
    )
    assert len(cmds) == 1
    assert adapter.negrisk_calls == [(_CID, 2)], "R2 FAIL: negRisk winner must use negRisk probe."
    assert adapter.standard_calls == [], "R2 FAIL: standard-CTF probe wrongly used for negRisk."


def test_standard_ctf_skipped_when_only_negrisk_probe_available(trade_conn):
    """Lane-availability: an adapter exposing ONLY the negRisk probe must NOT
    silently route a non-negRisk winner through it (that would derive the wrong
    positionId). It is skipped loudly instead."""
    from src.execution.inventory_redeem_sweep import sweep_chain_inventory_for_redeems

    class _NegRiskOnlyAdapter:
        def __init__(self):
            self.calls = []

        def get_negrisk_winning_position_balance(self, condition_id, index_set, *, holder=None):
            self.calls.append((condition_id, index_set))
            return {"ok": True, "balance_micro": 10_000_000, "position_id": int(_ASSET)}

    adapter = _NegRiskOnlyAdapter()
    cmds = sweep_chain_inventory_for_redeems(
        trade_conn, adapter, _SAFE, positions=[_std_position()],
    )
    assert cmds == [], "non-negRisk winner must not be routed through the negRisk probe."
    assert adapter.calls == [], "negRisk probe must not be called for a non-negRisk candidate."
