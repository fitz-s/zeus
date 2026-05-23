# PR-I.5 Web3 Redeem Adapter Wire — Feasibility Report
Created: 2026-05-17 | Authority: zeus-deep-alignment-audit-skill operator brief

---

## §1 Current adapter shape

File: `src/venue/polymarket_v2_adapter.py`

**Stub at line 611–623:**
```python
def redeem(self, condition_id: str) -> dict[str, Any]:
    """Redeem winning shares when the SDK exposes a redeem method."""
    return {
        "success": False,
        "errorCode": "REDEEM_DEFERRED_TO_R1",
        "errorMessage": "R1 settlement command ledger must own pUSD redemption side effects",
        "condition_id": condition_id,
    }
```

**Inputs**: `condition_id: str` only. The adapter already holds `signer_key`, `funder_address`,
`chain_id=137`, `polygon_rpc_url`, and `_rpc_call` (a plain-urllib JSON-RPC caller).

**Expected return on real success**: `{"success": True, "tx_hash": "0x...", "block_number": int}`
(per `settlement_commands.py:443-445` extraction logic: `_extract_tx_hash`, `_extract_int`).

Protocol (`PolymarketV2AdapterProtocol` line 160): `def redeem(self, condition_id: str) -> dict[str, Any]`
— no additional parameters in the protocol contract.

---

## §2 Web3 infrastructure available

**web3 library**: NOT installed. `pip show web3` → "Package(s) not found". SCAFFOLD
§I.0 empirically verified this on 2026-05-16 and added the note to `requirements.txt`
comment. It is not a transitive dependency of `py_clob_client_v2`.

**eth_account 0.13.7**: installed (transitive from `py_clob_client_v2` → `py_order_utils`).
Can sign raw transactions: `Account.sign_transaction(tx_dict, private_key)` → `.raw_transaction`.

**eth_abi**: installed (transitive). Can ABI-encode CTF calldata:
`encode(['address','bytes32','bytes32','uint256[]'], [...])` — verified working in this
session without web3.

**eth_utils**: installed. `keccak(text=...)` for function selector derivation — verified.

**Existing JSON-RPC caller**: `_json_rpc_call` at `polymarket_v2_adapter.py:970` — a plain
`urllib.request` caller that already handles `eth_call` (used in `_chain_collateral_allowance_micro`).
It can also call `eth_sendRawTransaction` with a hex-encoded signed transaction.

