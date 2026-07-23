# Created: 2026-05-19
# Last reused or audited: 2026-05-20
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-20; last_reused=2026-05-20
# Purpose: Antibody tests — signed raw_tx_hex must never appear in logs or return
#   payloads of the autonomous wrap broadcast path.
# Reuse: Run after any change to polymarket_v2_adapter._wrap_via_safe or
#   wrap_unwrap_commands. No daemon or on-chain state required.
# Authority basis: .omc/plans/2026-05-19-auto-wrap-post-redeem.md; operator brief session fda4e853
"""Autonomous wrap broadcasts exactly once without exposing signed bytes.

Root cause (parallel to codereview-may19 P0-2): a signed raw transaction is a
broadcastable payload. If logs, DB event payloads, stdout/stderr collectors,
alerting, or backups see it, they can broadcast it outside the command lifecycle.

Antibody contracts (sed-flip verifiable):
  W1: APPROVE and WRAP logs do not contain a long signed hex blob.
  W2: Return payloads do not contain a broadcastable raw transaction.
  W3: Each invocation sends exactly one transaction and returns its hash.

Sed-flip (antibody meta-verify):
  - Add `"raw_tx_hex": raw_hex` to the return dict → W2 turns RED.
  - Add `raw_hex` to logging → W1 turns RED.
  - Skip or duplicate ``eth_sendRawTransaction`` → W3 turns RED.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

WORKTREE_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent

_SIGNED_RAW_TX_PATTERN = re.compile(r"0x[0-9a-fA-F]{200,}")


def _assert_log_does_not_leak_raw_tx(caplog) -> None:
    leaks = []
    for record in caplog.records:
        msg = record.getMessage()
        m = _SIGNED_RAW_TX_PATTERN.search(msg)
        if m:
            leaks.append((record.name, record.levelname, m.group(0)[:32] + "...", msg[:120]))
    assert not leaks, (
        f"WRAP P0 antibody FAIL: signed raw_tx_hex leaked into logs at {len(leaks)} site(s). "
        f"First: logger={leaks[0][0]} level={leaks[0][1]} prefix={leaks[0][2]!r}"
    )


def _assert_payload_has_no_raw_tx(payload: dict) -> None:
    assert "raw_tx_hex" not in payload, (
        f"WRAP P0 antibody FAIL: return payload contains 'raw_tx_hex'. "
        f"Any observer can broadcast it. Payload keys: {sorted(payload.keys())}"
    )
    for v in payload.values():
        if isinstance(v, str) and _SIGNED_RAW_TX_PATTERN.search(v):
            pytest.fail(
                "WRAP P0 antibody FAIL: a payload value contains a long signed hex blob. "
                "Replace with SHA-256 fingerprint."
            )


def _make_adapter(funder_address: str, signer_key: str):
    """Build a PolymarketV2Adapter with mocked RPC for unit testing."""
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    adapter = PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address=funder_address,
        signer_key=signer_key,
        chain_id=137,
        signature_type=2,
        polygon_rpc_url="https://polygon-bor-rpc.publicnode.com",
        api_creds=None,
        q1_egress_evidence_path=None,
    )
    return adapter


def _patch_rpc_for_wrap(adapter, *, safe_nonce: int = 5, eoa_matic_wei: int = 10**18):
    """Patch _rpc_call to return canned values for Safe pre-flight + gas + broadcast."""
    import eth_abi

    safe_addr = adapter.funder_address
    signer_eoa = "0xB19Ce122089237025aD046a0eA61E66a5Fa4cc8b"
    sent_raw: list[str] = []

    def _fake_rpc_call(rpc_url, method, params):
        if method == "eth_call":
            data = params[0].get("data", "")
            if data.startswith("0xffa1ad74"):  # VERSION()
                # Return ABI-encoded string "1.3.0"
                return "0x" + eth_abi.encode(["string"], ["1.3.0"]).hex()
            if data.startswith("0xa0e67e2b"):  # getOwners()
                return "0x" + eth_abi.encode(["address[]"], [[signer_eoa]]).hex()
            if data.startswith("0xaffed0e0"):  # nonce()
                return hex(safe_nonce)
        if method == "eth_getBalance":
            return hex(eoa_matic_wei)
        if method == "eth_getTransactionCount":
            return "0x1"
        if method == "eth_gasPrice":
            return hex(30 * 10**9)
        if method == "eth_estimateGas":
            return hex(200_000)
        if method == "eth_sendRawTransaction":
            sent_raw.append(params[0])
            return "0x" + "a" * 64  # fake tx hash
        raise AssertionError(f"Unexpected RPC call: {method}")

    adapter._rpc_call = _fake_rpc_call
    return signer_eoa, sent_raw


@pytest.mark.parametrize("tx_kind", ["APPROVE", "WRAP"])
def test_wrap_broadcast_does_not_leak_and_sends_once(caplog, tx_kind):
    """The broadcast path hides signed bytes and sends exactly once."""
    # Use a real private key (test key only, no funds)
    _TEST_SIGNER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    _TEST_SAFE_ADDR = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"

    adapter = _make_adapter(_TEST_SAFE_ADDR, _TEST_SIGNER_KEY)
    signer_eoa, sent_raw = _patch_rpc_for_wrap(adapter)

    with caplog.at_level(logging.DEBUG, logger="src.venue.polymarket_v2_adapter"):
        result = adapter._wrap_via_safe(
            safe_address=_TEST_SAFE_ADDR,
            amount_micro=1_000_000,
            tx_kind=tx_kind,
            signer_eoa=signer_eoa,
        )

    _assert_log_does_not_leak_raw_tx(caplog)
    _assert_payload_has_no_raw_tx(result)
    assert result["success"] is True
    assert result["tx_hash"] == "0x" + "a" * 64
    assert len(sent_raw) == 1


def test_wrap_calldata_approve_first_arg_is_onramp():
    """Assert APPROVE calldata arg-0 encodes to POLYGON_COLLATERAL_ONRAMP_ADDRESS (V2).

    Updated 2026-05-20: V1 WCOL (POLYGON_PUSD_WRAPPER_ADDRESS) deprecated; spender
    is now CollateralOnramp. Reference tx for V1 failure: GS013 on every estimateGas.
    Reference tx for V2 success: 0x62da84b7b9287680d4af727caaed732e4d6875341893626587dc3e20471dff3a

    Sed-flip: change APPROVE selector or spender address → this test turns RED.
    """
    from src.venue.polymarket_v2_adapter import (
        POLYGON_COLLATERAL_ONRAMP_ADDRESS,
        _build_wrap_calldata,
    )
    import eth_abi

    calldata = _build_wrap_calldata("APPROVE", "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f", 1_000_000)
    assert calldata.startswith("0x095ea7b3"), f"APPROVE selector wrong: {calldata[:10]}"
    decoded = eth_abi.decode(["address", "uint256"], bytes.fromhex(calldata[10:]))
    assert decoded[0].lower() == POLYGON_COLLATERAL_ONRAMP_ADDRESS.lower(), (
        f"APPROVE calldata arg-0={decoded[0]} != POLYGON_COLLATERAL_ONRAMP_ADDRESS={POLYGON_COLLATERAL_ONRAMP_ADDRESS}"
    )
    assert decoded[1] == 1_000_000


def test_wrap_calldata_wrap_v2_encoding():
    """Assert WRAP calldata uses V2 CollateralOnramp encoding: wrap(asset, to, amount).

    Updated 2026-05-20: V1 selector 0xbf376c7a (wrap(address,uint256)) deprecated.
    V2 selector 0x62355638 (wrap(address _asset, address _to, uint256 _amount)).
    VERIFIED on-chain: tx 0x62da84b7b9287680d4af727caaed732e4d6875341893626587dc3e20471dff3a
    block 87167823. pUSD landed at safe_address.

    Sed-flip: change wrap selector or arg encoding order → this test turns RED.
    """
    from src.venue.polymarket_v2_adapter import (
        POLYGON_USDCE_ADDRESS,
        _build_wrap_calldata,
    )
    import eth_abi

    safe_addr = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"
    calldata = _build_wrap_calldata("WRAP", safe_addr, 1_500_000)
    assert calldata.startswith("0x62355638"), f"WRAP selector wrong: {calldata[:10]}"
    decoded = eth_abi.decode(["address", "address", "uint256"], bytes.fromhex(calldata[10:]))
    assert decoded[0].lower() == POLYGON_USDCE_ADDRESS.lower(), (
        f"WRAP calldata arg-0 (_asset)={decoded[0]} != POLYGON_USDCE_ADDRESS={POLYGON_USDCE_ADDRESS}"
    )
    assert decoded[1].lower() == safe_addr.lower(), (
        f"WRAP calldata arg-1 (_to)={decoded[1]} != safe_addr={safe_addr}"
    )
    assert decoded[2] == 1_500_000
