# State-Vocabulary Canonical Redesign — first-principles design

```
# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: this report (design-of-record proposal); grounded in live zeus_trades.db SELECT DISTINCT,
#   working-tree HEAD de147e29, codegraph/CRG coupling analysis, and a frontier-model (ChatGPT Pro) consult
#   round-1 + round-2 re-reasoning. NOT yet ratified into docs/authority/.
# Status: DESIGN. §8 (reducer semantic contract) completes when consult round-2 lands.
```

## 0. Problem, verdict, headline number

**Problem.** Zeus's order/position/settlement lifecycle is modeled by **~53 distinct state-bearing vocabulary objects** (Python enums, `frozenset`s, DB `CHECK` value-sets, untyped status-string fields) plus ~70 reason/label taxonomies, scattered across ~30 live files. The same real-world lifecycle is re-encoded multiple times under synonym-divergent spellings, with several states that exist only as bandaids for incomplete reconciliation. This sprawl fights itself at runtime and is a source of money-path bugs.

**Verdict (high confidence).** Do **not** collapse the sprawl into one vocabulary. Collapse it into **one event-sourced TRUTH reducer per orthogonal mechanism**, with venue/chain **truth** and local **optimistic/unknown belief** kept deliberately separate. Zeus already contains the correct architectural seed in `src/contracts/position_truth.py` (venue facts + immutable local intent → canonical position events → projections); **the defect is that many modules bypass that reducer with parallel strings and frozensets.** The superior design is therefore not "tidier enums" — it is demoting every redundant vocabulary to an **adapter or a pure projection** over a single reducer.

**Headline number.** Of **53** state-bearing objects, **13** survive as canonical source vocabularies or reducer-owned projections; **40 (75.5%) are collapsible.** At the value level the reduction is larger: live-DB `SELECT DISTINCT` shows most defined values are never written (e.g. `position_lots.state` uses 2 of 7 defined values; `settlement_commands.state` 3 of 8).

## 1. Root mechanism — coupling is by-VALUE, not by-TYPE

`codegraph_impact` on the enum **classes** is near-empty (`CommandState` → 12 nodes, mostly tests; `LifecycleState` → 4; `FillAuthority` → 2) — because modules do **not** depend on the enum type. They compare against raw string `.value`s. But the string **values** span 2–7× more files than the typed symbols (`"LIVE"` 21, `"CONFIRMED"` 18, `"CANCELLED"` 17 vs `CommandState` 7). **Nothing typed enforces vocabulary consistency, so the synonym sets drift independently. That is the root mechanism of the bug surface.** The single most important property of the target design: **make the coupling typed** — one owned type per axis at the ingress/DB boundary, every consumer importing the type — so re-introducing a synonym becomes a compile/lint failure, not a silent runtime drift.

## 2. The 10 canonical axes (minimal, orthogonal)

Two axes are independent only if states on both can be simultaneously true without contradiction. The minimal complete set for a Polymarket-settled engine:

| # | Axis | Tracks | Truth or projection |
|---|---|---|---|
| A1 | **OrderCommand** | Zeus-local command/outbox lifecycle (intent → submit → ack/unknown → review) | local epistemic truth |
| A2 | **VenueOrderFact** | CLOB order status (NOT_SEEN/LIVE/PARTIALLY_MATCHED/MATCHED/CANCELLED/EXPIRED/REJECTED) | venue truth |
| A3 | **VenueTradeFact** | post-match confirmation (MATCHED→MINED→CONFIRMED; RETRYING/FAILED) | venue/chain truth |
| A4 | **PositionExposureLot** | per-lot exposure (OPTIMISTIC→CONFIRMED→EXIT_PENDING→ECON_CLOSED→SETTLED) + authority facet | venue/chain truth + provenance |
| A5 | **PositionPhase** | coarse lifecycle (pending_entry→active→day0→pending_exit→econ_closed→settled / voided / admin_closed / quarantined_review) | **projection** over A1–A4,A7,A8 |
| A6 | **ExitProgress** | sell-command progression | **projection** over A1/A2/A3 of the sell command — ideally no source-of-truth enum |
| A7 | **ChainVisibility** | per-position ERC-1155 attribution (synced/local_only/chain_only_review/…); **separate** per-cycle snapshot completeness | chain truth |
| A8 | **MarketResolution** | UMA/source/venue resolution (unresolved→source→published→disputed→resolved YES/NO→void_50_50→revised) | venue/oracle truth |
| A9 | **RedemptionAccounting** | redeem observation/accounting (submission FORBIDDEN — accounting only) | accounting |
| A10 | **CollateralWrap** | USDC↔CTF wrap/unwrap command | chain command |

