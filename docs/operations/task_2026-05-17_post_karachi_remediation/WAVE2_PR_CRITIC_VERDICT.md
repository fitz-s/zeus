# WAVE-2 PR-LEVEL CRITIC VERDICT (PRE-OPEN)

**Reviewer**: opus critic, fresh context (a8889ca3856833cf5)
**Mode**: THOROUGH → escalation NOT triggered (0 CRIT, 1 MAJ → below 3-MAJ threshold)
**Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17`
**HEAD at review time**: `cc82c8a7a4` (96 files, +14,320/−164 LOC, 68 commits)
**Cwd**: `/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/zeus-deep-alignment-audit-skill`

---

## VERDICT: NEEDS-FIX-BEFORE-OPEN

One SEV-2 MAJOR (F109 OVERBOOK over-void on unequal-share duplicates) plus 3 MINORs.
The MAJOR is contingent (does not fire on current prod state — verified by direct
zeus_trades.db read showing 1 dup token with `shares=(6.0, 6.0)`) but the antibody
suite has **zero coverage of the broken branch**, violating
`feedback_antibody_recursion_metaverify_essential`. Fix is ~5 LOC + 1 test case;
should land in this PR rather than carry-forward.

---

## Severity histogram

- SEV-1: **0**
- SEV-2: **1**  (F109 consolidator OVERBOOK over-void on unequal-share duplicates)
- SEV-3: **3**  (provenance-header format drift; station_migration_alerts.json stale-tracked; K1 antibody scope narrowness)

---

## Pre-commitment predictions

Expected to find:
1. Hidden silent-except in newly-touched code paths (Karachi anchor) — **NOT FOUND** (log_trade_entry hard-raises at db.py:5990; update_trade_lifecycle hard-raises via BridgeAbsentError at db.py:6225).
2. Antibody tests that pass but lack sed-break/restore meta-verify — **PARTIAL** (RUN-12 antibody validated by test-logic read, not actual sed-break; F109 boot wire antibody passes 5/5 but covers only equal-share dups).
3. Opt-in flag accidentally defaulting ON — **NOT FOUND** (ZEUS_TAKER_CROSSING_ENABLED="0", ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED="false", ZEUS_CALIBRATION_AUTO_PROMOTE_ENABLED="false" — all OFF).
4. Provenance header drift — **FOUND MINOR** (2 files use `Lifecycle: created=...` format instead of CLAUDE.md's canonical `Created: ...`).
5. K1 sweep regressions in non-script src/ paths — **FOUND MINOR** (bare `FROM validated_calibration_transfers` in src/data/calibration_transfer_policy.py:854 and src/engine/evaluator.py:555 — currently only called from tests; antibody scope is K1_FIXED_SCRIPTS only).

Found NOT predicted: F109 over-void on unequal-share duplicates (SEV-2).

---

## Probe-by-probe results

| # | Probe | Verdict | Citation |
|---|---|---|---|
| 1 | F109 consolidator boot wire | **PASS** | src/main.py:1300-1340 + :1427; 5/5 antibody PASS; fail-tolerant logged-WARNING + boot continues confirmed |
| 2 | K1 helper SELECT/INSERT correctness sweep | **PASS w/ MINOR** | bare `FROM validated_calibration_transfers` in src/data/calibration_transfer_policy.py:854 + src/engine/evaluator.py:555; antibody scope (K1_FIXED_SCRIPTS) does not cover these; currently safe (test-only callers) |
| 3 | Karachi-bridge TRIGGER + synthesizer SAVEPOINT safety | **PASS** | scripts/migrations/202605_position_current_bridge_required_trigger.py BEFORE INSERT trigger; cycle_runtime.py:3528-3565 wraps log_trade_entry + projection write inside `sp_candidate_*` SAVEPOINT; bridge assertion at :3552-3560; ROLLBACK TO SAVEPOINT on raise; trade_decisions row exists before position_current INSERT fires TRIGGER → passes |
| 4 | F44 obs_v2_live_tick scheduler registration | **PASS** | src/ingest_main.py:1290-1294 (`add_job` cron minute=15, `max_instances=1, coalesce=True, misfire_grace_time=3600`); _k2_obs_v2_tick at :333-361 wraps `sqlite3.connect` inside `db_writer_lock(WriteClass.BULK)` at scripts/obs_v2_live_tick.py:326; advisory `dual_run_lock("obs_v2")` prevents duplicate runs |
| 5 | F35/F39/F9/F34 opt-in defaults | **PASS** | F34: `ZEUS_TAKER_CROSSING_ENABLED="0"` default at src/engine/cycle_runtime.py:809; F39: `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED="false"` default at src/data/calibration_transfer_policy.py:655 + self-policing exit at scripts/evaluate_calibration_transfer_oos.py:662-664; F9: `ZEUS_CALIBRATION_AUTO_PROMOTE_ENABLED="false"` default at src/ingest_main.py:1123-1129 (skips with log if flag missing); F35: unconditional ingest_main job for `bridge_oracle_to_calibration` (file-only writer, idempotent, no DB lock needed per docstring) — replaces cross-repo cron, no flag gate by design |
| 6 | Provenance headers | **MINOR (SEV-3)** | 15/17 new py files have canonical `# Created:` + `# Authority basis:` headers; 2 files (src/state/position_duplicate_consolidator.py, scripts/migrations/202605_position_current_idempotent_open_per_token.py) use alternative `Lifecycle: created=...; last_reviewed=...` format — semantically equivalent but breaks CLAUDE.md format-grep tooling |
| 7 | No-manual-precedent audit (operator scripts writer-lock) | **PASS** | tests/test_operator_script_lock_contract.py 24/24 pass; obs_v2_live_tick.py raw `sqlite3.connect` at :327 wrapped inside `db_writer_lock(db_path, WriteClass.BULK)` at :326 — properly serialised |
| 8 | Karachi blast radius (c30f28a5-d4e) | **PASS** | Karachi position is single-row (per state/zeus_trades.db direct query: 1 dup token, not c30f28a5-d4e); F109 consolidator HAVING COUNT(*) > 1 filter NO-OPs on single-row; TRIGGER fires only on new INSERT (existing orphans unaffected); synthesizer at update_trade_lifecycle.py:6212-6243 repairs orphans on next lifecycle event |
| 9 | RUN-12 settlement_commands coverage antibody | **PASS w/ MINOR** | 3/4 pass + 1 skipped (live_drift); CI antibody seeds closed position → calls enqueue_redeem_command → asserts row exists in settlement_commands; sed-break/restore meta-verify NOT performed (read-only critic); accepted on test-logic read (test_enqueue_creates_settlement_command_row would fail if INSERT at src/execution/settlement_commands.py:288-295 were removed) |
| 10 | Test drift (xfail/skip without removal criteria) | **PASS** | all new skips have explicit conditions (file-not-present, env-specific, "no economically_closed positions older than 24h"); no bare xfail introduced |
| 11 | Cycle/lifecycle hard-fail wiring | **PASS** | src/engine/lifecycle_events.py:37 `_non_empty` skips `unknown_entered_at` QUARANTINE_SENTINEL; src/state/db.py:6225-6228 update_trade_lifecycle raises BridgeAbsentError when bridge absent AND synthesizer fails; no silent except — verified by grep |
| 12 | station_migration_alerts.json runtime artifact | **MINOR (SEV-3)** | tracked at repo root (`station_migration_alerts.json`, 4-line empty JSON, written 2026-05-09); production code writes to `state/station_migration_alerts.json` per src/data/station_migration_probe.py:220 — repo-root copy is a stale legacy artifact; NOT in WAVE-2 delta (git diff origin/main..HEAD shows no change); should be .gitignored OR moved to `state/` and removed from tracking; carry-forward, NOT WAVE-2-blocking |

