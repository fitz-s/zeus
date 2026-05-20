#!/usr/bin/env python
# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PR #219 V2 wrap path correction; reference tx 0x62da84b7b9287680d4af727caaed732e4d6875341893626587dc3e20471dff3a (block 87167823, 1.587297 USDC.e → pUSD via CollateralOnramp)
"""USDC.e → pUSD via CollateralOnramp v2 (post-2026-04-28 architecture).

CORRECT path discovered:
  CollateralOnramp = 0x93070a847efEf7F70739046A929D47a521F5B8ee
    wrap(_asset, _to, _amount)   selector 0x62355638
  Owner = 0x47ebfac3353314c788b96cdcbf41daadfe03629c (same as pUSD owner)
  USDCE NOT paused. eth_call simulation from Safe succeeded.

Differs from Zeus's existing src/venue/polymarket_v2_adapter.py:_build_wrap_calldata
which uses:
  - selector 0xbf376c7a (wrap(address,uint256)) — wrong signature
  - target POLYGON_PUSD_WRAPPER_ADDRESS = 0x3A3BD7bb... (WCOL, V1 artifact, owner-locked)

This script:
  1. APPROVE USDCE → Onramp for amount
  2. WAIT confirm
  3. WRAP via Onramp.wrap(USDCE, Safe, amount)
  4. WAIT confirm
  5. VERIFY Safe pUSD balance += amount

Reuses Zeus Safe execTransaction infrastructure (src/venue/safe_exec.py).
"""
import os, sys, time, requests, json
sys.path.insert(0, "/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/auto-wrap-rebase")
os.environ.setdefault("ZEUS_MODE", "live")

from eth_utils import keccak
from eth_account import Account
from src.data.polymarket_client import resolve_polymarket_credentials
from src.venue.safe_exec import (
    build_safe_tx_hash, sign_safe_tx, build_exec_transaction_calldata,
)

RPC = os.environ.get("POLYGON_RPC_URL", "https://polygon.drpc.org")
USDCE = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
PUSD = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
CHAIN_ID = 137
AMOUNT = 1_587_297  # exact USDC.e on Safe; matches Karachi payout


def post(payload):
    r = requests.post(RPC, json=payload, timeout=30)
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"RPC error: {j['error']}")
    return j["result"]


def erc20_balance(token, holder):
    sel = "0x70a08231"
    arg = holder[2:].lower().rjust(64, "0")
    res = post({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                "params": [{"to": token, "data": sel + arg}, "latest"]})
    return int(res, 16) if res and res != "0x" else 0


def erc20_allowance(token, owner, spender):
    sel = "0xdd62ed3e"
    o = owner[2:].lower().rjust(64, "0")
    s = spender[2:].lower().rjust(64, "0")
    res = post({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                "params": [{"to": token, "data": sel + o + s}, "latest"]})
    return int(res, 16) if res and res != "0x" else 0


def safe_nonce(safe):
    # Safe.nonce() selector 0xaffed0e0
    res = post({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                "params": [{"to": safe, "data": "0xaffed0e0"}, "latest"]})
    return int(res, 16)


def get_receipt(tx_hash, timeout_s=180):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        rc = post({"jsonrpc": "2.0", "id": 1,
                   "method": "eth_getTransactionReceipt", "params": [tx_hash]})
        if rc is not None:
            return rc
        time.sleep(5)
    return None


def send_safe_tx(safe, signer_key, inner_to, inner_data_hex):
    """Build + sign + broadcast Safe execTransaction. Return tx_hash."""
    signer_eoa = Account.from_key(signer_key).address
    nonce = safe_nonce(safe)
    inner_data = bytes.fromhex(inner_data_hex.removeprefix("0x"))
    tx_hash_bytes = build_safe_tx_hash(
        safe_address=safe, chain_id=CHAIN_ID,
        to=inner_to, value=0, data=inner_data,
        operation=0, nonce=nonce,
    )
    sig = sign_safe_tx(tx_hash_bytes, signer_key)
    exec_data = build_exec_transaction_calldata(
        to=inner_to, value=0, data=inner_data, operation=0, signatures=sig,
    )
    # nonce + gas
    eoa_nonce = int(post({"jsonrpc": "2.0", "id": 1,
                          "method": "eth_getTransactionCount",
                          "params": [signer_eoa, "pending"]}), 16)
    gas_price = int(post({"jsonrpc": "2.0", "id": 1,
                          "method": "eth_gasPrice", "params": []}), 16)
    gas_est = int(post({"jsonrpc": "2.0", "id": 1, "method": "eth_estimateGas",
                        "params": [{"from": signer_eoa, "to": safe, "data": exec_data}]}), 16)
    gas_limit = (gas_est * 12) // 10
    print(f"  estimated gas: {gas_est} → limit {gas_limit}")
    # build + sign EOA tx
    from eth_account import Account as Acc
    raw = Acc.sign_transaction({
        "chainId": CHAIN_ID, "nonce": eoa_nonce, "to": safe,
        "value": 0, "gas": gas_limit, "gasPrice": gas_price,
        "data": exec_data,
    }, signer_key)
    raw_hex = raw.raw_transaction.hex()
    if not raw_hex.startswith("0x"):
        raw_hex = "0x" + raw_hex
    txh = post({"jsonrpc": "2.0", "id": 1, "method": "eth_sendRawTransaction",
                "params": [raw_hex]})
    return txh


