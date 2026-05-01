# Region-Mid R1L1 Joint Converged Result

Created: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)
Status: CLOSED (judge ledger §18)
Authors: opponent-mid + proponent-mid (joint)

## Canonical converged statement (judge-accepted, 196 chars)

> Mid R1L1: K=4 (PAYLOAD_BIND, CANCEL_FAIL, PARTIAL/RESTING, EXCH_RECON) + A1 new-auth-surface + §8/C1.5. F-001 row CLOSED, payload OPEN. F-006 split; F-010 RED-decorative. Cards mid-01..06.

## K-decision substance (K=5)

K-counting note: judge canonical accepts K=4 string-compression where mid-03 carries TWO K-decisions (ERR-TYPING + CANCEL-FAIL) in one slice. Slice content honors K=5 audit substance:

- **K1 PAYLOAD_BIND** (mid-02) — F-001 payload + F-003 + F-008 binding. SIGNED_ORDER_PERSISTED event, signed_order_hash + payload_hash + invariant_hash columns (3 signing-derived; identity columns owned by up-04 per X-UM-1).
- **K2 ERR-TYPING** (folded into mid-03) — SUBMIT_TIMEOUT_UNKNOWN + CLOSED_MARKET_UNKNOWN events refining existing SUBMIT_UNKNOWN; A3 typed-error closure rides on shared event-types.
- **K3 PARTIAL/RESTING** (mid-04) — PARTIAL_FILL_OBSERVED payload schema (filled_size, remaining_size); ACCEPTED-vs-RESTING discrimination via venue_status — **R2-OPEN: enum-split vs payload-discrimination** decided at L2 file:line audit per Fitz Constraint #4.
- **K4 CANCEL-FAIL** (folded into mid-03) — CANCEL_FAILED + CANCEL_REPLACE_BLOCKED events + closed-law amendment to INV-29.
- **K5 EXCH-RECON** (mid-05) — F-006 closure: get_open_orders + get_trades enumeration + journal-diff (NEW module).

K6 settlement (REDEEM_REQUESTED + REDEEMED) = OUT-OF-SCOPE for Mid → Down region (D2 pUSD redemption flow per routing yaml lines 319-337).

## 6-card on-disk roster

| Card | Title | Owner | Size |
|---|---|---|---|
| mid-01 | A1 K4 RED → durable-cmd (new authority surface) | proponent-mid | 3822 B |
| mid-02 | A1.5 PAYLOAD_BIND (3 signing hashes; depends on up-04) | proponent-mid | 8160 B |
| mid-03 | A4.5 STATE_GRAMMAR_AMEND (K2 + K4; A3 folded) | opponent-mid | 7542 B |
| mid-04 | A4.5b PARTIAL/RESTING (K3; R2-OPEN) | opponent-mid | 6111 B |
| mid-05 | EXCHANGE_RECONCILE_SWEEP (K5) | proponent-mid | 8936 B |
| mid-06 | §8.3 ↔ INV-29 compat-map + C1.5 relationship tests | opponent-mid | 7461 B |

## Sub-sequence lock

**mid-02 BEFORE mid-01** — A1 emits CANCEL_REQUESTED via insert_command/append_event. Without mid-02 signed-payload binding landing first, CANCEL events have no signed-payload provenance, leaving F-010 closure incomplete on payload side.

## Dependency graph

```
up-04 (Up region — owns condition_id ALTER per X-UM-1)
   ↓
mid-02 (PAYLOAD_BIND, signing-derived hashes)
   ↓
mid-01 (A1 RED → durable-cmd)

mid-03 (STATE_GRAMMAR_AMEND)
   ↓ blocks: mid-01, mid-04, mid-06

mid-04 (PARTIAL/RESTING)
   ↓ blocks: mid-05 (CANCEL_FAILED + PARTIAL_FILL grammar consumed by EXCH_RECON)

mid-05 (EXCH_RECON) depends on mid-03

mid-06 (§8.3 compat + C1.5) depends on mid-01..05 (relationship test target)
```

## Verdicts on Apr26 review findings

| Finding | Verdict | Owning slice |
|---|---|---|
| F-001 row-state | CLOSED at HEAD by INV-30 (executor.py:815/832/917/988 chain) | — |
| F-001 payload-bytes | RESIDUAL (no signed_order_hash column at venue_command_repo.py:194-242) | mid-02 |
| F-002 SDK error matrix | RESIDUAL (executor.py:547-577 flattens exceptions to status="rejected") | mid-03 (folded A3) |
| F-003 signed-order-hash | RESIDUAL (NC-19 idempotency is local hash, not signed hash) | mid-02 |
| F-004 partial-fill first-class | RESIDUAL (PARTIAL state exists but payload schema empty) | mid-04 |
| F-005 cancel-failure | RESIDUAL (no CANCEL_FAILED state; no CANCEL_REPLACE_BLOCKED event) | mid-03 |
| F-006 reconciliation | SPLIT — INV-31 reconciles local rows; F-006 needs exchange-side enum (command_recovery.py:71-77 punts orphans to REVIEW_REQUIRED) | mid-05 |
| F-008 outcome-token identity | RESIDUAL (4 identity columns missing; coordinated via X-UM-1 → up-04) | up-04 |
| F-010 RED authority | RESIDUAL — RED is decorative-capability at HEAD (riskguard 0 hits for command_bus emission; cycle_runner.py:67 "Does NOT post sell orders in-cycle"; riskguard.py:1080-1082 "forced exit sweep is a Phase 2 item") | mid-01 (NEW authority surface, NC-17 grammar-bounded) |
| F-012 happy-path tests only | RESIDUAL — needs cross-module relationship tests | mid-06 |

