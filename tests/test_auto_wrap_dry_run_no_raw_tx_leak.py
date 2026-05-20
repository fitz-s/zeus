# Created: 2026-05-19
# Last reused or audited: 2026-05-20
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-20; last_reused=2026-05-20
# Purpose: Antibody tests — signed raw_tx_hex must never appear in logs or return
#   payloads of wrap dry-run paths (parallel to codereview-may19 P0-2 for redeem).
# Reuse: Run after any change to polymarket_v2_adapter._wrap_via_safe or
#   wrap_unwrap_commands dry-run paths. No daemon or on-chain state required.
# Authority basis: .omc/plans/2026-05-19-auto-wrap-post-redeem.md; operator brief session fda4e853
"""Antibody tests: signed raw_tx_hex must never appear in logs or return
payloads of wrap dry-run paths.

Root cause (parallel to codereview-may19 P0-2): a signed raw transaction is a
broadcastable payload. If logs, DB event payloads, stdout/stderr collectors,
alerting, or backups see it, they can broadcast it and bypass the no-side-effect
intent of dry-run.

Antibody contracts (sed-flip verifiable):
  W1: Wrap APPROVE dry-run log does NOT contain a long signed hex blob.
  W2: Wrap WRAP dry-run log does NOT contain a long signed hex blob.
  W3: Both dry-run return payloads do NOT have a 'raw_tx_hex' key.
  W4: Both paths emit a 'dry_run_fingerprint' (≤16 hex chars).

Sed-flip (antibody meta-verify):
  - Add `"raw_tx_hex": raw_hex` to _wrap_via_safe return dict in dry-run branch
    → W3 asserts False (RED).
  - Add `raw_hex` to the logger.warning format string in dry-run branch
    → W1/W2 asserts False (RED).
  - Remove `dry_run_fingerprint` from return dict
    → W4 asserts False (RED).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
        f"WRAP P0 antibody FAIL: dry-run return payload contains 'raw_tx_hex'. "
        f"Any observer can broadcast it. Payload keys: {sorted(payload.keys())}"
    )
    for v in payload.values():
        if isinstance(v, str) and _SIGNED_RAW_TX_PATTERN.search(v):
            pytest.fail(
                "WRAP P0 antibody FAIL: a payload value contains a long signed hex blob. "
                "Replace with SHA-256 fingerprint."
            )


def _assert_payload_has_fingerprint(payload: dict) -> None:
    assert "dry_run_fingerprint" in payload, (
        "WRAP P0 antibody FAIL: dry-run payload missing 'dry_run_fingerprint'. "
        "Operators need this to correlate logs with broadcastable bytes."
    )
    fp = payload["dry_run_fingerprint"]
    assert (
        isinstance(fp, str)
        and len(fp) <= 16
        and all(c in "0123456789abcdef" for c in fp.lower())
    ), f"dry_run_fingerprint={fp!r} is not a ≤16-hex-char SHA-256 prefix."


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
            return "0x" + "a" * 64  # fake tx hash
        raise AssertionError(f"Unexpected RPC call: {method}")

    adapter._rpc_call = _fake_rpc_call
    return signer_eoa


@pytest.mark.parametrize("tx_kind", ["APPROVE", "WRAP"])
def test_wrap_dry_run_does_not_leak_raw_tx_hex_in_logs(caplog, tx_kind):
    """W1+W2: Wrap dry-run logs must not contain the signed raw transaction hex.

    Sed-flip: add `raw_hex` to logger.warning() in the dry-run branch of
    _wrap_via_safe → this test turns RED.
    """
    import os
    # Use a real private key (test key only, no funds)
    _TEST_SIGNER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    _TEST_SAFE_ADDR = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"

    adapter = _make_adapter(_TEST_SAFE_ADDR, _TEST_SIGNER_KEY)
    signer_eoa = _patch_rpc_for_wrap(adapter)

    with patch.dict(os.environ, {"ZEUS_AUTONOMOUS_WRAP_DRY_RUN": "1"}):
        with caplog.at_level(logging.DEBUG, logger="src.venue.polymarket_v2_adapter"):
            result = adapter._wrap_via_safe(
                safe_address=_TEST_SAFE_ADDR,
                amount_micro=1_000_000,
                tx_kind=tx_kind,
                signer_eoa=signer_eoa,
            )

    _assert_log_does_not_leak_raw_tx(caplog)


@pytest.mark.parametrize("tx_kind", ["APPROVE", "WRAP"])
def test_wrap_dry_run_return_payload_has_no_raw_tx_hex(tx_kind):
    """W3: Dry-run return dict must not contain 'raw_tx_hex'.

    Sed-flip: add `"raw_tx_hex": raw_hex` to the dry-run return dict in
    _wrap_via_safe → this test turns RED.
    """
    import os
    _TEST_SIGNER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    _TEST_SAFE_ADDR = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"

    adapter = _make_adapter(_TEST_SAFE_ADDR, _TEST_SIGNER_KEY)
    signer_eoa = _patch_rpc_for_wrap(adapter)

    with patch.dict(os.environ, {"ZEUS_AUTONOMOUS_WRAP_DRY_RUN": "1"}):
        result = adapter._wrap_via_safe(
            safe_address=_TEST_SAFE_ADDR,
            amount_micro=1_000_000,
            tx_kind=tx_kind,
            signer_eoa=signer_eoa,
        )

    assert result["errorCode"] == "WRAP_DRY_RUN_LOGGED", (
        f"Expected WRAP_DRY_RUN_LOGGED errorCode, got {result.get('errorCode')!r}"
    )
    _assert_payload_has_no_raw_tx(result)


@pytest.mark.parametrize("tx_kind", ["APPROVE", "WRAP"])
def test_wrap_dry_run_payload_has_fingerprint(tx_kind):
    """W4: Dry-run return dict must contain 'dry_run_fingerprint' (≤16 hex chars).

    Sed-flip: remove `"dry_run_fingerprint": _dry_run_fingerprint` from the
    dry-run return dict → this test turns RED.
    """
    import os
    _TEST_SIGNER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    _TEST_SAFE_ADDR = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"

    adapter = _make_adapter(_TEST_SAFE_ADDR, _TEST_SIGNER_KEY)
    signer_eoa = _patch_rpc_for_wrap(adapter)

    with patch.dict(os.environ, {"ZEUS_AUTONOMOUS_WRAP_DRY_RUN": "1"}):
        result = adapter._wrap_via_safe(
            safe_address=_TEST_SAFE_ADDR,
            amount_micro=1_000_000,
            tx_kind=tx_kind,
            signer_eoa=signer_eoa,
        )

    _assert_payload_has_fingerprint(result)


def test_wrap_calldata_approve_first_arg_is_pusd_wrapper():
    """Assert APPROVE calldata arg-0 encodes to POLYGON_PUSD_WRAPPER_ADDRESS.

    Sed-flip: change APPROVE selector or spender address → this test turns RED.
    """
    from src.venue.polymarket_v2_adapter import (
        POLYGON_PUSD_WRAPPER_ADDRESS,
        _build_wrap_calldata,
    )
    import eth_abi
    from eth_utils import to_checksum_address

    calldata = _build_wrap_calldata("APPROVE", "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f", 1_000_000)
    assert calldata.startswith("0x095ea7b3"), f"APPROVE selector wrong: {calldata[:10]}"
    decoded = eth_abi.decode(["address", "uint256"], bytes.fromhex(calldata[10:]))
    assert decoded[0].lower() == POLYGON_PUSD_WRAPPER_ADDRESS.lower(), (
        f"APPROVE calldata arg-0={decoded[0]} != POLYGON_PUSD_WRAPPER_ADDRESS={POLYGON_PUSD_WRAPPER_ADDRESS}"
    )
    assert decoded[1] == 1_000_000


def test_wrap_calldata_wrap_first_arg_is_safe_address():
    """Assert WRAP calldata arg-0 encodes to safe_address (recipient).

    This is the 'UNVERIFIED' assumption: wrap(address to, uint256 amount).
    This test makes the assumption explicit and verifiable.

    Sed-flip: change wrap arg encoding order → this test turns RED.
    """
    from src.venue.polymarket_v2_adapter import _build_wrap_calldata
    import eth_abi

    safe_addr = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"
    calldata = _build_wrap_calldata("WRAP", safe_addr, 1_500_000)
    assert calldata.startswith("0xbf376c7a"), f"WRAP selector wrong: {calldata[:10]}"
    decoded = eth_abi.decode(["address", "uint256"], bytes.fromhex(calldata[10:]))
    assert decoded[0].lower() == safe_addr.lower(), (
        f"WRAP calldata arg-0={decoded[0]} != safe_addr={safe_addr}. "
        f"If first live tx shows pUSD did NOT land at safe_addr, update this test "
        f"AND the encoder in _build_wrap_calldata."
    )
    assert decoded[1] == 1_500_000
