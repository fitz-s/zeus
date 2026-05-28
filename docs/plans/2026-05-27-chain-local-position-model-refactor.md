# Plan: Chain/Local Position Model Refactor (Findings 1-10)

> Created: 2026-05-27 | Status: IN PROGRESS | Branch: claude/chain-local-refactor

## Goal

Eliminate multi-authority chain/local position model. Make `position_current.phase` the single durable lifecycle phase, `Position` a projection-only adapter, chain-only inventory typed `ChainOnlyFact` (never fake `Position`), and split positive chain observation from absence observation. Make whole class of bugs unconstructable.

## Context

Live-money trading system. Current main carries multiple overlapping conversion systems: mutable `Position`, canonical phase fold, per-cycle chain completeness, per-position chain visibility, exit substates, order status strings, `venue_commands`, `position_events`, `position_current`, legacy `trade_decisions`. Defensive code grows; primitives keep colliding.

10 findings from first-principles audit (5 P1 bugs, 4 P2 complexity debt, 1 P3 doc drift). Full text: `docs/plans/2026-05-27-chain-local-position-model-refactor-findings.md` (persisted alongside this plan in the same PR).

P0 live-money merge rule: single-purpose, self-describing, annotated tag. No bundling.

## Approach

Ship PR A (read-only invariant tests + field map) first — proves invariants, fails strict-xfail on known violations, zero runtime risk. Then PR C0 (split `chain_verified_at` into positive vs absence) — smallest behavior fix on a CONFIRMED BUG with clear blast radius. STOP for review before PR B (typed model rename) and PR C+ (reconciliation rewrite).

Sequence (per source doc §7):
- **PR A** — invariant tests + field map (this PR scope)
- **PR C0** — split positive/absence chain observation timestamps (this PR scope)
- PR B — `ChainSnapshotCompleteness` / `VenueVisibilityStatus` rename + typed facts (deferred)
- PR C — reconciliation emits canonical facts/events, no fake `Position` (deferred)
- PR D — projection rebuild, downstream consumers (deferred)
- PR E — legacy cleanup/deletion (deferred)
- PR F — docs/topology/review-rules alignment (deferred)

## Tasks

### PR A — Invariant tests + field map (this session)

- [ ] A1. Write field/authority map
  - File: `docs/plans/2026-05-27-position-field-authority-map.yaml`
  - What: Machine-readable map of every cited field/row from §2 — `field`, `physical_meaning`, `legit_producers`, `readers`, `schema_location`, `disposition`. Source: §2 table.

- [ ] A2. Invariant test — no fake `Position` reaches trading path
  - File: `tests/state/test_inv_no_fake_position_in_trading_path.py` (NEW)
  - What: Assert no `Position` with `trade_id` starting `CHAIN_ONLY_` or `quarantine_` is yielded by `portfolio.get_open_positions()` (excluding `chain_view` mutation branch which is itself slated for removal). Strict-xfail until PR D.

- [ ] A3. Invariant test — `chain_verified_at` never written on negative observation
  - File: `tests/state/test_inv_chain_verified_at_positive_only.py` (NEW)
  - What: Run reconcile with active local position absent from chain snapshot. Assert `pos.chain_verified_at` unchanged from pre-reconcile value. Strict-xfail (will pass after PR C0).

- [ ] A4. Invariant test — no arbitrary `Position.state` outside `LifecycleState` enum
  - File: `tests/state/test_inv_position_state_enum_closed.py` (NEW)
  - What: Static grep test — every literal assignment `pos.state = "..."` or `state="..."` in `src/state/` and `src/execution/` uses a value defined in `LifecycleState`. Strict-xfail on `quarantine_size_mismatch`.

- [ ] A5. Invariant test — no `get_open_positions(chain_view=...)` mutation
  - File: `tests/state/test_inv_get_open_positions_pure.py` (NEW)
  - What: Build `Position` with known `shares=10`, `entry_price=0.40`; pass `chain_view` with `size=7, avg_price=0.45`; assert `pos.shares == 10` and `pos.entry_price == 0.40` after call. Strict-xfail until PR D.

- [ ] A6. Invariant test — `quarantine_size_mismatch` produces canonical review phase
  - File: `tests/state/test_inv_size_mismatch_canonical_phase.py` (NEW)
  - What: Trigger size mismatch + canonical correction unavailable. Assert resulting `position_current.phase` is in `{QUARANTINED, REVIEW_REQUIRED}` and `state` not equal to literal `"quarantine_size_mismatch"`. Strict-xfail until PR C.

