# Chain/Local Position Model — First-Principles Audit Findings (2026-05-27)

> Source: conversation prompt 2026-05-27, persisted for cross-session durability.
> See plan: `docs/plans/2026-05-27-chain-local-position-model-refactor.md`

## 1. Reconstructed first-principles model

Authority order:
```
Venue facts + immutable local intent
  → venue_commands / venue_order_facts / venue_trade_facts / venue_position_facts
  → canonical position events
  → position_current / LocalProjection
  → runtime Position adapter
  → monitor / exit / reporting / learning
```

Object layers:
1. **Venue truth** — Polymarket/CLOB/CTF facts. Immutable. Never "patched" by local projection.
2. **Local intent / decision truth** — Zeus decision to submit/cancel/exit/redeem. May predate venue ack. `venue_submission_envelopes` and `venue_commands` are correct seeds.
3. **Venue command / order / trade fact journal** — side-effect boundary + venue-returned facts. Must feed canonical fold.
4. **Canonical position event fold** — sole durable lifecycle authority. Phases: `pending_entry → active → day0_window → pending_exit → economically_closed → settled/voided/admin_closed/quarantined/review_required`.
5. **Local runtime projection** — `Position` becomes projection/cache from canonical events + latest venue facts. NOT independent authority.

Exceptions as typed facts (not fake positions):
`ChainOnlyFact`, `LocalIntentWithoutVenueAck`, `VenueOrderFactWithoutProjection`, `VenuePositionFactWithoutIntent`, `PartialFillFact`, `CancelRemainderFact`, `SettlementFact`, `RedeemFact`, `ApiSnapshotCompleteness`, `RecoveryGapFact`.

`CHAIN_UNKNOWN` must NOT void local position; only `CHAIN_EMPTY` (proven complete) may.

## 2. Authority surfaces present in current main

1. Mutable `Position` / `PortfolioState` (`portfolio.py`) — still self-described as "source of truth".
2. Canonical lifecycle phase (`lifecycle_manager.py`) — `LifecyclePhase` exists but derives from runtime strings via `phase_for_runtime_position()`.
3. Per-cycle chain completeness classifier (`src/state/chain_state.py.ChainState`) — `CHAIN_SYNCED/CHAIN_EMPTY/CHAIN_UNKNOWN`.
4. Per-position chain visibility (`src/contracts/semantic_types.py.ChainState`) — `synced/local_only/chain_only/quarantined/exit_pending_missing`. **NAME COLLISION** with (3).
5. Venue command journal (`executor.py`, `venue_command_repo.py`) — closest to target model; append-only.
6. `position_events` / `position_current` projection — partial schema.
7. Legacy `trade_decisions` mirror — duplicate status/fill/order/chain fields.

## 3. Confirmed simplification opportunities

1. Replace chain-only sentinel `Position` with `ChainOnlyFact`.
2. Split per-cycle chain completeness from per-position venue visibility (resolve name collision).
3. Make `position_current.phase` the only durable lifecycle phase.
4. Eliminate runtime economics mutation from chain view (`get_open_positions(chain_view=)`).
5. Replace rescue-as-fill with typed recovery facts.

## 4. Findings

### Finding 1 — P1 `chain_verified_at` polluted by absence observations [CONFIRMED BUG]

- Reconcile writes `pos.chain_verified_at = now_iso` when local position is MISSING from chain.
- Classifier then treats that timestamp as a recent positive observation.
- Fix: split `chain_verified_at` (positive) and `last_chain_absence_observed_at` (negative).
- Files: `src/state/chain_reconciliation.py`, `src/state/chain_state.py`, `src/engine/lifecycle_events.py`, `src/state/projection.py`, `src/state/db.py`.
- Tests: missing-from-chain branch must not advance positive timestamp; classifier reads positive only.
- **THIS PR (C0)** — smallest high-confidence behavior fix.

### Finding 2 — P1 `quarantine_size_mismatch` is illegal lifecycle string [LIKELY BUG]