---

## SEV-2 MAJOR detail

### MAJ-1: F109 consolidator OVERBOOK over-void on unequal-share duplicates
**File**: `src/state/position_duplicate_consolidator.py:254-262` (function `consolidate`) and `:319-327` (function `consolidate_token`)
**Confidence**: HIGH

**Defect**: The OVERBOOK void loop subtracts row.shares from `excess` then breaks when `excess <= 1e-9`. When duplicate rows have unequal shares, this over-voids:

```python
excess = db_sum - chain_shares
for position_id, shares, _first_at in triples:
    if excess <= 1e-9:
        break
    _void_row(conn, position_id=position_id, reason=_VOIDED_REASON)
    voided_here.append(position_id)
    excess -= shares
```

Counter-example: shares=[3, 5], chain=4, db_sum=8, excess=4.
- Iter 1: void row1 (3), excess = 4-3 = 1. Loop continues (1 > 1e-9).
- Iter 2: void row2 (5), excess = 1-5 = -4. Loop exits.
- **Result**: both rows voided; remaining DB shares = 0; chain shares = 4. DB now under-represents on-chain exposure by 4 shares. No active row owns the chain holding.

This contradicts the module docstring claim at line 25-26: "The youngest active row owns the on-chain exposure going forward."

**Why this matters**: at first boot after the partial UNIQUE INDEX deploys, the consolidator runs over pre-existing duplicates. The migration pre-flight passes (no dups remain), so the index installs successfully — but the DB silently loses authority over a fraction of on-chain shares. Subsequent EXIT/redeem paths reading position_current would see zero exposure where chain holds N>0 shares; reconciliation paths would flag DIVERGENT but only AFTER positions are functionally unrecoverable from DB.

