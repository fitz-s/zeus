# Structural diff — Our 3-region debate plan vs `ZEUS_ULTIMATE_PLAN_V2_IMPROVED.md` (V2.1)

Created: 2026-04-26 (post multi-review R2)
Inputs:
- Our plan: `docs/operations/task_2026-04-26_ultimate_plan/ULTIMATE_PLAN.md` (R2, 23 cards, 162.5h)
- V2.1: `/Users/leofitz/Downloads/ZEUS_ULTIMATE_PLAN_V2_IMPROVED.md` (593 lines, dated 2026-04-27 — written before our R2 multi-review iteration)

V2.1 reads the same source artifacts but proposes a different decomposition.
This doc catalogs where V2.1's critiques LAND, where they don't, what implementation
realities expose in our 3-region structure, and how to reconcile.

---

## 1. Decomposition philosophy: regions vs lifecycle phases

| Plan | Decomposition axis | Top-level units |
|---|---|---|
| Ours | **Code-locality regions** | Up (boundary/provenance) · Mid (execution) · Down (transport) + cross-cuts X1-X4 |
| V2.1 | **Data-lifecycle phases** | Z (foundation) → U (snapshot+provenance) → M (execution lifecycle) → R (settlement) → T (parity) → A (strategy) → G (gates) |

**Why this matters for implementation.** Code locality is a *static* property
(a function lives in one module). Data lifecycle is a *dynamic* property
(an order moves through 7+ stages from intent → confirmed). Implementation
adds capability **per stage**, not per module. When you add a single
new column on `venue_commands` to capture `clob_token_ids_raw`:

- In OUR plan: up-04 owns ALTER, mid-02 contributes 3 hashes, down-07
  contributes `collateral_token`. ONE column-add coordinates across 3
  regions + an X-UM-1 cross-cut + Mid R2L2 absorption.
- In V2.1: it's U2 (raw provenance schema). One phase, one commit. Other
  phases consume the column via documented interface.

The 4 cross-cuts (X1-X4) and 3 X-region routings (X-MD-1, X-UD-1, X-UM-1)
in our plan are **evidence of decomposition strain**. V2.1's lifecycle
DAG (Z0 → Z1 → Z2 → Z3 → Z4 → U1 → U2 → M1 → M2 → M3 → M4 → M5 → T1 → G1)
has zero cross-phase coordination beyond depends_on arrows.

---

## 2. Critiques that LAND (V2.1 wins)

### 2.1 Preserve PROVENANCE not SDK-call SHAPE

V2.1: *"Delete: 'preserve two-step create_order/post_order seam at all costs.'
Replace with: Preserve provenance, not a specific SDK call shape. … wrap it
in a `VenueSubmissionEnvelope` that captures pre-sign intent, options,
signed order if exposed, SDK version, raw request/response, and all venue
IDs."*

Our X1 triple-belt antibody pins `polymarket_client.py:194-197`. If V2 SDK
ships a `create_and_post_order` convenience method in v1.1, our antibody
fails on a benign refactor. **V2.1's `VenueSubmissionEnvelope` is the
correct level of abstraction.** The envelope captures whatever the SDK
exposes — one-step or two-step — and persists provenance fields above the
SDK boundary. F-001 payload-binding is then a property of the envelope,
not the seam.

**Verdict**: V2.1 wins. Our plan should add a `VenueSubmissionEnvelope`
contract above mid-02, and demote the seam-pinning antibodies to "the
envelope MUST capture signed_order_hash regardless of SDK shape."

### 2.2 MATCHED ≠ FILLED ≠ CONFIRMED at trade level

V2.1: *"`MATCHED` is not final fill truth. Portfolio finality must use
`CONFIRMED` trade state … `MATCHED`, `MINED`, `CONFIRMED`, `RETRYING`,
and `FAILED` must exist as first-class trade states."*

