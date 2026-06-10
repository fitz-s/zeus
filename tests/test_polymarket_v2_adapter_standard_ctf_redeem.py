# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator redeem directive 2026-06-10 ($19 stuck on a standard-CTF
#   NO winner). On-chain verification 2026-06-10: condition 0xde5f67…d9c asset
#   …360377 outcome="No"/outcomeIndex=1 -> positionId derives from
#   getPositionId(USDC.e, getCollectionId(0, conditionId, 1<<1=2)). pUSD collateral
#   does NOT match; YES bitmask (1) does NOT match.
# Lifecycle: created=2026-06-10; last_reviewed=2026-06-10; last_reused=never
# Purpose: Antibody — the standard-CTF redeem lane (a) derives the winning
#   positionId from USDC.e collateral (not pUSD), (b) translates the Zeus binary
#   label (1=NO, 2=YES) into the on-chain CTF bitmask (1<<slot) so a NO winner is
#   never encoded as the losing YES token, and (c) builds redeemPositions calldata
#   with USDC.e collateral and the bitmask. These are the two bug classes that
#   would have made the stuck $19 redeem revert.
# Reuse: Run when modifying _build_redeem_calldata, _zeus_index_set_to_ctf_bitmask,
#   or get_standard_ctf_winning_position_balance.
"""Antibodies for the standard-CTF (non-negRisk) redeem lane."""

from __future__ import annotations

from src.venue.polymarket_v2_adapter import (
    CTF_GET_COLLECTION_ID_SELECTOR,
    CTF_GET_POSITION_ID_SELECTOR,
    CTF_REDEEM_POSITIONS_SELECTOR,
    ERC1155_BALANCE_OF_SELECTOR,
    POLYGON_CTF_ADDRESS,
    POLYGON_PUSD_ADDRESS,
    POLYGON_USDCE_ADDRESS,
    PolymarketV2Adapter,
    _build_redeem_calldata,
    _zeus_index_set_to_ctf_bitmask,
)

_CONDITION_ID = "0x" + "cd" * 32
_SAFE = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"


# ---------------------------------------------------------------------------
# Zeus label -> CTF bitmask convention (the single source of truth).
# ---------------------------------------------------------------------------

def test_zeus_label_to_ctf_bitmask_mapping():
    """Zeus 2 (YES, slot0) -> CTF 1; Zeus 1 (NO, slot1) -> CTF 2.

    Sed-flip: make this a pass-through (return zeus_index_set) -> the NO winner
    would encode bitmask 1 (the losing YES token) -> RED here and the calldata
    test below."""
    assert _zeus_index_set_to_ctf_bitmask(2) == 1
    assert _zeus_index_set_to_ctf_bitmask(1) == 2


def test_zeus_label_to_ctf_bitmask_rejects_non_binary():
    import pytest

    for bad in (0, 3, -1):
        with pytest.raises(ValueError):
            _zeus_index_set_to_ctf_bitmask(bad)


# ---------------------------------------------------------------------------
# Standard-CTF redeem calldata: USDC.e collateral + bitmask translation.
# ---------------------------------------------------------------------------

def test_redeem_calldata_uses_usdce_collateral_not_pusd():
    """The collateral word must be USDC.e, never pUSD. A pUSD collateral derives
    the wrong positionId and the on-chain redeem reverts (verified 2026-06-10)."""
    calldata = _build_redeem_calldata(_CONDITION_ID, [2])  # Zeus YES
    assert calldata.startswith(CTF_REDEEM_POSITIONS_SELECTOR)
    body = calldata.removeprefix(CTF_REDEEM_POSITIONS_SELECTOR).lower()
    assert POLYGON_USDCE_ADDRESS.removeprefix("0x").lower() in body, (
        "standard-CTF redeem calldata must carry USDC.e collateral."
    )
    assert POLYGON_PUSD_ADDRESS.removeprefix("0x").lower() not in body, (
        "standard-CTF redeem calldata must NOT carry pUSD collateral."
    )


def test_redeem_calldata_no_winner_encodes_ctf_bitmask_2():
    """A Zeus NO winner (label 1) must encode CTF indexSet bitmask 2 in the
    redeemPositions calldata's uint256[] — the slot1 (NO) token. This is the
    exact $19-stuck case.

    Sed-flip: drop the Zeus->bitmask translation -> the tail word becomes 1 -> RED."""
    calldata = _build_redeem_calldata(_CONDITION_ID, [1])  # Zeus NO
    body = calldata.removeprefix(CTF_REDEEM_POSITIONS_SELECTOR)
    # redeemPositions(address, bytes32, bytes32, uint256[]): the dynamic array is
    # the last component — its single element is the final 32-byte word.
    last_word = int(body[-64:], 16)
    assert last_word == 2, f"NO winner must encode CTF bitmask 2, got {last_word}"


def test_redeem_calldata_yes_winner_encodes_ctf_bitmask_1():
    calldata = _build_redeem_calldata(_CONDITION_ID, [2])  # Zeus YES
    body = calldata.removeprefix(CTF_REDEEM_POSITIONS_SELECTOR)
    last_word = int(body[-64:], 16)
    assert last_word == 1, f"YES winner must encode CTF bitmask 1, got {last_word}"


