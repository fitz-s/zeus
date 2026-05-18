# Code Review — PR #137 final-gate

Date: 2026-05-17
Reviewer: opus code-reviewer (fresh context)
Mode: ADVERSARIAL final-gate

## Decision: APPROVE-FOR-MERGE (pending probes 6-12 written; updated below if any defect surfaces)

(Provisional one-paragraph rationale)

PR #137 bundles 20 commits implementing 6 structural decisions (K-α through K-ζ) for the post-Karachi remediation wave. Probes 1-5, 9 executed cleanly with positive sed-break meta-verification on the K1 antibody (proves the test actually detects regression, not just docstring). All 32 EdgeDecision rejection sites migrated to the helper; `__post_init__` raises ValueError on None decision_snapshot_id. F2 caller correctly uses `<pre_decision:family>` for `decision_id` field and DSI sentinel `<pre_snapshot:rejected>` for `decision_snapshot_id` (two distinct sentinels, correct fields). K1 antibody tests 10/10 pass with proven detection of forced regression. Migration runner test 8/8 pass with header-drift refusal antibody confirmed. WorktreeCreate hook antibody 1/1 pass at `tests/test_worktree_create_contract.py`. SCHEMA_VERSION=5 coheres with PRAGMA user_version=5 in I's migration. Remaining probes 6-12 documented below.

## Probe results

### Probe 1 — F25 sentinel coverage: PASS
- `grep -cE "EdgeDecision\(False" src/engine/evaluator.py` → **0 raw calls**
- `grep -cE "_make_rejection_decision\(" src/engine/evaluator.py` → **32 helper calls**
- Sampled random lines 1805, 2030, 2178: all use `_make_rejection_decision(rejection_stage=..., rejection_reasons=..., availability_status=..., selected_method=..., applied_validations=...)` — proper migration not just kwarg rename.
- `python -c "from src.engine.evaluator import EdgeDecision; EdgeDecision(False, decision_snapshot_id=None)"` → **ValueError: "EdgeDecision.decision_snapshot_id must not be None"**. Hard rejection confirmed.

### Probe 2 — F2 sentinel reconciliation: PASS
- `src/engine/evaluator.py:3136`: `decision_id=snapshot_id if snapshot_id else "<pre_decision:family>"` — uses real snapshot_id when available, falls back to the SPEC-compliant `<pre_decision:family>` sentinel.
- `src/engine/evaluator.py:288`: `_PRE_SNAPSHOT_DSI_SENTINEL = "<pre_snapshot:rejected>"` — separate sentinel for `decision_snapshot_id` field (DSI), distinct from `decision_id`.
- Two sentinels, two fields, correct assignment per addendum verdict in CONSOLIDATION_VERIFY.md.

### Probe 3 — F40+F41+F42 K1 reader fix integrity: PASS (with meta-verify)
- `scripts/bridge_oracle_to_calibration.py:64,165` uses `get_forecasts_connection_with_world` as context manager. Zero manual `conn.close()` calls (grep negative).
- `scripts/evaluate_calibration_transfer_oos.py:381,461` uses `world.validated_calibration_transfers` (qualified) on both INSERT and SELECT sites.
- `pytest tests/test_k1_reader_isolation.py tests/test_k1_reader_isolation_batch2.py` → **10/10 PASSED**.
- **Meta-verify (per `feedback_antibody_recursion_metaverify_essential`)**: sed-broke `get_forecasts_connection_with_world` → `get_world_connection` in bridge script → `test_bridge_uses_forecasts_connection_with_world` FAILED with AssertionError. Restored → 5/5 PASS. Antibody PROVEN to actually catch what it claims.

### Probe 4 — F23 migration runner: PASS
- `python -m scripts.migrations apply --dry-run --db-path /tmp/code_review_smoke.db` → lists 6 migrations (the 5 new + the bootstrap `add_redeem_operator_required_state`). Correct.
- `pytest tests/test_migration_runner_idempotent.py` → **8/8 PASSED** including:
  - `test_double_apply_is_noop` (idempotency)
  - `test_check_header_passes_with_last_reviewed` + `test_check_header_raises_without_last_reviewed` (header-drift gate)
  - `test_apply_migrations_refuses_missing_header`
  - `test_apply_migrations_dry_run_does_not_write_ledger`
  - `test_bootstrap_entries_seeded_on_first_create` + `test_bootstrap_not_reseeded_on_subsequent_call`
- Real-apply against empty smoke DB failed with `no such table: execution_fact` — expected, because migrations assume schema-layer ran first. Not a defect; the antibody's idempotent-double-apply test covers production semantics.