**Not lifecycle axes** (kept separate, typed per lane): provenance/authority (TruthAuthority, ObservationAuthority, ScanFreshness, DepthProofSource, AuthorityTier — `VERIFIED` in one lane ≠ `VERIFIED` in another); control/gating (heartbeat, cutover, risk level, entry-block, rollout); reason taxonomies (rejection/no-trade/exit/void/ghost reasons — labels on transitions, not states).

## 3. Current → ideal mapping (summary; full verdict table in the consult answer)

Verdict legend: KEEP-AS-CANONICAL / MERGE-INTO / REDUNDANT-SYNONYM-OF / BANDAID(root cause) / DEAD / CROSS-AXIS-CONFLATION(split).

- **A1 CommandState** → CROSS-AXIS-CONFLATION: `PARTIAL/FILLED/CANCELLED/EXPIRED` are A2/A3 facts projected onto the command row — remove from command truth. Collapse `UNKNOWN`+`SUBMIT_UNKNOWN_SIDE_EFFECT` → one principled `UNKNOWN_SIDE_EFFECT`; `REJECTED`+`SUBMIT_REJECTED` → `REJECTED_LOCAL`/`REJECTED_VENUE`.
- **A2/A3 fill vocabularies** (`fill_tracker`'s 6 frozensets, `order_truth_reducer`'s 4, `family_exclusive_dedup`'s, `governor`'s, raw REST/WS strings) → REDUNDANT-SYNONYM / MERGE into one canonical normalizer + typed predicates.
- **A4 governor `ExposureState`/frozensets** → REDUNDANT-SYNONYM-OF `PositionExposureLotState`; `QUARANTINED` → `REVIEW_REQUIRED`. `FillAuthority`/`CausalityStatus` KEEP as provenance facets; `RecoveryAuthority` MERGE-INTO authority facet.
- **A5 `LifecycleState`** → REDUNDANT-SYNONYM-OF `LifecyclePhase` (entered/holding→active, pending_tracked→pending_entry); keep `LifecyclePhase` canonical (DB-stored `UNKNOWN` excluded).
- **A6 `ExitState` + `position.order_status` sell values** → CROSS-AXIS-CONFLATION: split entry projection / exit projection / processing-error event; ideally derive exit, single owner for sell truth.
- **A7** two `ChainState` classes → split permanently (`VenueVisibilityStatus` per-position, `ChainSnapshotCompleteness` per-cycle); fold reactive review variants into review-state + reason/TTL.
- **A8 `SettlementOutcome`** → KEEP as fixed-int compatibility DAG; ideal splits evidence/resolution/redemption via additive projection (do not renumber).
- **A9 `SettlementState`** → CROSS-AXIS (name only): it is redemption accounting, not market settlement; submission path is a BANDAID (forbidden).

## 4. Redundancy headline + live-DB over-definition (committed facts)