Our state-machine compression (mid-04 PARTIAL_FILL_OBSERVED with payload
discriminator) collapses 5 distinct on-chain truth states into 2
(PARTIAL/FILLED). On Polygon: `MATCHED` is off-chain orderbook engine;
`MINED` is on-chain inclusion; `CONFIRMED` is reorg-survived; `FAILED` is
on-chain revert (rare but real for partial-fill-then-token-transfer-fail).

**Real-world failure mode our plan hides**: if a strategy trains
calibration on `PARTIAL_FILL_OBSERVED` events that turn out to be
`FAILED`-on-chain, the calibration corpus is poisoned. Our up-08
FROZEN_REPLAY_HARNESS would catch the regression *eventually* but the
plan doesn't prevent the poisoning at write-time.

**Verdict**: V2.1 wins. We need a separate trade-facts projection with
MATCHED/MINED/CONFIRMED/RETRYING/FAILED states. This is the second
state-machine split V2.1 demands.

### 2.3 Position-lot OPTIMISTIC vs CONFIRMED exposure

V2.1: *"`OPTIMISTIC_EXPOSURE`, `CONFIRMED_EXPOSURE`, `EXIT_PENDING`,
`ECONOMICALLY_CLOSED_OPTIMISTIC`, `ECONOMICALLY_CLOSED_CONFIRMED`,
`SETTLED`."*

Our LifecyclePhase enum (`pending_entry → active → day0_window →
pending_exit → economically_closed → settled`) has no optimistic-vs-confirmed
split. A position is `active` whether the trade is MATCHED-only or
CONFIRMED. The risk allocator (when we get there) cannot distinguish
"capital at risk pending chain confirmation" from "capital actually
deployed on confirmed exposure."

**Verdict**: V2.1 wins. This is a third state-machine split.

### 2.4 CutoverGuard as runtime states, not runbook

V2.1: *"`CutoverGuard` module with states: `NORMAL`, `PRE_CUTOVER_FREEZE`,
`CUTOVER_DOWNTIME`, `POST_CUTOVER_RECONCILE`, `LIVE_ENABLED`."*

Our Wave E "cutover" is operator-driven runbook. If operator fires the
flip while a stale GTC order is resting on V1, we have no runtime gate
to prevent placement during downtime, no automated post-cutover sweep,
no `VENUE_WIPED_REVIEW` state for orders observed on V1 but absent on
V2 post-cutover.

**Verdict**: V2.1 wins. Z1 CutoverGuard is genuinely missing from our
plan. Wave E should be a code module with state machine, not a runbook.

### 2.5 Heartbeat is mandatory, not conditional

V2.1: *"Heartbeat is mandatory for any live resting-order strategy.
Zeus must not submit GTC/GTD orders unless HeartbeatSupervisor is
healthy."*

Our down-06 is D2-gated on Q-HB cadence inquiry. The conditional framing
allowed us to defer the architectural decision. But V2.1 is right:
heartbeat is the only mechanism that prevents a network partition from
silently auto-cancelling resting orders without Zeus knowing. Even if
Polymarket's mandate is "soft" (no fixed cadence), Zeus's own
risk-correctness needs heartbeat.

**Verdict**: V2.1 wins. down-06 should promote from D2-gated to mandatory.

### 2.6 pUSD as full collateral ledger, not balanceOf rewire

V2.1: *"`CollateralLedger` with: pUSD balance, pUSD allowance to Exchange,
CTF token balance per outcome token, CTF token allowance, reserved pUSD
from open buy orders, reserved tokens from open sell orders, legacy
USDC.e balance and wrapping state. … Sell preflight: token balance
available >= sell size + reserved open sells. pUSD balance must not be
used to approve normal exit sells."*

Our down-07 is `balanceOf()` rewire + redemption. The 8-row pUSD
propagation matrix asserts pUSD doesn't leak into riskguard / chain_recon /
calibration (negative tests). But it doesn't address the **buy/sell
asymmetry**: `pUSD` is BUY collateral; `CTF outcome tokens` are SELL
inventory. Our exit-path may try to use pUSD to approve a sell that
needs a token reservation.

