# Phase Critic Report — 9-branch post-Karachi remediation review

Date: 2026-05-17
Reviewer: phase critic (opus, fresh context)
Mode: ADVERSARIAL (escalated — CB-1 schema-version omission is shipping defect)

## Verdict summary table
| Branch | per-branch defects found | cross-branch defects involved | merge-block? |
|---|---|---|---|
| A migration-runner | 1 MINOR (entry-fn divergence) | CB-1 (no user_version bump in runner) | NO — but operator-runbook required |
| B lineage-null-family | 0 substantive | — | NO |
| C k1-reader-sweep | 0 substantive | CB-3 (antibody scope) | NO |
| D sentinel-and-insert-ignore | 0 substantive | CB-1 indirectly (touches position_events sentinel space) | NO |
| E worktree-hook | **1 CRITICAL** (source fix missing from branch) | — | **YES** |
| F lineage-command-id-f7 | 0 substantive | CB-1, CB-4 (live cascade) | NO |
| G migrations-bundle | 0 substantive on branch logic | **CB-1 (schema-version omission)**, CB-2 | **YES until CB-1 fix** |
| H k1-readers-batch-2 | 1 MINOR (shell vs python style drift) | CB-3 (antibody dup) | NO |
| I pr-i5a-winning-index-set | 1 MAJOR (user_version not bumped despite SCHEMA_VERSION=5) | **CB-1**, CB-4 | **YES until CB-1 fix** |

**Net: 1 CRITICAL (E), 1 SHARED CRITICAL (CB-1 hits G+I), 1 MAJOR (I), 3 MINOR. Merge BLOCKED on E and CB-1.**

## Per-branch defects

### A: migration-runner-2026-05-17
- A1 (header regex matches existing migration): **PASS**. Existing `202605_add_redeem_operator_required_state.py` line 2 has `last_reviewed=2026-05-16` — regex `last_reviewed=\d{4}-\d{2}-\d{2}` matches.
- A2 (existing migration exposes `up(conn)`): **FAIL → bootstrap workaround**. Existing migration defines `_migrate_one_db`, `main(argv)`, `_bump_user_version` — NOT `up(conn)`. Runner sidesteps via `_BOOTSTRAP_APPLIED = {"202605_add_redeem_operator_required_state"}`. Workable but creates a 2-style ecosystem footgun. **MINOR.**
- A3 (bootstrap seed semantics): **PASS** — runs only when `_migrations_applied` is empty (table create). Operator-drop edge case: re-seeds correctly.
- A4 (dry-run target): **PASS** — `--db-path` supplied → `sqlite3.connect(args.db_path)`; dry-run writes nothing.
- **Hidden defect (becomes CB-1)**: runner does NOT update `PRAGMA user_version` after applying migrations. The only existing migration that bumps user_version is the bootstrap-seeded one. After ANY new migration applied via this runner, `user_version` stays stale.

### B: lineage-null-family-2026-05-17
- B1 (31 sites migrated): **PASS** — 32 calls of `_make_rejection_decision` in `evaluator.py`; ZERO remaining raw `EdgeDecision(False,...)`. All early-rejection paths use the helper.
- B2 (`__post_init__` rejects None): **PASS for explicit None**. Default value is `""` (empty string) which bypasses the check — but `_make_rejection_decision` always stamps the sentinel `"<pre_snapshot:rejected>"`, so happy path covered.
- B3 (sentinel passes truthy check): **PASS** — `"<pre_snapshot:rejected>"` is non-empty; `decision_chain.py:394` `if not normalized["decision_snapshot_id"]` reads False; no `missing_decision_snapshot_id` degraded reason appended.
- B4 (cycle_runtime mutation): **PASS** — non-frozen dataclass; `__post_init__` runs only at construction. Post-construction `_d.market_phase = _phase_value` mutation unaffected.
- Naming inconsistency: spec FIX_SEV1_BUNDLE.md §F2 said sentinel `"<pre_decision:family>"`; agent shipped `"<pre_snapshot:rejected>"`. Different concept (snapshot-not-yet-resolved vs decision-family-pre). Document drift; no functional impact.

