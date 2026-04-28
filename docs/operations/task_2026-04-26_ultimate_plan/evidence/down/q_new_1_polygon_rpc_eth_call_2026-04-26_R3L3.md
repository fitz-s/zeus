# Q-NEW-1 Re-Verification — Down R3L3

Created: 2026-04-26 (during Down R3L3 turn-1, fresh on-chain probe by proponent)
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)

## Method

Fresh on-chain `eth_call` dispatched in this run via direct curl POST to public Polygon RPC, no SDK intermediation.

```
RPC endpoint:  https://polygon.drpc.org
Contract:      0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB
Block tag:     latest
Method:        eth_call
```

## Block height at probe

```json
Request:  {"jsonrpc":"2.0","id":3,"method":"eth_blockNumber","params":[]}
Response: {"id":3,"jsonrpc":"2.0","result":"0x5211a7d"}
Decimal:  86055549 (verified via Python int("5211a7d", 16))
```

## symbol() probe

ABI selector: `0x95d89b41` (keccak256("symbol()")[:4])

```json
Request:  {"jsonrpc":"2.0","id":1,"method":"eth_call","params":[{"to":"0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB","data":"0x95d89b41"},"latest"]}
Response: {"id":1,"jsonrpc":"2.0","result":"0x000000000000000000000000000000000000000000000000000000000000002000000000000000000000000000000000000000000000000000000000000000047055534400000000000000000000000000000000000000000000000000000000"}
```

ABI decode (string offset + length + bytes):
- offset: `0x20` (32, points to length field)
- length: `0x04` (4 bytes)
- data: `0x70555344` = ASCII "pUSD" (`p`=0x70, `U`=0x55, `S`=0x53, `D`=0x44)

**Decoded symbol(): "pUSD"**

## name() probe

ABI selector: `0x06fdde03` (keccak256("name()")[:4])

```json
Request:  {"jsonrpc":"2.0","id":2,"method":"eth_call","params":[{"to":"0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB","data":"0x06fdde03"},"latest"]}
Response: {"id":2,"jsonrpc":"2.0","result":"0x0000000000000000000000000000000000000000000000000000000000000020000000000000000000000000000000000000000000000000000000000000000e506f6c796d61726b657420555344000000000000000000000000000000000000"}
```

ABI decode:
- offset: `0x20` (32)
- length: `0x0e` (14 bytes)
- data: `0x506f6c796d61726b657420555344` = ASCII "Polymarket USD" (14 chars)

**Decoded name(): "Polymarket USD"**

## Conclusion

Q-NEW-1 STABLE. pUSD is a distinct ERC-20 (`symbol()="pUSD"`, `name()="Polymarket USD"`) at block height 86055549 on Polygon mainnet. No proxy-upgrade migration since the original 2026-04-26 11:12 probe by opponent-down. Earlier "marketing label for USDC" judge ruling (overturned in Down R1L1) remains overturned. Phase 2.C / Q5 / F1 pUSD redemption work is REAL.

## Reproduce

```bash
curl -sS -X POST https://polygon.drpc.org \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_call","params":[{"to":"0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB","data":"0x95d89b41"},"latest"]}'
# Expected: result containing 0x70555344 ("pUSD")
```

## Authority

Memory `feedback_on_chain_eth_call_for_token_identity` honored: on-chain bytes are the canonical authority for ERC-20 identity, ranking above docs / SDK source / inference.