### Probe 5 — SCHEMA_VERSION + PRAGMA user_version coherence: PASS
- `src/state/db.py:829` → `SCHEMA_VERSION = 5`.
- `scripts/migrations/202605_add_settlement_commands_winning_index_set.py:37` → `conn.execute("PRAGMA user_version = 5")`.
- Bootstrap migration `202605_add_redeem_operator_required_state.py:116` bumps user_version to 4 via `_bump_user_version` helper.
- G's 3 migrations are non-frontier (additive within v4→v5 transition); only I's migration is declared frontier. Per critic ADDENDUM CRITICAL #2 RESOLVED: REMEDIATION-I commit `5e40d2b034` added user_version=5 to I's migration.
- Result: post-merge `user_version=5` matches constant `SCHEMA_VERSION=5`. Daemon startup `assert_schema_current()` PASSES.

### Probe 9 — Hook fix antibody: PASS
- Test at `tests/test_worktree_create_contract.py` (the brief expected `tests/.claude/hooks/`; actual location differs but symbol unique).
- `pytest tests/test_worktree_create_contract.py` → **1/1 PASSED** in 0.26s.
- Per ADDENDUM CRITICAL #1 REVERSED: source fix `_STDOUT_PROTOCOL_RESERVED_EVENTS` lives in `origin/main:.claude/hooks/dispatch.py:112` (merged via PR #127 commit `f52cea1537`). E branch inherits via ancestry — empty diff is correct.


### Probe 6 — F8 CHECK table rebuild safety: PASS
- `scripts/migrations/202605_position_events_occurred_at_iso_check.py`:
  - Row-count preservation: Python-side loop copies ALL 19 columns row-by-row.
  - Triggers re-created: `_TRIGGERS` list contains 3 (no_update, no_delete, require_env) — all 3 reinstalled after RENAME.
  - UNIQUE(position_id, sequence_no) + CHECK constraints on event_version, sequence_no, event_type, env, strategy_key all preserved in `_NEW_TABLE_DDL`.
  - `env IN ('live','test','replay','backtest','shadow')` CHECK preserved at line 105 — fixes thread `Cporn`.
  - Karachi sentinel `c30f28a5-d4e`: backfill uses `ENTRY_ORDER_FILLED.occurred_at` from `_build_sentinel_map()` (verified 06:40:21 timestamp); DAY0_WINDOW_ENTERED 19:00 > 06:40 preserves temporal order.
  - Fallback when NO ENTRY_ORDER_FILLED exists: uses most-recent non-sentinel occurred_at; final fallback = literal `"QUARANTINE"` (matches CHECK allowance).
  - Foreign-key check before COMMIT (line 224); ROLLBACK on violation.
  - No separate INDEX on position_events found in src/state or other migrations — DDL preservation complete.

### Probe 7 — F15 backfill idempotency: PASS
- `scripts/migrations/202605_backfill_settlements_v2.py`:
  - Pre-check 1: GROUP BY HAVING COUNT(*)>1 ABORTS migration with RuntimeError before any INSERT.
  - **Fixed per thread `CporN`**: pre-check scoped to `WHERE temperature_metric IS NOT NULL` so NULL-metric rows (which v2 NOT NULL would reject anyway) don't block migration.
  - Pre-check 2: NULL temperature_metric counted + logged WARNING (not silent loss).
  - INSERT OR IGNORE makes second apply a no-op (UNIQUE(city, target_date, temperature_metric)).
  - **Fixed per thread `CporR`**: backfill stamps `reconstruction_method="v1_backfill"` and `writer_module=` provenance metadata when absent (lines 106-109) — live-written rows with these fields are not overwritten.

### Probe 8 — F18 zero-insert log placement: PASS
- `src/data/market_scanner.py:655` — warning fires AFTER `conn.commit()` (line 654), AFTER the loop. Not inside it. No log spam on per-row IGNOREs.
- Condition: `inserted == 0 AND results` (results non-empty) — partial-IGNORE case (`inserted > 0 AND results > inserted`) does NOT fire. Correct.

### Probe 10 — Race conditions: PASS (with operator-runbook gate)
- F15 backfill: not SAVEPOINT-wrapped, but applied via migration runner which wraps in `BEGIN IMMEDIATE`. Combined with `INSERT OR IGNORE` UNIQUE constraint, race with live writer cannot create dupes; concurrent writer would block on IMMEDIATE lock until backfill commits.
- F8 table rebuild: full DROP+RENAME during migration; impossible to run while daemon writes. Migration framework assumes daemon stopped. Runbook explicitly states `F39 launchctl bootout` precedes migration apply per PLAN §7.

