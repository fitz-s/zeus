# Created: 2026-07-02
# Last reused or audited: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 row "CTF convert/split/merge" (W2.4 packet).
"""Tests for PolymarketV2Adapter.split_positions/merge_positions/convert_positions
(W2.4 — inert this packet, no production caller).

Mocks the SDK/chain boundary ONLY (the adapter's ``rpc_call`` constructor
kwarg, the same urllib JSON-RPC seam every existing preflight/broadcast test
in this file family mocks) — never the new split_positions/merge_positions/
convert_positions/_broadcast_ctf_operation_via_safe methods themselves, so
the real preflight -> calldata -> sign -> broadcast pipeline runs end to end.
"""

from __future__ import annotations

import re

import pytest
from eth_abi import decode as abi_decode
from eth_account import Account

from src.venue.polymarket_v2_adapter import (
    AUTONOMOUS_CTF_CONVERSION_DRY_RUN_ENV,
    AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV,
    CTF_MERGE_POSITIONS_SELECTOR,
    CTF_SPLIT_POSITION_SELECTOR,
    NEGRISK_CONVERT_POSITIONS_SELECTOR,
    NEGRISK_MERGE_POSITIONS_SELECTOR,
    NEGRISK_SPLIT_POSITION_SELECTOR,
    POLYGON_CTF_ADDRESS,
    POLYGON_NEGRISK_ADAPTER_ADDRESS,
    PolymarketV2Adapter,
)

CONDITION_ID = "ab" * 32
MARKET_ID = "cd" * 32
AMOUNT_MICRO = 1_500_000
SIGNER_KEY = "0x" + "22" * 32
SAFE_ADDRESS = "0x0000000000000000000000000000000000000001"
SIGNER_EOA = Account.from_key(SIGNER_KEY).address
FAKE_TX_HASH = "0x" + "ab" * 32


class FakeRpc:
    """Records every call and returns a canned, well-formed response per
    JSON-RPC method — the exact preflight/broadcast sequence
    _broadcast_ctf_operation_via_safe issues (same shape as _wrap_via_safe's)."""

    def __init__(self, *, send_raw_tx_response: str | Exception = FAKE_TX_HASH) -> None:
        self.calls: list[tuple[str, str, list]] = []
        self.send_raw_tx_response = send_raw_tx_response

    def __call__(self, rpc_url: str, method: str, params: list):
        self.calls.append((rpc_url, method, params))
        if method == "eth_call":
            data = params[0]["data"]
            if data == "0xffa1ad74":  # Safe VERSION()
                import eth_abi as _eth_abi
                encoded = _eth_abi.encode(["string"], ["1.3.0"])
                return "0x" + encoded.hex()
            if data == "0xa0e67e2b":  # Safe getOwners()
                import eth_abi as _eth_abi
                encoded = _eth_abi.encode(["address[]"], [[SIGNER_EOA]])
                return "0x" + encoded.hex()
            if data == "0xaffed0e0":  # Safe nonce()
                return "0x5"
            raise AssertionError(f"unexpected eth_call data={data!r}")
        if method == "eth_getBalance":
            return hex(10**18)  # 1 MATIC, well above the 0.05 floor
        if method == "eth_getTransactionCount":
            return "0x1"
        if method == "eth_gasPrice":
            return "0x3b9aca00"
        if method == "eth_estimateGas":
            return "0x186a0"
        if method == "eth_sendRawTransaction":
            if isinstance(self.send_raw_tx_response, Exception):
                raise self.send_raw_tx_response
            return self.send_raw_tx_response
        raise AssertionError(f"unexpected RPC method={method!r}")

    def calldata_sent_to_estimate_gas(self) -> str:
        for _, method, params in self.calls:
            if method == "eth_estimateGas":
                return params[0]["data"]
        raise AssertionError("eth_estimateGas was never called")


def _adapter(rpc: FakeRpc) -> PolymarketV2Adapter:
    return PolymarketV2Adapter(
        funder_address=SAFE_ADDRESS,
        signature_type=2,
        chain_id=137,
        polygon_rpc_url="https://example.invalid/rpc",
        signer_key=SIGNER_KEY,
        rpc_call=rpc,
    )


def _decode_inner_call(exec_calldata_hex: str) -> tuple[str, bytes]:
    """Decode Safe.execTransaction(...) calldata back to (to, inner_data)."""
    raw = bytes.fromhex(exec_calldata_hex.removeprefix("0x"))
    args = abi_decode(
        ["address", "uint256", "bytes", "uint8", "uint256", "uint256", "uint256",
         "address", "address", "bytes"],
        raw[4:],
    )
    to, _value, data = args[0], args[1], args[2]
    return to, data


# ── Kill switch (default OFF) ────────────────────────────────────────────────

def test_split_positions_disabled_by_default_no_rpc_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, raising=False)
    rpc = FakeRpc()
    adapter = _adapter(rpc)
    result = adapter.split_positions(
        CONDITION_ID, AMOUNT_MICRO, safe_address=SAFE_ADDRESS, signer_eoa=SIGNER_EOA,
    )
    assert result["success"] is False
    assert result["errorCode"] == "CTF_CONVERSION_DISABLED"
    assert rpc.calls == []  # no chain I/O at all