**Real-world failure mode**: holding 100 YES tokens, low pUSD, want to
exit-sell. With our plan, the SELL preflight checks `get_balance()`
which returns pUSD balance. Sell submits and FAILS at the venue because
the inventory check (CTF token balance) is not separately tracked. Our
NC-NEW-F + ZEUS_PUSD_REDEMPTION_ENABLED gates only cover the redemption
path, not normal exit-sell.

**Verdict**: V2.1 wins. Z4 CollateralLedger should replace down-07's
`balanceOf rewire` with a multi-asset ledger that distinguishes buy
collateral from sell inventory and tracks reservations.

### 2.7 5-projection state-machine split

V2.1: 5 distinct projections —
1. `venue_commands` (intent + submit)
2. `venue_order_facts` (exchange order lifecycle)
3. `venue_trade_facts` (trade/settlement)
4. `position_lots` (portfolio exposure)
5. `settlement_commands` (resolution/redeem)

Our HEAD has 4 enums (CommandState + LifecyclePhase + LifecycleState +
ChainState/ExitState) but they're incidental — accumulated history, not
designed as 4 projections. Our K=5 K-decisions all amend the SAME
CommandState/CommandEventType grammar, which is why INV-29 amendment
became a planning-lock event.

**Verdict**: V2.1 wins. Our K-collapse compressed too aggressively.
"K=5 K-decisions on one grammar" is shorter than "5 grammars each with
their own decisions" but the latter is structurally cleaner and avoids
INV-29 amendment becoming a global-coordination event.

### 2.8 A1/A2 dominance work in plan, not deferred

V2.1: A1 alpha/execution benchmark + A2 risk allocator are part of the
plan (post T1). Our §4 Dominance Roadmap defers them.

**Verdict**: V2.1 wins partially. Multi-review scientist already flagged
this — calling our plan a "live-readiness gate" not "dominance plan" was
the honest relabel. But V2.1 says the dominance layer should be IN the
execution plan, not a future packet. This is an organizational call. If
the operator's actual intent is "Zeus dominates live market" (per
original prompt), then A1/A2 ARE in scope.

---

## 3. Critiques that DON'T fully land (our plan wins)

### 3.1 V2.1's "V2 is P0 not low-risk"

V2.1: *"Delete: 'CLOB V2 plan structurally collapsed / low-risk drop-in.'
… CLOB V2 is a P0 venue adapter migration."*

V2.1 OVERSTATES this. Our Down R1L1 didn't say V2 is low-risk; it said
the V2 PLAN was over-scoped against SDK reality (mandatory heartbeat
unsourced, EIP-712 binary switch wrong, fee_rate_bps "removed" wrong, V1
release date 2026-02-19 not 2026-04-19, unified V1+V2 client). All of
this was grep-verified against py-clob-client-v2 v1.0.0 commit
`a2ec069f`. V2.1 doesn't cite SDK source.

**The real correction**: V2 is **P0 in scope**, but the V2 *PLAN PACKET*
was over-scoped against actual SDK behavior. Our Down collapse was
correct on SDK reality; it was wrong only in framing severity. The
V2-as-P0 framing should be inherited from V2.1, but the SDK-source-
falsification work we did should NOT be undone.

### 3.2 V2.1 has no concrete file:line citations

Our plan post-multi-review has grep-verified file:line anchors (32/57
NONE drift, 17 LINE_DRIFT mitigated, 2 SEMANTIC_MISMATCH reframed).
V2.1 is architectural prose with acceptance-test descriptions but no
specific test names, no file:line anchors, no NC-NEW antibody bodies.

**Verdict**: our concrete-implementation work survives any V2.1 reframing.

### 3.3 V2.1 has no antibody system