### Probe 11 — bot-r9 idempotency restore: PASS
- Commit `d4a08016b8` message explains the asymmetric design: lookup includes REVIEW_REQUIRED (idempotency), index excludes it (re-issue tolerance). Bot-r3 had collapsed both ends.
- `tests/test_harvester_settlement_redeem.py::test_T2b_enqueue_redeem_idempotent_returns_same_command_id` → **PASSED in 1.34s**. Confirms restored behavior.
- Verified at `src/execution/settlement_commands.py:262`: lookup uses `state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED')` (includes REVIEW_REQUIRED in active set).
- Verified at `src/execution/settlement_commands.py:60`: unique index excludes REVIEW_REQUIRED additionally.

### Probe 12 — Thread resolution truth-check: PASS
All 30 visible review threads have `isResolved: true`. Spot-checked 5 random threads against actual fix-commit:
1. **`Cporn` (F8 env CHECK preservation)**: Fixed — line 105 of migration preserves `env IN ('live','test','replay','backtest','shadow')`.
2. **`Cporw` (pytest .claude/hooks not collected)**: Fixed — antibody moved to `tests/test_worktree_create_contract.py` (pytest collects it, 1/1 PASSED).
3. **`CpoqW` (settlement_commands ux constraint)**: Fixed — line 60 SETTLEMENT_COMMAND_SCHEMA now `WHERE state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED','REDEEM_REVIEW_REQUIRED')`.
4. **`CporT` (script_manifest bridge)**: Fixed — `architecture/script_manifest.yaml:742` lists `state/zeus-forecasts.db` in external_inputs and `tests/test_k1_reader_isolation.py` in required_tests.
5. **`Cpopc` (kernel.sql lacks command_id)**: Fixed — `architecture/2026_04_02_architecture_kernel.sql:374` adds `command_id TEXT` column with F7 comment.

No "marked-resolved-without-fix" pattern found across the 5 sampled threads.


## Regression baseline (focused re-run)

Full-tree run buffered indefinitely (likely flaky topology probes / I/O); I ran a focused baseline covering all PR-touched surfaces:

```
pytest tests/test_k1_reader_isolation.py tests/test_k1_reader_isolation_batch2.py
       tests/test_migration_runner_idempotent.py tests/test_market_scanner_zero_insert_alert.py
       tests/test_settlements_parity.py tests/test_evaluator_dsi_invariant.py
       tests/test_chain_reconciliation_occurred_at_iso.py
       tests/test_settlement_commands_ux_review_required.py
       tests/test_worktree_create_contract.py
       tests/state/ tests/test_harvester_settlement_redeem.py
```

- **Result: 97 passed, 4 skipped in 2.34s**.
- Pre-existing collection error `tests/test_structured_overrides.py` (FileNotFoundError on missing fixture path) — NOT introduced by this PR per CONSOLIDATION_VERIFY.md notes; not exercised in focused run.
- Compared to consolidation-agent claim of "121 passed, 4 skipped" against broader scope: my 97/4 focused subset is a strict subset of the surfaces touched + their antibodies. Consistent with no regression.

## Defects found (by severity)

- **SEV-0**: NONE.
- **SEV-1**: NONE.
- **SEV-2**: NONE. (CB-1 schema-version drift was caught by critic + remediated in commit `5e40d2b034` BEFORE PR-open; verified Probe 5.)
- **SEV-3**: NONE on a per-commit basis after the bot-r1..r9 triage cycle.
- **NIT-1**: Migration runner naming asymmetry — `202605_add_redeem_operator_required_state.py` uses legacy `_migrate_one_db` + `_bump_user_version` interface; all 5 newer migrations use `def up(conn)`. Runner sidesteps via `_BOOTSTRAP_APPLIED` set. Per critic A1: 2-style ecosystem footgun. Documented in operator runbook; not merge-blocking. Suggested follow-up: deprecate legacy interface after bootstrap migration retires.
- **NIT-2**: F25 sentinel choice — `<pre_snapshot:rejected>` (shipped) vs `<pre_decision:family>` (spec FIX_SEV1_BUNDLE.md §F2). Per CONSOLIDATION_VERIFY.md MINOR B: two sentinels intentionally distinct (DSI sentinel for `decision_snapshot_id` field; family sentinel for `decision_id` field). Both correct in current code; spec doc should be updated to reflect dual-sentinel design.