`SELECT DISTINCT` on live `zeus_trades.db` (2026-06-29):
- `venue_commands.state` (641 rows): CANCELLED 357, FILLED 139, EXPIRED 82, REJECTED 54, SUBMIT_REJECTED 7, REVIEW_REQUIRED 2 → both REJECTED+SUBMIT_REJECTED live (61 rows to fold); FILLED/CANCELLED/EXPIRED persisted on the command row confirms the A1 cross-axis conflation **in data**.
- `venue_order_facts.state` (~16.4k): EXPIRED 14952, LIVE 671, CANCEL_CONFIRMED 533, MATCHED 156, PARTIALLY_MATCHED 62, VENUE_WIPED 3 → **no OPEN/RESTING/ACCEPTED rows: the persisted column is already canonical; the synonym soup lives in CODE branching on raw strings.**
- `venue_trade_facts.state`: MATCHED 154, CONFIRMED 142, MINED 96 (RETRYING/FAILED transient).
- `position_lots.state`: CONFIRMED_EXPOSURE 119, OPTIMISTIC_EXPOSURE 66 → **only 2 of 7 defined values ever live.**
- `position_current.phase`: voided 489, settled 105, quarantined 32, admin_closed 17, economically_closed 7, pending_exit 1, day0_window 1 → `voided` dominates (phantom/unfilled pile); `quarantined` bandaid phase populated.
- `settlement_commands.state`: REDEEM_INTENT_CREATED 37, REDEEM_CONFIRMED 25, REDEEM_REVIEW_REQUIRED 19 → **only 3 of 8 defined values live.**

→ Most "defined" states have **zero live rows** → the unused values need **no data migration**, only writer-removal + CHECK-narrow.

**Bandaids + root cause (representative):** `quarantine_fill_failed`/`quarantine_void_failed` (legacy non-vocabulary, 0 rows, no writer); `REVIEW_REQUIRED` family + 3 `REVIEW_CLEARED_*` events (non-atomic write-event-then-call-venue); error-sentinel `order_status` (`fill_ledger_write_failed`…) (multi-step fill write not atomic); `abandoned_unsubmitted_ghost` (no transactional outbox/idempotency key); `repair_confirmed_phantom_voids` (a void pass mis-voids real fills → a second pass undoes it); `HOLD_REST_IN_PROGRESS` (policy string used as a family lock); `CHAIN_CONFIRMED_ZERO`/`CHAIN_ABSENT_…`/`ENTRY_AUTHORITY_QUARANTINED` (reactive enum accretion after live crashes — single source-of-truth writer-set + CHECK generator removes the pattern).

## 5. Bug surface (severity-ranked; S1 = money/exposure correctness)

- **S1** resting synonyms (`LIVE/RESTING/OPEN/ACCEPTED`): `fill_tracker.RESTING_OPEN_STATUSES` recognizes 4, `order_truth_reducer._OPEN_STATES` 3, `_ORDER_FACT_STATES` stores LIVE/RESTING but not OPEN/ACCEPTED → one module sees a resting order another treats as absent → family/maker dedup mis-fires.
- **S1** partial synonyms; **S1** `MATCHED` order-fact vs trade-fact (zero-remainder fill vs early on-chain) conflated by bare `== "MATCHED"`; **S1** `CANCELLED` vs `CANCELED` (one branch misses American spelling).
- **S1** four order-fill vocabularies (`CommandState`/`venue_order_facts.state`/`OrderResult.status`/`position.order_status`) — not four truths; if `OrderResult.status="filled"` outruns an A3 CONFIRMED, position materialization fights later reconciliation.
- **S1** `LifecycleState` vs `LifecyclePhase` (`pos.state=="active"` silently misses entered/holding); **S1** two `ChainState` classes (wrong-class coercion); **S1** `position.order_status` vs `exit_state` double-store sell state.
- **S2** `SettlementOutcome` vs `SettlementState` naming; `economically_closed` treated as terminal by some sets; `QUARANTINED`/`VERIFIED` reused across lanes.
- **S3** single-member `BlockStage.DISCOVERY`; reason strings embedded in state fields.

## 6. Impact / coverage / coupling map