Our 6 NC-NEW antibodies (A: venue-commands-repo-only; B: snapshot
append-only; C: order-semantics-only; D: cycle_runner CommandBus
allowlist function-scope; E: RESTING-not-enum; F: single-tombstone)
encode rules into semgrep + runtime tests. V2.1 mentions antibodies but
doesn't formalize them.

**Verdict**: our antibody system carries forward unchanged.

### 3.4 V2.1's Q-NEW-1 absence

V2.1 says *"`Q-HB` resolved-positive by official order docs."* It doesn't
have the equivalent of our Q-NEW-1 on-chain `eth_call` resolution for
pUSD identity. The pUSD distinct-ERC-20 fact is a load-bearing collateral
correctness input.

**Verdict**: our Q-NEW-1 RPC evidence + memory rule survives.

### 3.5 V2.1 has no multi-review process

Our 5-angle multi-review caught 80% citation drift, EIP-712 determinism
fragility, mid-01 ownership ambiguity, hidden coupling between mid-02
NON-NULL and up-04 DEFAULT NULL. V2.1 is single-author.

**Verdict**: process survives. V2.1's revisions should themselves be
multi-reviewed.

---

## 4. Cross-region implementation realities

User's prompt: *"在实际implement过程中，可能会出现很多触及不同区域的情况"* —
in actual implementation, work will inevitably touch multiple regions.

The 3-region decomposition was the RIGHT abstraction for **adversarial
debate** (each region has distinct attack axes: boundary/provenance vs
execution-semantics vs transport-mechanics). It is the WRONG abstraction
for **execution sequencing** because most implementation tasks cross
regions.

### Concrete cross-region scenarios

**Scenario A: Add `clob_token_ids_raw` column for Gamma↔CLOB provenance**
- Our plan: up-04 owns the ALTER (Up); mid-02 ensures it's populated at
  signing seam (Mid); down-01 pulls raw response from V2 SDK (Down). Three
  regions, X-UM-1 routing.
- V2.1: U2 raw provenance schema. One phase. Gamma payload, CLOB
  market-info hash, raw orderbook hash all live in one place.

**Scenario B: Implement signer.sign hash interception**
- Our plan: down-01 D1 SDK swap preserves seam (Down); mid-02 PAYLOAD_BIND
  intercepts (Mid); up-04 stores the hash (Up); X1 verdict locks the
  seam (cross-cut). Triple-belt antibody pins polymarket_client.py:194-197.
- V2.1: Z2 venue adapter creates VenueSubmissionEnvelope. Captures hash
  if SDK exposes it; falls back to raw_request_hash if not. One phase.

**Scenario C: Heartbeat coroutine + apscheduler interaction**
- Our plan: down-06 D2-gated (Down); NC-NEW-F single-tombstone (Down);
  mid-07 WS_OR_POLL_SUFFICIENCY mentions heartbeat health (Mid); main.py
  scheduler integration (cross). Touches Down + Mid + main.py.
- V2.1: Z3 HeartbeatSupervisor is a single phase. Placement gate at Z2
  enforces "GTC/GTD require HEALTHY"; gate is consumed at one site.

**Scenario D: Sell-token reservation for exit-sell**
- Our plan: not addressed. mid-04 PARTIAL_FILL_OBSERVED tracks fills but
  not pre-submit token reservation. down-07 only tracks pUSD balance.
- V2.1: Z4 CollateralLedger tracks `reserved tokens from open sell orders`
  per `(position_id, token_id)`. M4 cancel/replace mutex consumes this.

**Scenario E: User WebSocket gap → REST sweep**
- Our plan: mid-07 decision slice (WS or polling). mid-05 sweep depends
  on mid-03 grammar. If WS path chosen, gap-detection logic touches mid-07
  + mid-05. Two cards but tight coupling.
- V2.1: M3 user-channel ingest with explicit gap detection → REST
  backfill via M5 reconcile sweep. Phases compose cleanly.

**Pattern**: every scenario in our plan crosses regions and incurs X-cut
coordination cost. Every scenario in V2.1 stays in one phase or composes
phases through documented interfaces.