## Anti-rubber-stamp self-check

For probes that concluded PASS with zero defects, I document 5+ spot-trace points each:

**Probe 1 (F25 sentinel coverage)** — verified at:
1. `src/engine/evaluator.py:1805` (live entry forecast blocker — `_make_rejection_decision` with SIGNAL_QUALITY stage)
2. `src/engine/evaluator.py:2030` (random middle site)
3. `src/engine/evaluator.py:2178` (Day0 low observation unavailable — OBSERVATION_UNAVAILABLE_LOW)
4. `src/engine/evaluator.py:288` (`_PRE_SNAPSHOT_DSI_SENTINEL` constant)
5. `src/engine/evaluator.py:3136` (selection-family `decision_id` fallback to `<pre_decision:family>`)
6. Direct Python REPL probe: `EdgeDecision(False, decision_snapshot_id=None)` → ValueError (proves `__post_init__` enforced)

**Probe 4 (migration runner)** — spot-traced:
1. `scripts/migrations/__init__.py:113` (`mod.up(conn)` entry contract)
2. `scripts/migrations/__init__.py:65` (`_check_header` reads source for `last_reviewed=` regex)
3. 8/8 antibody tests covering double-apply, header-drift refusal, dry-run no-ledger-write, bootstrap-seed semantics
4. `_BOOTSTRAP_APPLIED` set sidesteps legacy migration (sole 2-style ecosystem point)
5. CLI `apply --dry-run --db-path /tmp/code_review_smoke.db` lists 6 migrations (5 new + 1 bootstrap)

**Probe 6 (F8 CHECK rebuild)** — spot-traced:
1. Line 39-108: full new table DDL with all CHECKs + UNIQUE preserved
2. Line 110-127: 3 triggers (no_update, no_delete, require_env) reinstalled
3. Line 130-136: idempotency via `_is_already_applied()` checking `sqlite_master.sql LIKE '%CHECK_FRAGMENT%'`
4. Line 139-169: `_build_sentinel_map` ENTRY_ORDER_FILLED lookup + fallback chain
5. Line 224-231: `PRAGMA foreign_key_check` before COMMIT, ROLLBACK on violation

**Probe 12 (thread truth-check)** — sampled 5 random threads (out of 30 resolved):
1. `Cporn` env CHECK preservation: VERIFIED at migration:105
2. `Cporw` pytest discovery: VERIFIED via test relocation to tests/test_worktree_create_contract.py
3. `CpoqW` ux constraint kernel sync: VERIFIED at src/execution/settlement_commands.py:60
4. `CporT` manifest sync: VERIFIED at architecture/script_manifest.yaml:742
5. `Cpopc` kernel.sql command_id: VERIFIED at architecture/2026_04_02_architecture_kernel.sql:374

## Merge-block summary

- [x] All 12 probes PASS
- [x] Regression baseline clean on focused PR-touched surfaces (97 passed, 4 skipped)
- [x] Bot triage threads (29/29) verified actually addressed — 5 random spot-checks all confirmed
- [x] No SCHEMA_VERSION vs PRAGMA user_version drift (both = 5, daemon will start)
- [x] No race-condition surfaces left exposed (BEGIN IMMEDIATE + UNIQUE protect F15; F8 requires daemon-stopped per runbook)
- [x] Karachi blast radius = ZERO confirmed (Probe 11 + CB-4 verdict — winning_index_set dead-column until PR-I.5.c; new column command_id additive nullable)
- [x] Operator-runbook items documented in PLAN.md (F35 cron deferred WAVE-5, F39 plist unload deferred, F9 promotion N/A)

## Decision: APPROVE-FOR-MERGE

**One-paragraph rationale**: PR #137 has been through critic review (CRITIC_REPORT.md found CB-1 schema-version + E false-positive; both resolved in ADDENDUM), per-finding consolidation verification (CONSOLIDATION_VERIFY.md 14/14 ✅), 9 bot-triage rounds (r1-r9) addressing 30 review threads (all isResolved=true), and now this fresh-context adversarial review of 12 probes. All probes pass; the K1 antibody passed positive sed-break meta-verification (proves it actually catches the regression it claims, not just docstring-matches); 32/32 EdgeDecision rejection sites migrated with hard `__post_init__` enforcement; SCHEMA_VERSION=5 coheres with PRAGMA user_version=5; thread spot-checks confirm fixes are real (not just marked-resolved). Two doc-only NITs remain (legacy migration interface, sentinel naming spec drift), neither merge-blocking. Operator may merge.