**No existing sign/send code in src/ or scripts/**: zero hits for `eth_sendRawTransaction`,
`sendTransaction`, `signTransaction`.

**`web3` import probe in main.py (line 288)**: present — reconciler tries `from web3 import Web3`
and logs a WARN if missing, exits cleanly. This is the PR-I stub seam for reconciliation.
The adapter wire does NOT require the `web3` library if we use `eth_account` + `eth_abi` +
the existing `_json_rpc_call` directly.

---

## §3 Polymarket CTF redeemPositions ABI

Standard Gnosis Conditional Tokens Framework ABI:
```
redeemPositions(
    address collateralToken,   // USDC (POLYGON_PUSD_ADDRESS = 0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB)
    bytes32 parentCollectionId, // all zeros (top-level positions)
    bytes32 conditionId,        // from settlement_commands row
    uint256[] indexSets         // winning bin: [1] for YES=0x1, [2] for NO=0x2
)
```

**CTF contract address on Polygon**: standard Gnosis CTF at
`0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`. NOT found in zeus config files. Must be
hardcoded or resolved at deploy time.

**POLYGON_PUSD_ADDRESS** already in `polymarket_v2_adapter.py:55`:
`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`.

`redeemPositions` selector (keccak256 first 4 bytes): `0x01b7037c` — verified via
`eth_utils.keccak` in this session.

The `condition_id` is already in `settlement_commands` (e.g. `c5faddf4...` for Karachi).
The `indexSets` determination requires knowing which outcome won (`YES=1` or `NO=2`).
The `payout_asset` in the settlement row is `pUSD`; the winning direction is not currently
stored in `settlement_commands` — it would need to come from position data.

**Gap**: `settlement_commands` schema does not store `winning_index_set`. The row has
`token_amounts_json` (can carry this) but the enqueue caller (`harvester.py:2181-2191`)
would need to pass it. This is an additional schema/enqueue change not currently scoped.

---

## §4 Private key / signer access

`PolymarketV2Adapter.__init__` receives `signer_key: str` (line 193). In the live daemon,
`PolymarketV2Adapter()` is called at `src/main.py:226` with **no arguments**, which means
it relies on default values — but `signer_key` has no default and is required. Tracing
further: the adapter is instantiated only inside `_redeem_submitter_cycle` which is a
scheduler tick; the adapter it passes to `submit_redeem` gets its credentials from the
same keychain path as the CLOB order adapter used for entries.

The existing `bin/keychain_resolver.py` provides Keychain credential access. The live
adapter instance used for order submission (separate from the redeem submitter path) is
wired elsewhere in `src/main.py` — the redeem submitter at line 226 constructs
`PolymarketV2Adapter()` bare, which would fail in production unless the required args are
pulled from environment/config. This is an existing adapter construction gap (not a new
PR-I.5 concern) but must be resolved as part of the signer wire.

**Summary**: `signer_key` is already in the adapter's `self.signer_key`. Once the adapter
instance is properly constructed (credentials wired), the private key is available for
`Account.sign_transaction`.

---

## §5 Minimum-diff fix shape

**py_clob_client_v2 has no redeem method**: `ClobClient` method list has 60+ methods,
none named `redeem`, `settle`, or `ctf`. No SDK shortcut available.

**Path A — full web3 wire via existing infrastructure (no `web3` library needed)**:

Replace the stub with:
1. ABI-encode `redeemPositions` calldata using `eth_abi.encode` + `eth_utils.keccak`
   (both already installed).
2. Build raw tx dict: `{'to': CTF_ADDRESS, 'data': calldata, 'chainId': 137, 'nonce': N,
   'gas': ~200000, 'gasPrice': from_rpc}`.
3. Sign with `eth_account.Account.sign_transaction(tx, self.signer_key)`.
4. Broadcast via `self._rpc_call(self.polygon_rpc_url, 'eth_sendRawTransaction',
   [signed.raw_transaction.hex()])`.
5. Return `{"success": True, "tx_hash": tx_hash_from_rpc_response}`.

This is ~60 LOC added to `polymarket_v2_adapter.py` plus 2 private helpers.

**New dependencies introduced**:
- `eth_abi` (already transitively installed, needs explicit pin in `requirements.txt`)
- `eth_utils` (already installed)
- `eth_account` (already installed)
- No `web3` library required

**Additional data gaps to fill before this can work**:
1. CTF contract address must be hardcoded or added to config (1 line).
2. `winning_index_set` must flow through from harvester → `settlement_commands.token_amounts_json`
   → `submit_redeem` → `adapter.redeem(condition_id, index_sets)` — **requires protocol change**
   to `PolymarketV2AdapterProtocol` and all call sites.
3. Nonce management: must query `eth_getTransactionCount` per call; no nonce cache exists.
4. Gas price: query `eth_gasPrice` or use a fixed multiplier.

---

## §6 Test strategy in 7h

**Mainnet test**: cannot test without real gas on Polygon mainnet. Mumbai testnet is
deprecated (shut down Feb 2024). Amoy testnet exists but CTF deployment status unknown —
requires verification not feasible in 7h.

**Viable mock-mode test (no broadcast)**:
- Construct the signed raw transaction (all steps through `sign_transaction`) using a test
  private key and a mock condition_id.
- Assert: `raw_transaction` is non-empty bytes; `hash` is 32 bytes; calldata starts with
  `0x01b7037c` selector; ABI-decoded args match inputs.
- Use `unittest.mock.patch` on `_rpc_call` to capture the `eth_sendRawTransaction` call
  and assert the payload hex equals the signed raw tx.
- Does NOT verify on-chain execution, only adapter construction and wire.

**This is the only testable path in 7h**. Real-mode dry-run (sign but don't broadcast) is
equivalent to the mock test since there's no testnet CTF available.

---

## §7 Risk assessment for shipping in 7h

### Path A: Full web3 wire (eth_account + eth_abi, no web3 library)

| Dimension | Estimate | Notes |
|---|---|---|
| Implementation | 3–4h | Calldata builder, nonce+gas helpers, test |
| Reviewer | 1–1.5h | Tier-0 venue surface; requires adversarial review |
| Test | 0.5h | Mock-mode only; no real-broadcast test feasible |
| Regression risk | HIGH | Changes Tier-0 venue surface; introduces `eth_sendRawTransaction` path; index_sets protocol change required across 3+ files |

Critical risks:
- **winning_index_set gap**: not stored in `settlement_commands`; requires harvester + schema changes not currently scoped. Without this, the adapter cannot know which outcome won.
- **Nonce reuse race**: if two redeem commands fire concurrently (not current reality, but possible), duplicate nonce = one silently fails.
- **double-redeem hazard on retry**: SCAFFOLD §J explicitly flags: adapter MUST be idempotent on `condition_id`; CTF `redeemPositions` is idempotent by spec but nonce management on retry is not.
- **adapter constructor gap**: `PolymarketV2Adapter()` at `main.py:226` passes no args; `signer_key` required — this must be fixed first.

### Path B: Use Polymarket CLOB SDK

NO — `ClobClient` has no redeem method. Not viable.

### Path C: Backport from prior branch

`git log --all --oneline` shows no prior branch with web3/redeem work. No stashes exist.
No prior implementation to backport.

### Path D: Pre-bake signed tx (sign in advance, broadcast on settlement-detected)

- Requires knowing `condition_id` + `index_sets` before settlement (both known only after
  UMA resolves). Not feasible to pre-sign.
- Also: nonce must be current at broadcast time. Pre-signed tx would have a stale nonce
  unless no other txs fire from the wallet.

---

## §8 Backstop if PR-I.5 cannot ship

**Operator action for Karachi 5/17**:
1. Monitor `logs/zeus-live.err` for `[REDEEM_OPERATOR_REQUIRED] command_id=...` (appears
   within ~65 min of UMA write).
2. Claim Karachi $0.59 via Polymarket UI (browser wallet).
3. Run: `python scripts/operator_record_redeem.py --condition-id 0xc5faddf4810e0c14659dbdf170599dcb8304ef42afcccb84992b4d8fcb0f44ae --tx-hash <0x...>`
4. Confirm `settlement_commands` row is `REDEEM_TX_HASHED`.

**Precedent cost**: the `operator_record_redeem.py` script exists precisely to make this
a logged, audited action — not a silent bypass. Per SCAFFOLD §I.0, this is "Path A-as-scoped":
the cascade chain fires programmatically up to `REDEEM_OPERATOR_REQUIRED`; the operator
CLI completes the final hop. This is NOT the "manual completion" that
`feedback_first_live_order_no_manual_completion` prohibits — that feedback forbids
manually completing the ORDER itself (entry side). The redeem is a post-settlement claim.

**Antibody for precedent**:
- Add an `operator_actions_audit` table entry (already tracked by `operator_record_redeem.py`
  via `settlement_command_events`).
- Add a `docs/operations/task_2026-05-17_post_karachi_remediation/KARACHI_MANUAL_CLAIM_EXCEPTION.md`
  entry explicitly noting: "PR-I.5 not yet shipped; one-time operator claim; does NOT set
  precedent for skipping automated settlement once PR-I.5 lands."
- The `terminal_at` CHECK constraint (`settlement_commands.terminal_at` is NULL until
  `REDEEM_TX_HASHED`) already enforces populated tx_hash.

---

## §9 Verdict + recommendation

**NO-GO for PR-I.5 in 7h.**

Rationale:
1. **winning_index_set is not stored** in `settlement_commands`; wiring it requires changes
   to `harvester.py`, `settlement_commands.py` schema, `PolymarketV2AdapterProtocol`,
   and `submit_redeem` signature — a multi-file Tier-0 change.
2. **Adapter constructor gap** at `main.py:226` must be resolved before any live signing.
3. **No real-broadcast test feasible** in 7h (Mumbai deprecated; Amoy CTF status unknown).
4. **SCAFFOLD §I.0 + §J already assessed this**: "Tier-0 venue-surface work, big scope add,
   NOT Karachi-window-fittable." That assessment is accurate and confirmed by this audit.

**Least-bad backstop**: Path A-as-scoped (operator CLI). The `REDEEM_OPERATOR_REQUIRED`
state and `operator_record_redeem.py` CLI were built exactly for this case. The cascade
fires programmatically through `REDEEM_INTENT_CREATED → SUBMITTED → OPERATOR_REQUIRED`.
Operator manually claims via Polymarket UI, records via CLI. This is AUDITED and does not
violate the no-manual-completion rule on the entry/order side.

**Top 3 risks when PR-I.5 does eventually ship**:
1. **winning_index_set derivation**: must confirm which outcome wins before calling
   `redeemPositions` — wrong `indexSets` = gas spent, no payout, stuck row.
2. **double-redeem on retry**: `REDEEM_SUBMITTED → RETRYING → re-submit` can hit same
   condition_id twice; adapter idempotency on `redeemPositions` (CTF spec: idempotent)
   must be verified empirically on Polygon, not assumed.
3. **Adapter construction in `_redeem_submitter_cycle`**: `PolymarketV2Adapter()` at
   `main.py:226` passes no credentials; signer_key/funder_address must be wired from
   the same keychain source as the entry adapter.

---

## §10 Existing PR/branch check

`git log --all --oneline | grep -iE "PR-I.5|web3|redeem.adapter|polymarket.redeem"` — zero hits.

`git branch -a | grep -i redeem` — zero hits.

`git stash list` — empty.

No prior implementation exists. PR-I.5 is greenfield.

---

*Hard cap compliance: ~1,950 words.*
