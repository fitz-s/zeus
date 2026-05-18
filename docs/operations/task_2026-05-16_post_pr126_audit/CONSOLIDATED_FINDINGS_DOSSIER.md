# CONSOLIDATED FINDINGS DOSSIER — Zeus Deep Alignment Audit (Runs #1–#13)

**Created**: 2026-05-17 (Run #13)
**Authority**: This document is the **single source of truth** for every finding F1–F84 surfaced during the post-PR-126 audit campaign. Consult this file first; the per-run `RUN_*.md` narratives remain canonical for storytelling but no longer need to be read serially.

**Cross-document index**:
- Master index table (sortable): `FINDINGS_REFERENCE_v2.md`
- Per-run narrative: `RUN_7_findings.md` (initial), `RUN_8_findings.md` (F28–F31), `RUN_8_resolution_sweep.md` (closure ledger), `RUN_9_oracle_rate_shenzhen.md` (F32–F34), `RUN_10_silent_gap_archeology.md` (F35–F39), `RUN_11_f36_rootcause_and_q1q2q3.md` (Run #10 retraction + F40–F42), `RUN_12_f42_reader_sweep.md` (F43–F80), `RUN_13_pr137_ambiguous_alias_reverse.md` (F44–F50 close, F81–F84 new).
- Audit history: `.claude/skills/zeus-deep-alignment-audit/AUDIT_HISTORY.md`

**Numbering note**: F-numbers are globally unique going forward. v1 predecessor (`task_2026-05-16_deep_alignment_audit/`) uses a separate F-namespace and is FROZEN — cross-walk lives in `RUN_8_resolution_sweep.md`.

**Severity scale**: SEV-0 ship-blocker / silent corruption · SEV-1 high data-integrity · SEV-2 medium process · SEV-3 hygiene.

**Status values**: FIXED · FIXED-held · FIXED-by-PR-### · CLEAN (verified non-issue post-trace) · DEAD (no callers, retire) · OPEN · OPEN-acknowledged · OPEN-progress · NEW · DEFERRED · RETRACTED · SUPERSEDED-by-F##

**PR closure map (referenced throughout)**:
- **PR #114** (`eba80d2b9d`, merged 2026-05-14) — K1 forecast DB split; moved 7 forecast-class tables to `zeus-forecasts.db`, archived sources as `_archived_2026_05_11`
- **PR #126** (cascade liveness) — closes F14, F16
- **PR #130** (ref/authority docs)
- **PR #132 / PR #133** (db_writer_lock Phase 0/0.5/1, Track A.6)
- **PR #137** (merged 2026-05-17T23:15Z) — closes F2, F7, F8, F15, F18, F23, F25, F27, F29, F30, F40, F41, F42-batch-2, CB-1; defers F26

---

## SEV-0 findings (ship blockers / silent corruption)

### F14 — Redeem cascade liveness contract missing
- **Severity**: SEV-0
- **Status**: FIXED (PR #126)
- **Owner module**: `architecture/`
- **Discovery**: Run #4 — operator review of REDEEM flow found no documented contract for cascade liveness; settlement-to-redeem transitions could orphan positions silently.
- **Pre-context**: Pre-K1 settlement architecture had implicit liveness assumptions; Karachi-class outcomes started exposing edge cases.
- **Evidence**: Architecture doc review + traces of orphaned settlement_commands rows.
- **Resolution**: PR #126 introduced explicit cascade liveness contract + state-machine spec.
- **Residue**: None — verified clean in Run #7.
- **Audit trail**: Last verified Run #7. Commit ref: PR #126 (see git log).

### F16 — Redeem auto-execution gate missing operator path
- **Severity**: SEV-0
- **Status**: FIXED (PR #126)
- **Owner module**: `src/execution/settlement_commands.py`
- **Discovery**: Run #4 — auto-execution flow had no operator-review fork; mandatory REDEEM_OPERATOR_REQUIRED state didn't exist.
- **Pre-context**: Karachi-class small-dollar positions were transitioning straight to REDEEM without human review.
- **Evidence**: Flow trace showing auto-execution bypassing operator review.
- **Resolution**: PR #126 added REDEEM_OPERATOR_REQUIRED terminal state + UNIQUE index gating.
- **Residue**: F27/F29 surfaced post-PR-126 review gap (unique index didn't exclude blocking states); both closed by PR #137.
- **Audit trail**: Last verified Run #7. Commits: PR #126, PR #137 (`5e40d2b034`).

### F25 — Triple-NULL systemic snapshot-write failure (3 fact tables)
- **Severity**: SEV-0
- **Status**: FIXED-by-PR-137 (Strategy R)
- **Owner module**: `src/selection/, src/opportunity/, src/signal/`
- **Discovery**: Run #7 — `opportunity_fact.snapshot_id`, `probability_trace_fact.decision_snapshot_id`, `selection_hypothesis_fact.decision_id` all NULL at 1518/1518 (68% NULL hemorrhage).
- **Pre-context**: Decision Snapshot Invariant (DSI) was implicit; 31 early-rejection EdgeDecision constructors in `evaluator.evaluate_candidate` skipped snapshot stamping.
- **Evidence**: F2/F24 originally tracked NULL counts on selection_hypothesis_fact; Run #7 expanded to 3-table systemic failure with row-count proof.
- **Resolution**: PR #137 introduced `_make_rejection_decision(...)` helper that stamps `<pre_snapshot:rejected>` sentinel. `__post_init__` rejects `None` (frozen=True omitted because cycle_runtime.py:2584 mutates market_phase post-construction).
- **Residue**: Position dataclass missing `decision_id` field — filed as follow-up issue #27. Sentinel string format is convention, not type-enforced.
- **Audit trail**: Run #7 introduced, PR #137 closed. Tests: `tests/engine/test_evaluator_dsi_invariant.py`. Commit: PR #137.

## SEV-1 findings (high data-integrity)

### F1 — ZEUS_FORECASTS_DB legacy path
- **Severity**: SEV-1
- **Status**: FIXED
- **Owner module**: `src/state/db.py`
- **Discovery**: Run #1 — legacy hard-coded forecasts DB path not respecting K1 split contract.
- **Pre-context**: Pre-K1 split, forecasts table family lived in zeus-world.db. Code referenced legacy single-DB layout.
- **Evidence**: Grep for hard-coded paths in db.py.
- **Resolution**: Path resolved to env-configurable `ZEUS_FORECASTS_DB_PATH` constant.
- **Residue**: None.
- **Audit trail**: Last verified Run #7.

### F2 — decision_id NULL on selection_hypothesis_fact
- **Severity**: SEV-1
- **Status**: FIXED-by-PR-137 / SUPERSEDED-into-F25
- **Owner module**: `src/selection/...`
- **Discovery**: Run #1 — selection_hypothesis_fact.decision_id observed NULL.
- **Pre-context**: Lineage join key for decision-to-hypothesis traversal.
- **Evidence**: Row counts confirmed NULL prevalence growing across runs.
- **Resolution**: PR #137 — `_record_selection_family_facts` now forwards `decision_id`.
- **Residue**: Position dataclass missing `decision_id` (follow-up #27).
- **Audit trail**: Run #1 first seen; Run #6 expanded as F24 (accelerated 693→1518 NULLs); Run #7 reframed as F25 systemic. PR #137 fixed. Test: `tests/state/test_lineage_join_keys.py`.

### F3 — Unit-system co-mingling (°C / °F)
- **Severity**: SEV-1
- **Status**: OPEN
- **Owner module**: `src/signal/, src/calibration`
- **Discovery**: Run #2 — mixed temperature units passed between modules without type enforcement; risk of silent unit confusion in calibration math.
- **Pre-context**: Zeus operates on °C internally but ingests °F from some US data sources.
- **Evidence**: Code-trace of unit-handling boundaries; no type system enforces °C-only.
- **Resolution**: Not fixed. PR #137 did not address. Per Fitz Constraint #4, the right fix is a type-system that makes °C/°F mixing unwritable (NewType + TypeError on mix).
- **Residue**: OPEN — should be next-wave SEV-1 priority. No tests, no antibody.
- **Audit trail**: Run #2 first seen; last verified Run #5.

### F7 — order_intent / venue_command lineage
- **Severity**: SEV-1
- **Status**: FIXED-by-PR-137
- **Owner module**: `src/execution/`
- **Discovery**: Run #3 — execution_fact lacked command_id linkable invariant; venue_command rows could not be traced to originating order_intent.
- **Pre-context**: Lineage chain order_intent → venue_command → execution required for post-trade audit.
- **Evidence**: FK trace showed missing column on execution_fact.
- **Resolution**: PR #137 added `execution_fact.command_id` column with COALESCE preservation (set-once, no NULL-overwrite).
- **Residue**: None.
- **Audit trail**: Run #3 first seen; PR #137 closed. Test: `tests/state/test_execution_fact_command_id.py`.

### F8 — observations_v2 dual-write window (became position_events.occurred_at)
- **Severity**: SEV-2/SEV-1
- **Status**: FIXED-by-PR-137
- **Owner module**: `src/data/, src/state/db.py`
- **Discovery**: Run #3 — dual-write window identified during K1 prep; PR #137 reframed as position_events.occurred_at CHECK constraint.
- **Pre-context**: Migration windows that allow dual-write expose race-condition data loss.
- **Evidence**: Karachi `c30f28a5-d4e` had `unknown_entered_at` literal in occurred_at column.
- **Resolution**: PR #137 — `position_events.occurred_at` CHECK constraint disallows `unknown_entered_at`; 3 historical rows backfilled to ISO via `ENTRY_ORDER_FILLED.occurred_at` lookup.
- **Residue**: None.
- **Audit trail**: Run #3 first seen; PR #137 closed. Tests: `tests/state/test_position_events_check_constraint.py`, `tests/test_chain_reconciliation_occurred_at_iso.py`.

### F13 — settlement_commands ux index excludes only 2 terminals
- **Severity**: SEV-1
- **Status**: EXPANDED-into-F27/F29
- **Owner module**: `src/state/db.py`
- **Discovery**: Run #4 — UNIQUE INDEX `ux_settlement_commands_active_condition_asset` had narrow terminal-state exclusion.
- **Pre-context**: Index needs to exclude all terminal/blocking states to allow re-create after operator review.
- **Evidence**: Index DDL showed only 2 states excluded.
- **Resolution**: Scope reframed into F27 (REDEEM_OPERATOR_REQUIRED) + F29 (REDEEM_REVIEW_REQUIRED) — both closed by PR #137.
- **Residue**: None.
- **Audit trail**: Run #4 first seen; Run #7-#8 reframed; PR #137 closed.

### F15 — Chain reconciliation skip_voiding interplay (became settlements_v2 backfill)
- **Severity**: SEV-1
- **Status**: FIXED-by-PR-137
- **Owner module**: `src/state/chain_reconciliation.py`
- **Discovery**: Run #4 — skip_voiding logic interacted poorly with settlements_v2 row presence.
- **Pre-context**: settlements_v2 was missing 1583 rows that existed in v1.
- **Evidence**: Row-count delta v1 vs v2.
- **Resolution**: PR #137 — 1583-row backfill from settlements v1 → v2.
- **Residue**: None.
- **Audit trail**: Run #4 first seen; PR #137 closed. Test: `tests/test_settlements_parity.py`.

### F17 — forecasts.db user_version drift risk
- **Severity**: SEV-1
- **Status**: FIXED-held
- **Owner module**: `src/state/db.py`
- **Discovery**: Run #5 — schema-version pragma drift risk on forecasts DB.
- **Pre-context**: PRAGMA user_version drift causes silent schema/code mismatches.
- **Evidence**: Pragma value observed at 0 / unset.
- **Resolution**: Closed by guarded-set in PRAGMA contract.
- **Residue**: None; CB-1 (separate finding) further enforced.
- **Audit trail**: Run #5 first seen; last verified Run #7.

### F23 — Migration runner architecturally bare (no ledger)
- **Severity**: SEV-1
- **Status**: FIXED-by-PR-137
- **Owner module**: `scripts/migrations/`
- **Discovery**: Run #6 — migrations had no ledger/idempotency contract; double-apply risk.
- **Pre-context**: Operational discipline requires ledger of applied migrations.
- **Evidence**: scripts/migrations/ had no `__main__.py` runner.
- **Resolution**: PR #137 added `scripts/migrations/{__init__,__main__}.py` runner with idempotent ledger.
- **Residue**: F30 enforces last_reviewed header drift gate (integrated). F26 deferred (SQLITE allowlist dedup).
- **Audit trail**: Run #6 first seen; PR #137 closed. Test: `tests/test_migration_runner_idempotent.py`.

### F27 — REDEEM_OPERATOR_REQUIRED unique-index lockout (PR-126 review gap)
- **Severity**: SEV-1
- **Status**: FIXED-by-PR-137
- **Owner module**: `src/state/db.py + src/execution/settlement_commands.py`
- **Discovery**: Run #7 — UNIQUE INDEX did not exclude REDEEM_OPERATOR_REQUIRED, blocking re-create after operator review.
- **Pre-context**: PR #126 introduced REDEEM_OPERATOR_REQUIRED but did not update the unique-index exclusion list.
- **Evidence**: Index DDL did not exclude the new state.
- **Resolution**: PR #137 added REDEEM_OPERATOR_REQUIRED to active-row exclusion list.
- **Residue**: F29 sibling (REDEEM_REVIEW_REQUIRED) closed same PR.
- **Audit trail**: Run #7; PR #137. Test: `tests/test_settlement_commands_ux_review_required.py`.

### F32 — Oracle bridge writer not scheduled → runtime permanently MISSING for every city
- **Severity**: SEV-1
- **Status**: OPEN (script fixed in F40, cron entry STILL MISSING)
- **Owner module**: `scripts/bridge_oracle_to_calibration.py + cron`
- **Discovery**: Run #9 — Shenzhen pending-fill investigation surfaced that oracle_rate field was permanently MISSING for every city.
- **Pre-context**: bridge_oracle_to_calibration.py is responsible for materializing oracle truth into calibration lake.
- **Evidence**: Cron inventory showed no scheduled invocation of the bridge script.
- **Resolution**: PR #137 fixed the SCRIPT (F40 — K1 repoint). The CRON entry remains absent. F35 (Run #10) confirmed never-ran-in-prod.
- **Residue**: OPEN — operator must add cron/launchd entry. Currently emitting {} silently when manually invoked from a fixed binary.
- **Audit trail**: Run #9 first seen; Run #10 confirmed via launchd inventory; F40/F35 sibling. PR #137 fixed script half.

### F35 — bridge_oracle_to_calibration unscheduled (operator-memory archeology)
- **Severity**: SEV-1
- **Status**: OPEN
- **Owner module**: `scripts/bridge_oracle_to_calibration.py + cron`
- **Discovery**: Run #10 — archeological dig into operator memory confirmed F32 is real and NEVER-RAN-IN-PROD.
- **Pre-context**: Sibling/confirmation of F32.
- **Evidence**: Operator memory + launchd plist inventory.
- **Resolution**: Same as F32. PR #137 fixed script; cron entry still pending.
- **Residue**: OPEN.
- **Audit trail**: Run #10.

### F40 — scripts/bridge_oracle_to_calibration.py hardcodes state/zeus-world.db after PR #114 K1 split
- **Severity**: SEV-1
- **Status**: FIXED-by-PR-137
- **Owner module**: `scripts/bridge_oracle_to_calibration.py:71`
- **Discovery**: Run #11 — script hardcoded zeus-world.db; would emit {} even when scheduled because settlements moved to forecasts.db.
- **Pre-context**: PR #114 K1 split (eba80d2b9d, 2026-05-14) migrated 7 forecast tables to zeus-forecasts.db but did not sweep readers.
- **Evidence**: Source line 71 with hardcoded path; settlements row count: 0 (world) / 5599 (forecasts).
- **Resolution**: PR #137 repointed to `get_forecasts_connection_with_world()` helper.
- **Residue**: F32/F35 cron-entry still OPEN.
- **Audit trail**: Run #11; PR #137. Test: `tests/test_k1_reader_isolation.py`.

### F41 — scripts/evaluate_calibration_transfer_oos.py uses get_world_connection; reads dead calibration_pairs_v2
- **Severity**: SEV-1
- **Status**: FIXED-by-PR-137
- **Owner module**: `scripts/evaluate_calibration_transfer_oos.py:684`
- **Discovery**: Run #11 — script read calibration_pairs_v2 from world DB (0 rows post-K1; 91M rows in forecasts DB).
- **Pre-context**: PR #114 K1 split missed this reader.
- **Evidence**: Live log regression 2026-05-10 → 2026-05-17 confirms zero-iteration runs.
- **Resolution**: PR #137 repointed + `world.*` table qualification.
- **Residue**: None.
- **Audit trail**: Run #11; PR #137. Test: `tests/test_k1_reader_isolation.py`.

### F42 — META: PR #114 K1 split migrated writers but did NOT sweep ~30 reader callers
- **Severity**: SEV-1 META
- **Status**: PARTIALLY-CLOSED-by-PR-137
- **Owner module**: `src/, scripts/ (~30 callers)`
- **Discovery**: Run #11 — meta finding: F40+F41 confirmed real; ~10 more files suspect.
- **Pre-context**: K1 split shipped without reader-side audit.
- **Evidence**: PR #114 commit diff vs current src/ + scripts/ greps for migrated table reads on world conn.
- **Resolution**: Run #12 mechanical sweep: 37 broken / 40 OK / 26 ETL-transitional / 11 src/ live-runtime CLEAN. PR #137 closed 4 scripts (batch-2) + verified 11 src/. F44–F50 src/ AMBIGUOUS re-traced clean in Run #13 (F46/F48 are confirmed-open, the rest CLEAN/DEAD). 17 scripts residue (F51–F62, F64–F70, F73–F75, F77).
- **Residue**: 17 scripts/ DEAD-READ residue (mostly backfill/one-shot tools, not Karachi 5/17 hot-path). F46 (cycle_runtime market_events_v2 dual-write upstream) is hot — see F81.
- **Audit trail**: Run #11 first seen; Run #12 decomposed into F43–F80; Run #13 closed F44/F45/F47/F49/F50 src/. PR #137 closed F40/F41/+4 scripts.

### F46 — src/state/db.py:3614 _insert_forward_market_event — caller-trace AMBIGUOUS, dual-write upstream
- **Severity**: SEV-2 → SEV-1 (escalated by F81)
- **Status**: OPEN-CONFIRMED (Run #13)
- **Owner module**: `src/state/db.py:3614`
- **Discovery**: Run #12 — AMBIGUOUS reader of market_events_v2 via conn-param.
- **Pre-context**: K1 migrated market_events_v2; cycle_runtime.py:2364 calls log_forward_market_substrate which calls _insert_forward_market_event.
- **Evidence**: Run #13 caller trace: db.py:3949 → log_forward_market_substrate (def db.py:3830) → cycle_runtime.py:2366. cycle_runtime opens no conn — receives from main.py:1372 boot. Disk evidence: market_events_v2 has rows in ALL THREE dbs (world 2112, forecasts 10552, zeus_trades 7964) — DUAL/TRIPLE-WRITE LEAK = F81.
- **Resolution**: Not fixed. PR #137 touched db.py (23 additions) but did not patch the upstream dual-write path.
- **Residue**: Root cause for F81. Needs cycle_runtime conn-source audit + writer consolidation.
- **Audit trail**: Run #12; Run #13 caller-trace + F81 escalation.

### F48 — src/engine/monitor_refresh.py:1041 _recent_settlement_deltas — DEAD-READ silent-zero
- **Severity**: SEV-2
- **Status**: OPEN-CONFIRMED (Run #13)
- **Owner module**: `src/engine/monitor_refresh.py:1041`
- **Discovery**: Run #12 — AMBIGUOUS reader of settlements via conn-param.
- **Pre-context**: Monitor lane reads settlements for persistence-anomaly detection.
- **Evidence**: Caller chain: cycle_runtime.py:2015 refresh_position(conn) — conn is position-lane (world). settlements K1-migrated (5599 forecasts / 0 world).
- **Resolution**: Not fixed.
- **Residue**: Silent-zero deltas → `_check_persistence_anomaly` doesn't fire on real persistence drift. Degraded anomaly detection, not money-loss.
- **Audit trail**: Run #12; Run #13 caller-trace confirmed DEAD-READ.

### F63 — scripts/data_chain_monitor.sh:26 raw sqlite3.connect(zeus-world.db) on source_run → permanently 0
- **Severity**: SEV-1
- **Status**: OPEN (HOT — observability blindness)
- **Owner module**: `scripts/data_chain_monitor.sh:26`
- **Discovery**: Run #12 — observability script hardcoded world DB for source_run.
- **Pre-context**: Post-K1, source_run lives in forecasts.db (16 rows) / world has 0.
- **Evidence**: Script L26-29 raw sqlite3.connect.
- **Resolution**: Not fixed.
- **Residue**: HOT — operator monitoring is blind to source_run health.
- **Audit trail**: Run #12.

### F71 — scripts/check_forecast_live_ready.py:169 source_run readiness gate vacuous-pass risk
- **Severity**: SEV-1
- **Status**: OPEN (HOT)
- **Owner module**: `scripts/check_forecast_live_ready.py:169`
- **Discovery**: Run #12 — readiness gate likely reads world (caller-conn not verified); silent vacuous-pass risk.
- **Pre-context**: Live boot probe — if it reads dead world.source_run (always 0), it would pass-by-default and miss real source-run failures.
- **Evidence**: Source line 169.
- **Resolution**: Not fixed.
- **Residue**: HOT — live-readiness gate may be silently green.
- **Audit trail**: Run #12.

### F81 — K1 dual-write LEAK to zeus-world.db post-PR-114
- **Severity**: SEV-1
- **Status**: NEW (Run #13)
- **Owner module**: `src/data/market_scanner.py + cycle_runtime upstream`
- **Discovery**: Run #13 reverse silent-gap scan.
- **Pre-context**: PR #114 K1 split moved 7 tables to forecasts.db on 2026-05-11. zeus-world.db should be quiescent for those tables post-split.
- **Evidence**: `sqlite3 state/zeus-world.db` shows `market_events_v2` has 2112 rows with timestamps **2026-05-12 08:30 → 2026-05-13 16:45** (POST-K1) and `observations` has 145 rows (with DIFFERENT schema — no recorded_at — F83 sibling). Active writer continuing to drip into world.db post-K1.
- **Resolution**: Not fixed.
- **Residue**: Root suspect: `src/data/market_scanner.py:610` (F22 OPEN — raw `sqlite3.connect` to world for market_events_v2). Triage script in RUN_13 §Task 4.
- **Audit trail**: Run #13.

### F82 — K1 triple-write FAN-OUT to zeus_trades.db
- **Severity**: SEV-1
- **Status**: NEW (Run #13)
- **Owner module**: `scripts/ + src/ raw sqlite3.connect to zeus_trades.db`
- **Discovery**: Run #13 reverse silent-gap scan.
- **Pre-context**: zeus_trades.db is a 610MB third DB not in the K1 contract (world+forecasts only).
- **Evidence**: `sqlite3 state/zeus_trades.db` shows `market_events_v2` 7964 rows, timestamps **2026-05-02 → 2026-05-17T14:59** (HOURS-OLD, live writer active).
- **Resolution**: Not fixed.
- **Residue**: Provenance unknown. Could be stale daemon copy or unintended writer. Triage: grep `zeus_trades.db` in src/+scripts/+launchd plists.
- **Audit trail**: Run #13.

### F83 — Schema drift between world.db and forecasts.db observations tables
- **Severity**: SEV-2
- **Status**: NEW (Run #13)
- **Owner module**: `state/zeus-world.db schema vs state/zeus-forecasts.db schema`
- **Discovery**: Run #13 — investigating F81 evidence.
- **Pre-context**: K1 migration should have left world.db `observations` empty or archived; instead it has 145 stale rows with a DIFFERENT (older) schema.
- **Evidence**: `SELECT recorded_at FROM observations` errors on world.db (`no such column: recorded_at`) but succeeds on forecasts.db.
- **Resolution**: Not fixed.
- **Residue**: Schema drift means even if F81 dual-write is patched, the 145 stale rows + schema fork must be reconciled.
- **Audit trail**: Run #13.

### F84 — Backfill scripts call daily_observation_writer — caller conn-source not verified
- **Severity**: SEV-3
- **Status**: NEW (Run #13)
- **Owner module**: `scripts/backfill_hko_xml.py + scripts/backfill_hko_daily.py + scripts/backfill_wu_daily_all.py`
- **Discovery**: Run #13 — F50 caller trace.
- **Pre-context**: 3 backfill scripts import `write_daily_observation_with_revision` from `src/data/daily_observation_writer.py`.
- **Evidence**: grep `write_daily_observation_with_revision` returned 3 backfill callers.
- **Resolution**: Not fixed.
- **Residue**: Sibling of F60 (backfill_wu_daily_all already DEAD-READ). The HKO backfills similar status.
- **Audit trail**: Run #13.

## SEV-2 findings (medium — process / data discipline)

For full SEV-2 / SEV-3 inventory see `FINDINGS_REFERENCE_v2.md` master index table (F4–F12 closures, F18/F19/F20/F21/F22 OPEN, F26 DEFERRED, F28 META, F31 reader-audit, F37 cal-transfer-eval, F43, F44–F50 src/ residue ledger above, F51–F80 scripts/ DEAD-READ inventory).

### Quick-reference: PR #137 closures vs residue

**FIXED-by-PR-137**: F2, F7, F8, F15, F18, F23, F25, F27, F29, F30, F40, F41, F42-batch-2, CB-1 — **14 findings**.

**DEFERRED**: F26.

**RETRACTED**: F36, F38 (Run #11 invalid-provenance — Run #10 queried wrong DB).

**SUPERSEDED**: F24 → F25.

**FIXED earlier**: F1, F4, F14, F16, F17.

**CLEAN-by-Run-13-trace**: F44, F45, F47, F50, F46-partial-but-escalates-to-F81, F48-confirmed-bad.

**DEAD (no callers, retire)**: F49.

**OPEN (need cron/operator/structural decision)**:
- SEV-1: F3 (unit-system), F32 (oracle cron), F35 (sibling), F46 (cycle_runtime upstream of F81), F63 (data_chain_monitor world), F71 (live-ready gate world), **F81** (K1 dual-write leak), **F82** (zeus_trades triple-write), **F83** (schema drift).
- SEV-2: F5, F6, F9, F10, F11, F12, F19, F20, F21, F22, F28, F31, F33, F34, F37, F39, F43, F48, F51–F62, F64–F70, F73–F75, F77, F84.
- SEV-3: F68, F76, F78, F79, F80.

### Recommended next-wave priorities (Run #14 / next PR)
1. **F81 + F82 + F22** (K1 dual/triple-write leak) — bundle as one structural decision: consolidate `market_events_v2` writers to single forecasts-only writer; quiesce world.db + zeus_trades.db writes; add post-write antibody asserting `row_count_world == row_count_zeus_trades == 0` post-migration.
2. **F32 / F35** Oracle bridge cron — operator action (add launchd plist).
3. **F46** cycle_runtime conn-source audit (root of F81).
4. **F3** unit-system NewType migration (Fitz #4: make °C/°F mix unwritable).
5. **F48** monitor_refresh settlements conn repoint.
6. **F63 / F71** observability scripts world→forecasts repoint (HOT — operator-visibility).
7. **scripts/ batch-3** — close F51–F62 + F64–F70 + F73–F75 + F77 via same `get_forecasts_connection_with_world()` helper applied in PR #137 batch-2.


## Run #14 status delta (2026-05-17, branch fix/wave-2-lineage-and-k1-cleanup-2026-05-17 @ b973ece)

### New findings
- **F87 SEV-1 HOT**: `com.zeus.forecast-live` daemon DOWN (exit 1). Karachi forecast ingestion broken. **Fix-and-restart BEFORE next Karachi monitor tick.**
- **F90 SEV-1**: cron source-of-truth ambiguity — `cron/jobs.json` 82KB vs live crontab 2 lines. Build `tools/ops/cron_reconcile.py`; decide source-of-truth.
- F85, F86, F91, F92 SEV-2: daemon log inversion, SIGTERM forensic gap, heartbeat consumer unverified, silent auth-fallback.
- F88, F89 SEV-3: hygiene (plist naming + split supervision topology).

### Updated status
| F# | Prior | Now (Run #14) | Reason |
|---|---|---|---|
| F22 + F81 + F82 | OPEN (triple-write theory) | **DUAL-WRITE-CONFIRMED + DECISION-A2** | re-counted: forecasts (+638/24h) + trades (+1276/24h) live; world.db DEAD. Verdict A2. See `RUN_14_track_A_market_events_decision.md`. |
| F46 | CONFIRMED-OPEN | **ROOT-CAUSE-PINNED + FIX-SPECIFIED** | cycle_runner.py:78 trades-rooted conn → `log_forward_market_substrate`. 1-line fix in `RUN_14_track_B_f46_f48_fixes.md`. |
| F48 | CONFIRMED-DEAD-READ | **FIX-SPECIFIED + KARACHI-HOT** | `monitor_refresh.py:1041` reads legacy `settlements`; silent return-1.0 on 3-day NULL. 1-line repoint to `settlements_v2` + counter metric. |

### Updated OPEN sections (additions only)
- SEV-1 (new): **F87**, **F90**.
- SEV-2 (new): F85, F86, F91, F92.
- SEV-3 (new): F88, F89.

### Karachi 5/17 + 5/19 ops gate (READ FIRST)
Before next Karachi monitor tick:
1. [ ] **F87** — `launchctl kickstart -k gui/$(id -u)/com.zeus.forecast-live`; confirm PID + exit 0; tail .err for crash cause.
2. [ ] **F48** — probe `_persistence_discount('Karachi', date(2026,5,17), …)`; if `PERSISTENCE_CHECK_DISABLED` fires, deploy `settlements_v2` repoint.
3. [ ] **F90** — `crontab -l | diff - <(jq -r '...' cron/jobs.json)`; verify no Karachi-supporting job silently dropped.

### Recommended Run #15 priorities
1. F87 (today), F48 (today), F90 (this week).
2. Land A2 fix (Track A) + F46 root-cause patch as ONE PR; alias lint (Track C) as a separate hygiene PR.
3. F85 / F86 / F91 / F92 as ops-debt PR.
4. Carry forward open SEV-1: F3, F32, F35, F63, F71, **F87**, **F90**.

### Documents in this run
- `RUN_14_track_A_market_events_decision.md`
- `RUN_14_track_B_f46_f48_fixes.md`
- `RUN_14_track_C_alias_lint_patch.md`
- `RUN_14_track_D_daemon_supervision.md`
- `AUDIT_HISTORY.md` (new index file)


## Run #15 Track 1 status delta (2026-05-17, branch fix/wave-2-lineage-and-k1-cleanup-2026-05-17 @ 7fb380c5)

### F90 reframe — Run #14 premise disproven
Run #14 logged F90 SEV-1 as "82KB jobs.json vs 2-line crontab, 40 jobs un-scheduled." Run #15 Track 1 disproves the premise:
- `crontab -l` is 71 lines / **24 active commands** (Run #14 likely sampled wrong / used a stub crontab).
- `jobs.json` (42 jobs) IS executed by `ai.openclaw.node` daemon (PID 17251). Evidence: 10/11 enabled jobs have `cron/runs/<jobid>.jsonl` mtimes < 1 day old.
- 31 disabled jobs are intentionally dormant (explicit `enabled: false` flag), not silently un-scheduled.
- F90 SEV-1 → SEV-3 (architectural source-of-truth ambiguity remains; not Karachi-blocking).

### New SEV-1 (was hiding under F90's wrong framing)
- **F90a SEV-1**: 3 enabled jobs failing every tick with `cron payload.model 'openai-codex/gpt-5.4-mini' rejected by agents.defaults.model`:
  - `memory-observer` (*/15 min) — observability infra broken
  - `finance-subagent-scanner` (*/20 8-16 Mon-Fri) — LLM market scanner broken
  - `finance-subagent-scanner-offhours` — same root cause
  Fix: jobs.json payload.model reconciliation against openclaw.json `agents.defaults.model`. Manual model-id selection required.

### New SEV-2
- **F90b**: `memory-reflector` + `memory-dream-cycle` timing out on most invocations. Memory pipeline degraded.
- **F95 (Karachi-defensive)**: `zeus-antibody-scan` + `zeus-daily-audit` disabled in jobs.json since 2026-04-14/15 with NO crontab/launchd replacement. Karachi regression coverage gap. Restore via JSON enabled-flag flip + `launchctl kickstart ai.openclaw.node`.

### New SEV-3
- **F90c**: No `tools/ops/cron_reconcile.py` across 3 scheduler layers. Sketch + CI gate in `RUN_15_track1_f90_cron_diff.md` §6.
- **F93 (Karachi-direct)**: Single 10:00 UTC `oracle_snapshot_listener.py` tick is SPOF for Karachi WU/HKO data. Add 16:00 UTC retry tick (one-liner in §5 Top-4).
- **F94**: `cron/jobs-state.json` is structurally empty (all 42 entries `{}`); real state is in `cron/runs/<jobid>.jsonl`. Likely cause of Run #14 misread.

### Recommended Run #15 Track 1 ops actions (operator-pasteable in `RUN_15_track1_f90_cron_diff.md` §5)
1. **F90a fix** (SEV-1): reconcile payload.model in jobs.json (3 jobs). Backup, edit, kickstart openclaw-node.
2. **F90b fix** (SEV-2): tune memory-reflector / dream-cycle timeoutMs OR chunk.
3. **F95 fix** (SEV-2 Karachi): re-enable zeus-antibody-scan + zeus-daily-audit in jobs.json.
4. **F93 fix** (SEV-3 Karachi): add 16:00 UTC oracle_snapshot_listener.py retry to crontab.
5. **F90c antibody**: ship `tools/ops/cron_reconcile.py` + pre-commit hook + cron_jobs_tracker.py `--alert-on-failure` flag.

### Documents in this track
- `RUN_15_track1_f90_cron_diff.md` (NEW)


## Run #16 Track A status delta (2026-05-17, branch fix/wave-2-lineage-and-k1-cleanup-2026-05-17)

### Headline
- **F87 (SEV-1 HOT) → CLOSED-FALSE-ALARM.** `com.zeus.forecast-live` PID 10397 is healthy (process alive, `.err` mtime within 1 min, heartbeat fresh). Run #14 misread `launchctl list` column 2 (which is the LAST exit status, not current state) as live status. No Karachi exposure; no restart required.
- **F85 (SEV-2) → ROOT-CAUSE-PINNED + FIX-SPECIFIED.** Plist layer ruled out — all 7 `com.zeus.*.plist` files have distinct `StandardOutPath`/`StandardErrorPath`. Root cause is Python `logging.basicConfig()` default `StreamHandler` writing to `sys.stderr` at 4 daemon entry points. Dual-handler patch spec'd (INFO→stdout, WARNING+→stderr) with operator verification probe. No production code mutated.

### Updated status
| F# | Prior | Now (Run #16 A) | Reason |
|---|---|---|---|
| F87 | SEV-1 NEW HOT (Run #14) | **CLOSED-FALSE-ALARM** | Evidence: `ps -ef \| grep forecast_live_daemon` → PID 10397 alive; `ls -la logs/zeus-forecast-live.err` → mtime 2026-05-17 18:59 (fresh); `launchctl list` column 2 value `1` is prior incarnation's exit code, not current state. Antibody added to LEARNINGS.md §1. |
| F85 | SEV-2 NEW (Run #14) | **ROOT-CAUSE-PINNED + FIX-SPEC** | Plist forensics: 7/7 plists wire distinct `.log`/`.err` paths — layer ruled out. Code forensics: `grep -n basicConfig src/main.py src/ingest_main.py src/ingest/forecast_live_daemon.py src/riskguard/riskguard.py` → 4 sites, all pass only `level=logging.INFO[+format=]`, none pass `stream=`. CPython `StreamHandler.__init__` defaults `stream=sys.stderr` → all INFO writes go to `.err`. Patch spec in RUN_16_track_A §3. |

### Carry-forward open SEV-1 (post Run #16 A close of F87)
F3, F32, F35, F63, F71, **F90a**, F48 (HOT-FIX-SPEC), F103. **F87 removed** from carry-forward.

### Karachi 5/17 + 5/19 ops gate (updated)
1. ~~F87 daemon restart~~ — **NOT REQUIRED** (false-alarm).
2. F48 — apply Run #15 T2 §5 hot-fix Edits A–C (forecasts.-schema-qualified SELECT + counter).
3. F85 fix — **LOW priority for Karachi gate** (observability only; daemons functionally healthy). Bundle with F86 SIGTERM-handler patch in same daemon-hygiene PR.
4. F90a (SEV-1 from Run #15 T1) — payload.model reconciliation.

### Audit-discipline meta-finding
Run #14 F87 was the 4th SEV-1 in this audit reframed/closed-false-alarm on second pass (after F90, F48, F87). Common root cause: claim derived from a status indicator (launchctl column, jobs.json line count, settlements rowcount) without the cross-check probe. **LEARNINGS.md §3** codifies the probe-then-claim rule as a precondition for any future SEV-1 opening in this audit lane.

### Documents in this run
- `RUN_16_track_A_f85_log_routing_f87_close.md` (NEW)
- `LEARNINGS.md` (NEW — cross-run antibody catalog)


## Run #16 Track G additions (2026-05-17, branch fix/wave-2-lineage-and-k1-cleanup-2026-05-17)

### Headline
- **Treasury layer reconciles cleanly.** Chain pUSDC $189.05 ↔ `risk_state.initial_bankroll` $189.05 ↔ `collateral_ledger_snapshots.pusd_balance_micro` (latest snapshot 30 s before audit). Open `CTF_SELL` reservation matches on-chain reserved tokens. No drift at the wallet / collateral layer.
- **Position-layer book-keeping diverges from on-chain.** 4 distinct SEV-1 patterns found (F106–F109), one of which (F109 — double-book of London 5/19) is a $1.86 / 6-share over-book today that becomes a $6 phantom-PnL event if the position settles WIN.
- **Karachi 5/17 (c30f28a5-d4e):** cost basis reconciles exactly to on-chain shares × entry price; but the `position_lots` audit trail for this position is empty (F107). Real money, real shares, missing audit row.
- **F106 silent-empty-join is the same antibody category as Run #16 A LEARNINGS §3** (probe-then-claim): the obvious `USING(position_id)` between `position_lots` and `position_current` returns zero rows because of an INT-vs-TEXT keyspace mismatch — and a naive reconciliation script would report "all clean" while diverging silently.

### New SEV-1 carry-forward (post Run #16 T G)
F3, F32, F35, F63, F71, **F90a**, F48 (HOT-FIX-SPEC), F103, **F106 (META)**, **F107**, **F108**, **F109**.

### Karachi 5/17 + 5/19 ops gate (updated)
1. **F109 ops gate** (immediate, before London 5/19 settles): manually void one of `{0a0e3b72-46e, 7557a029-4ad}` OR confirm downstream PnL deduplicates by `(token_id, market_id)`.
2. F107 (Karachi 5/17): no immediate action — position is real, audit trail can be backfilled. Risk: post-settle PnL queries that depend on `position_lots` will skip Karachi.
3. F108: do not ship any new monitor that aggregates `venue_trade_facts.filled_size` until the `v_venue_trade_facts_latest` view exists.
4. F106 antibody: add `views/v_position_lots_canonical.sql` joining via `venue_commands.command_id` so the wrong shape becomes a known-bad pattern.

### Reframing-watch
F106 + F108 are both "silent-no-op aggregation" defects. They join the pattern set with F90 (jobs.json line-count misread), F87 (`launchctl` column-2 misread), F48 (bare-name SELECT silently binds to wrong DB), F102 (`temp_persistence` empty). All five share the antibody: **the probe must observe a positive sample of the value being claimed, not just the success of the syntactic operation**. LEARNINGS.md §3 codifies this — F106/F108 entries should be added there in a follow-up doc PR.

### Documents in this track
- `RUN_16_track_G_financial_reconciliation.md` (NEW)


## Run #16 Track F status delta (2026-05-17, branch fix/wave-2-lineage-and-k1-cleanup-2026-05-17)

### Headline
- **F111 (SEV-1 HOT) NEW.** Two London 5/19 positions (`0a0e3b72-46e`, `7557a029-4ad`, both `opening_inertia`) STUCK in `pending_exit` with 12+ `EXIT_ORDER_REJECTED` retries each between 22:13–23:59 UTC (cadence ≈ 7–8 min, no backoff acceleration, no `EXIT_BACKOFF_EXHAUSTED` or `CHAIN_QUARANTINED` terminator). `updated_at` refresh on every retry masks them from the classical >12 h `pending_exit` orphan query. Same code path will govern Karachi `c30f28a5-d4e`'s own exit tonight — same-class risk.
- **F108 (SEV-2) NEW.** `EXIT_ORDER_REJECTED` rows persistently mis-log `phase_before='active'` on every retry because `src/execution/exit_lifecycle.py:460` derives `phase_before` from the in-memory `pre_exit_state` default `"holding"` rather than the persisted `position_current.phase`. Same defect mirrored at `src/execution/command_recovery.py:820`. 26 known false-transition rows in 7 d. Event-replay reconstruction in `src/state/projection.py:73` would synthesize a fictitious oscillation `active↔pending_exit` 13× per stuck position.
- **F109 (SEV-2) NEW.** All 5 currently `economically_closed` positions reached that phase WITHOUT any `EXIT_INTENT` / `EXIT_ORDER_POSTED` / `EXIT_ORDER_REJECTED` row recording the `active→pending_exit` step — the `EXIT_ORDER_FILLED` row jumps `phase_before='pending_exit'` from nothing. Happy-path exit performs a silent state write that bypasses event-sourcing. Lineage from `decision_log` → exit fill is broken at the intent layer (relates to F7 class).
- **F110 (SEV-3) NEW.** `position_events.occurred_at` carries the literal string `"unknown_entered_at"` on CHAIN_SYNCED events (Karachi c30f28a5 sq=3; Manila bf0a16f5 sq=3). TEXT column accepts it; `julianday()` breaks silently.
- **F112 (SEV-3) NEW.** `position_events.event_type` enum (19 values) has no dedicated `PHASE_RECONCILED` event — phase transitions piggyback on lifecycle action events, leaving F109-class silent transitions with no canonical home in the append-only log.
- **F113 (SEV-3) NEW.** No codified mapping between `position_current.phase` (9 values) and `position_lots.state` (7 values). Lot↔position consistency audit cannot be written without reverse-engineering the map from runtime behavior.

### Updated status (Run #16 F)

| F# | Prior | Now | Reason |
|---|---|---|---|
| F108 | — | **NEW SEV-2** | `pre_exit_state` default `"holding"` → false `active` literal in 26 reject rows. |
| F109 | — | **NEW SEV-2** | Happy-path exit emits `EXIT_ORDER_FILLED(pending_exit→economically_closed)` with no prior `active→pending_exit` event row. 5/5 economically_closed affected. |
| F110 | — | NEW SEV-3 | `occurred_at='unknown_entered_at'` literal in CHAIN_SYNCED. |
| F111 | — | **NEW SEV-1 HOT** | London 5/19 twins; 12+ rejects each; no terminator wired; live-money 5/19 exit unhedged. |
| F112 | — | NEW SEV-3 | Schema enum lacks `PHASE_RECONCILED`. |
| F113 | — | NEW SEV-3 | `phase ↔ lot.state` mapping uncodified. |

### Karachi 5/17 + 5/19 ops gate (updated)
1. **Highest priority NEW**: live-trading operator must monitor Karachi `c30f28a5-d4e`'s upcoming exit event stream tonight; if `EXIT_ORDER_REJECTED` count reaches 3 within 30 min, force-quarantine manually (F111 same-class).
2. **Higher priority NEW**: operator should query `payload_json` of the 24 EXIT_ORDER_REJECTED rows on the two London twins to extract the reject reason (out of scope for this READ-ONLY audit); the structural-vs-transient distinction determines whether retries can ever succeed.
3. F48 / F85 / F86 / F90a actions from prior runs unchanged.

### Carry-forward open SEV-1 (post Run #16 F)
F3, F32, F35, F63, F71, **F90a**, F48 (HOT-FIX-SPEC), F103, **F111** (NEW HOT).

### Audit-discipline meta-finding
Run #16 F surfaces a SEV-1 HOT that PASSES the LEARNINGS §3 probe-then-claim rule on first opening: claim "London twins stuck" is backed by 26 enumerated event rows with monotone cadence (not a status indicator) AND a documented absence of `EXIT_BACKOFF_EXHAUSTED` in the 7d stream. F108–F110, F112, F113 are observability/schema findings backed directly by the same evidence rows. Pattern: "trace every legal transition in a state machine" probe is a high-yield audit angle — 6 findings (1 HOT) in a single track on a system thought to be well-understood.

### Documents in this run
- `RUN_16_track_F_position_lifecycle_correctness.md` (NEW)


---

## Run #16 T B — F102 verdict + F106 / F107 new

### F102 — `temp_persistence` empty everywhere → VERDICT: MIGRATED, both ends broken
- **Severity**: SEV-2 (HOT — silently degrades Karachi 5/17 persistence-anomaly discount to 1.0 for 100% of HIGH calls)
- **Status**: **MIGRATED-BOTH-ENDS-BROKEN** (Run #16 T B). NOT NEVER-WIRED. NOT RETIRED.
- **Owner**: `scripts/etl_temp_persistence.py:31` (writer) + `src/engine/monitor_refresh.py:1065` (reader)
- **Evidence**:
  - Writer IS scheduled: `src/main.py:413` (live daemon, APScheduler 06:00 daily) and `src/ingest_main.py:580` (ingest daemon, lock-wrapped). Latest "OK" run 2026-05-17 06:00:29 (`logs/zeus-ingest.err`).
  - Writer exits 0 daily but writes ≈0 rows — discarded by `n < 3` filter (line 132) because source has only 145 rows across 5 days × 51 cities × 4 seasons × 9 delta buckets.
  - Writer reads `observations` from `zeus-world.db` (145 rows). K1 split (PR #114, 2026-05-14) re-homed canonical `observations` to `zeus-forecasts.db` (43,971 rows / 868 distinct dates / 2023-12-27 → 2026-05-16). Writer was not migrated.
  - Reader `monitor_refresh.py:1065` uses bare-name `FROM temp_persistence`; under cycle conn (`trades.db` MAIN + `world` + `forecasts` ATTACH), bare resolves to MAIN (`zeus_trades.db`, 0 rows).
- **1-line F48 L1065 fix**: `"SELECT frequency, n_samples FROM world.temp_persistence "` — schema-qualifier matches K1 binding discipline from Run #15 T2. **Necessary** (locks reader to writer's target DB) but **NOT sufficient** at runtime — world copy still 0 rows until F106 is fixed. Pair with Run #15 T2's `PERSISTENCE_FALLBACK_TRIGGERED` counter for observability.
- **Audit trail**: Run #15 T2 surfaced; Run #16 T B verdict pinned.

### F106 — `etl_temp_persistence.py` reads stale `world.observations`
- **Severity**: SEV-2 (HOT — root cause of F102 left-half)
- **Status**: NEW (Run #16 T B)
- **Owner**: `scripts/etl_temp_persistence.py:31` (binding) and `:65-77` (source query)
- **Evidence**: Writer's `from src.state.db import get_world_connection as get_connection` points at `zeus-world.db` (145 obs rows / 5 days). Canonical observations are in `zeus-forecasts.db` (43,971 / 868 days) post-K1 split. Sibling defect-class suspect: `etl_diurnal_curves.py`, `etl_hourly_observations.py` (same `get_world_connection` pattern, both run by the same `_etl_recalibrate_body` loop).
- **Fix shape**: open a forecasts conn for the source SELECT (or ATTACH `forecasts` to the world conn) while keeping the target write on `world.temp_persistence`. Cross-DB conn helpers already exist (e.g., `get_trade_connection_with_world`).
- **Audit trail**: Run #16 T B discovery.

### F107 — `_etl_recalibrate_body` swallows ETL row counts
- **Severity**: SEV-3
- **Status**: NEW (Run #16 T B)
- **Owner**: `src/ingest_main.py:600-612` and `src/main.py:127-138`
- **Evidence**: ETL scripts return `{"stored": N}` plus stdout `print(f"Stored {stored} persistence entries")`. The recalibrate wrapper inspects `r.stderr` only; stdout is captured but discarded. Result dict reports `OK` for every exit-0, including "exit 0 with 0 rows written". This is exactly how F102/F106 stayed invisible for ≥15 days.
- **Fix shape**: parse `r.stdout` for `Stored \d+` (or any structured row-count line), include in result dict (`'etl_temp_persistence.py': 'OK (stored=552)'`), and trip an alarm on `stored=0` two days running.
- **Audit trail**: Run #16 T B discovery.

### Documents in this run
- `RUN_16_track_B_f102_temp_persistence_ownership.md` (NEW)