def test_merge_positions_disabled_by_default_no_rpc_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, raising=False)
    rpc = FakeRpc()
    adapter = _adapter(rpc)
    result = adapter.merge_positions(
        CONDITION_ID, AMOUNT_MICRO, safe_address=SAFE_ADDRESS, signer_eoa=SIGNER_EOA,
    )
    assert result["success"] is False
    assert result["errorCode"] == "CTF_CONVERSION_DISABLED"
    assert rpc.calls == []


def test_convert_positions_disabled_by_default_no_rpc_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, raising=False)
    rpc = FakeRpc()
    adapter = _adapter(rpc)
    result = adapter.convert_positions(
        MARKET_ID, 3, AMOUNT_MICRO, safe_address=SAFE_ADDRESS, signer_eoa=SIGNER_EOA,
    )
    assert result["success"] is False
    assert result["errorCode"] == "CTF_CONVERSION_DISABLED"
    assert rpc.calls == []


# ── Dry-run (enabled + dry-run) ──────────────────────────────────────────────

def test_split_positions_dry_run_builds_signs_no_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, "1")
    monkeypatch.setenv(AUTONOMOUS_CTF_CONVERSION_DRY_RUN_ENV, "1")
    rpc = FakeRpc()
    adapter = _adapter(rpc)
    result = adapter.split_positions(
        CONDITION_ID, AMOUNT_MICRO, safe_address=SAFE_ADDRESS, signer_eoa=SIGNER_EOA,
    )
    assert result["success"] is False
    assert result["errorCode"] == "CTF_CONVERSION_DRY_RUN_LOGGED"
    assert re.fullmatch(r"[0-9a-f]{16}", result["dry_run_fingerprint"])
    # No key in the response leaks a raw signed transaction (antibody parity
    # with test_polymarket_v2_adapter_dry_run_no_raw_tx_leak.py's contract).
    assert "raw_tx_hex" not in result
    assert not any(
        isinstance(v, str) and re.fullmatch(r"0x[0-9a-fA-F]{130,}", v)
        for v in result.values()
    )
    # Build+sign happened (estimateGas was reached) but broadcast was not.
    methods_called = [m for _, m, _ in rpc.calls]
    assert "eth_estimateGas" in methods_called
    assert "eth_sendRawTransaction" not in methods_called


# ── Live broadcast + routing (enabled, dry-run off) ──────────────────────────

def test_split_positions_standard_ctf_routes_to_ctf_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, "1")
    monkeypatch.delenv(AUTONOMOUS_CTF_CONVERSION_DRY_RUN_ENV, raising=False)
    rpc = FakeRpc()
    adapter = _adapter(rpc)
    result = adapter.split_positions(
        CONDITION_ID, AMOUNT_MICRO, safe_address=SAFE_ADDRESS, signer_eoa=SIGNER_EOA,
        neg_risk=False,
    )
    assert result["success"] is True
    assert result["tx_hash"] == FAKE_TX_HASH

    to, inner_data = _decode_inner_call(rpc.calldata_sent_to_estimate_gas())
    assert to.lower() == POLYGON_CTF_ADDRESS.lower()
    assert "0x" + inner_data[:4].hex() == CTF_SPLIT_POSITION_SELECTOR


def test_split_positions_neg_risk_routes_to_negrisk_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, "1")
    monkeypatch.delenv(AUTONOMOUS_CTF_CONVERSION_DRY_RUN_ENV, raising=False)
    rpc = FakeRpc()
    adapter = _adapter(rpc)
    result = adapter.split_positions(
        CONDITION_ID, AMOUNT_MICRO, safe_address=SAFE_ADDRESS, signer_eoa=SIGNER_EOA,
        neg_risk=True,
    )
    assert result["success"] is True

    to, inner_data = _decode_inner_call(rpc.calldata_sent_to_estimate_gas())
    assert to.lower() == POLYGON_NEGRISK_ADAPTER_ADDRESS.lower()
    assert "0x" + inner_data[:4].hex() == NEGRISK_SPLIT_POSITION_SELECTOR


def test_merge_positions_standard_ctf_routes_to_ctf_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, "1")
    monkeypatch.delenv(AUTONOMOUS_CTF_CONVERSION_DRY_RUN_ENV, raising=False)
    rpc = FakeRpc()
    adapter = _adapter(rpc)
    result = adapter.merge_positions(
        CONDITION_ID, AMOUNT_MICRO, safe_address=SAFE_ADDRESS, signer_eoa=SIGNER_EOA,
        neg_risk=False,
    )
    assert result["success"] is True
    to, inner_data = _decode_inner_call(rpc.calldata_sent_to_estimate_gas())
    assert to.lower() == POLYGON_CTF_ADDRESS.lower()
    assert "0x" + inner_data[:4].hex() == CTF_MERGE_POSITIONS_SELECTOR


