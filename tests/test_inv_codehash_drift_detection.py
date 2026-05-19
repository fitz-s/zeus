# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=2026-05-19|never
# Purpose: Runtime antibody — detect silent contract upgrade of internal_resolver_post_2026_02_21
#          by re-verifying on-chain codehash against EraAuthorityBasis literal.
# Reuse: Run on every CI pytest sweep; fails red if deployed codehash diverges from
#        ERA_BASIS_INTERNAL_RESOLVER.on_chain_codehash in src/state/settlement_writers.py
"""Codehash drift detection antibody — INV-ERA-2.

Calls eth_getCode against the internal resolver contract at
0x69c47De9D4D3Dad79590d61b9e05918E03775f24 (Polygon mainnet), hashes
the returned bytecode via keccak256, and asserts the result matches the
compile-time literal recorded in ERA_BASIS_INTERNAL_RESOLVER.on_chain_codehash.

Two assertions per the design contract:
  C1: eth_chainId == 0x89 (Polygon mainnet) — wrong RPC would invalidate C2.
  C2: keccak256(eth_getCode(address, 'latest')) == ERA_BASIS_INTERNAL_RESOLVER.on_chain_codehash

Sed-flip verification contract:
  Temporarily mutate ERA_BASIS_INTERNAL_RESOLVER.on_chain_codehash in
  src/state/settlement_writers.py to any wrong value — C2 goes RED with a
  message naming both the deployed codehash and the literal.  Revert to restore
  GREEN.  The test MUST fail against the altered literal, not the test file.

Marker: @pytest.mark.requires_rpc — requires outbound HTTPS to Polygon RPC.
  CI default: included (requires_rpc is NOT in pytest.ini addopts deselect filter).
  Offline/sandbox: pytest -m "not requires_rpc" to skip.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error

import pytest

# Source the literal from the production module so sed-flip propagates.
# Do NOT hardcode the codehash string here — that defeats the antibody.
from src.state.settlement_writers import ERA_BASIS_INTERNAL_RESOLVER

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Primary Polygon mainnet JSON-RPC endpoint.  publicnode.com returns 403 in
# some sandbox environments; quiknode.pro public endpoint is the proven fallback.
_RPC_ENDPOINTS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://rpc-mainnet.matic.quiknode.pro",
]

_POLYGON_CHAIN_ID = "0x89"

_RESOLVER_ADDRESS = ERA_BASIS_INTERNAL_RESOLVER.on_chain_address


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_rpc(rpc_url: str, method: str, params: list) -> object:
    """Minimal read-only JSON-RPC call via stdlib urllib (no chain writes)."""
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        separators=(",", ":"),
    ).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={
            "content-type": "application/json",
            "user-agent": "zeus-codehash-antibody/1.0 (read-only)",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        decoded = json.loads(resp.read())
    if "error" in decoded:
        raise RuntimeError(f"JSON-RPC error from {rpc_url}: {decoded['error']}")
    return decoded.get("result")


def _try_rpc_call(method: str, params: list) -> tuple[str, object]:
    """Try each RPC endpoint in order; return (winning_url, result) or raise."""
    last_exc: Exception | None = None
    for url in _RPC_ENDPOINTS:
        try:
            result = _json_rpc(url, method, params)
            return url, result
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            last_exc = exc
            continue
    raise RuntimeError(
        f"All Polygon RPC endpoints unreachable: {_RPC_ENDPOINTS!r}. "
        f"Last error: {last_exc}"
    ) from last_exc


def _keccak256_hex(data: bytes) -> str:
    """keccak256 hash → '0x'-prefixed hex string."""
    try:
        from eth_hash.auto import keccak  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "eth_hash is not installed; cannot compute keccak256 for codehash antibody. "
            "Install with: pip install eth-hash[pycryptodome]"
        ) from exc
    return "0x" + keccak(data).hex()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.requires_rpc
def test_codehash_drift_detection_chain_id() -> None:
    """C1: RPC endpoint is Polygon mainnet (chain_id == 0x89).

    A wrong RPC would make C2 meaningless, so we assert chain identity first.
    """
    rpc_url, chain_id = _try_rpc_call("eth_chainId", [])
    assert chain_id == _POLYGON_CHAIN_ID, (
        f"Unexpected chain_id from {rpc_url}: got {chain_id!r}, expected {_POLYGON_CHAIN_ID!r}. "
        "This means the RPC endpoint is not Polygon mainnet — codehash comparison is invalid."
    )


@pytest.mark.requires_rpc
def test_codehash_drift_detection_bytecode_matches_literal() -> None:
    """C2: keccak256(deployed bytecode) matches ERA_BASIS_INTERNAL_RESOLVER.on_chain_codehash.

    Fails loudly with both codehashes if the contract was upgraded (proxy swap),
    so an operator can immediately distinguish upgrade from test-environment issues.

    Sed-flip contract: mutate ERA_BASIS_INTERNAL_RESOLVER.on_chain_codehash in
    src/state/settlement_writers.py → this test goes RED naming both hashes.
    """
    # Confirm mainnet first to make the comparison meaningful.
    rpc_url, chain_id = _try_rpc_call("eth_chainId", [])
    assert chain_id == _POLYGON_CHAIN_ID, (
        f"RPC endpoint {rpc_url!r} is not Polygon mainnet (chain_id={chain_id!r}); "
        "skipping codehash comparison — result would be meaningless."
    )

    _url, bytecode_hex = _try_rpc_call("eth_getCode", [_RESOLVER_ADDRESS, "latest"])

    assert isinstance(bytecode_hex, str) and bytecode_hex.startswith("0x"), (
        f"eth_getCode returned unexpected value: {bytecode_hex!r}. "
        "Expected a '0x'-prefixed hex string."
    )
    assert len(bytecode_hex) > 2, (
        f"eth_getCode returned empty bytecode ('0x') for {_RESOLVER_ADDRESS!r}. "
        "The address has no deployed contract on Polygon mainnet — "
        "check if the address constant in ERA_BASIS_INTERNAL_RESOLVER is correct."
    )

    bytecode_bytes = bytes.fromhex(bytecode_hex.removeprefix("0x"))
    deployed_codehash = _keccak256_hex(bytecode_bytes)
    literal_codehash = ERA_BASIS_INTERNAL_RESOLVER.on_chain_codehash

    assert deployed_codehash == literal_codehash, (
        f"CODEHASH DRIFT DETECTED — the internal resolver contract may have been upgraded.\n"
        f"  Contract address : {_RESOLVER_ADDRESS}\n"
        f"  Deployed codehash: {deployed_codehash}\n"
        f"  Literal codehash : {literal_codehash}\n"
        "If the contract was upgraded, update ERA_BASIS_INTERNAL_RESOLVER.on_chain_codehash "
        "in src/state/settlement_writers.py AND re-verify era provenance semantics before "
        "writing any new settlement rows."
    )