### Why this matters

For a 162.5h plan executed over 4-6 weeks by a small team, decomposition
strain compounds:
- Each cross-region X-cut is a coordination meeting / PR description /
  reviewer routing decision.
- The 3-region structure forces every implementer to maintain mental
  state about cross-cut dependencies.
- INV-29 amendment as planning-lock event blocks Mid until cross-region
  governance ack.
- Multi-review caught 80% citation drift partly BECAUSE the same
  implementation site had to be cited from 3 cards (Up + Mid + Down) and
  each citation rotted independently.

V2.1's lifecycle phases are LOCAL (each phase owns one set of tables
+ one state machine) with EXPLICIT INTERFACES (next phase reads from
the documented projection). Implementation cost is lower; merge conflicts
are lower; reviewer load is lower.

---

## 5. What to keep vs what to change

### 5.1 Keep (our plan's strengths)

1. **23 slice cards with file:line citations** — these are concrete
   implementation anchors, not architectural prose.
2. **NC-NEW-A..F antibody system** with semgrep + runtime tests.
3. **Q-NEW-1 RPC eth_call resolution** for pUSD identity.
4. **EIP-712 determinism evidence gate** for mid-02 contract lock.
5. **up-04 ↔ mid-02 cross-yaml constraint contract** (legacy NULL-safe).
6. **mid-01 NC-NEW-D function-scope antibody** + cycle_runner-as-proxy
   ownership lock.
7. **Multi-review process** (architect / critic / explore / scientist /
   verifier).
8. **Wave-A entry conditions** (citation re-grep, EIP-712 evidence, etc.).

### 5.2 Adopt from V2.1 (structural moves we missed)

1. **VenueSubmissionEnvelope** above mid-02. Demote seam-pinning to
   envelope-pinning. F-001 payload binding is a property of the envelope
   regardless of SDK call shape.
2. **5 distinct state projections**: venue_commands +
   venue_order_facts + venue_trade_facts + position_lots +
   settlement_commands. Stop compressing into one CommandState.
3. **MATCHED/MINED/CONFIRMED/RETRYING/FAILED** at trade-fact level.
   Calibration corpus consumes CONFIRMED only.
4. **OPTIMISTIC_EXPOSURE vs CONFIRMED_EXPOSURE** at position-lot level.
   Risk allocator distinguishes optimistic from confirmed.
5. **CutoverGuard module** with NORMAL → PRE_CUTOVER_FREEZE →
   CUTOVER_DOWNTIME → POST_CUTOVER_RECONCILE → LIVE_ENABLED states.
   Replace Wave E runbook with code.
6. **HeartbeatSupervisor mandatory** for GTC/GTD; placement gate at
   Z2-equivalent layer; demote down-06 from D2-gated to required.
7. **CollateralLedger** with pUSD balance + pUSD allowance + CTF token
   balance per outcome + reserved buy/sell sizes + wrap/unwrap commands
   + legacy USDC.e classification. Replace down-07's `balanceOf` rewire.
8. **A1 StrategyBenchmarkSuite + A2 RiskAllocator** ON the plan, not
   deferred to §4 Dominance Roadmap. Per multi-review scientist + V2.1
   alignment.
9. **Settlement_commands ledger** (R1) — REDEEM_INTENT_CREATED →
   REDEEM_TX_HASHED → REDEEM_CONFIRMED → REDEEM_FAILED. Currently our
   K6 settlement is "out-of-scope deferred to Down".

### 5.3 Reframe (decomposition merger)

The 3-region debate decomposition was the right abstraction for
adversarial review; the lifecycle-phase decomposition is the right
abstraction for execution. **Recommended reconciliation**: maintain
both views.

- **Debate view** (frozen): 3 regions × 3 layers + 4 cross-cuts +
  multi-review. Source of truth for slice card content.
