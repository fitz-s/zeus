# Critic — Adversarial Review of ULTIMATE_PLAN 2026-04-26

Reviewer: critic (10-axis adversarial template per memory directive).
HEAD anchor verified: `874e00cc0244135f49708682cab434b4d151d25d`.

## TOP_5_ATTACKS

### 1. Apr26 axis-45 (User WebSocket) MISSING from all 20 cards
Apr26 §6 axis-45 (FAIL/S1, "very high" confidence) + axis-24 mandate "user WS
or documented polling loop with equivalent guarantees". Grep all 20 yamls: zero
`WebSocket|user.ws` hits. mid-05 substitutes a `get_open_orders+get_trades+
get_positions` poll — but `get_trades` "DOES NOT EXIST on HEAD" + X1-conditional;
yaml self-acknowledges "misses fills that happened-and-were-cancelled within sweep
interval". F-006 closure-claim is partial.

### 2. Apr26 §F-012 fix design NOT minted as a slice
"Deterministic fake CLOB + failure injection + restart simulation + resolved-
market fixtures" — F-012 rebranded to "happy-path audit"; mid-06 mentions fake
CLOB only as "recommended investment in conftest". Apr26 axis-31 (paper/live
parity, FAIL/S1) and axis-49 ("failure-mode test suite", "very high") have ZERO
slice ownership.

### 3. mid-02 hash-determinism antibody is fragile
`test_signed_order_hash_unique_per_idempotency_key` "relies on EIP-712 signing
determinism across retries"; mid-02 yaml says behavior "must be verified at
R2/R3 cite-time". EIP-712 typically embeds nonces — non-deterministic by
default. If signing has fresh entropy per call, antibody passes by accident or
breaks; F-003 exchange-proven idempotency (S0) is theatrical.

### 4. mid-01 ownership ambiguity unresolved in the yaml
mid-01 risks state "OWNERSHIP-AMBIGUITY (open R2): riskguard direct vs
cycle_runner-proxy". ULTIMATE_PLAN §2.2 claims "locked at cycle_runner-as-proxy
(NC-NEW-D allowlist)" — but mid-01.yaml lists both options and does NOT mint
NC-NEW-D in `antibody_test`. Wave B cannot ship without committing.

### 5. axis-18 SUBMIT_TIMEOUT (S0) reconciliation is single-routed
Every S0 timeout-after-post case requires mid-05's polling sweep to close.
mid-05 depends on mid-03 (BLOCKED on INV-29). No standalone S0 gate.

## GREP_VERIFIED_DRIFT (5/5 sample, 4 drift = 80%)

| Citation | Yaml claim | HEAD reality | Status |
|---|---|---|---|
| `polymarket_client.py:195-196` (down-01, mid-02 SEAM) | `signed=create_order; result=post_order` | Lines 194-195 (off by 1) | DRIFT |
| `polymarket_client.py:268` (down-01) | `from py_clob_client.clob_types import AssetType, BalanceAllowanceParams` | Line 268 = `def get_open_orders`; real import inline at line 347 | SEVERE |
| `polymarket_client.py:353` (§2.3 dual-gate) | "runtime gate at :353" | Line 353 = `def redeem` | SEVERE |
| `cycle_runner.py:359-373` (mid-01) | "2-cycle latency RED→cancel via pos.exit_reason" | 359-373 = force_exit_review SWEEP — Phase-2 same-cycle force-exit ALREADY landed | STALE PREMISE |
| `harvester.py:1244-1264` (down-07) | T2-G redemption + "USDC claimable later" | Confirmed | OK |

Memory `feedback_zeus_plan_citations_rot_fast` predicted 20-30%; actual 80%.
Plan documents L20 grep-gate but did not run it on its own cards.

## WORST_CASE_TIMING

Operator never delivers Q1 / Q-HB / Q-FX-1 / INV-29 amendment / impact_report:
- **Wave A** (Up, 46h): ships modulo critic-opus calendar.
- **Wave B** (Mid, 58h): mid-03 BLOCKED on INV-29; mid-03 blocks mid-01 + mid-04
  + mid-06; mid-05 depends on mid-03. **ZERO of Wave B ships.** F-001
  payload-bytes, F-003, F-004, F-005, F-006, F-010 (6 S0/S1) UNCLOSED.
- **Wave C**: down-01 needs Q1-zeus-egress. NOT-shippable. down-04/05/03
  ~4.5h parallelizable.
- **Wave D**: down-06 SKIP. down-07 BLOCKED on Q-FX-1 file.

Estimate: **~30-35h shippable / 120.5h = 25-29%.** "Zeus dominates live market"
is fully gated on operator throughput on 5 questions with no SLAs.

## RISK_S0_OR_S1

**S0-RESIDUAL after full plan execution:**
1. WS-driven fill ingestion absent — polling miss-window unmeasured.
2. Crash-recovery DURING PARTIAL_FILL mid-cycle untested by any antibody.
3. F-003 closure depends on unverified EIP-712 signer determinism (#3).

**S1-RESIDUAL:**
4. axis-31 paper/live parity — no slice. CI green ≠ live-money safe.
5. axis-44 typed retry/backoff/rate-limit — partial mid-03 K2; no backoff design.
6. axis-26/47 signature_type — up-02 dispatcher exists but no allowance/proxy/
   funder validation gate at startup.

**Hidden coupling (prompt Q5):** mid-02 absorbs `signed_order_hash` /
`payload_hash` / `signed_at_utc` into up-04's 15-col ALTER. up-04 says "DEFAULT
NULL". mid-02's invariant "`signed_order_hash` NON-NULL before place_limit_order
returns" is an implicit CHECK that up-04 does NOT encode. Legacy-row backfill
will write NULL and trip mid-02's antibody on innocent historical rows. No
cross-yaml constraint contract.

## VERDICT

**REVISE — substantive gaps, 80% citation drift on sample, operator-bottleneck
cascades across Wave B.**

Required before APPROVE:
1. Mint User-WebSocket-or-equivalent slice (axis-45/24).
2. Mint deterministic-fake-CLOB / failure-injection-suite slice (§F-012 +
   axis-31/49).
3. Re-run L20 grep-gate on EVERY yaml against HEAD 874e00c.
4. Commit mid-01 ownership in yaml (cycle_runner-proxy + NC-NEW-D in
   `antibody_test`).
5. Verify EIP-712 signing determinism BEFORE mid-02 contract lock; if
   non-deterministic, redesign F-003 closure.
6. Cross-yaml CHECK-constraint coordination contract up-04 ↔ mid-02.
7. Operator-shedding path: Wave B ships CommandEventType-only changes without
   INV-29 CommandState amendment; defer grammar-state to sub-slice.

Plan rests on real architectural work but ships claims faster than antibodies.
No "pattern proven" or "narrow scope self-validating" language used per memory
directive.
