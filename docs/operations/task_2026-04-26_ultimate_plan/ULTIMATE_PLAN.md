# Zeus Ultimate Plan — 2026-04-26 (R2 post multi-review)

Status: **DEBATE CLOSED + MULTI-REVIEW APPLIED** — 3 regions × 3 layers + 4 cross-cuts + 5-angle review folded.
HEAD anchor: `874e00cc0244135f49708682cab434b4d151d25d` (`main`).
Judge: team-lead@zeus-ultimate-plan-judge.
Document revision: R2 (R1 deferred to git history; R2 reflects post multi-review consolidation).

This plan synthesizes (i) a multi-round opus debate across three regions
(Up = boundary + provenance; Mid = execution truth + state machine;
Down = CLOB v2 transport + downstream gates), then (ii) a 5-angle parallel
multi-review (architect / critic / explore / scientist / verifier), into one
honest dependency-ordered execution sequence. Per-section detail lives in
`evidence/<region>/converged_R<N>L<N>.md`,
`evidence/multi_review/<role>_report.md`,
`evidence/multi_review/MULTI_REVIEW_SYNTHESIS.md`,
`slice_cards/<id>.yaml`, and `dependency_graph.mmd`.

**Honest scope label** (per multi-review scientist): this plan is a **live-readiness
gate**. It closes the safety bar that lets Zeus trade real capital without silent
S0 losses. It does NOT improve edge. Edge / forecast / calibration / learning
work is deferred to a separate "Dominance Roadmap" — see §4.

---

## §1 Dedupe map: Apr26 review → slices

Routing artifact: `evidence/apr26_findings_routing.yaml` (heuristic baseline,
revised below by region debate + multi-review).

Final coverage position post-multi-review (23 slice cards):