**Realist check**: production state (verified by direct `zeus_trades.db` SELECT) currently has exactly 1 duplicate token with `shares=(6.0, 6.0)` (equal). On this state, the loop happens to stop correctly (excess=6, iter 1 voids 6, excess=0, loop exits). So **this defect does not fire on current production state**.

**However**: per `feedback_antibody_recursion_metaverify_essential`, the antibody test suite at `tests/state/test_f109_consolidator_boot_wire.py` contains zero unequal-share test cases — all 5 tests use `shares=6.0` or single-row scenarios. The broken branch is invisible to the antibody. Per project methodology (CLAUDE.md "make the category impossible, not just the instance" + own protocol "contingent-state mitigations don't earn the realist-check downgrade"), this is SEV-2 MAJOR.

**Why this matters**: Karachi-class regressions occur precisely when the antibody covers the OBSERVED case but not the CATEGORY. The fix shipped here works for London 5/19 (equal shares) but a future Karachi-style asymmetric-share dup would silently corrupt DB authority — and the antibody would still go green.

**Fix shape** (~5 LOC + 1 test case):
```python
# Option A: void only when next row keeps cumulative_void <= excess + tolerance,
# else partial-void: update shares to (current_shares - remaining_excess)
voided_shares_cum = 0.0
for position_id, shares, _first_at in triples:
    remaining = excess - voided_shares_cum
    if remaining <= 1e-9:
        break
    if shares <= remaining + 1e-9:
        _void_row(conn, position_id=position_id, reason=_VOIDED_REASON)
        voided_here.append(position_id)
        voided_shares_cum += shares
    else:
        # partial-void: reduce shares on this row to (shares - remaining); do NOT phase->voided
        _reduce_shares(conn, position_id=position_id, by=remaining, reason=_VOIDED_REASON_PARTIAL)
        voided_shares_cum += remaining
        break

# Option B (simpler, more conservative): if any row's shares > remaining_excess, abort
# the consolidation for this token and classify DIVERGENT — operator handles manually.
# Karachi-safe; matches the "no manual precedent" directive's spirit by failing closed
# rather than silently corrupting authority.
```

Add antibody test: `test_consolidator_overbook_unequal_shares_does_not_corrupt_db_authority` with shares=[3, 5], chain=4, asserting remaining DB sum equals chain shares post-consolidation.

`consolidate_token` (lines 281-333) shares the same logic but has no production caller currently — fix in both functions for symmetry.

---

## SEV-3 MINOR findings

### MIN-1: K1 antibody scope narrowness
**File**: `tests/test_k1_reader_isolation.py:288` (`@pytest.mark.parametrize("script_name", K1_FIXED_SCRIPTS)`)
**Confidence**: HIGH

The K1 antibody scans only `bridge_oracle_to_calibration.py` and `evaluate_calibration_transfer_oos.py`. Bare `FROM validated_calibration_transfers` exists in `src/data/calibration_transfer_policy.py:854` and `src/engine/evaluator.py:555` — currently only called from tests (no production caller per grep), but if either gets wired to a `get_forecasts_connection_with_world()` MAIN-forecasts connection in a future PR, they become silent dead-reads matching exactly the F40/F41 pattern this antibody was meant to prevent permanently.

**Fix shape**: extend antibody to scan `src/data/calibration_transfer_policy.py` and `src/engine/evaluator.py` for the same world-class-table-unqualified pattern, OR add a project-wide grep in `tests/test_world_class_table_qualification.py` that scans all of src/ + scripts/ for `\bFROM\s+(?!world\.)<WORLD_ONLY_TABLE>`.

### MIN-2: Provenance header format drift
**Files**: `src/state/position_duplicate_consolidator.py:1`, `scripts/migrations/202605_position_current_idempotent_open_per_token.py:1`
**Confidence**: HIGH

CLAUDE.md mandates:
```
# Created: YYYY-MM-DD
# Last reused or audited: YYYY-MM-DD
# Authority basis: <law doc / Phase tag / spec ref>
```
Both files use the alternative `# Lifecycle: created=...; last_reviewed=...; last_reused=...` format. Semantically equivalent but breaks any tooling that greps for the canonical pattern (e.g., the file-header provenance auditor implied by the CLAUDE.md rule).