- [ ] A7. Run new tests, confirm strict-xfail behavior
  - Run: `python -m pytest tests/state/test_inv_*.py -v --tb=short`
  - Expected: All new tests either PASS or `XFAIL` (strict). No XPASS, no error.

- [ ] A8. Commit PR A
  - Title: `test(state): chain/local invariant tests + field-authority map (PR A — no runtime change)`
  - Body: cite findings 1-10, list strict-xfails with target PR per task.

### PR C0 — Split positive vs absence chain observation (this session)

- [ ] C0-1. Add absence-observation field to `Position`
  - File: `src/state/portfolio.py`
  - What: Add `last_chain_absence_observed_at: Optional[str] = None` to `Position` dataclass. `chain_verified_at` retains "positive observation only" semantics.

- [ ] C0-2. Update `chain_reconciliation` missing-from-chain branches
  - File: `src/state/chain_reconciliation.py`
  - What: In branches where local position is absent from chain snapshot (active local-only, pending exit missing), write `pos.last_chain_absence_observed_at = now_iso`. STOP writing `pos.chain_verified_at = now_iso` on absence. Positive-observation branches (token present in snapshot) keep writing `chain_verified_at`.

- [ ] C0-3. Update `classify_chain_state()` to read positive only
  - File: `src/state/chain_state.py`
  - What: Freshness gate consults `chain_verified_at` only when it represents positive observation. Document the contract in module docstring. No fallback to absence timestamp.

- [ ] C0-4. Update `lifecycle_events._projection_updated_at()`
  - File: `src/engine/lifecycle_events.py`
  - What: Remove `chain_verified_at` from projection-updated-at fallback chain. Use `updated_at` / `entered_at` only.

- [ ] C0-5. Update existing tests
  - Files: `tests/test_dt4_chain_three_state.py`, `tests/test_live_safety_invariants.py`
  - What: Add positive-vs-absence test. Flip A3 invariant from strict-xfail to pass.

- [ ] C0-6. Schema/migration
  - File: `src/state/db.py` or migration file
  - What: Add nullable `last_chain_absence_observed_at` column to projection if persistence needed. If event-payload-only, document and skip DB change.

- [ ] C0-7. Run full state test suite
  - Run: `python -m pytest tests/state/ tests/test_dt4_chain_three_state.py tests/test_live_safety_invariants.py -v`
  - Expected: all pass; A3 xfail flipped to pass.

- [ ] C0-8. Commit PR C0
  - Title: `fix(state)-P1: split positive chain observation from absence (Finding 1)`
  - Body: cite finding 1 (P1 timestamp collision). Single-purpose live-money merge.

### STOP — review gate

- [ ] R1. Open PR A draft, request operator review
- [ ] R2. Open PR C0 draft, request operator review
- [ ] R3. Operator decides whether to proceed to PR B (typed model rename) in next session

## Risks / Open Questions

- **R-1**: A2 fake-`Position` test may collide with existing `is_quarantine_placeholder` filter semantics. If pre-existing tests already catch this category, A2 becomes a strict-xfail of zero value. Mitigation: read `tests/state/` for existing coverage before authoring; if duplicate, skip A2.

- **R-2**: C0 changes the meaning of `chain_verified_at` on existing legacy rows. Migration question: do we backfill `last_chain_absence_observed_at` from polluted `chain_verified_at`? Default plan: leave legacy rows alone; new writes are clean; classifier conservatively treats legacy `chain_verified_at` after a `chain_state IN ('local_only','exit_pending_missing')` event as untrusted. Documented in commit body.

- **R-3**: C0-3 classifier change may cause more `CHAIN_UNKNOWN` classifications during cutover. This is fail-safe (no void) but may dampen entries/exits briefly. Acceptable given P1 bug severity.

- **R-4**: Test naming convention — `test_inv_*` per `tests/state/`. Verify before commit.

- **R-5**: Findings doc not yet written as standalone file; only in conversation transcript. Persist to `docs/plans/2026-05-27-chain-local-position-model-refactor-findings.md` for future sessions.

## Out of scope (deferred to PR B+)

- Renaming dual `ChainState` enums (Finding 7).
- Removing fake chain-only `Position` constructor (Finding 3).
- Replacing aggregate-balance-as-fill rescue path (Finding 5).
- Removing `get_open_positions(chain_view=)` mutation (Finding 4).
- Expanding `position_current` schema (Finding 8).
- Typed learning API (Finding 9).
- Doc/README cleanup (Finding 10).

Each becomes a separate single-purpose PR per source doc §7.