- `chain_reconciliation` writes `pos.state = "quarantine_size_mismatch"` when canonical size correction unavailable. Not in `LifecycleState` enum. Maps to `UNKNOWN`. May leak into open exposure / runtime gates.
- Fix: replace with canonical `REVIEW_REQUIRED` / `QUARANTINED` phase + payload reason. Stop writing arbitrary runtime strings.
- Files: `src/state/chain_reconciliation.py`, `src/state/lifecycle_manager.py`, `src/engine/lifecycle_events.py`, `src/state/portfolio.py`, `src/state/projection.py`.

### Finding 3 — P1 Chain-only tokens as fake `Position` in two incompatible ways [CONFIRMED COMPLEXITY DEBT]

- Two synthetic-`Position` constructors with different identity/economics for the same chain-only token (one in `chain_reconciliation`, one in `portfolio.load_portfolio()` from suppression rows).
- `unknown_entered_at` sentinel proves leakage already required a patch.
- Fix: emit `ChainOnlyFact` review fact; never construct `Position`. Quarantine gate reads review queue.
- Files: `src/state/chain_reconciliation.py`, `src/state/portfolio.py`, `src/engine/cycle_runner.py`, `src/engine/monitor_refresh.py`, `src/execution/exit_triggers.py`, `src/control/control_plane.py`.

### Finding 4 — P1 `get_open_positions(chain_view=...)` mutates economics without canonical event [CONFIRMED BUG]

- Mutates `pos.shares`, `pos.entry_price`, `pos.chain_state = "synced"` from a chain view. No `position_events` row.
- Violates "append canonical event before projection mutation" law.
- Fix: remove mutation branch; return overlay tuple or use canonical reconciliation path.
- Files: `src/state/portfolio.py`, `src/state/chain_reconciliation.py`, `src/engine/cycle_runtime.py`.

### Finding 5 — P1 Rescue path can convert aggregate chain balance into verified fill [LIKELY BUG]

- Pending rescue sets `entry_fill_verified=True`, `order_status="filled"`, `state="entered"`, fabricates `entered_at=now`, overwrites `entry_price`/`cost_basis_usd`/`size_usd`/`shares` from aggregate `avg_price/cost/size`.
- Aggregate balance ≠ trade fill fact. Fill time, submitted order identity, exact avg fill economics not proven.
- Fix: split `balance_observed` (degraded recovery, `VENUE_POSITION_OBSERVED_LINKED_TO_INTENT`, `training_eligible=false`) vs `fill_verified` (requires trade fact).
- Files: `src/state/chain_reconciliation.py`, `src/engine/lifecycle_events.py`, `src/state/venue_command_repo.py`, `src/execution/exchange_reconcile.py`, `src/execution/harvester.py`.

### Finding 6 — P2 Multiple lifecycle authorities [CONFIRMED COMPLEXITY DEBT]

- `LifecyclePhase`, `LifecycleState`, `ChainState`, `ExitState`, `order_status`, and `position_current.phase` all participate in lifecycle decisions. `phase_for_runtime_position()` derives phase from mutable strings.
- `exchange_reconcile.py` defines its own open-state sets; `cycle_runtime` has more.
- Fix: `position_current.phase` is sole authority. Runtime fields are derived adapter fields. Canonical event reducer owns phase writes.

### Finding 7 — P2 Two `ChainState` types share the same name [CONFIRMED COMPLEXITY DEBT]

- `src/state/chain_state.py.ChainState` (snapshot completeness) vs `src/contracts/semantic_types.py.ChainState` (per-position visibility). Imports ambiguous; refactors risky.
- Fix: rename to `ChainSnapshotCompleteness` and `VenueVisibilityStatus`. Keep alias for one release.

### Finding 8 — P2 `position_current` not yet a complete reconstructible projection [REVIEW_REQUIRED]

- Schema omits `exit_state`, `chain_shares`, `chain_verified_at`, `entered_at`, fill authority, order/fill provenance.
- Loader uses fallback reconstruction. Restart may lose retry state, chain recency, fill authority.
- Fix: expand schema or join command/fact tables via typed API. Add `filled_at`, `submitted_at`, `accepted_at`, `exit_state`, `fill_authority`, `chain_seen_at`, `chain_absence_at`, `chain_shares`, `source_fact_id`.