def test_merge_positions_neg_risk_routes_to_negrisk_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, "1")
    monkeypatch.delenv(AUTONOMOUS_CTF_CONVERSION_DRY_RUN_ENV, raising=False)
    rpc = FakeRpc()
    adapter = _adapter(rpc)
    result = adapter.merge_positions(
        CONDITION_ID, AMOUNT_MICRO, safe_address=SAFE_ADDRESS, signer_eoa=SIGNER_EOA,
        neg_risk=True,
    )
    assert result["success"] is True
    to, inner_data = _decode_inner_call(rpc.calldata_sent_to_estimate_gas())
    assert to.lower() == POLYGON_NEGRISK_ADAPTER_ADDRESS.lower()
    assert "0x" + inner_data[:4].hex() == NEGRISK_MERGE_POSITIONS_SELECTOR


def test_convert_positions_routes_to_negrisk_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, "1")
    monkeypatch.delenv(AUTONOMOUS_CTF_CONVERSION_DRY_RUN_ENV, raising=False)
    rpc = FakeRpc()
    adapter = _adapter(rpc)
    result = adapter.convert_positions(
        MARKET_ID, 5, AMOUNT_MICRO, safe_address=SAFE_ADDRESS, signer_eoa=SIGNER_EOA,
    )
    assert result["success"] is True
    to, inner_data = _decode_inner_call(rpc.calldata_sent_to_estimate_gas())
    assert to.lower() == POLYGON_NEGRISK_ADAPTER_ADDRESS.lower()
    assert "0x" + inner_data[:4].hex() == NEGRISK_CONVERT_POSITIONS_SELECTOR
    decoded = abi_decode(["bytes32", "uint256", "uint256"], inner_data[4:])
    assert decoded[0] == bytes.fromhex(MARKET_ID)
    assert decoded[1] == 5
    assert decoded[2] == AMOUNT_MICRO


# ── Preflight failures reject before broadcast ───────────────────────────────

def test_split_positions_safe_version_mismatch_rejects_before_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, "1")

    class BadVersionRpc(FakeRpc):
        def __call__(self, rpc_url, method, params):
            if method == "eth_call" and params[0]["data"] == "0xffa1ad74":
                import eth_abi as _eth_abi
                encoded = _eth_abi.encode(["string"], ["1.2.0"])
                self.calls.append((rpc_url, method, params))
                return "0x" + encoded.hex()
            return super().__call__(rpc_url, method, params)

    rpc = BadVersionRpc()
    adapter = _adapter(rpc)
    result = adapter.split_positions(
        CONDITION_ID, AMOUNT_MICRO, safe_address=SAFE_ADDRESS, signer_eoa=SIGNER_EOA,
    )
    assert result["success"] is False
    assert result["errorCode"] == "CTF_CONVERSION_SAFE_VERSION_UNSUPPORTED"
    assert all(m != "eth_sendRawTransaction" for _, m, _ in rpc.calls)


def test_merge_positions_owner_mismatch_rejects_before_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, "1")

    class OtherOwnerRpc(FakeRpc):
        def __call__(self, rpc_url, method, params):
            if method == "eth_call" and params[0]["data"] == "0xa0e67e2b":
                import eth_abi as _eth_abi
                from eth_utils import to_checksum_address as _checksum
                other_owner = _checksum("0x" + "00" * 19 + "ff")
                encoded = _eth_abi.encode(["address[]"], [[other_owner]])
                self.calls.append((rpc_url, method, params))
                return "0x" + encoded.hex()
            return super().__call__(rpc_url, method, params)

    rpc = OtherOwnerRpc()
    adapter = _adapter(rpc)
    result = adapter.merge_positions(
        CONDITION_ID, AMOUNT_MICRO, safe_address=SAFE_ADDRESS, signer_eoa=SIGNER_EOA,
    )
    assert result["success"] is False
    assert result["errorCode"] == "CTF_CONVERSION_SAFE_OWNER_MISMATCH"
    assert all(m != "eth_sendRawTransaction" for _, m, _ in rpc.calls)


def test_convert_positions_broadcast_failure_is_ambiguous_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """eth_sendRawTransaction raising is a genuinely AMBIGUOUS outcome (the tx
    may have still reached the mempool) — the errorCode must carry the
    _BROADCAST_FAILED suffix so callers (ctf_conversion_commands.execute_conversion)
    can map it to UNKNOWN rather than a clean FAILED reject."""
    monkeypatch.setenv(AUTONOMOUS_CTF_CONVERSION_ENABLED_ENV, "1")
    rpc = FakeRpc(send_raw_tx_response=RuntimeError("connection reset"))
    adapter = _adapter(rpc)
    result = adapter.convert_positions(
        MARKET_ID, 1, AMOUNT_MICRO, safe_address=SAFE_ADDRESS, signer_eoa=SIGNER_EOA,
    )
    assert result["success"] is False
    assert result["errorCode"] == "CTF_CONVERSION_BROADCAST_FAILED"
    assert result["errorCode"].endswith("_BROADCAST_FAILED")
