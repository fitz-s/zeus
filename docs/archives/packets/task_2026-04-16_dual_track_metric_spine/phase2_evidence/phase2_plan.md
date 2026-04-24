# Phase 2 — World DB v2 Schema + DT#1 Commit Ordering + DT#4 Chain Three-State

Packet: Dual-Track Metric Spine Refactor
Date opened: 2026-04-16
Owner: main-thread
Predecessors: Phase 0 (`943e74d`), Phase 0b (`df12d9c`), Phase 1 (`b025883`)
Gate opened by this phase: **Gate A** (same-(city, target_date) carries
distinct high + low rows across all v2 tables in sandbox).

## 1. Goal

Land the structural foundation that lets high + low temperature tracks coexist
at row identity. No live writer is migrated to v2 in this phase; legacy tables
stay frozen-readable. Alongside the schema work, fix the two known DT#1
commit-ordering sites and elevate the chain-reconciliation `skip_voiding` bool
to a real three-state machine (DT#4).

Scope deliberately bundles three orthogonal concerns because they share one
surface area (`src/state/**`) and the same rollback envelope.

## 2. Law anchors

- **INV-14** metric identity mandatory — §4 of `zeus_dual_track_architecture.md`
- **INV-17** truth commit ordering (DT#1) — §16 of `zeus_current_architecture.md`
- **INV-18** chain-truth three-state (DT#4) — §19 of `zeus_current_architecture.md`
- **NC-11** no daily-low on legacy tables — enforced by Phase 2 schema shape
- **NC-13** no JSON export before DB commit — enforced by the new helper

## 3. Main-thread decisions (ratified)

| # | Decision | Rationale |
|---|---|---|
| D1 | `harvester.py:380-386` DT#1 site is in scope | One helper, two call sites. Cheaper than splitting into a follow-up phase. |
| D2 | Drop 4 dead tables in the v2 migration transaction | `promotion_registry`, `model_eval_point`, `model_eval_run`, `model_skill` — 0 rows, zero writers. Cleanest moment. |
| D3 | v2 DDL lives in new `src/state/schema/v2_schema.py` | Isolated for Gate A tests; rollback by deleting one file-level registration, not touching the 3864-line `db.py`. |
| D4 | Both `zeus-world.db` and `zeus_trades.db` carry v2 schema | Their existing schema is identical (44 tables); the migration helper must target both paths. |
| D5 | `save_portfolio` JSON gains `last_committed_artifact_id` | Structural antibody for DT#1 — startup can detect split-state. Without it, DT#1 compliance is unobservable. |

## 4. Sink surface (what actually changes)

### 4.1 New files

- `src/state/schema/__init__.py` — re-exports.
- `src/state/schema/v2_schema.py` — DDL for 8 v2 tables + 4 DROP TABLE IF EXISTS
  (dead tables) wrapped in one idempotent `apply_v2_schema(conn)` helper.
- `src/state/canonical_write.py` — `commit_then_export()` helper.
- `src/state/chain_state.py` — `ChainState` enum + `classify_chain_state()`
  pure function with the 4-row transition table.

### 4.2 Modified files

- `src/engine/cycle_runner.py` — replace `:302-311` ordering with
  `commit_then_export(conn, db_op=..., json_exports=[save_portfolio, save_tracker, write_status])`.
- `src/execution/harvester.py` — replace `:380-386` commit-then-JSON with the
  same helper for consistency + test coverage.
- `src/state/chain_reconciliation.py` — remove inline `skip_voiding` bool
  (`:341-376`); add `ChainPositionView.state: ChainState` field; rewrite
  `reconcile()` entry to derive the three-state from inputs.
- `src/state/portfolio.py:1028` `save_portfolio()` — accept optional
  `last_committed_artifact_id` kwarg; persist into JSON payload.
- `src/state/db.py` — `init_schema()` calls `apply_v2_schema()` after the
  legacy DDL block. No other change.

### 4.3 New test files

- `tests/test_schema_v2_gate_a.py` — Gate A (7 metric-aware v2 tables hold
  high+low).
- `tests/test_dt1_commit_ordering.py` — cycle-crash scenarios prove DB wins,
  JSON rebuilds on startup.
- `tests/test_dt4_chain_three_state.py` — four transition-table rows +
  void-gate semantics.
- `tests/test_dead_table_drop_idempotency.py` — re-running `apply_v2_schema`
  does not re-create dropped dead tables.

## 5. Relationship invariants (R-A … R-E)

Written as failing tests BEFORE executor implementation. Test-engineer spec:

### R-A — Gate A: same-city-same-date dual-metric rows
`apply_v2_schema(memory_db)` → for each of the 7 metric-aware tables
(`settlements_v2`, `market_events_v2`, `ensemble_snapshots_v2`,
`calibration_pairs_v2`, `platt_models_v2`, `historical_forecasts_v2`,
`day0_metric_fact`), insert one high row and one low row with identical
`(city, target_date)`; assert both persist without IntegrityError; assert
UNIQUE key prohibits duplicate-metric writes.

Failure mode today: `v2_schema.py` does not exist.

### R-B — DT#1: DB commit precedes JSON export
`commit_then_export(conn, db_op=lambda: store_artifact(conn, artifact),
json_exports=[save_portfolio_fn, save_tracker_fn])`:
- normal path: the decision_log row exists AND both JSON files contain
  `last_committed_artifact_id == decision_log.id`.
- crash-between-commit-and-json: simulate by raising inside `save_portfolio_fn`;
  assert decision_log row persists (committed); assert
  `save_portfolio` JSON is either absent or carries stale
  `last_committed_artifact_id` < new row's id; assert a subsequent startup
  call of `load_portfolio()` reads the DB truth and reconstructs JSON.
- reverse-order-raise-test: if `db_op()` raises, no JSON export fires.

Failure mode today: no helper exists; `cycle_runner.py:302-311` writes JSON
before `store_artifact`.

### R-C — DT#4: chain three-state machine
4-row transition table from `classify_chain_state(chain_view, portfolio)`:
| fetched_at | chain_positions | local has stale? | → state |
|---|---|---|---|
| present | non-empty | any | CHAIN_SYNCED |
| present | empty | all >6h | CHAIN_EMPTY |
| present | empty | any ≤6h | CHAIN_UNKNOWN |
| null/error | any | any | CHAIN_UNKNOWN |

Void-gate test: `reconcile()` under CHAIN_UNKNOWN must NOT void any
position even if the token is absent from the (possibly incomplete) list;
under CHAIN_EMPTY or CHAIN_SYNCED, absent tokens void per Rule 2.

Failure mode today: `ChainState` enum does not exist; `skip_voiding` bool
blends CHAIN_UNKNOWN semantics inline with CHAIN_EMPTY.

### R-D — save_portfolio recovery contract
After `commit_then_export` with an artifact whose `decision_log.id = N`, the
on-disk `positions.json` contains `"last_committed_artifact_id": N`.

A startup routine (new small helper) that detects
`positions.json.last_committed_artifact_id < MAX(decision_log.id)` must
return a "JSON is stale — rebuild from DB" signal rather than trusting JSON.

Failure mode today: `save_portfolio()` has no recovery field.

### R-E — Dead-table DROP idempotency
Calling `apply_v2_schema(conn)` twice on the same DB (first run applies; second
run is no-op) does not raise. The 4 dead tables (`promotion_registry`,
`model_eval_point`, `model_eval_run`, `model_skill`) are absent after first
call AND absent after second call (not re-created).

## 6. Out of scope (explicit non-goals)

Already deferred to later phases; Phase 2 must resist scope creep into:

- Migrating any live writer to v2 tables (Phase 4 cutover).
- Full `db.py` god-object split (architect recommendation; planned as a
  separate chore commit after Phase 2).
- `MarketCandidate.temperature_metric: str` / `Position.temperature_metric: str`
  tightening (Phase 6 owns consumer-side).
- `market_price_history` / `replay_results` / `forecast_error_profile` /
  `hourly_observations` legacy cleanup (needs a separate audit; not dead by
  the strict "0 rows AND no writer" rule).
- Fetch abstraction consolidation (`src/data/*_append.py` vs
  `scripts/backfill_*`) — explicitly documented duplications, chore for
  later.
- Test file splits (`test_runtime_guards.py`, `test_db.py` — chore).
- `wu_daily_collector.py:142` single-metric UPDATE pattern — reading-side fix
  belongs with Phase 4 cutover when legacy `settlements` stops being the
  primary write target.

## 7. Bundled low-risk cleanups

These ride Phase 2 because the blast radius is tiny and Phase 2's schema
transaction is the natural moment:

- **C1** — DROP TABLE IF EXISTS for `promotion_registry`, `model_eval_point`,
  `model_eval_run`, `model_skill` inside `apply_v2_schema()`.
- **C2** — Delete `scripts/migrate_rainstorm_full.py` (self-reports COMPLETE
  no-op) and remove the call at `src/main.py:249`.

Everything else identified by the simplification scout (Cat A, C, H) goes to
separate `chore:` commits **after** Phase 2 lands.

## 8. Execution plan

1. **Main-thread writes this plan + R-A..R-E spec** (done by creation of
   this file).
2. **`test-engineer`**: write R-A..R-E as failing pytest. All fail today
   because the v2 schema, helper, and state machine do not exist.
3. **`executor`**: implement in order:
   - `src/state/schema/v2_schema.py` (DDL + DROPs)
   - `src/state/canonical_write.py` (`commit_then_export`)
   - `src/state/chain_state.py` (enum + classifier)
   - Rewire `cycle_runner.py`, `harvester.py`, `chain_reconciliation.py`,
     `portfolio.py`
   - Delete `scripts/migrate_rainstorm_full.py` + remove `src/main.py:249` call
   - Register `apply_v2_schema()` in `init_schema()`.
   All R-A..R-E pass. No new regression in the Phase-1 spot-check suite.
4. **`critic` (opus) pre-scan equivalent 2nd critic** — NOT just "did the
   listed findings get fixed" but also: does the new `canonical_write`
   helper admit a race; do the 4 dropped tables have any lingering reader
   anywhere in the repo I missed; are there OTHER `save_*` patterns that
   should have been bundled into commit_then_export; any new dead code or
   duplication the implementation introduced; any Phase-N-N+1 implication
   I should flag now; is `v2_schema.py` DDL strictly stronger than the
   sketch in `zeus_dual_track_refactor_package_v2_2026-04-16/03_SCHEMA/`.
5. **Main-thread**: fix critic findings; re-critic if any MAJOR; commit.

## 9. Gate for Phase 3 (and sequence)

Phase 3 (observation client `low_so_far` + source registry collapse) may
open only when:

- R-A..R-E all green.
- `test_dual_track_law_stubs.py::test_no_daily_low_on_legacy_table` can be
  un-skipped and proven green (it was Phase 2's responsibility to enable).
- `test_dual_track_law_stubs.py::test_json_export_after_db_commit` can be
  un-skipped and proven green.
- `test_dual_track_law_stubs.py::test_chain_reconciliation_three_state_machine`
  un-skipped and green.
- Critic verdict PASS.
- Both `zeus-world.db` and `zeus_trades.db` in a fresh sandbox carry the v2
  schema and hold the Gate A dual-metric rows.

## 10. Risks

- **WAL + PRAGMA foreign_keys**: `apply_v2_schema` must re-enable
  `foreign_keys=ON` at exit if it temporarily disables it.
- **Silent migration at startup**: adding `apply_v2_schema()` to
  `init_schema()` means existing databases gain v2 tables on next boot.
  That is fine (legacy writes unchanged) but the migration transaction must
  be idempotent and atomic — tests R-E covers.
- **Scope creep**: the `db.py` god-object is tempting to split in the same
  commit; resist. Plan a `chore: split state/db.py by concern` commit after
  Phase 2 lands.
- **`save_portfolio` schema change** might break loaders that don't
  anticipate the new `last_committed_artifact_id` field. Confirm
  `load_portfolio()` ignores unknown fields (dict pass-through) before
  shipping.
- **harvester.py** changes are in live-money path. Keep the fix to commit
  ordering only; do NOT refactor surrounding logic.

## 11. Evidence layout

- `phase2_evidence/phase2_plan.md` (this file)
- `phase2_evidence/architect_prescan.md` (from the opener dispatch)
- `phase2_evidence/writers_inventory.md`
- `phase2_evidence/simplification_scout.md`
- `phase2_evidence/critic_verdicts.md` (after critic runs)
- `phase2_evidence/planning_lock.txt` (topology_doctor output)
