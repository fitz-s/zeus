# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=2026-05-19
# Purpose: Antibody tests for NegRiskAdapter redeem calldata + routing
# Reuse: pytest tests/test_polymarket_v2_adapter_negrisk_redeem.py
# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PR #187 negRisk routing fix; on-chain evidence 2026-05-19
#   (NegRiskCtfAdapter at 0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296;
#    Karachi failed-redeem tx 0x0c85d94640d33...; successful reference
#    tx 0x4ce58f2683bd...);
#   .omc/plans/2026-05-19-negrisk-redeem-routing.md

"""Antibody tests for negRisk redeem calldata + routing.

Pins the Karachi reference call (condition_id=c5faddf4...44ae, YES side,
amount=1587297 micro-units) byte-for-byte against the on-chain ABI:

  NegRiskCtfAdapter.redeemPositions(bytes32 conditionId, uint256[] amounts)
  selector: 0xdbeccb23

Slot mapping (verified on-chain 2026-05-19):
  indexSet=2 (YES won) → amounts=[amount, 0]   (slot 0 = YES)
  indexSet=1 (NO  won) → amounts=[0, amount]   (slot 1 = NO)

These tests guard against three classes of regression:
  1) Calldata builder accepting/passing wrong amount (Karachi defect)
  2) Slot-mapping flip (swapping YES↔NO amounts)
  3) Wrong selector (calling standard CTF instead of negRisk adapter)
"""

from __future__ import annotations

import pytest
from eth_abi import decode as abi_decode

from src.venue.polymarket_v2_adapter import (
    NEGRISK_REDEEM_POSITIONS_SELECTOR,
    POLYGON_NEGRISK_ADAPTER_ADDRESS,
    _build_negrisk_redeem_calldata,
)


# ── Constants ────────────────────────────────────────────────────────────────
# Karachi reference vector: real Polymarket position, real condition_id.
KARACHI_CONDITION_ID = (
    "c5faddf4810e0c14659dbdf170599dcb8304ef42afcccb84992b4d8fcb0f44ae"
)
KARACHI_AMOUNT_MICRO = 1_587_297  # 1.587297 tokens × 1e6


# ── Byte-for-byte Karachi reference (YES) ────────────────────────────────────
def test_karachi_yes_calldata_byte_for_byte() -> None:
    """Pins the exact bytes that should have been broadcast for Karachi.

    Structure (standard ABI for fn(bytes32, uint256[])):
      selector(4) + conditionId(32) + offset(32) + len(32) +
      amounts[0](32) + amounts[1](32) = 164 bytes
    """
    calldata = _build_negrisk_redeem_calldata(
        KARACHI_CONDITION_ID, [2], KARACHI_AMOUNT_MICRO
    )

    expected = (
        "0xdbeccb23"
        + "c5faddf4810e0c14659dbdf170599dcb8304ef42afcccb84992b4d8fcb0f44ae"
        + "0000000000000000000000000000000000000000000000000000000000000040"  # offset
        + "0000000000000000000000000000000000000000000000000000000000000002"  # len(amounts)=2
        + "0000000000000000000000000000000000000000000000000000000000183861"  # amounts[0]=1587297
        + "0000000000000000000000000000000000000000000000000000000000000000"  # amounts[1]=0
    )
    assert calldata == expected, (
        f"Karachi byte-for-byte mismatch.\nGot:      {calldata}\nExpected: {expected}"
    )

    # Length sanity: 4-byte selector + 5 × 32-byte words = 164 bytes = 328 hex
    raw = bytes.fromhex(calldata[2:])
    assert len(raw) == 164


# ── Slot mapping (YES vs NO) ─────────────────────────────────────────────────
def test_yes_side_amounts_at_slot_zero() -> None:
    """indexSet=2 (YES) → amounts=[amount, 0]."""
    calldata = _build_negrisk_redeem_calldata(KARACHI_CONDITION_ID, [2], 1_000_000)
    decoded = abi_decode(["bytes32", "uint256[]"], bytes.fromhex(calldata[2:])[4:])
    assert list(decoded[1]) == [1_000_000, 0]


def test_no_side_amounts_at_slot_one() -> None:
    """indexSet=1 (NO) → amounts=[0, amount]."""
    calldata = _build_negrisk_redeem_calldata(KARACHI_CONDITION_ID, [1], 2_500_000)
    decoded = abi_decode(["bytes32", "uint256[]"], bytes.fromhex(calldata[2:])[4:])
    assert list(decoded[1]) == [0, 2_500_000]


def test_yes_and_no_slot_mappings_are_inverse() -> None:
    """Antibody: NO calldata is NOT YES calldata (swap-prevention).

    If a future refactor accidentally maps both indexSets to the same slot,
    YES==NO calldata. This test catches that immediately.
    """
    yes = _build_negrisk_redeem_calldata(KARACHI_CONDITION_ID, [2], 999_999)
    no = _build_negrisk_redeem_calldata(KARACHI_CONDITION_ID, [1], 999_999)
    assert yes != no, "slot mapping is broken: YES and NO produced identical calldata"