### C: k1-reader-sweep-2026-05-17
- C1: `get_forecasts_connection_with_world` exists at `src/state/db.py:205` — PASS.
- C3 (F41 INSERT qualifies world.validated_calibration_transfers): **PASS** — `scripts/evaluate_calibration_transfer_oos.py:381` uses `INSERT INTO world.validated_calibration_transfers`; table is in `db_table_ownership.yaml:665`.
- C4 (antibody scope): scope narrow to `K1_FIXED_SCRIPTS = ["bridge_oracle_to_calibration.py", "evaluate_calibration_transfer_oos.py"]`. Does NOT include allowlist for migration scripts — but ALSO does not flag them (param-loop only iterates the 2 fixed). PASS.

### D: sentinel-and-insert-ignore-2026-05-17
- D1 (`now` in scope at line 658): **PASS** — pre-commit version shows `now` reference in same logger.warning block. The fix replaces `rescued.entered_at = "unknown_entered_at"` with `rescued.entered_at = now`. Comment `# 'now' already in scope; line 668 uses it as _rescue_display_ts` is accurate.
- D2 (F18 zero-insert log): **PASS** — `inserted` and `results` are local to `_persist_market_events_to_db` (line 592); both in scope at line 655.
- D3 (F8 line 808 quarantine sentinel): not directly inspected in this branch; F8 CHECK landed in G. Sentinel comment will be tested as part of G's CHECK migration.

### E: worktree-hook-2026-05-17 — **CRITICAL DEFECT**
- E1 (`_STDOUT_PROTOCOL_RESERVED_EVENTS` includes WorktreeCreate/Remove): **FAIL**. `git diff origin/main..origin/fix/worktree-hook-2026-05-17 -- .claude/hooks/dispatch.py` returns **EMPTY**. The commit message claims dispatch.py was updated, but the branch ships ONLY the antibody test `tests/.claude/hooks/test_worktree_create_contract.py` (45 lines). `_STDOUT_PROTOCOL_RESERVED_EVENTS` symbol does NOT appear in current dispatch.py at all (grep negative).
  - **Impact**: antibody test will FAIL on first run if it actually asserts stdout-empty. The source fix is either in a sibling branch or was lost in the rebase. Merge would ship a failing test.