- **Execution view** (NEW): rewrite ULTIMATE_PLAN.md §3 with V2.1's
  Z/U/M/R/T/A/G phases. Each phase references the slice cards from
  the debate view. New cards (Z1 CutoverGuard, Z3 HeartbeatSupervisor
  promoted, Z4 CollateralLedger, M3 UserChannel, R1 SettlementLedger,
  T1 FakeVenue, A1/A2 dominance) added.

### 5.4 Card count after merger

- Debate-view cards retained: 23
- New cards from V2.1 adoption: 7 (Z1, Z4-as-replacement-for-down-07-extension,
  M3, R1, T1, A1, A2; some are replacements not net-new)
- Estimated post-merger card count: ~26-28
- Estimated post-merger total hours: ~220-260h (V2.1 is significantly
  bigger scope)

---

## 6. Recommendation

**Proposed plan revision: R3 — adopt V2.1's lifecycle decomposition while
preserving R2's concrete implementation work.**

R3 will:

1. Keep all 23 R2 slice cards as source-of-truth for concrete file:line
   work (frozen as `evidence/debate_artifacts/`).
2. Replace ULTIMATE_PLAN.md §3 with V2.1's Z/U/M/R/T/A/G phase structure.
3. Each V2.1 phase points to the R2 slice cards it consumes + adds new
   cards for V2.1's net-new work.
4. Demote our X1 seam-pinning verdict to "envelope-pinning" (V2.1 §31).
5. Split mid-04 PARTIAL_FILL_OBSERVED into mid-04a (order-fact) +
   mid-04b (trade-fact MATCHED/CONFIRMED) per V2.1 trade-level split.
6. Promote down-06 from D2-gated to mandatory (Z3 HeartbeatSupervisor).
7. Replace down-07 single-card with Z4 CollateralLedger multi-card
   sub-packet.
8. Add Z1 CutoverGuard, M3 UserChannel, R1 SettlementLedger, T1
   FakeVenue, A1 BenchmarkSuite, A2 RiskAllocator.
9. Reframe §4 from "deferred Dominance Roadmap" to "A1+A2 IN plan"; move
   only the most experimental (e.g., LEARNING_LOOP) to deferred.
10. Re-run multi-review on R3 before declaring DEBATE_CLOSED.

**Estimated R3 scope**: ~26-28 cards, 220-260h, critical path likely 80-100h.

**The honest framing**: V2.1 is a more correct plan for "make Zeus
dominate live market" than our R2. R2 is a more concrete plan for
"close the live-readiness safety bar". The merger (R3) is what the
operator actually needs.

---

## 7. Process learnings

1. **Region-based debate decomposition strains under cross-region
   implementation realities.** Future debates should consider whether
   the debate axis is the same as the implementation axis. If not,
   produce a separate "execution view" before declaring CLOSED.

2. **External authoring round captured what internal debate missed.**
   V2.1 was written outside our 3-region structure; it spotted MATCHED ≠
   CONFIRMED, CutoverGuard, CollateralLedger, VenueSubmissionEnvelope. A
   fresh-eyes external review at packet close (in addition to
   multi-review sub-agents) is a valuable signal.

3. **V2.1's lifecycle decomposition isn't just style — it's correctness.**
   Compressing 5 state machines into 1 (CommandState) made INV-29
   amendment a planning-lock event AND hid MATCHED ≠ CONFIRMED AND hid
   OPTIMISTIC vs CONFIRMED exposure. The compression cost is real.

4. **"Preserve provenance not seam" is a generalizable principle.**
   When pinning an antibody, ask: *what invariant does this protect against
   what failure?* If the failure is "future SDK refactor breaks our test",
   the antibody is too narrow. Pin to the invariant, not the syntax.

5. **Memory candidate**: `feedback_lifecycle_decomposition_for_execution`
   — when a plan crosses 3+ data-lifecycle stages (intent → submit →
   match → confirm → settle), prefer lifecycle-phase decomposition for
   execution sequencing even if debate decomposition was different. The
   adversarial axis and the execution axis are not the same axis.