# ── Selector + adapter address pinning ───────────────────────────────────────
def test_selector_is_negrisk_not_standard_ctf() -> None:
    """Karachi failure root cause: routed to standard CTF (0x01b7037c).

    Antibody: assert the negRisk selector is dbeccb23, NOT the standard CTF
    selector 01b7037c (which is what shipped in PR #183).
    """
    assert NEGRISK_REDEEM_POSITIONS_SELECTOR == "0xdbeccb23"
    calldata = _build_negrisk_redeem_calldata(KARACHI_CONDITION_ID, [2], 1)
    assert calldata.startswith("0xdbeccb23")
    assert "01b7037c" not in calldata[:10]


def test_adapter_address_is_polygon_negrisk() -> None:
    """Pinned on-chain bytecode verified 2026-05-19 (17KB at this address)."""
    assert POLYGON_NEGRISK_ADAPTER_ADDRESS == "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"


# ── Conditioning: condition_id encoded as bytes32 ────────────────────────────
def test_condition_id_with_0x_prefix_encodes_identically() -> None:
    """settlement_commands stores condition_id with 0x prefix; raw hex also works."""
    a = _build_negrisk_redeem_calldata(KARACHI_CONDITION_ID, [2], 100)
    b = _build_negrisk_redeem_calldata("0x" + KARACHI_CONDITION_ID, [2], 100)
    assert a == b


def test_condition_id_round_trips_through_abi() -> None:
    calldata = _build_negrisk_redeem_calldata(KARACHI_CONDITION_ID, [2], 42)
    decoded = abi_decode(["bytes32", "uint256[]"], bytes.fromhex(calldata[2:])[4:])
    assert decoded[0].hex() == KARACHI_CONDITION_ID


# ── Input validation (fail-closed) ───────────────────────────────────────────
def test_empty_index_sets_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _build_negrisk_redeem_calldata(KARACHI_CONDITION_ID, [], 100)


def test_multi_bin_index_sets_raises() -> None:
    """negRisk binary markets: exactly one index_set entry."""
    with pytest.raises(ValueError, match="exactly one"):
        _build_negrisk_redeem_calldata(KARACHI_CONDITION_ID, [1, 2], 100)


def test_invalid_index_set_value_raises() -> None:
    """Only indexSet ∈ {1, 2} accepted (binary markets)."""
    with pytest.raises(ValueError, match="indexSet=1.*indexSet=2"):
        _build_negrisk_redeem_calldata(KARACHI_CONDITION_ID, [3], 100)


def test_zero_amount_raises() -> None:
    """A zero amount would burn no tokens (Karachi failure mode)."""
    with pytest.raises(ValueError, match="positive"):
        _build_negrisk_redeem_calldata(KARACHI_CONDITION_ID, [2], 0)


def test_negative_amount_raises() -> None:
    with pytest.raises(ValueError, match="positive"):
        _build_negrisk_redeem_calldata(KARACHI_CONDITION_ID, [2], -1)


# ── redeem() routing surface (amount_per_slot threading) ─────────────────────
class _StubAdapter:
    """Constructs an adapter to test redeem() arg validation without web3."""

    def __init__(self) -> None:
        from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

        self.cls = PolymarketV2Adapter


def test_redeem_negrisk_requires_amount_per_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """When neg_risk=True and amount_per_slot is None, fail-closed before broadcast."""
    from src.venue.polymarket_v2_adapter import (
        AUTONOMOUS_REDEEM_ENABLED_ENV,
        PolymarketV2Adapter,
    )

    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")

    adapter = PolymarketV2Adapter(
        funder_address="0x0000000000000000000000000000000000000001",
        signature_type=2,
        chain_id=137,
        polygon_rpc_url="https://example.invalid/rpc",
        signer_key="0x" + "11" * 32,
    )
    result = adapter.redeem(
        KARACHI_CONDITION_ID,
        index_sets=[2],
        neg_risk=True,
        amount_per_slot=None,
    )
    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_NEGRISK_AMOUNT_MISSING", result


def test_redeem_standard_ctf_does_not_require_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    """When neg_risk=False, amount_per_slot is ignored — standard CTF path
    derives amounts from on-chain balance via the CTF contract itself.

    This test passes when the redeem() preflight does NOT reject a None
    amount_per_slot for the non-negRisk path (it should proceed to the next
    preflight stage, which will fail on the invalid RPC URL — that is fine;
    we just need to confirm the amount-missing guard does not fire here).
    """
    from src.venue.polymarket_v2_adapter import (
        AUTONOMOUS_REDEEM_ENABLED_ENV,
        PolymarketV2Adapter,
    )

    monkeypatch.setenv(AUTONOMOUS_REDEEM_ENABLED_ENV, "1")

    adapter = PolymarketV2Adapter(
        funder_address="0x0000000000000000000000000000000000000001",
        signature_type=2,
        chain_id=137,
        polygon_rpc_url="https://example.invalid/rpc",
        signer_key="0x" + "11" * 32,
    )
    result = adapter.redeem(
        KARACHI_CONDITION_ID,
        index_sets=[2],
        neg_risk=False,
        amount_per_slot=None,
    )
    # Must NOT be the negRisk-missing errorCode — that would mean the guard
    # incorrectly fired on the standard CTF path.
    assert result["errorCode"] != "REDEEM_NEGRISK_AMOUNT_MISSING", result