## §8.3 audit summary (17 transitions)

Resolved by opponent-mid audit against `src/state/venue_command_repo.py:42-84` _TRANSITIONS table (28 legal pairs):

- **4 ALREADY EXIST**: REVIEW_REQUIRED (reachable from 7 source states), PARTIALLY_FILLED ((ACKED|UNKNOWN|PARTIAL, PARTIAL_FILL_OBSERVED) → PARTIAL), REMAINING_CANCEL_REQUESTED ((PARTIAL, CANCEL_REQUESTED) → CANCEL_PENDING), RECONCILED_BY_POSITION (chain_reconciliation.py).
- **2 typing-only refinements** (existing event extended with new type members): SUBMIT_TIMEOUT_UNKNOWN refines SUBMIT_UNKNOWN; TRADE_CONFIRMED refines FILL_CONFIRMED.
- **11 genuinely NET_NEW** reduce to K1 + K2 + K3 + K4 + K5 + K6 (K6 settlement = Down region).

Routing yaml plan_coverage_pct: 25.8% under-counted by ~24% (4 over-counted as NET_NEW).

## L2 OPEN questions (queued for sequential L2 dispatch)

1. **mid-02 ↔ up-04 ALTER consolidation**: should mid-02 + up-04 merge into ONE migration owned by up-04, or stay as two coordinated migrations? L2 file:line audit of init_schema patterns decides.
2. **mid-05 ↔ INV-31 journal-write boundary**: who writes journal-diff events — EXCH_RECON or command_recovery? Mutual exclusion required to keep INV-30 clean.
3. **mid-01 ownership**: riskguard direct vs cycle_runner-as-proxy emits CANCEL on RED. Authority-direction proof needed.
4. **mid-04 K3 RESTING**: enum-split (CommandState.RESTING new member) vs venue_status payload-discrimination. R2 file:line audit on chain_reconciliation/lifecycle_manager downstream consumers decides per Fitz Constraint #4.
5. **signer.sign(order) seam grep-verification** per judge X1 §29 conditional verdict — mid-02 must commit to a stable interception seam compatible with V2 unified SDK.

## Cross-region cuts

- **X-UD-1** (Up↔Down F-011 sequencing on D-phase collapse) — ROUTED to X4
- **X-MD-1** (Mid↔Down signing-surface seam) — ROUTED to X1
- **X-UM-1** (Up↔Mid `condition_id` coordinated migration) — ROUTED to X3 (up-04 single ALTER, mid-02 depends_on: [up-04])

## Process-state notes

- Layer 1 architecturally CLOSED.
- Mid teammates idle until judge §86 sequential dispatch (Up first → Mid → Down → cross-cuts).
- Mid will not burn context on L2 prep until judge signals turn.

## Major architectural concessions logged

- **opponent-mid AV-1 wins** (judge ledger §73): F-001 row-state CLOSED by INV-30 but payload-bytes RESIDUAL. Routing yaml's "0% shipped" claim falsified.
- **opponent-mid AV-3 wins** (proponent C-A): F-006 distinct from INV-31 — exchange-side enumeration is genuinely new-work.
- **opponent-mid AV-6 wins** (proponent C-B): RED → durable-cmd is NEW authority surface, not preserved direction. NC-17 holds because grammar-bounded (CANCEL/DERISK only).
- **opponent-mid §8.3 audit wins**: 4 of 17 transitions already exist; routing yaml under-counted real coverage by ~24%.

## Failure modes avoided (per Fitz methodology)

- **Constraint #1 (structural decisions > patches)**: 17 transitions reduced to 5 K-decisions (K=5 audit) → 6 cards (slice cardinality). Did not chase 17 individual fixes.
- **Constraint #2 (translation-loss)**: Chose closed-law amendment with explicit re-audit list over dual-grammar (would have leaked translation-loss across mid-03/04/05 reconcilers).
- **Constraint #3 (immune system > security guard)**: mid-06 C1.5 relationship tests are antibodies (cross-module invariant), not alerts.
- **Constraint #4 (data provenance > code correctness)**: K3 RESTING locked R2-OPEN pending downstream-consumer audit; refused to lock payload-discrimination without auditing chain_reconciliation/lifecycle_manager interpretation.
