# Cross-region questions

Append entries here when a teammate identifies a topic that crosses Up/Mid/Down boundaries. Judge sweeps this file after each region closes.

Format:

```
## YYYY-MM-DD HH:MM — <region-author> — <one-line title>
- regions touched: [up, mid, down]
- summary: ≤80 words
- proposed disposition: route to X#, new cross-cut, or fold into <region>-<seq>
```

## 2026-04-26 — opponent-up — F-011 sequencing depends on Region-Down D2 collapse outcome
- regions touched: [up, down]
- summary: Region-Up R1L1 turn 3 converged on `SignedExecutionEnvelope` lifecycle-anchored atom AFTER D2.D (per opponent's Attack-4 push). But Region-Down WebFetch audit (judge ledger lines 64-72) shows V2 SDK is unified V1+V2 client and may collapse D2 4-phase → 1-phase, with pUSD/Phase-2.C dead, EIP-712 v1→v2 binary switch wrong, fee_rate_bps "removed" false. If D2.D as a discrete phase ceases to exist post-collapse, "sequence atom after D2.D" loses its anchor. Up-03/up-04 cards must reference Down's final D-phase shape.
- proposed disposition: new cross-cut X-UD-1 (Up↔Down sequencing dependency on D-phase collapse). Or fold into X3 if X3 is already "transport↔provenance sequencing".

## 2026-04-26 — proponent-up — X-UM-1 condition_id Up↔Mid coordinated migration
- regions touched: [up, mid]
- summary: up-04 EXTEND venue_commands schema adds `condition_id` column (per Apr26 F-008 fix design + opponent-up Attack-5 EXTEND framing). Region-Mid F-008 fix (YES/NO outcome-token identity at command level) ALSO needs `condition_id` on the same table. To avoid two competing ALTER TABLE migrations, up-04 must be the SINGLE coordinated migration that adds condition_id once with semantics that satisfy both Up's payload-residual provenance need AND Mid's outcome-identity-freezing need. Mid's mid-NN cards should depend on up-04, not redefine the column.
- proposed disposition: fold into X1 if X1 already covers Up↔Mid schema-coordination, otherwise create new cross-cut X-UM-1. Up-04 yaml card should reference whichever X# is assigned.

## 2026-04-26 — proponent-mid — A1 K4-RED→durable-cmd parallelizability with D2.A given V2 unified SDK
- regions touched: [mid, down]
- summary: Original X1 framing (judge spawn brief) had A1 K4-RED→durable-cmd vs CLOB-v2 D2.A delayed-status sequencing as a sequencing risk: if D2.A renames submit() or changes error semantics, A1's typed-error table breaks. Region-Down WebFetch finding (V2 SDK is unified V1+V2 client with per-token version resolution) reduces sequencing risk. PROPOSAL: A1 + D2.A are PARALLELIZABLE if A1 codes against py-clob-client's unified surface, NOT against V1-only or V2-only branches. Same applies to mid-02 (signed_order_hash + SIGNED_ORDER_PERSISTED): the ORDER signing surface in unified SDK must expose hash before post for both V1+V2 markets, OR mid-02 fails to close F-001 payload-binding for V2 markets. Cross-region question: does Region-Down's collapsed D-phase shape preserve a stable signing-surface seam where mid-02 can intercept signed_order_hash? If yes, A1 + mid-02 + D2.A all parallelizable. If no, mid-02 sequences AFTER Region-Down's surface stabilizes.
- proposed disposition: route to X1 (A1 vs D2.A sequencing). If X1 also covers signed-order-hash interception seam, fold mid-02 dependency in. Otherwise new cross-cut X-MD-1 (Mid↔Down signing-surface seam stability).

---

## Judge routing decisions (2026-04-26)

- **X-UD-1 (Up↔Down F-011 sequencing on D-phase collapse)** → FOLDED INTO X4. X4's verdict will state: "F-011 raw-payload persistence sequences AFTER Region-Down's collapsed D-phase, not after a specific D2.D phase. up-03/up-04 cards reference Down's final D-phase shape, not the original Phase 2.D."
- **X-MD-1 (Mid↔Down signing-surface seam)** → FOLDED INTO X1. X1's verdict will state: "A1 + mid-02 + D2.A are PARALLELIZABLE iff all code against py-clob-client unified surface (V1+V2 in same package per `_resolve_version()` + `_is_v2_order()`). The unified SDK exposes signed_order_hash before post via the `OrderArgs/OrderArgsV2 → signer.sign(order)` seam — mid-02 can intercept there for both V1+V2. CONDITION: Region-Down's D-phase final shape MUST preserve `signer.sign(order)` as a stable interception seam."
- **X-UM-1 (Up↔Mid `condition_id` coordinated schema migration)** → FOLDED INTO X3. X3's verdict will state: "up-04 owns the SINGLE coordinated ALTER TABLE on venue_commands (adds `condition_id` column) satisfying BOTH Up's payload-residual provenance need AND Mid's F-008 outcome-identity-freezing need. mid-NN cards depend on up-04 — they must NOT redefine the column. State-machine extension proposed by Apr26 §8.4 is implemented as additive enum members on existing CommandState (3 new: PARTIALLY_FILLED already exists per opponent-mid audit; 11 transitions are genuinely new — to be enumerated in mid-NN cards). Closed-enum amendment is governance, not architecture: gate via planning-lock + INV-29 amendment commit."
- pUSD/USDC label dispute (proponent-down boot point 1 vs opponent-down boot point 1) → unresolved at judge level (Polygonscan WebFetch 403'd, raw GitHub config.py 404'd, docs.polymarket.com confirms "pUSD" verbatim but doesn't define whether separate ERC-20). **Operator on-chain probe required**: query symbol() and name() on `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` via Polygonscan. If returns "USD Coin (PoS)" / "USDC" → impact-report's "pUSD" is marketing label, not a separate asset, and Phase 2.C / Q5 / F1 / F2 dissolve. If returns a different symbol → real bridge work required. Ledger as `Q-NEW-1`.