- **Coverage** (live-file blast radius), widest first: A1 order-fill ~30 → A8 settlement 35 (read-mostly) → A7 chain 29 → A5 lifecycle 12–18.
- **Per-hotspot READ/WRITE** (migration-edit concentration): `executor.py` 142W (live submit boundary), `fill_tracker.py` 54W, `command_recovery.py` 54W+319R (broadest, 5 axes, highest-risk surface), `exchange_reconcile.py` 42W, `lifecycle_manager.py` 35W (owns phase FSM), `event_reactor_adapter.py` 241R (router — missed string compare → silent mis-route), `portfolio.py`/`projection.py` low W but **authoritative** truth writes.
- **Read-only pilot sites** (zero write-risk first cutover): `risk_allocator/governor.py`, `strategy/family_exclusive_dedup.py`.
- **CRG betweenness chokepoints ∩ vocab hotspots**: `portfolio.py`(PortfolioState), `event_reactor_adapter.py`, `exchange_reconcile.py`, `db.py`(init_schema), `family_exclusive_dedup.py`.
- **DB de-risker**: all 6 vocab-bearing tables are trade_class, owned exclusively by **`zeus_trades.db`** → core CHECK-constraint migration is **single-DB**, no cross-DB INV-37 ATTACH needed (only `settlement_outcomes.authority` lives in `zeus-forecasts.db`, read-mostly).

Full coupling detail: `scratchpad/coupling_impact_map.md`.

## 7. Migration sequencing (reducer-first, adapter-first, verification-first; no caps/shadow/throttle)

De-risked ordering (final ordering pending §8 round-2):
1. Add canonical types + pure normalizers (`src/contracts/canonical_lifecycle.py`), no writer changes. Import-time assertion: every current frozenset value maps to exactly one canonical value or an explicit reason. **Low risk.**
2. Ingress normalization of venue raw REST/WS strings at the boundary (OPEN/RESTING/ACCEPTED→LIVE; PARTIAL*→PARTIALLY_MATCHED; CANCELED→CANCELLED; order-level FILLED→MATCHED only at zero remainder; trade statuses kept). Golden fixtures from live raw payloads; before/after `SELECT DISTINCT`.
3. Make one `VenueOrderTruthReducer` the **sole** order open/fill/no-fill classifier; replace local set membership in fill_tracker/recovery/family-dedup/maker-rest/governor with typed predicates. **Pilot first on the two READ-ONLY gates (governor, family_exclusive_dedup)** — zero write-risk smoke test — then the write hotspots.
4. Split command state from venue truth (A1 stops owning PARTIAL/FILLED/CANCELLED/EXPIRED); fold REJECTED/SUBMIT_REJECTED + UNKNOWN/SUBMIT_UNKNOWN via adapters.
5. Unify `LifecycleState` into `LifecyclePhase` (runtime adapter only); all branches read `position_current.phase` or `phase_for_runtime_position()`. Keep F109 clean.
6. Force-rename the two `ChainState` classes; ban bare `ChainState` imports (lint). No DB migration. (Severity downgraded — aliases already exist; this is the residual naming fix.)
7. Collapse exposure mirrors into `position_lots` reducer predicates.
8. Remove exit-state overlap; single owner for sell truth; stop writing sell states into `order_status`.
9. Split market-resolution from redemption naming **without renumbering** `SettlementOutcome` ints; add `MarketResolutionState.from_settlement_outcome()`.
10. Preserve redeem-submit prohibition; remove `REDEEM_SUBMITTED` from live write paths first, CHECK last.
11. Replace error sentinels with `ProcessingErrorEvent` under one SAVEPOINT (atomic append or rollback; no half-state).
12. Retire bandaids after the underlying reconciliation exists (durable outbox for ghosts; monotonic chain/order/trade reducer for phantom-void; finding closure/expiry).
13. SQLite CHECK migration ordering — **single-DB for the 6 core tables**: reader accepts old+new → write canonical → backfill → assert zero legacy rows → rebuild table with narrowed CHECK in one SAVEPOINT. (Cross-DB only for the forecasts-DB authority mirror, via ATTACH+SAVEPOINT under INV-37.)
14. Delete dead values last, with live-data proof (0 rows + no writer).

