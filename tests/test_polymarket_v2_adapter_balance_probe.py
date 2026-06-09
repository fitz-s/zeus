# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator redeem directive 2026-06-09 (chain-truth redeem inputs).
# Lifecycle: created=2026-06-09; last_reviewed=2026-06-09; last_reused=never
# Purpose: Antibody — adapter.get_negrisk_winning_position_balance derives the
#   winning negRisk position id via wcol()->getCollectionId->getPositionId and
#   reads balanceOf with the correct selectors, failing closed on RPC error.
# Reuse: Run when modifying the balance-probe derivation, the CTF/negRisk
#   selectors, or _eth_call_uint.
"""Antibody tests for the chain-truth balance probe in PolymarketV2Adapter."""

from __future__ import annotations

from src.venue.polymarket_v2_adapter import (
    CTF_GET_COLLECTION_ID_SELECTOR,
    CTF_GET_POSITION_ID_SELECTOR,
    ERC1155_BALANCE_OF_SELECTOR,
    NEGRISK_WCOL_SELECTOR,
    POLYGON_CTF_ADDRESS,
    POLYGON_NEGRISK_ADAPTER_ADDRESS,
    PolymarketV2Adapter,
)

_CONDITION_ID = "0x" + "cd" * 32
_WCOL = "0x3A3BD7bb9528E159577F7C2e685CC81A765002E2"
_SAFE = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"


def test_selectors_are_canonical():
    """Selectors must equal keccak256(sig)[:4]; pinned to prevent ABI drift."""
    from eth_utils import keccak

    assert NEGRISK_WCOL_SELECTOR == "0x" + keccak(text="wcol()")[:4].hex()
    assert CTF_GET_COLLECTION_ID_SELECTOR == "0x" + keccak(
        text="getCollectionId(bytes32,bytes32,uint256)")[:4].hex()
    assert CTF_GET_POSITION_ID_SELECTOR == "0x" + keccak(
        text="getPositionId(address,bytes32)")[:4].hex()
    assert ERC1155_BALANCE_OF_SELECTOR == "0x" + keccak(
        text="balanceOf(address,uint256)")[:4].hex()


def _build_stub_rpc(
    *,
    balance_micro: int,
    position_id: int = 0xABC,
    wcol: str = _WCOL,
    expected_ctf_index_set: int | None = None,
):
    """Return an rpc_call stub that answers wcol/getCollectionId/getPositionId/
    balanceOf by inspecting the selector prefix of the eth_call data."""
    calls = []

    def _rpc(url, method, params):
        assert method == "eth_call"
        to = params[0]["to"].lower()
        data = params[0]["data"]
        selector = data[:10]
        calls.append((to, selector))
        if selector == NEGRISK_WCOL_SELECTOR:
            assert to == POLYGON_NEGRISK_ADAPTER_ADDRESS.lower()
            return "0x" + wcol.removeprefix("0x").lower().rjust(64, "0")
        if selector == CTF_GET_COLLECTION_ID_SELECTOR:
            assert to == POLYGON_CTF_ADDRESS.lower()
            if expected_ctf_index_set is not None:
                # CONVENTION ANTIBODY (2026-06-09): the indexSet word MUST be
                # the CTF bitmask (1<<slot), NOT the Zeus label. Zeus 2 (YES,
                # slot0) -> CTF 1; Zeus 1 (NO, slot1) -> CTF 2. A pass-through
                # derives the OPPOSITE outcome's position (proven on-chain
                # against the Safe's 7 real winners, 2026-06-09).
                assert data.lower().endswith(format(expected_ctf_index_set, "064x")), (
                    f"getCollectionId indexSet word is not CTF bitmask "
                    f"{expected_ctf_index_set}; calldata tail={data[-64:]}"
                )
            return "0x" + ("11" * 32)
        if selector == CTF_GET_POSITION_ID_SELECTOR:
            assert to == POLYGON_CTF_ADDRESS.lower()
            # WCOL address must be embedded in the getPositionId calldata.
            assert wcol.removeprefix("0x").lower() in data.lower()
            return "0x" + format(position_id, "064x")
        if selector == ERC1155_BALANCE_OF_SELECTOR:
            assert to == POLYGON_CTF_ADDRESS.lower()
            # positionId must be the last 32-byte word of balanceOf calldata.
            assert data.lower().endswith(format(position_id, "064x"))
            return "0x" + format(balance_micro, "064x")
        raise AssertionError(f"unexpected selector {selector}")

    return _rpc, calls