- **F-001 row-state CLOSED on HEAD** by INV-30 + venue_command_repo (the routing
  yaml's "0% shipped" claim was grep-falsified by opponent-mid). F-001
  payload-bytes RESIDUAL → mid-02.
- **§8.3 17 transitions reduce to K=4-6 K-decisions**, not 17 patches. 4 already
  exist (REVIEW_REQUIRED, PARTIALLY_FILLED, REMAINING_CANCEL_REQUESTED,
  RECONCILED_BY_POSITION). 2 are typing-only refinements. 11 genuinely NET_NEW
  → K1..K5 in Mid + K6 settlement in Down.
- **F-007 design language is `ExecutableMarketSnapshot` table+gate**, not the
  `MarketIdentity` value-object initially proposed. Apr26 review §454 is
  authoritative.
- **F-011 raw payloads** owned by Up (up-03 + up-04 + raw_orderbook_jsonb
  extension). Apr26 §9 weather-vs-trading provenance asymmetry resolved.
- **CLOB v2 plan structurally collapsed** in Down R1L1 from 4 phases → ~1
  phase: V2 SDK is unified V1+V2 client; "mandatory 10s heartbeat", "delayed
  status", "EIP-712 binary switch", "fee_rate_bps removed" all grep-falsified
  against py-clob-client-v2 v1.0.0 commit `a2ec069f`.
- **Q-NEW-1 RESOLVED-DIVERGENT** by direct on-chain Polygon RPC `eth_call`
  (memory `feedback_on_chain_eth_call_for_token_identity`):
  `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` returns `symbol()="pUSD"` /
  `name()="Polymarket USD"`. pUSD IS distinct ERC-20 (contract repurposed
  from historical USDC.e). Phase 2.C / down-07 / Q-FX-1 scope is REAL.
- **Multi-review additions** (post-debate):
  - **Apr26 axis-45 User WebSocket / S1** was missing entirely from R1 — added as
    `mid-07` WS_OR_POLL_SUFFICIENCY (decision slice: WS or documented polling
    miss-window).
  - **Apr26 §F-012 + axis-31 + axis-49** (deterministic fake CLOB +
    failure-injection + 5 unowned P0 behavioral tests) added as `mid-08`
    FAILURE_INJECTION_SUITE.
  - **Probability chain integrity antibody** (frozen-replay before/after Wave A+B)
    added as `up-08` FROZEN_REPLAY_HARNESS to guard against silent calibration
    drift from up-04 ALTER.

Coverage prose:

> The Apr26 routing yaml started at 25.8% plan coverage with 23 NET_NEW findings
> under 3 proposed umbrella slices. The debate reframed coverage to ~75% via
> three structural realizations: (i) INV-30 already closes F-001 row-state;
> (ii) §8.3 transitions are 4-already-exist + 11 genuinely-new under K=5 Mid +
> K6 Down split; (iii) V2 plan transport-only is ORTHOGONAL to execution-layer
> correctness gaps. Multi-review pushed coverage further by closing 3 gaps the
> debate missed (WebSocket, failure-injection, frozen-replay). Net 23 slice
> cards span all S0/S1 residuals; 6 NC-NEW antibodies enforce; 162.5h budget.

---

## §2 Three-region converged conclusions + cross-cuts + multi-review folds

### §2.1 Region-Up — boundary + provenance + raw payloads (8 cards, 56h)

Slice cards: `up-01..up-08`. Evidence: `evidence/up/{_context_boot_*,
_layer1_resolved, converged_R2L2, converged_R3L3}.md`.

Conclusion: A 3-axis truth contract (`up-01 polymarket_truth_contract.yaml`:
finality_order × discovery_order × realtime_order) anchors F-007. A single
seam (`up-02 OrderSemantics.for_market()`) dispatches per-market trade
arguments, enforced by **NC-NEW-C** (`zeus-create-order-via-order-semantics-only`)
+ allowlist of 3 callers. F-011 raw payloads land in `up-03` ExecutableMarketSnapshot
(with `raw_orderbook_jsonb` extension per X4) plus `up-04` venue_commands ALTER
(15 cols incl. `condition_id`, `signed_order_hash` (absorbed from mid-02),
`payload_hash`, `signed_at_utc`, `collateral_token`, `raw_post_order_response`).
Snapshot freshness gated by `up-07` (Python single-insertion + **NC-NEW-A**
mirror of NC-16). UNVERIFIED rows rejected by 7 consumers per `up-06` (LANDS
FIRST defensive deploy). Append-only on snapshots enforced by SQLite trigger
+ **NC-NEW-B**. Precision conflicts fail-closed (`PrecisionAuthorityConflictError`).
**up-08 FROZEN_REPLAY_HARNESS** (multi-review addition) asserts P_raw → Size
chain is bit-identical pre/post Wave A+B against fixture portfolios — antibody
against silent calibration-schema drift from up-04 ALTER.

### §2.2 Region-Mid — execution truth + state machine + auto-pause (8 cards, 90h)

Slice cards: `mid-01..mid-08`. Evidence: `evidence/mid/{_context_boot_*,
converged_R1L1, converged_R2L2, converged_R3L3}.md`.

Conclusion: K=5 K-decisions (K1 PAYLOAD_BIND, K2 ERR-TYPING, K3 PARTIAL/RESTING,
K4 CANCEL-FAIL, K5 EXCH-RECON; K6 settlement-redeem is Down). F-001 ROW-state
CLOSED on HEAD by INV-30 + venue_command_repo. F-001 payload-bytes land via
`mid-02` (SIGNED_ORDER_PERSISTED + signer interception at
`polymarket_client.py:194-197` — the AST-shape seam preserved by X1).

Mid R3L3 + multi-review hardening:
- **mid-01 ownership LOCKED** (multi-review critic §4): cycle_runner-as-proxy
  (riskguard observability-only). NC-NEW-D allowlist is **function-scope**, not
  file-scope (multi-review architect §GAPS:1): `_execute_force_exit_sweep` only.
- **mid-02 EIP-712 determinism evidence gate** (multi-review critic §3): mid-02
  contract lock requires `evidence/mid/eip712_signing_determinism_2026-04-26.md`
  showing signing is deterministic across retries. If FALSE, F-003 closure
  redesigned: idempotency_key dedup (NC-19, already shipped) + signed_order_hash
  becomes forensic-only column.
- **up-04 ↔ mid-02 cross-yaml constraint contract** (multi-review architect
  §GAPS:5 + critic §HIDDEN_COUPLING): up-04 column DDL is DEFAULT NULL (additive,
  legacy-safe). Runtime NOT NULL enforcement is mid-02's Python-side gate, with
  antibody asserting min(rowid WHERE signed_order_hash IS NULL) > pre-mid-02
  marker rowid.
- RESTING is payload-discrim (`venue_status='RESTING'`), enforced by **NC-NEW-E**.
- F-006 reconciliation splits cleanly: INV-31 reconciles local rows;
  `exchange_reconcile.py` + `exchange_reconcile_findings` table (mid-05).
- §8.4 state-machine grammar-additive on existing CommandState; INV-29 amendment
  gated by planning-lock (X3).
- **mid-07 WS_OR_POLL_SUFFICIENCY** (multi-review addition): User WebSocket OR
  documented polling with measured miss-window — closes Apr26 axis-45 + axis-24.
- **mid-08 FAILURE_INJECTION_SUITE** (multi-review addition): deterministic fake
  CLOB + 5 P0 behavioral tests (duplicate-submit, rapid partial-fill, RED
  cancel-all behavioral, market-close-while-resting, restart-recovery) — closes
  Apr26 axis-31 + axis-49 + 5 unowned P0 tests flagged by scientist.

Sub-sequence: **mid-02 BEFORE mid-01**.

### §2.3 Region-Down — CLOB v2 + downstream gates (7 cards, 16.5h)

Slice cards: `down-01..down-07`. Evidence: `evidence/down/{_context_boot_*,
converged_R1L1, converged_R2L2, converged_R3L3}.md` + Q-NEW-1 RPC evidence at
`evidence/down/q_new_1_polygon_rpc_eth_call_2026-04-26_R3L3.md`.

Conclusion: V2 plan structurally OVER-SCOPED relative to SDK reality. Real V2
work collapses to D0 (operator Q1+Q-HB) + D1 unified-SDK drop-in swap (down-01,
preserves `signer.sign(order)` two-step seam at `polymarket_client.py:194-197`)
+ conditional D2 (down-06 heartbeat gated on Q-HB; **down-07 pUSD branch
ACTIVATED** post-Q-NEW-1) + D3 cutover runbook + D4 cleanup.

Multi-review hardening:
- **Q-FX-1 dual-gate at `polymarket_client.py:353` (def redeem body)**
  confirmed correct citation (multi-review explore: `:353` IS `def redeem`;
  gate inserts after `_ensure_client()` in body).
- **`FXClassification` enum requirement added** (multi-review architect
  §GAPS:3 + scientist VERDICT): env-flag is process gate, not type gate. New
  `src/contracts/fx_classification.py` enum + typed imports at PnL/accounting
  surfaces — misclassification becomes TypeError at call site, not string mismatch.
- **NC-NEW-F** (single-tombstone invariant) prevents new heartbeat coroutine
  from duplicating existing apscheduler tombstone-write at `main.py:367`.
- Packet status flipped to **dormant-tracker** (V1 dormant since 2026-02-19;
  no EOL announced); `impact_report` rewrite is critic-opus gate at Phase 0.F.
- Wave 2 B2/B4/B5 reroute to data-readiness packet, not Down.

### §2.4 Cross-cuts X1-X4

Evidence: `evidence/xcut/{X1,X2,X3,X4,MASTER_VERDICT}.md`.

- **X1 (signer.sign seam — X-MD-1 fold)**: PARALLELIZABLE iff seam preserved.
  Triple-belt: NC-NEW-A (Up) + Mid mid-02 single-emit antibody + Down down-03
  AST-shape antibody. Contract is AST shape, not line numbers. Multi-review
  verifier confirmed seam exists at `polymarket_client.py:194-197` on HEAD.
- **X2 (F-012 audit)**: original "0 violators" was scope-internal. Multi-review
  scientist + critic flagged 5 unowned Apr26 P0s. **mid-08 closes the gap**
  by minting all 5 P0s explicitly. Post-mid-08, F-012 is closed at packet scope
  AND at Apr26 §13 P0 list scope.
- **X3 (Apr26 §8.4 ↔ INV-29 + X-UM-1 condition_id ALTER)**: state-machine
  extension is grammar-additive only (NC-NEW-E enforces RESTING-not-enum).
  INV-29 amendment SCHEDULED, gated by planning-lock receipt before mid-03 ships.
  up-04 owns SINGLE coordinated ALTER (15 cols absorbing mid-02's 3 hashes);
  mid-02 depends_on up-04; mid-03 critic_gate requires planning-lock PASS receipt.
- **X4 (F-011 raw-payload + D-phase collapse — X-UD-1 fold)**: F-011 FULLY
  COVERED by Up cards. up-03 EXTENDS with `raw_orderbook_jsonb`. Replay test
  via up-08 frozen-replay harness.

---

## §3 Dependency-ordered execution sequence (revised post multi-review)

**Source**: `dependency_graph.mmd` (auto-generated by
`scripts/aggregate_slice_cards.py`).
**Slice summary**: `slice_summary.md`.

### Critical path (62h)

```
up-01 → up-03 → up-04 → mid-02 → mid-01 → mid-06 → mid-08
```

Was 44h pre multi-review. Grew to 62h because mid-08 (FAILURE_INJECTION_SUITE,
18h) is now on the critical path — its deps include mid-06 + mid-07 + mid-04 +
mid-03. This is honest scope expansion; the alternative was a silently
unowned 5-P0-test gap.

### Total effort (revised)

23 cards, 162.5h (low-end). Per-region:
- Up: 8 cards / 56h
- Mid: 8 cards / 90h
- Down: 7 cards / 16.5h

**Realistic high-end (per multi-review verifier)**: 180-200h with 12-20%
buffer on the range estimates that span 38-58h in Mid alone.

### Sequencing decisions (X-cut + multi-review verdicts)

- **X1 verdict**: mid-02 + down-01 PARALLELIZABLE; both commit AST-shape signer.sign
  seam preservation antibodies.
- **X3 verdict**: mid-03 BLOCKED until INV-29 amendment commit lands +
  planning-lock receipt cited in mid-03's PR description.
- **Mid sub-sequence**: **mid-02 → mid-01** (signed-payload binding before
  RED-emission ships) → mid-03 → mid-04 → mid-05 → mid-06 → mid-07 → mid-08.
- **Up sub-sequence**: **up-06 LANDS FIRST** (defensive deploy of UNVERIFIED
  rejection matrix before up-04 backfills legacy rows) → up-04 → up-08 (after
  Wave B settles).
- **Down sub-sequence**: down-02 → down-01 → down-07; down-06 D2-gated on Q-HB;
  down-07 ship-blocked on Q-FX-1 dual-gate (env-flag + evidence-file +
  FXClassification enum).

### Wave-A entry conditions (multi-review S0 closure required)

Before Wave A starts, the following multi-review S0 items MUST close:

1. **Citation re-grep audit** of all 23 cards within 24h of Wave A start.
   Anchor citations to **stable symbols** (function name + sentinel comment),
   NOT bare line numbers. Multi-review explore showed 17/57 LINE_DRIFT in 1
   week of debate; without re-anchoring, drift will compound across 4-6 week
   execution.
2. **EIP-712 determinism evidence file** at
   `evidence/mid/eip712_signing_determinism_2026-04-26.md` MUST land before
   mid-02 contract lock. If determinism is FALSE, F-003 design redesigned.
3. **mid-01 ownership lock + NC-NEW-D function-scope antibody** confirmed in
   YAML (DONE — slice_cards/mid-01.yaml updated post-multi-review).
4. **up-04 ↔ mid-02 cross-yaml constraint contract** confirmed in YAML (DONE —
   slice_cards/up-04.yaml updated post-multi-review).
5. **mid-03 planning-lock pseudo-dep** documented (DONE — slice card carries
   critic_gate prose; manual executor awareness required since planning-lock
   is not in YAML `depends_on`).

### Operator escalation list (NOT engineering)

The plan cannot fully execute until operator delivers:

1. **Q1-zeus-egress** — host probe of `clob-v2.polymarket.com/version` FROM
   Zeus daemon machine with `funder_address` headers.
2. **Q-HB** — Polymarket support / Discord inquiry on heartbeat cadence.
3. **Q-FX-1** — USDC.e ↔ pUSD PnL classification decision; produces
   `q-fx-1_classification_decision_2026-04-26.md` operator signoff +
   FXClassification enum value selection.
4. **INV-29 amendment commit** — closed-law amendment for grammar-additive
   CommandState changes. Gated by planning-lock receipt per X3.
5. **CLOB v2 cutover go/no-go** — at end of D3 runbook.
6. **`impact_report` rewrite** — critic-opus gate at Phase 0.F before Down
   packet resumes.

### Recommended execution order

1. **Wave A** (Up foundation, ~26h): up-01 + up-06 (parallel) → up-02 → up-03 →
   up-04 (15-col ALTER) → up-07. **Multi-review S0 gates 1-5 must close
   before Wave A starts.**
2. **Wave B** (Mid execution truth, ~90h, gated on Wave A up-04): mid-02 →
   mid-01; mid-03 (after INV-29 amendment) → mid-04 → mid-05 → mid-06 →
   mid-07 → mid-08 (relationship + failure-injection across all Mid).
3. **Wave C** (Down operator-questions + drop-in, ~12h): down-04 + down-05
   (parallel doc edits) → down-02 (operator) → down-03 (antibody) → down-01
   (D1 swap, gated on up-04 + signer seam preservation).
4. **Wave D** (Down conditionals, ~6h + 6h gated): down-06 if Q-HB positive;
   down-07 once Q-FX-1 evidence file + FXClassification enum land.
5. **Wave E** (cutover): D3 runbook + 7-day monitoring + D4 cleanup.

up-05 (SignedExecutionEnvelope) lands any time after up-04 + up-07. **up-08
FROZEN_REPLAY_HARNESS** lands LATE in Wave B (after mid-01..06 settle).

---

## §4 Dominance Roadmap (deferred — NOT in this plan)

Multi-review scientist verdict: this plan is a **live-readiness gate**, not a
dominance plan. **0 of 23 cards improve edge.** The probability chain
(`51 ENS members → daily max → MC → P_raw → Platt → P_cal → α-fusion →
P_posterior → Edge & DBS-CI → Kelly → Size`) is preserved by omission, not
proven; up-08 FROZEN_REPLAY_HARNESS asserts the plan does not BREAK calibration
but does not improve it.

The following work is NECESSARY for live-market dominance and is explicitly
deferred to a future packet:

1. **EDGE_OBSERVATION_PACKET** — alpha-decay tracker per `strategy_key`
   (settlement-capture / shoulder-bin / center-bin / opening-inertia) with
   weekly drift assertion. Apr26 §1.5 strategy-family table is the contract.
   None of the 23 cards enforce it.
2. **CALIBRATION_HARDENING_PACKET** — Extended Platt (A·logit + B·lead_days +
   C) parameter monitoring; Monte Carlo noise calibration vs realized; α-fusion
   weight tuning; double-bootstrap CI tightness on small-sample bins.
3. **ATTRIBUTION_DRIFT_PACKET** — `strategy_key` is the canonical attribution
   identity (per AGENTS.md "strategy families"). No detector exists for
   silent attribution drift (e.g., a position labeled `shoulder_bin_sell`
   but executed against `center_bin_buy` semantics).
4. **LEARNING_LOOP_PACKET** — settlement-corpus → calibration update →
   parameter-drift → re-fit pipeline. Apr26 §11 corpus deferred; high/low
   split + DST resolved fixtures need owners. Apr26 Phase 4 silently dropped.
5. **WS_OR_POLL_TIGHTENING_PACKET** — even after mid-07 closes the safety bar,
   reactive WS lets Zeus respond faster than competitors during opening-inertia
   and shoulder-bin entry windows. Edge-improving, not safety-closing.

These packets each warrant their own debate cycle. They are NOT prerequisites
for shipping Wave A+B+C+D+E. They ARE prerequisites for credibly claiming
live-market dominance.

---

## §5 Multi-review verdict snapshot

| Reviewer | Verdict | Key contribution |
|---|---|---|
| architect (opus) | APPROVE_WITH_CONDITIONS | K-collapse genuine; 4 conditions (NC-NEW-D function-scope, FXClassification enum, citation re-grep, mid-05 actuator) all folded into R2 |
| critic (opus) | REVISE → CONDITIONAL APPROVE post-R2 | 5 attacks landed; 4 closed by mid-07 + mid-08 + EIP-712 evidence gate + cross-yaml contract; remaining is the Q1/Q-HB/Q-FX-1 operator bottleneck |
| explore (sonnet) | 32/57 NONE, 17 LINE_DRIFT, 2 SEMANTIC_MISMATCH | LINE_DRIFT mitigated by symbol-anchored citations + Wave-A-entry re-grep; SEMANTIC_MISMATCH on Q-FX-1 cite reframed (`:353` IS `def redeem`; gate inserts in body) |
| scientist (opus) | CONDITIONAL → CONDITIONAL APPROVE post-R2 | Plan honestly relabeled as live-readiness gate (§4 Dominance Roadmap added); up-08 frozen-replay closes calibration-drift antibody |
| verifier (sonnet) | CONDITIONALLY FEASIBLE | Critical path arithmetic correct; 162.5h optimistic floor (180-200h realistic); mid-03 planning-lock manual-only |

**Aggregate**: APPROVE_WITH_CONDITIONS — Wave A may start once the 5 entry
conditions in §3 close.

Multi-review process learning: in-debate 10-min grep-gates miss compound
drift. 5-angle parallel review at packet close caught what 12+ converged
debate rounds did not. Memory: `feedback_multi_angle_review_at_packet_close`.

---

## Appendix: artifact locations

- `judge_ledger.md` — judge-side debate progress
- `cross_region_questions.md` — cross-region escapes + judge routing
- `RETROSPECTIVE_2026-04-26.md` — process learnings
- `evidence/apr26_findings_routing.yaml` — heuristic routing
- `evidence/{up,mid,down}/converged_R<N>L<N>.md` — per-region per-layer results
- `evidence/xcut/{X1,X2,X3,X4,MASTER_VERDICT}.md` — cross-cut verdicts
- `evidence/down/q_new_1_polygon_rpc_eth_call_2026-04-26_R3L3.md` — on-chain
  RPC evidence (block 86055549)
- `evidence/multi_review/{architect,critic,citation_verification,trading_correctness,feasibility}_report.md`
  — 5 multi-review reports
- `evidence/multi_review/MULTI_REVIEW_SYNTHESIS.md` — synthesized verdict +
  S0/S1/S2 action items
- `slice_cards/{up,mid,down}-NN.yaml` — 23 slice cards
- `dependency_graph.mmd` + `slice_summary.md` — auto-generated aggregation
