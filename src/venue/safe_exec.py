# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: /tmp/SAFE_REDEEM_AUTOMATION_DESIGN.md Option A (2026-05-18)
"""Pure-function Safe v1.3.0 execTransaction helpers.

All functions are stateless.  No class, no network I/O.

Constants are verified by keccak re-computation (see src/venue/safe_exec_verify.md
and test_polymarket_redeem_web3_wire.py::test_safe_tx_hash_pinned_against_reference).

Safe v1.3.0 EIP-712 struct:
  SafeTx(address to, uint256 value, bytes data, uint8 operation,
         uint256 safeTxGas, uint256 baseGas, uint256 gasPrice,
         address gasToken, address refundReceiver, uint256 nonce)

Domain separator:
  EIP712Domain(uint256 chainId, address verifyingContract)
"""
from __future__ import annotations

# ── Pinned constants ─────────────────────────────────────────────────────────
# keccak256('SafeTx(address to,uint256 value,bytes data,uint8 operation,
#            uint256 safeTxGas,uint256 baseGas,uint256 gasPrice,
#            address gasToken,address refundReceiver,uint256 nonce)')
# Verified: eth_utils.keccak(b'SafeTx(...)') == this value.
SAFE_TX_TYPEHASH: bytes = bytes.fromhex(
    "bb8310d486368db6bd6f849402fdd73ad53d316b5a4b2644ad6efe0f941286d8"
)

# keccak256('EIP712Domain(uint256 chainId,address verifyingContract)')
# Safe v1.3.0 omits 'name'/'version' from the domain — unlike older Safe
# versions that include them.  Pinned to prevent silent cross-version break.
DOMAIN_SEPARATOR_TYPEHASH: bytes = bytes.fromhex(
    "47e79534a245952e8b16893a336b85a3d9ea9fa8c573f3d803afb92a79469218"
)

# Safe v1.3.0 compatibility string
SAFE_V1_3_VERSION: str = "1.3.0"

# keccak256('execTransaction(address,uint256,bytes,uint8,uint256,uint256,
#            uint256,address,address,bytes)')[:4]
# Selector for Safe.execTransaction — pinned to catch ABI drift.
EXEC_TX_SELECTOR: bytes = bytes.fromhex("6a761202")


# ── Core helpers ─────────────────────────────────────────────────────────────

def build_safe_tx_hash(
    safe_address: str,
    chain_id: int,
    to: str,
    value: int,
    data: bytes,
    operation: int,
    nonce: int,
    *,
    safe_tx_gas: int = 0,
    base_gas: int = 0,
    gas_price_safe: int = 0,
    gas_token: str = "0x0000000000000000000000000000000000000000",
    refund_receiver: str = "0x0000000000000000000000000000000000000000",
) -> bytes:
    """Compute the Safe v1.3.0 EIP-712 transaction hash.

    For a 1-of-1 self-paid Safe, pass default zero values for safe_tx_gas,
    base_gas, gas_price_safe, gas_token, and refund_receiver.

    Returns the 32-byte hash that the Safe owner must sign.
    """
    import eth_abi
    from eth_utils import keccak

    # Inner SafeTx hash: keccak of ABI-encoded struct
    data_hash = keccak(data)
    safe_tx_encoded = eth_abi.encode(
        [
            "bytes32",   # SAFE_TX_TYPEHASH
            "address",   # to
            "uint256",   # value
            "bytes32",   # keccak(data)
            "uint8",     # operation
            "uint256",   # safeTxGas
            "uint256",   # baseGas
            "uint256",   # gasPrice (Safe-level, not tx-level)
            "address",   # gasToken
            "address",   # refundReceiver
            "uint256",   # nonce
        ],
        [
            SAFE_TX_TYPEHASH,
            to,
            value,
            data_hash,
            operation,
            safe_tx_gas,
            base_gas,
            gas_price_safe,
            gas_token,
            refund_receiver,
            nonce,
        ],
    )
    safe_tx_hash_inner = keccak(safe_tx_encoded)

    # Domain separator
    domain_encoded = eth_abi.encode(
        ["bytes32", "uint256", "address"],
        [DOMAIN_SEPARATOR_TYPEHASH, chain_id, safe_address],
    )
    domain_sep = keccak(domain_encoded)

    # Final EIP-712 hash: 0x1901 || domain_sep || safe_tx_hash_inner
    return keccak(b"\x19\x01" + domain_sep + safe_tx_hash_inner)


def sign_safe_tx(tx_hash_bytes: bytes, signer_key: str) -> bytes:
    """Sign a Safe transaction hash with a private key.

    Returns a 65-byte compact signature: r (32) || s (32) || v (1).
    v is Ethereum-style (27 or 28).  Safe v1.3.0 requires v >= 27 for
    standard EOA ECDSA signatures (signature_type 0 in Safe convention).
    """
    from eth_account import Account

    # Safe pre-signs the raw 32-byte EIP-712 hash directly (no additional
    # EIP-191 wrapping).  Account._sign_hash accepts the raw 32-byte digest.
    signed = Account._sign_hash(tx_hash_bytes, signer_key)
    r = signed.r.to_bytes(32, "big")
    s = signed.s.to_bytes(32, "big")
    v = bytes([signed.v])
    return r + s + v


def build_exec_transaction_calldata(
    to: str,
    value: int,
    data: bytes,
    operation: int,
    signatures: bytes,
    *,
    safe_tx_gas: int = 0,
    base_gas: int = 0,
    gas_price_safe: int = 0,
    gas_token: str = "0x0000000000000000000000000000000000000000",
    refund_receiver: str = "0x0000000000000000000000000000000000000000",
) -> str:
    """Build ABI-encoded calldata for Safe.execTransaction.

    Returns a 0x-prefixed hex string ready for inclusion in a raw tx.

    Arg order matches Safe v1.3.0 ABI:
      execTransaction(address to, uint256 value, bytes data, uint8 operation,
                      uint256 safeTxGas, uint256 baseGas, uint256 gasPrice,
                      address gasToken, address refundReceiver, bytes signatures)
    """
    import eth_abi

    encoded_args = eth_abi.encode(
        [
            "address",   # to
            "uint256",   # value
            "bytes",     # data
            "uint8",     # operation
            "uint256",   # safeTxGas
            "uint256",   # baseGas
            "uint256",   # gasPrice
            "address",   # gasToken
            "address",   # refundReceiver
            "bytes",     # signatures
        ],
        [
            to,
            value,
            data,
            operation,
            safe_tx_gas,
            base_gas,
            gas_price_safe,
            gas_token,
            refund_receiver,
            signatures,
        ],
    )
    return "0x" + EXEC_TX_SELECTOR.hex() + encoded_args.hex()