**Fix shape**: rewrite the two headers to the canonical format. ~4 LOC across 2 files.

### MIN-3: station_migration_alerts.json stale-tracked at repo root
**File**: `station_migration_alerts.json` (repo root)
**Confidence**: HIGH

Tracked file at repo root contains `{"alerts": [], "alerts_count": 0, "written_at": "2026-05-09T15:31:09.072472+00:00"}`. Production writers target `state/station_migration_alerts.json` per `src/data/station_migration_probe.py:220`. The repo-root copy is a stale legacy artifact (last touched 2026-05-09 by commit `2f436a6b21`). Not in WAVE-2 delta. Brief asks "why stashed during rebase rather than .gitignore'd?" — answer: pre-existing tracking; nobody removed it.

**Fix shape**: separate cleanup PR — `git rm station_migration_alerts.json` + add to .gitignore. Carry-forward, NOT WAVE-2-blocking.

---

## What's missing (gap analysis)

- **No antibody for F109 consolidator on unequal-share duplicates** (covered above as MAJ-1).
- **No antibody for consolidate_token reachability**: function exists but has no production caller. Either wire it to `update_trade_lifecycle` per its docstring, or delete it (dead code accumulates lint debt and obscures the lifecycle contract).
- **No sed-break/restore meta-verify for RUN-12 antibody** in this critic pass (read-only); previous critic claimed it ran; accepted on faith but recorded as carry-forward.
- **No prod-state snapshot** in the F109 trace doc (`docs/operations/task_2026-05-17_f109_fix/TRACE.md` line 56 says "DB sum=12, chain=6" — but doesn't enumerate the 17 pre-existing orphan rows from the migration trigger comment to confirm they all have equal shares). Without this, the realist-check downgrade rests on a single SQL query I ran at critic-time, not on a documented invariant.

---

## Multi-perspective notes

- **Executor**: F109 consolidator function signature is clean; the docstring claim about "youngest active row owns chain exposure" is at odds with the loop semantics — would expect a careful executor to ship a partial-void or DIVERGENT-on-asymmetric guard. Recommend executor reread MAJ-1.
- **Stakeholder**: PR delivers all 12 probe contracts; F44 + F109 + Karachi bridge + opt-in defaults all land correctly. The single MAJOR is small, well-localized, and fixable in <30 minutes. Open-after-fix.
- **Skeptic**: 96 files / +14k LOC PR finding only 1 MAJOR is suspicious. Confidence check: I read all 17 new files' headers, all 12 probe sites' citations, and ran 4 of the 5 named regression test suites end-to-end. I did NOT review every modified file in the bulk-changed set (cycle_runtime.py, evaluator.py, db.py, projection.py have hundreds of lines of diff each — only the WAVE-2-relevant slices were verified). A more adversarial pass across the entire diff could surface additional findings. Recommend pairing a second critic for the bulk-touched files in a follow-up review.

---

## Verdict justification

NEEDS-FIX-BEFORE-OPEN because:
1. The F109 over-void defect violates project methodology even when contingent-state-mitigated. Per the user's own protocol ("contingent-state mitigations don't earn the realist-check downgrade") and `feedback_antibody_recursion_metaverify_essential`, the antibody coverage gap is itself the finding — fixing prod-state-today is necessary but not sufficient.
2. Fix is ~5 LOC + 1 test case — too small to defer to a separate PR per `feedback_pr_unit_of_work_not_loc` (fix is structurally part of this PR's F109 thread).
3. After MAJ-1 fix: APPROVE-FOR-OPEN. The 3 MINORs are carry-forward.

No ADVERSARIAL escalation triggered (1 MAJOR < 3-MAJOR threshold). Skeptic-perspective recommends paired-critic for bulk-touched files but does not block.

---

## Open questions (unscored)

- Should `consolidate_token` be wired to `update_trade_lifecycle` per its docstring, or deleted as dead code? Operator decision.
- Is the F109 partial UNIQUE INDEX's deploy-order assumption ("consolidator runs first") enforced by any test other than the migration's own pre-flight raise? (Currently relies on the boot sequence in src/main.py being correct; reordering would break silently at first boot rather than at test time.)
- station_migration_alerts.json: separate cleanup PR or roll into WAVE-3 housekeeping batch?

---

*Brief deviation: regression baseline expected "148 passed, 5 skipped" — the 5 named test files only collect 54 tests; actual 53 passed + 1 skipped matches collection. Brief count was wrong, not a regression.*