## 8. Reducer semantic contract (consult round-2 — committed)

Full contract: `/tmp/cgc_answer_followup1.txt`. Summary:

- **Sole classifier = `src/execution/order_truth_reducer.py`, TYPED + WIDENED** (not replaced). It already emits the proof classes `TERMINAL_NO_FILL/TERMINAL_FILLED/TERMINAL_PARTIAL/PARTIAL_WITH_REMAINDER/LIVE_RESTING/UNKNOWN_SIDE_EFFECT/REVIEW_REQUIRED` (verified). Type its `proof_class`→`OrderProofClass`, `state`→`VenueOrderStatus`; add the ingress normalizer. **`proof_class` typing DONE — Phase 1 step 2a, backward-compatible (StrEnum), all 3 live consumers (`venue_command_repo:2923` set-membership, `exchange_reconcile:2322` JSON store, `:4551` ==) verified; 56 tests green.** `state` typing + ingress wiring deferred (the `"UNKNOWN"` proof value has no `VenueOrderStatus` member → needs `state: VenueOrderStatus | None` + test update).
- **New `src/contracts/canonical_lifecycle.py`** = typed enums (`VenueOrderStatus` 6, `VenueTradeStatus` 5, `CommandTruthState` 11, `ExposureState` 2, `OrderProofClass`, `PositionPhase`, `ExitProgress`, `VenueStatusIngress`) + the 3 ingress normalizers + **INV-CL-1** (raw status legal only at ingress; CI lint enforces). **DONE — Phase 1 step 1, TDD, 43 tests green.**
- **New `src/state/canonical_projections.py`** = pure `derive_position_phase()`, `derive_exit_progress()`, `derive_order_result_status()`, `family_dedup_blocks_entry()`, `exposure_micro_for_governor()`. REUSE `position_truth.py`'s existing typed facts (`LocalIntent`/`VenueOrderFact`/`VenueTradeFact`/`VenuePositionFact`, `FillAuthority`/`CausalityStatus`) — do NOT create parallel `CommandFact`/`OrderFact`/`TradeFact` (that would re-introduce redundancy).
- **Committed**: persisted `CANCEL_CONFIRMED` stays (no rename → no write migration); A5 `position_current.phase` stays **materialized** (F109 + 32 live quarantined rows + fast `WHERE`), derive-on-write via `derive_position_phase()`→`fold_lifecycle_phase()`; A6 `ExitProgress` **fully eliminated** to a view (single owner = exit command + its venue facts; `order_status` stops storing sell states); A4 `position_lots.state` → **2 active-exposure values only** (closure/exit/settlement/quarantine derived elsewhere).
- **Authority model**: A4 keeps exactly **2 facets** — `FillAuthority` (how strongly we know fill economics; `venue_position_observed` load-bearing for shared wallet, do NOT merge) + `CausalityStatus` (training/P&L eligibility). `RecoveryAuthority` → derived `derive_recovery_class(fill_authority, causality_status)`.
- **Do NOT extend `CanonicalPositionEventKind`** in the pilot (already a closed correct grammar).
- **INV-CL-1 cutover surface (measured)**: **41 raw-status-branch sites across 12 files** (`executor`, `fill_tracker`, `command_recovery`, `exchange_reconcile`, `venue_command_repo`, `polymarket_v2_adapter`, `polymarket_user_channel`, `exit_safety`, `live_order_reconcile`, `decision_kernel/clock`, `data/collection_frontier`, `backtest/fill_simulator`). These become typed-predicate calls; the grep is the lint baseline.

## 9. Where this could be wrong (carry these as guardrails)