# ---------------------------------------------------------------------------
# Standard-CTF balance probe: USDC.e-derived positionId + balanceOf.
# ---------------------------------------------------------------------------

def _build_stub_rpc(*, balance_micro: int, position_id: int = 0xABC,
                    expected_ctf_index_set: int | None = None):
    calls = []

    def _rpc(url, method, params):
        assert method == "eth_call"
        to = params[0]["to"].lower()
        data = params[0]["data"]
        selector = data[:10]
        calls.append((to, selector))
        if selector == CTF_GET_COLLECTION_ID_SELECTOR:
            assert to == POLYGON_CTF_ADDRESS.lower()
            if expected_ctf_index_set is not None:
                assert data.lower().endswith(format(expected_ctf_index_set, "064x")), (
                    f"getCollectionId indexSet word is not CTF bitmask "
                    f"{expected_ctf_index_set}; tail={data[-64:]}"
                )
            return "0x" + ("11" * 32)
        if selector == CTF_GET_POSITION_ID_SELECTOR:
            assert to == POLYGON_CTF_ADDRESS.lower()
            # USDC.e (NOT WCOL, NOT pUSD) must be embedded in getPositionId calldata.
            assert POLYGON_USDCE_ADDRESS.removeprefix("0x").lower() in data.lower()
            return "0x" + format(position_id, "064x")
        if selector == ERC1155_BALANCE_OF_SELECTOR:
            assert to == POLYGON_CTF_ADDRESS.lower()
            assert data.lower().endswith(format(position_id, "064x"))
            return "0x" + format(balance_micro, "064x")
        raise AssertionError(f"unexpected selector {selector}")

    return _rpc, calls


def test_standard_probe_returns_live_balance_and_derived_position():
    rpc, calls = _build_stub_rpc(balance_micro=10_000_000, position_id=0xDEAD)
    adapter = PolymarketV2Adapter(
        funder_address=_SAFE, signer_key="0x" + "1" * 64,
        polygon_rpc_url="https://rpc.example", rpc_call=rpc,
    )
    out = adapter.get_standard_ctf_winning_position_balance(_CONDITION_ID, 1)
    assert out["ok"] is True
    assert out["balance_micro"] == 10_000_000
    assert out["position_id"] == 0xDEAD
    assert out["holder"] == _SAFE
    # No wcol() call: standard CTF derives directly from USDC.e (3 reads, not 4).
    selectors = [c[1] for c in calls]
    assert selectors == [
        CTF_GET_COLLECTION_ID_SELECTOR,
        CTF_GET_POSITION_ID_SELECTOR,
        ERC1155_BALANCE_OF_SELECTOR,
    ]


def test_standard_probe_maps_zeus_index_to_ctf_bitmask():
    """Zeus 2 (YES) -> CTF bitmask 1; Zeus 1 (NO) -> CTF bitmask 2."""
    rpc, _ = _build_stub_rpc(balance_micro=1, expected_ctf_index_set=1)
    adapter = PolymarketV2Adapter(
        funder_address=_SAFE, signer_key="0x" + "1" * 64,
        polygon_rpc_url="https://rpc.example", rpc_call=rpc,
    )
    out = adapter.get_standard_ctf_winning_position_balance(_CONDITION_ID, 2)
    assert out["ok"] and out["zeus_index_set"] == 2 and out["ctf_index_set"] == 1

    rpc, _ = _build_stub_rpc(balance_micro=1, expected_ctf_index_set=2)
    adapter = PolymarketV2Adapter(
        funder_address=_SAFE, signer_key="0x" + "1" * 64,
        polygon_rpc_url="https://rpc.example", rpc_call=rpc,
    )
    out = adapter.get_standard_ctf_winning_position_balance(_CONDITION_ID, 1)
    assert out["ok"] and out["zeus_index_set"] == 1 and out["ctf_index_set"] == 2


def test_standard_probe_fails_closed_on_rpc_error():
    def _boom(url, method, params):
        raise RuntimeError("rpc down")

    adapter = PolymarketV2Adapter(
        funder_address=_SAFE, signer_key="0x" + "1" * 64,
        polygon_rpc_url="https://rpc.example", rpc_call=_boom,
    )
    out = adapter.get_standard_ctf_winning_position_balance(_CONDITION_ID, 1)
    assert out["ok"] is False
    assert out["errorCode"] == "REDEEM_BALANCE_PROBE_FAILED"


def test_standard_probe_rejects_non_binary_index_set():
    adapter = PolymarketV2Adapter(
        funder_address=_SAFE, signer_key="0x" + "1" * 64,
        polygon_rpc_url="https://rpc.example", rpc_call=lambda *a: "0x0",
    )
    out = adapter.get_standard_ctf_winning_position_balance(_CONDITION_ID, 3)
    assert out["ok"] is False
    assert out["errorCode"] == "REDEEM_CALLDATA_BUILD_FAILED"