def main():
    creds = resolve_polymarket_credentials()
    safe = creds["funder_address"]
    pk = creds["private_key"]
    signer_eoa = Account.from_key(pk).address

    print(f"Safe       : {safe}")
    print(f"EOA        : {signer_eoa}")
    print(f"Onramp     : {ONRAMP}")
    print(f"Amount     : {AMOUNT} micro USDC.e ({AMOUNT/1e6})")

    usdce_bal = erc20_balance(USDCE, safe)
    pusd_pre = erc20_balance(PUSD, safe)
    print(f"\nPRE: USDC.e={usdce_bal} pUSD={pusd_pre}")
    if usdce_bal < AMOUNT:
        print(f"FAIL: Safe USDC.e {usdce_bal} < {AMOUNT}")
        return 2

    # === Step 1: APPROVE USDCE → Onramp ===
    allow_pre = erc20_allowance(USDCE, safe, ONRAMP)
    print(f"\n=== Step 1/3: APPROVE USDCE → Onramp (current allowance={allow_pre}) ===")
    if allow_pre < AMOUNT:
        approve_sel = "0x095ea7b3"
        approve_data = approve_sel + ONRAMP[2:].lower().rjust(64, "0") + hex(AMOUNT)[2:].rjust(64, "0")
        print(f"  inner calldata: {approve_data}")
        tx1 = send_safe_tx(safe, pk, USDCE, approve_data)
        print(f"  broadcast tx: {tx1}")
        rc = get_receipt(tx1, 180)
        if not rc or int(rc.get("status", "0x0"), 16) != 1:
            print(f"  FAIL: receipt={rc}")
            return 3
        print(f"  APPROVE confirmed block={int(rc['blockNumber'],16)}")
        allow_post = erc20_allowance(USDCE, safe, ONRAMP)
        print(f"  allowance now: {allow_post}")
    else:
        print(f"  ALREADY APPROVED — skip")

    # === Step 2: WRAP via Onramp ===
    print(f"\n=== Step 2/3: Onramp.wrap(USDCE, Safe, {AMOUNT}) ===")
    wrap_sel = "62355638"  # wrap(address,address,uint256)
    wrap_data = ("0x" + wrap_sel
                 + USDCE[2:].lower().rjust(64, "0")
                 + safe[2:].lower().rjust(64, "0")
                 + hex(AMOUNT)[2:].rjust(64, "0"))
    print(f"  inner calldata: {wrap_data}")
    tx2 = send_safe_tx(safe, pk, ONRAMP, wrap_data)
    print(f"  broadcast tx: {tx2}")
    rc = get_receipt(tx2, 180)
    if not rc or int(rc.get("status", "0x0"), 16) != 1:
        print(f"  FAIL: receipt={rc}")
        return 4
    print(f"  WRAP confirmed block={int(rc['blockNumber'],16)} logs={len(rc.get('logs',[]))}")

    # === Step 3: Verify ===
    print(f"\n=== Step 3/3: VERIFY pUSD balance delta ===")
    pusd_post = erc20_balance(PUSD, safe)
    usdce_post = erc20_balance(USDCE, safe)
    delta_pusd = pusd_post - pusd_pre
    delta_usdce = usdce_pre = usdce_bal - usdce_post
    print(f"  POST: USDC.e={usdce_post} pUSD={pusd_post}")
    print(f"  DELTA: USDC.e={-delta_usdce:+d}, pUSD={delta_pusd:+d}")

    if delta_pusd >= AMOUNT * 99 // 100:
        print(f"\nSUCCESS: deposit IS NOW CASH. +{delta_pusd} pUSD micro ({delta_pusd/1e6} pUSD)")
        return 0
    print(f"\nFAIL: pUSD delta {delta_pusd} below expected {AMOUNT}")
    return 5


if __name__ == "__main__":
    sys.exit(main())