1. Balance-only recovery (`FillAuthority.VENUE_POSITION_OBSERVED` / `RecoveryAuthority.BALANCE_ONLY`) is **load-bearing**, not scar tissue — real tradable exposure without full causality (shared-wallet reality). Do not collapse into CONFIRMED/OPTIMISTIC without preserving the authority tier.
2. `SettlementOutcome` may be intentionally over-modeled for weather source-revision + UMA dispute; the migration is additive projection/renaming, never renumbering or eager deletion.
3. Migration risk exceeds cleanup benefit if done schema-first; it only dominates reducer-first/adapter-first/verification-first, or the historical reactive-`ChainState` incidents repeat in a new namespace.

## 10. Local verification log (Claude Code = verification authority)

Confirmed against working tree + live DB + grep + codegraph/CRG: dead (0 rows + no writer) — `HEARTBEAT_CANCEL_SUSPECTED`, `GHOST_DUPLICATE`, `quarantine_fill_failed`, `quarantine_void_failed`, `REDEEM_SUBMITTED` (set only behind the forbidden `submit_redeem` path). NOT dead but cross-axis-conflated — `CANCEL_UNKNOWN` (live `semantic_cancel_status` payload), `CANCEL_FAILED` (live `CommandEventType` + transition). Candidate-dead (no writer) — `SETTLED_NOT_IN_API`, `EXIT_FAILED`, `RetrainStatus.RUNNING`, `SettlementFact.outcome="VOIDED"`. ChainState dup already alias-mitigated in hot paths (residual = bare imports). LifecycleState/LifecyclePhase confirmed runtime-vs-persisted two-layer split via `phase_for_runtime_position()`.

Raw evidence: `scratchpad/states_01..05`, `consult_context_digest.md`, `local_verification_notes.md`, `coupling_impact_map.md`, consult answers `/tmp/cgc_answer_REQ-20260629-134032-8738ab.txt` (round-1) + round-2.

## 11. Implementation status (2026-06-29)

