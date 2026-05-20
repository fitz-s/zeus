# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PR #219 V2 wrap path correction; live reference tx
#   0x62da84b7b9287680d4af727caaed732e4d6875341893626587dc3e20471dff3a
#   (block 87167823, Polygon mainnet, 1.587297 USDC.e → pUSD via CollateralOnramp)
"""Antibody tests: V2 CollateralOnramp wrap path constants and calldata encoding.

Validates that the post-2026-04-28 wrap path uses:
  - POLYGON_COLLATERAL_ONRAMP_ADDRESS = 0x93070a847efEf7F70739046A929D47a521F5B8ee
  - COLLATERAL_ONRAMP_WRAP_SELECTOR = 0x62355638
  - WRAP calldata encodes wrap(address _asset, address _to, uint256 _amount) with
      _asset = POLYGON_USDCE_ADDRESS, _to = safe, _amount = amount
  - APPROVE calldata spender = POLYGON_COLLATERAL_ONRAMP_ADDRESS (not deprecated WCOL)

Each test includes a comment indicating which literal would cause failure if sed-flipped.
"""
from __future__ import annotations

import pytest


SAFE_ADDR = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"
AMOUNT_MICRO = 1_587_297


# T1 ─────────────────────────────────────────────────────────────────────────
def test_collateral_onramp_address_constant():
    """T1: POLYGON_COLLATERAL_ONRAMP_ADDRESS must match live-verified Onramp address.

    Sed-flip: change the address literal in source → this test turns RED.
    """
    from src.venue.polymarket_v2_adapter import POLYGON_COLLATERAL_ONRAMP_ADDRESS

    # sed-flip target: "0x93070a847efEf7F70739046A929D47a521F5B8ee"
    assert POLYGON_COLLATERAL_ONRAMP_ADDRESS.lower() == "0x93070a847efef7f70739046a929d47a521f5b8ee", (
        f"POLYGON_COLLATERAL_ONRAMP_ADDRESS={POLYGON_COLLATERAL_ONRAMP_ADDRESS!r} "
        "does not match live-verified CollateralOnramp. "
        "Reference tx: 0x62da84b7b9287680d4af727caaed732e4d6875341893626587dc3e20471dff3a"
    )


# T2 ─────────────────────────────────────────────────────────────────────────
def test_collateral_onramp_wrap_selector_constant():
    """T2: COLLATERAL_ONRAMP_WRAP_SELECTOR must equal 0x62355638.

    Sed-flip: change the selector literal in source → this test turns RED.
    """
    from src.venue.polymarket_v2_adapter import COLLATERAL_ONRAMP_WRAP_SELECTOR

    # sed-flip target: "0x62355638"
    assert COLLATERAL_ONRAMP_WRAP_SELECTOR == "0x62355638", (
        f"COLLATERAL_ONRAMP_WRAP_SELECTOR={COLLATERAL_ONRAMP_WRAP_SELECTOR!r} "
        "does not match wrap(address,address,uint256) = 0x62355638. "
        "Reference tx: 0x62da84b7b9287680d4af727caaed732e4d6875341893626587dc3e20471dff3a"
    )


# T3 ─────────────────────────────────────────────────────────────────────────
def test_build_wrap_calldata_wrap_exact_encoding():
    """T3: WRAP calldata = selector + USDCE padded + safe padded + amount padded.

    Full exact-equality check against reference encoding.
    Verifies: selector 0x62355638, arg layout (_asset, _to, _amount), correct addresses.
    Sed-flip: change selector or arg order in _build_wrap_calldata → this test turns RED.
    """
    import eth_abi
    from src.venue.polymarket_v2_adapter import (
        POLYGON_USDCE_ADDRESS,
        _build_wrap_calldata,
    )

    calldata = _build_wrap_calldata("WRAP", SAFE_ADDR, AMOUNT_MICRO)

    # selector check
    # sed-flip target: "0x62355638"
    assert calldata.startswith("0x62355638"), (
        f"WRAP calldata selector={calldata[:10]!r} expected 0x62355638"
    )

    # full ABI decode: wrap(address _asset, address _to, uint256 _amount)
    raw = bytes.fromhex(calldata[10:])
    decoded = eth_abi.decode(["address", "address", "uint256"], raw)

    assert decoded[0].lower() == POLYGON_USDCE_ADDRESS.lower(), (
        f"_asset={decoded[0]!r} != POLYGON_USDCE_ADDRESS={POLYGON_USDCE_ADDRESS!r}"
    )
    assert decoded[1].lower() == SAFE_ADDR.lower(), (
        f"_to={decoded[1]!r} != safe_address={SAFE_ADDR!r}"
    )
    assert decoded[2] == AMOUNT_MICRO, (
        f"_amount={decoded[2]!r} != {AMOUNT_MICRO!r}"
    )

    # Exact full calldata comparison against reference encoding
    selector_bytes = bytes.fromhex("62355638")
    expected_args = eth_abi.encode(
        ["address", "address", "uint256"],
        [POLYGON_USDCE_ADDRESS, SAFE_ADDR, AMOUNT_MICRO],
    )
    expected = "0x" + (selector_bytes + expected_args).hex()
    assert calldata == expected, (
        f"WRAP calldata mismatch.\ngot:      {calldata}\nexpected: {expected}"
    )


# T4 ─────────────────────────────────────────────────────────────────────────
def test_build_wrap_calldata_approve_spender_is_onramp():
    """T4: APPROVE calldata spender arg must equal POLYGON_COLLATERAL_ONRAMP_ADDRESS.

    If someone reverts the spender back to the deprecated V1 WCOL address, this test fails.
    Sed-flip: change spender in _build_wrap_calldata APPROVE branch → this test turns RED.
    """
    import eth_abi
    from src.venue.polymarket_v2_adapter import (
        POLYGON_COLLATERAL_ONRAMP_ADDRESS,
        _build_wrap_calldata,
    )

    calldata = _build_wrap_calldata("APPROVE", SAFE_ADDR, AMOUNT_MICRO)

    assert calldata.startswith("0x095ea7b3"), (
        f"APPROVE selector={calldata[:10]!r} expected 0x095ea7b3"
    )

    raw = bytes.fromhex(calldata[10:])
    decoded = eth_abi.decode(["address", "uint256"], raw)

    # sed-flip target: POLYGON_COLLATERAL_ONRAMP_ADDRESS literal in source
    assert decoded[0].lower() == POLYGON_COLLATERAL_ONRAMP_ADDRESS.lower(), (
        f"APPROVE spender={decoded[0]!r} != POLYGON_COLLATERAL_ONRAMP_ADDRESS="
        f"{POLYGON_COLLATERAL_ONRAMP_ADDRESS!r}. "
        "Reverted to deprecated V1 WCOL? That path reverts with GS013."
    )
    assert decoded[1] == AMOUNT_MICRO, (
        f"APPROVE amount={decoded[1]!r} != {AMOUNT_MICRO!r}"
    )
