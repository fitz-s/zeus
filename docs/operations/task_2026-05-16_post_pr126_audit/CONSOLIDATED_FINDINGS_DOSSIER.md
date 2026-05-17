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