DONE + verified (TDD, all green except confirmed-pre-existing branch failures):
- **Step 1** — `src/contracts/canonical_lifecycle.py`: typed enums (`VenueOrderStatus`, `VenueTradeStatus`, `CommandTruthState`, `ExposureState`, `OrderProofClass`) + 3 ingress normalizers folding the synonym soup + INV-CL-1. `tests/test_canonical_lifecycle.py` (43).
- **Step 2a** — typed the sole reducer's output: `order_truth_reducer.py` `proof_class` → `OrderProofClass` (StrEnum, backward-compatible). All 3 live consumers verified (`venue_command_repo:2923` set-membership, `exchange_reconcile:2322` JSON store, `:4551` ==). `tests/test_order_truth_reducer.py`.
- **Step 2b** — `src/state/canonical_projections.py`: A4 exposure predicates (`counts_as_active_exposure`/`is_closed_exposure`/`is_optimistic_exposure`/`weighted_lot_exposure_micro`), behavior-identical to governor. `tests/test_canonical_projections.py` (22).
- **Step 3 (read-only pilot #1)** — `risk_allocator/governor.py` cut off its local `_ACTIVE_EXPOSURE_STATES`/`_CLOSED_EXPOSURE_STATES` frozensets onto the centralized typed predicates. **Equivalence proven**: `tests/test_governor_scope_lattice.py` 13 green (exposure math unchanged). First live gate migrated off duplicated vocabulary.
- **Step 4 (INV-CL-1 enforcement)** — `tests/test_inv_cl1_no_raw_venue_status_branching.py`: ratcheting antibody pinning the migration baseline (9 files still branch on venue-jargon raw status `RESTING`/`CANCELED`/`PARTIALLY_FILLED`/`PARTIALLY_MATCHED`); a NEW offender or a silently-cleared baseline file both fail. Locks the core invariant during the multi-step cutover.
- **Step 5 (reducer input typing)** — `order_truth_reducer._TERMINAL_STATES`/`_OPEN_STATES` tied to canonical `VenueOrderStatus` (StrEnum set-interchangeable → byte-identical membership; reducer tests green).
- **Step 6 (open-order-fact unification)** — new `canonical_projections.OPEN_ORDER_FACT_STATES` + `is_open_order_fact()` (the `{LIVE,RESTING,PARTIALLY_MATCHED}` classification re-encoded 4×). Migrated THREE consumers onto the single source: `venue_command_repo:2921` (membership), `maker_rest_escalation` (SQL-IN set), `event_reactor_adapter` (bridge node, membership). Equivalence: `test_rest_then_cross_policy`, `test_maker_rest_escalation`, reducer + projection tests green (82). Only the `fill_tracker.RESTING_OPEN_STATUSES` copy (adds OPEN/ACCEPTED) remains — deferred to the fill_tracker write-hotspot step.

PRE-EXISTING failures (confirmed via revert-and-retest, NOT introduced here): `tests/test_money_path_lifecycle_replay.py::…crash_boundaries` (1, `execution_capability` validation) + `tests/test_unknown_side_effect.py` (12). Co-tenant / branch territory on `hotfix/redecision-execution-seams`.

- **Step 7 (fill_tracker tied to canonical)** — `fill_tracker`'s 6 frozensets sourced from `VenueTradeStatus`/`VenueOrderStatus` (byte-identical, asserted; 604 green).
- **Step 8 (dead-value elimination — 4 removed, 0-row + no-writer proven)** — `GHOST_DUPLICATE`, `quarantine_fill_failed`, `quarantine_void_failed` (`portfolio.py` truth hub; aligned with the existing `test_live_safety_invariants` absence antibody), `HEARTBEAT_CANCEL_SUSPECTED` (`venue_command_repo._ORDER_FACT_STATES`). Truth-hub edits added **zero** new failures (revert-and-retest: 27 pre-existing identical). 427 green.

- **Step 9 (projection layer — the design's core, additive + TDD)** — `canonical_projections` now has the full pure-derive set: `derive_exit_progress` (A6), `derive_order_result_status` (A1), `derive_position_phase` (A5, 10-rule monotonic precedence), `derive_recovery_class` (A4 authority — `RecoveryAuthority` derived from `FillAuthority`+`CausalityStatus`, no 3rd stored facet) + the new `ExitProgress`/`LegacyOrderResultStatus`/`PositionPhase` enums. 117 green.
- **Step 10 (A5 unification — real cutover)** — `LifecyclePhase = PositionPhase` (one enum, not two). `LifecyclePhase is PositionPhase` == True; the S1 two-enum bug fixed. Zero new failures (27 pre-existing identical).
- **Step 11 (A7 lock)** — antibody `test_inv_chain_state_alias.py` forbids bare `import ChainState` (the two classes are imported only as `VenueVisibilityStatus`/`ChainSnapshotCompleteness`; verified zero current bare imports).

Full session consolidated: **524 green** across the canonical layer + every cutover + antibodies.

REMAINING — DEEP WIRING (each replaces scattered special-cased money-path logic with the unified derives; a correctness change per site, not a mechanical swap):
- Wire `derive_position_phase`/`derive_exit_progress`/`derive_order_result_status` into the real writers (`projection.py`/`lifecycle_manager` fact→boolean plumbing; `exit_lifecycle`/`portfolio` stop storing sell state; `executor` OrderResult producers). The pure decisions are built; the per-site fact-assembly + equivalence is the work.
- A8/A9 settlement split is NOT a clean projection: `SettlementOutcome` conflates market-resolution (YES/NO) with position-outcome (WIN/LOSE) — untangling needs the position side.
- `family_exclusive_dedup` reducer-based blocking rewrite (10-file harness).
- CHECK-constraint narrowing (single-DB) for the CHECK-coupled dead values.