### Finding 9 — P2 Learning/rescue authority enforced by scanner antibodies, not typed APIs [CONFIRMED COMPLEXITY DEBT]

- `LEARNING_AUTHORITY_REQUIRED = "VERIFIED"` is a literal string scan, not type boundary.
- `resolve_rescue_authority()` may return `UNVERIFIED`; `_emit_rescue_event()` writes `causality_status="UNKNOWN"`.
- Fix: training writer accepts only `VerifiedTrainingExample` type. Rescue/audit rows separate type.

### Finding 10 — P3 Docs/source still teach unsafe "void immediately" model [CONFIRMED COMPLEXITY DEBT]

- README and `chain_reconciliation.py` module doc say "Local but NOT on chain → VOID immediately".
- Runtime correctly skips void under `CHAIN_UNKNOWN`, but doc encourages future agents to "restore" the unsafe semantics.
- Fix: doc cleanup. Every void mention must name `CHAIN_EMPTY` precondition.

## 5. Target architecture

Typed objects:
- `LocalIntent { decision_id, snapshot_id, position_id, market/condition/token, intended notional, submitted limit price, created_at }`
- `VenueCommand { command_id, intent_kind, state, idempotency_key, venue_order_id?, timestamps }`
- `VenueOrderFact { venue_order_id, order_state, accepted_at/observed_at, raw payload hash }`
- `VenueTradeFact { venue_trade_id, venue_order_id, fill_state, filled_size, avg_fill_price, observed_at, authority }`
- `VenuePositionFact { token_id, condition_id, size, avg_price, cost_basis, snapshot_id, snapshot_completeness }`
- `ChainSnapshotCompleteness { CHAIN_SYNCED, CHAIN_EMPTY, CHAIN_UNKNOWN }`
- `VenueVisibilityStatus { synced, local_only, chain_only, exit_pending_missing, review_required }`

Canonical events: `POSITION_OPEN_INTENT, ENTRY_ORDER_POSTED, ENTRY_ORDER_FILLED, VENUE_POSITION_OBSERVED, CHAIN_SIZE_CORRECTED, EXIT_INTENT_CREATED, EXIT_ORDER_FILLED, SETTLEMENT_RECORDED, REDEEM_REQUESTED, ADMIN_VOIDED, REVIEW_REQUIRED`.

After refactor, impossible:
- Chain-only fake `Position`.
- Arbitrary `Position.state` outside canonical adapter.
- `chain_verified_at` written on negative observation.
- Aggregate chain balance masquerading as verified fill.
- Direct projection mutation without event/fact append.
- Learning row from UNVERIFIED rescue facts.
- Confusion between chain snapshot completeness and per-position visibility.

## 6. PR sequence (per source §7)

| PR | Scope | Risk | Order |
|----|-------|------|-------|
| A | Invariant tests + field map | Zero runtime change | NOW |
| C0 | Split positive/absence chain timestamps | Low; CONFIRMED BUG fix | NOW |
| B | `ChainSnapshotCompleteness`/`VenueVisibilityStatus` rename + typed facts | Aliases keep compat | After A+C0 review |
| C | Reconciliation emits canonical facts, no fake `Position` | Medium; needs feature flag | After B |
| D | Projection rebuild + downstream consumers | Medium; restart-replay tests gate | After C |
| E | Legacy cleanup/deletion | Low; static grep gates | After D |
| F | Docs/topology/review-rule alignment | Doc-only | After E |

## 7. Final verdict

- **NOT** merely over-defended. Repo is fundamentally multi-authority.
- Correct architecture is partially present (`venue_commands`, `venue_order_facts`, `venue_trade_facts`, `position_events`, `position_current`) but does NOT yet dominate runtime behavior.
- Defensive code remains necessary because fake/sentinel positions, direct runtime mutations, dual-write paths, and string-derived lifecycle phase are still live.
- Smallest high-confidence code PR: **PR C0** (timestamp split) + **PR A** (invariant tests).