def test_probe_returns_live_balance_and_derived_position():
    rpc, calls = _build_stub_rpc(balance_micro=3_210_000, position_id=0xDEAD)
    adapter = PolymarketV2Adapter(
        funder_address=_SAFE,
        signer_key="0x" + "1" * 64,
        polygon_rpc_url="https://rpc.example",
        rpc_call=rpc,
    )
    out = adapter.get_negrisk_winning_position_balance(_CONDITION_ID, 1)
    assert out["ok"] is True
    assert out["balance_micro"] == 3_210_000
    assert out["position_id"] == 0xDEAD
    assert out["holder"] == _SAFE
    # All four on-chain reads issued in order.
    selectors = [c[1] for c in calls]
    assert selectors == [
        NEGRISK_WCOL_SELECTOR,
        CTF_GET_COLLECTION_ID_SELECTOR,
        CTF_GET_POSITION_ID_SELECTOR,
        ERC1155_BALANCE_OF_SELECTOR,
    ]


def test_probe_maps_zeus_index_to_ctf_bitmask():
    """CONVENTION ANTIBODY: Zeus 2 (YES, slot0) -> CTF indexSet 1;
    Zeus 1 (NO, slot1) -> CTF indexSet 2.

    Sed-flip: revert the mapping to a pass-through -> the stub's calldata
    assertion fires -> RED. This is the bug class that derived the LOSING
    token for all 7 real winners on 2026-06-09 (live=0, pid mismatch)."""
    # Zeus YES label 2 must hit chain as CTF bitmask 1.
    rpc, _ = _build_stub_rpc(balance_micro=1, expected_ctf_index_set=1)
    adapter = PolymarketV2Adapter(
        funder_address=_SAFE, signer_key="0x" + "1" * 64,
        polygon_rpc_url="https://rpc.example", rpc_call=rpc,
    )
    out = adapter.get_negrisk_winning_position_balance(_CONDITION_ID, 2)
    assert out["ok"] is True
    assert out["zeus_index_set"] == 2 and out["ctf_index_set"] == 1

    # Zeus NO label 1 must hit chain as CTF bitmask 2.
    rpc, _ = _build_stub_rpc(balance_micro=1, expected_ctf_index_set=2)
    adapter = PolymarketV2Adapter(
        funder_address=_SAFE, signer_key="0x" + "1" * 64,
        polygon_rpc_url="https://rpc.example", rpc_call=rpc,
    )
    out = adapter.get_negrisk_winning_position_balance(_CONDITION_ID, 1)
    assert out["ok"] is True
    assert out["zeus_index_set"] == 1 and out["ctf_index_set"] == 2


def test_probe_zero_balance_is_ok_true_balance_zero():
    rpc, _ = _build_stub_rpc(balance_micro=0, position_id=0x1)
    adapter = PolymarketV2Adapter(
        funder_address=_SAFE, signer_key="0x" + "1" * 64,
        polygon_rpc_url="https://rpc.example", rpc_call=rpc,
    )
    out = adapter.get_negrisk_winning_position_balance(_CONDITION_ID, 1)
    assert out["ok"] is True and out["balance_micro"] == 0


def test_probe_fails_closed_on_rpc_error():
    def _boom(url, method, params):
        raise RuntimeError("rpc down")

    adapter = PolymarketV2Adapter(
        funder_address=_SAFE, signer_key="0x" + "1" * 64,
        polygon_rpc_url="https://rpc.example", rpc_call=_boom,
    )
    out = adapter.get_negrisk_winning_position_balance(_CONDITION_ID, 1)
    assert out["ok"] is False
    assert out["errorCode"] == "REDEEM_BALANCE_PROBE_FAILED"


def test_probe_rejects_non_binary_index_set():
    adapter = PolymarketV2Adapter(
        funder_address=_SAFE, signer_key="0x" + "1" * 64,
        polygon_rpc_url="https://rpc.example", rpc_call=lambda *a: "0x0",
    )
    out = adapter.get_negrisk_winning_position_balance(_CONDITION_ID, 3)
    assert out["ok"] is False
    assert out["errorCode"] == "REDEEM_CALLDATA_BUILD_FAILED"