- E2: antibody DOES subprocess-invoke + assert `proc.stdout.strip() == ""` + assert `"[advisory:worktree_create_advisor]" in proc.stderr` — correct contract. But will fail without E1's source change.
- E3: no other consumer parses dispatch.py stdout for these events (confirmed — `_STDOUT_PROTOCOL_RESERVED_EVENTS` isn't a public API). Side-effect risk LOW.

**E must be rejected and re-shipped with the dispatch.py source edit.**

### F: lineage-command-id-f7-2026-05-17
- F1 (OrderResult dataclass `command_id: Optional[str] = None`): **PASS** — `src/execution/executor.py` adds field. No serialization callers found that would break.
- F2 (COALESCE preservation): **PASS** — pattern is Python-side `stored_command_id = command_id if command_id not in (None, "") else current["command_id"]`. Equivalent semantics, safer than SQL `excluded.command_id` because handles current=None.
- F3 (existing INSERT without command_id): **PASS** — column nullable, all existing INSERT statements unchanged. New column auto-NULL.

### G: migrations-bundle-f8check-f15-f29-2026-05-17
- G1 (F8 row-count preservation): **NOT INDEPENDENTLY VERIFIED** — depends on operator probe. The agent's SELECT INTO with `CASE occurred_at = 'unknown_entered_at' THEN sentinel_map[position_id] ELSE occurred_at END` pattern preserves all rows; fallback path uses `QUARANTINE` when no fill exists. PASS structurally.
- G2 (Karachi DAY0_WINDOW order): **PASS** — agent's claim 19:00 DAY0_WINDOW > 06:40 sentinel substitute is chronologically + lexicographically consistent.
- G3 (F15 backfill NULL pre-check): **PASS structurally** — `null_metric_count` query runs BEFORE INSERT; only logs warning, does not abort. Good.
- G4 (F29 SAVEPOINT-wrapped): **PASS** — comment says "SAVEPOINT (not BEGIN) allows retry-on-busy".
- **Carries CB-1 defect**: 3 migrations applied via runner, none bumps `PRAGMA user_version`. Daemon refuses start post-merge unless operator manually bumps version OR runner is extended.

### H: k1-readers-batch-2-2026-05-17
- H1 (`data_chain_monitor.sh`): **PASS** — sed swap clean: `sqlite3.connect('state/zeus-forecasts.db', timeout=2)` with K1 comment.
- H2 (`ingest_grib_to_snapshots.py` write_class="bulk"): **PASS** — `conn = get_forecasts_connection(write_class="bulk")` line 996.
- H3 (build_correlation_matrix not in this batch — reclassified): **N/A** for this branch. **MINOR**: mixed shell + python style — shell script uses bare `sqlite3.connect` import-shadow (`import sqlite3, json` in a `.sh` file is impossible, the comment refers to the python sub-call inside). Confirmed file is a shell wrapper around a `python -c` heredoc — acceptable.

### I: pr-i5a-winning-index-set-2026-05-17
- I1 (SCHEMA_VERSION bumped 4→5 + startup assertion): **MAJOR DEFECT**. `src/state/db.py:829` bumps constant to `5`. `src/main.py:1023` daemon startup calls `assert_schema_current(conn)` which compares `PRAGMA user_version` to constant. **The migration `202605_add_settlement_commands_winning_index_set.py` does NOT execute `PRAGMA user_version = 5`**. After merge + migration apply, user_version stays 4, daemon refuses start indefinitely. Spec text in db_table_ownership.yaml even notes "SCHEMA_VERSION did not change in lockstep" as the risk — this is exactly that risk.
- I2 (winning_index_set encoding `'["2"]'`): JSON string containing JSON array. Reader contract not yet implemented (PR-I.5.c). Storage shape acceptable.
- I3 (CTF index mapping buy_yes→[2], buy_no→[1]): **PASS** — guarded by `exit_price > 0` (winning side only); maps direction correctly. Comment explains binary-only limitation.

## Cross-branch defects (HIGH-VALUE)

### CB-1: **Schema-version drift — CRITICAL, MERGE-BLOCKING**
- Branch I sets `SCHEMA_VERSION = 5` in `src/state/db.py`.
- Migrations from F, G, I (4 total via runner) do NOT call `conn.execute("PRAGMA user_version = N")`.
- Runner framework (A) does NOT bump user_version on apply.
- The only pre-existing migration that bumps version is the bootstrap-seeded one (which has its own `_bump_user_version` helper, not exposed to the runner).
- Result: post-merge, DBs have `user_version=4` but code expects `user_version=5`. `assert_schema_current()` raises. Daemon retries 5 min then fatal exit (per `_startup_world_db_schema_ready_check`).
- **Fix options**: (a) I's migration must bump user_version to 5; OR (b) Runner framework should accept an optional `target_user_version` per-migration; OR (c) Single operator runbook step "PRAGMA user_version=5 after migrations apply".
- **Evidence**: `src/state/db.py:2399 conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")` only runs at fresh DB create, not migration apply. `src/state/db.py:2410-2416 assert_schema_current()` is the hard gate.

### CB-2: Migration application order
- 4 new migrations alphabetical apply order: F (add_execution_fact_command_id) → I (add_settlement_commands_winning_index_set) → G_backfill_v2 → G_position_events_iso_check → G_ux_review_required.
- Cross-dependencies: NONE found. Each touches independent tables/columns. PASS.
- Risk: if F8 CHECK rebuild (G) fires before sentinel `unknown_entered_at` rows exist in production, the CHECK on the new table would reject existing data — but `_build_sentinel_map` substitutes BEFORE the table swap. Verified PASS.

### CB-3: Antibody-test name collisions
- B `tests/state/test_lineage_join_keys.py` + F `tests/state/test_execution_fact_command_id.py`: both probe lineage. No fixture name conflict (each defines own `trade_conn` consumer); no shared session-level setup.
- C `test_k1_reader_isolation.py` + H `test_k1_reader_isolation_batch2.py`: parallel files, parametrized scopes are disjoint (`K1_FIXED_SCRIPTS` vs batch-2 list). PASS but should be consolidated post-merge.
- G adds 3 test files in `tests/` and `tests/state/`. No name collision with existing tests.

### CB-4: Karachi blast radius re-verification
- F touches `src/state/db.py` (log_execution_fact) AND `src/execution/executor.py` (OrderResult). I touches `src/execution/settlement_commands.py` AND `src/execution/harvester.py`. Both on live cascade.
- F adds optional `command_id` arg — existing callers unaffected (default None). Live cascade WILL begin populating column once new orders flow. NO regression risk identified.
- I adds optional `winning_index_set` arg to `enqueue_redeem_command`/`request_redeem`. Existing callers unaffected (default None). Harvester `_resolve_settlement_pnl` (line ~2120 onward) is the ONLY new caller that populates it. Live cascade gets winning_index_set only for `exit_price > 0` positions — fail-safe degradation (None for unknown).
- "Zero blast radius" claim plausible but I's downstream consumer (PR-I.5.c will read `winning_index_set` for chain redeem call) is NOT yet implemented. Until then, this is dead-column data. Fine for staging.

### CB-5: Rebase ancestry
- F, G, I include commit `20104955ba` (A's runner commit) as ancestor — confirmed via `git log --oneline origin/main..origin/fix/<X>`. They depend on A. Merge order: **A must merge first** (or A's commit absorbed via squash). C, D, H do not depend on A and can merge independently.
- B does NOT include A as ancestor (single commit `0d7d8caff4`). Independent merge.
- E does NOT include A as ancestor (single commit `9d19e2767c`) — but E is rejected on E1 grounds.

### CB-6: Hook fix side-effect
- N/A — branch E ships antibody only; dispatch.py unchanged. CB-6 is moot until E re-ships with source edit.

### CB-7: Hook bypass / PR open
- All 9 commits authored by `113385294+yuxuan53@users.noreply.github.com` (Fitz). No `--no-verify` indication in commit subjects. Operator stated no PRs opened — confirmed: branches exist on `origin` but no `gh pr list` evidence checked here.
- Each branch is a SINGLE commit (A+F+G+I share A's commit via rebase; net new commits 1 each). LOC discipline: each branch is < 1000 LOC self-authored. Tiny-PR risk (E ships 45 lines) — would need `ZEUS_PR_ALLOW_TINY=1` per user memory feedback_pr_300_loc_threshold.

### CB-8: pytest collection
- `tests/state/` collection: 50 tests collected, 46 pass + 4 skip on main (baseline pre-merge). PASS.
- New test files from B/C/D/F/G/H/I: all importable via standard pytest (no exotic conftest tricks). Antibody for E will fail without source edit.

## Regression baseline (re-run on PRIMARY repo main)
- `python -m pytest tests/state/ tests/test_harvester_settlement_redeem.py -q`: **46 passed, 4 skipped** (PASS).
- Pre-existing failures: NONE observed in this subset.
- New failures introduced by any branch: NOT independently verified (would require merge + run); CB-1 will cause `tests/state/test_schema_current_invariant.py` style assertions to fail post-merge.

## Recommended merge order
1. **A** (foundational runner) — absorb as squash if F/G/I rebase needed.
2. **B** + **D** + **H** + **C** — independent, low-risk, can merge in any order.
3. **F** — depends on A; safe additive column.
4. **G** — depends on A; **HOLD until CB-1 fix**.
5. **I** — depends on A; **HOLD until CB-1 fix**.
6. **E** — **REJECT; re-submit with source edit included**.

## Operator-action items
1. **CB-1 (CRITICAL)**: Decide schema-version bump strategy before merging G or I:
   - Recommended: add `conn.execute("PRAGMA user_version = 5")` at the end of `202605_add_settlement_commands_winning_index_set.py up()`; G's migrations don't bump because they're non-additive structural changes within version 4 → 5 transition. OR extend runner framework to require each migration to declare `target_user_version`.
2. **E (CRITICAL)**: Re-ship `fix/worktree-hook-2026-05-17` with the actual `_STDOUT_PROTOCOL_RESERVED_EVENTS` edit to `.claude/hooks/dispatch.py`. Current branch is antibody-only.
3. **A (MINOR)**: Document that future migrations MUST use `def up(conn)` entry; consider deprecating the legacy `_migrate_one_db` pattern after bootstrap migration retires.
4. **B (DOC)**: Reconcile sentinel naming `"<pre_snapshot:rejected>"` vs spec `"<pre_decision:family>"`. Either acceptable; pin in FIX_SEV1_BUNDLE.md.

## ADDENDUM 2026-05-17 (post-remediation)

### CRITICAL #1 (E branch) — REVERSED as FALSE POSITIVE

Orchestrator fact-check after critic report:
- `git show origin/main:.claude/hooks/dispatch.py | grep _STDOUT_PROTOCOL_RESERVED_EVENTS` → line 112 (present)
- `git log --all -S "_STDOUT_PROTOCOL_RESERVED_EVENTS"` → introduced by `f52cea1537 feat(antibodies-2): close 6 critic-deferred items from PR #127` (already merged to main)
- E branch was created off main AFTER `f52cea1537`; therefore inherits the dispatch.py fix automatically
- E branch's empty-diff for dispatch.py is CORRECT — source fix lives in main; E adds only the antibody test

**E branch verdict reversed**: ✅ READY-FOR-PR-REVIEW. No re-ship needed.

**Critic miss**: report checked `diff origin/main..origin/fix/worktree-hook-...` and concluded "fix missing"; should have also checked `origin/main:.claude/hooks/dispatch.py` for the symbol. The diff being empty does NOT mean the symbol is absent from main; it means main and branch agree on this file. Future critic briefs should add: "if a fix is claimed in commit message but diff is empty, verify symbol presence in origin/main directly before flagging as defective."

### CRITICAL #2 (CB-1) — RESOLVED via REMEDIATION-I

Branch I received follow-up commit `5e40d2b034` adding `conn.execute("PRAGMA user_version = 5")` at end of `up()` in `202605_add_settlement_commands_winning_index_set.py`. New test `test_migration_bumps_user_version` passes (5/5). G's 3 migrations remain non-version-bumping (additive within v4→v5 transition; only I is the declared frontier). ✅ READY-FOR-PR-REVIEW.

### Net post-addendum verdict
- E: PASS (was false positive)
- I: PASS (CB-1 fixed)
- 0 CRITICAL remaining
- 3 MINOR remaining (A 2-style migration ecosystem, B sentinel naming, doc only)
- Merge order from §"Recommended merge order" stands

## Anti-rubber-stamp self-check
For branches with zero substantive defects (B, C, D, F):
- **B**: spot-traced `evaluator.py:278` (post_init), `:288` (sentinel constant), `:301` (helper call site), `:1758` (one of 32 caller-side updates), `decision_chain.py:394` (consumer truthy check) — 5 points.
- **C**: spot-traced `bridge_oracle_to_calibration.py:64,165,166` (cross-DB cm), `evaluate_calibration_transfer_oos.py:381,461` (world.* qualified writes), `test_k1_reader_isolation.py:46-50` (param scope) — 5+ points.
- **D**: spot-traced `chain_reconciliation.py:658` (now substitution), `market_scanner.py:602,608,655` (zero-insert branch), confirmed `now` in scope from pre-commit. 4 points.
- **F**: spot-traced `executor.py:564,585` (OrderResult), `db.py:5650,5657,5699` (write helper preservation logic) — 5 points.

I did NOT default to "looks fine" on any branch. E is the load-bearing rejection; CB-1 is the load-bearing schema defect.

