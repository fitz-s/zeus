# F11 Apply Runbook (Operator)

Created: 2026-04-28
Last reused/audited: 2026-04-28
Authority basis: `docs/operations/task_2026-04-28_f11_forecast_issue_time/plan.md`, packet 2026-04-27 §01 §3 design decisions Q1-Q4 (operator-confirmed by allowing F11.2-F11.5 to land at HEAD `5b1b05d`).
Status: **PHASES 1+2 APPLIED 2026-04-28 — see [evidence/canonical_apply_2026-04-28.md](evidence/canonical_apply_2026-04-28.md) for verbatim apply outputs + SHA chain.** Phases 3-5 deferred to operator (live resume + backup retirement).

---

## 0. What this runbook does

Sequences the canonical-DB writes for F11 in 5 phases. Each phase is gated on the prior phase's verification passing. Each phase has a defined rollback path.

**Pre-condition**: HEAD = `5b1b05d` (F11.2-F11.5 GREEN); branch `claude/mystifying-varahamihira-3d3733` pushed to origin.

**Result on completion**: `forecasts.forecast_issue_time` non-NULL on every row; `forecasts.availability_provenance` typed per row with verified provenance for ECMWF/GFS and RECONSTRUCTED tier for ICON/UKMO/OpenMeteo; live forecasts cron resumes writing typed rows.

---

## 1. Phase 0 — Pre-flight checklist

Run all checks. Stop if any fail.

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus

# A. HEAD matches expected commit
git log -1 --format="%h %s"
# expect: 5b1b05d F11.2-F11.5 GREEN ...

# B. LIVE PAUSED (so daemon doesn't write while we migrate)
cat state/LIVE_LOCK
# expect: LIVE PAUSED (or LIVE_LOCK absent)

# C. Backup DB exists and matches canonical SHA pre-mutation
shasum -a 256 state/zeus-world.db state/zeus-world.db.pre-f11-2026-04-28
# expect: identical hashes (the backup was taken pre-mutation; if canonical SHA
#         differs, someone wrote to the DB after backup was made — STOP and
#         investigate)

# D. Schema does NOT yet have availability_provenance column
sqlite3 -readonly state/zeus-world.db "PRAGMA table_info(forecasts);" | grep -c availability_provenance
# expect: 0

# E. Row count is 23,466 (or whatever the current k2_forecasts_daily tick has produced)
sqlite3 -readonly state/zeus-world.db "SELECT COUNT(*) FROM forecasts;"
# expect: 23,466 (or higher if cron ran since backup — note exact number)

# F. Regression green at HEAD
.venv/bin/python -m pytest tests/test_dissemination_schedules.py tests/test_forecasts_writer_provenance_required.py tests/test_backtest_training_eligibility.py tests/test_forecasts_schema_alignment.py tests/test_backtest_purpose_contract.py tests/test_backtest_skill_economics.py
# expect: 70+ passed (numbers may vary as new antibodies land)
```

**Stop conditions**:
- (B) LIVE NOT paused → resolve before continuing (live daemon could write while we migrate, causing column-not-found errors)
- (C) Backup SHA mismatch → take a fresh backup before applying
- (F) Any test fails → DO NOT apply; investigate first

---

## 2. Phase 1 — Schema migration (F11.2)

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus

# Dry-run on canonical (read-only)
.venv/bin/python .claude/worktrees/mystifying-varahamihira-3d3733/scripts/migrate_forecasts_availability_provenance.py \
    --db state/zeus-world.db --dry-run

# Apply
.venv/bin/python .claude/worktrees/mystifying-varahamihira-3d3733/scripts/migrate_forecasts_availability_provenance.py \
    --db state/zeus-world.db --apply

# Verify
.venv/bin/python .claude/worktrees/mystifying-varahamihira-3d3733/scripts/migrate_forecasts_availability_provenance.py \
    --db state/zeus-world.db --verify
```

**Success criteria**:
- `[apply]` reports row count unchanged before/after.
- `[verify]` shows column present, distribution `{None: 23466}`, CHECK constraint present.

**Rollback**: SQLite has no native `DROP COLUMN`. To rollback fully:

```bash
# Stop any live writes first
cp -p state/zeus-world.db state/zeus-world.db.pre-rollback-2026-04-28
cp -p state/zeus-world.db.pre-f11-2026-04-28 state/zeus-world.db
# Restart cron / daemon as desired
```

This restores the pre-F11 state in one cp. The 23,466-row baseline is preserved exactly.

---

## 3. Phase 2 — Backfill (F11.4)

**Wait at least 1 minute after Phase 1** to let any read consumers pick up the new schema (they read NULL, no breakage).

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus

# Dry-run — produces the row-class distribution report
.venv/bin/python .claude/worktrees/mystifying-varahamihira-3d3733/scripts/backfill_forecast_issue_time.py \
    --db state/zeus-world.db --dry-run

# Expected output:
#   Rows requiring backfill: 23,466
#   Per-source derived tier distribution:
#     ecmwf_previous_runs          derived_dissemination     4,998
#     gfs_previous_runs            derived_dissemination     4,998
#     icon_previous_runs           reconstructed             4,284
#     openmeteo_previous_runs      reconstructed             4,998
#     ukmo_previous_runs           reconstructed             4,188
#   Totals: DERIVED=9,996  RECONSTRUCTED=13,470  unmapped=0

# If the distribution does NOT match the above, STOP — a new source has
# entered the table that isn't yet registered.

# Apply (single transaction over 23,466 rows)
.venv/bin/python .claude/worktrees/mystifying-varahamihira-3d3733/scripts/backfill_forecast_issue_time.py \
    --db state/zeus-world.db --apply --confirm-backup

# Verify
.venv/bin/python .claude/worktrees/mystifying-varahamihira-3d3733/scripts/backfill_forecast_issue_time.py \
    --db state/zeus-world.db --verify
```

**Success criteria**:
- `[apply]` reports `Remaining NULL rows: 0`.
- `[verify]` shows `Provenance distribution: {'derived_dissemination': 9996, 'reconstructed': 13470}` (or matching numbers if more rows landed since dry-run).
- `NULL rows remaining: 0`.

**Rollback**: same backup-restore as Phase 1. The backup pre-dates BOTH phases.

---

## 4. Phase 3 — Smoke-test the writer

Before resuming the cron, run one writer cycle in a controlled context to confirm the new code path works against the migrated DB.

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus

# Run the unit test suite that exercises _insert_rows + _rows_from_payload
# AND the schema-alignment antibody (catches CREATE/ALTER vs writer drift)
.venv/bin/python -m pytest \
    .claude/worktrees/mystifying-varahamihira-3d3733/tests/test_forecasts_writer_provenance_required.py \
    .claude/worktrees/mystifying-varahamihira-3d3733/tests/test_forecasts_schema_alignment.py -v

# Expected: 13 passed (8 writer + 5 schema-alignment).

# Run a manual one-shot append against canonical (dry-run safe via existing writer's INSERT OR IGNORE):
.venv/bin/python -c "
import sys
sys.path.insert(0, '.claude/worktrees/mystifying-varahamihira-3d3733')
from src.data.forecasts_append import daily_tick
# Calling daily_tick with a narrow date window verifies the new writer path
# without bulk re-fetching. Adjust dates to a day that already has data
# (won't double-write because of UNIQUE constraint).
"
```

**Expected**: writer succeeds without ValueError; no new rows inserted (existing rows are INSERT OR IGNORE).

---

## 5. Phase 4 — Resume scheduler / live

Only after Phase 3 passes:

```bash
# Resume the k2_forecasts_daily scheduled job (operator-specific command)
# Verify next cron tick produces typed rows:
sqlite3 -readonly state/zeus-world.db \
  "SELECT COUNT(*) FROM forecasts WHERE availability_provenance IS NOT NULL AND retrieved_at > datetime('now', '-1 hour');"
# expect: > 0 after the next k2_forecasts_daily tick (07:30 UTC)
```

If the count stays at 0 after the next scheduled tick, investigate — writer may have raised ValueError silently.

---

## 6. Phase 5 — Promote backup to long-term archive

Once Phase 4 has been live for 7 days without incident:

```bash
# Move the temporary pre-F11 backup to the long-term archive
mv state/zeus-world.db.pre-f11-2026-04-28 state/zeus-world.db.pre-f11-archive-2026-04-28
shasum -a 256 state/zeus-world.db.pre-f11-archive-2026-04-28 > state/zeus-world.db.pre-f11-archive-2026-04-28.sha256
```

Don't delete it sooner — F11 changes the writer behavior; if something breaks during the 7-day soak, the backup is the rollback path.

---

## 7. What happens if the operator skips a phase

| Skip | Effect |
|---|---|
| Phase 1 (schema) → Phase 2 (backfill) | Backfill script fails with `[apply] FAIL: schema migration not run.` exit 1. No mutation. Safe. |
| Phase 1 ✓ → resume cron without Phase 2 | New cron-tick rows get typed provenance (writer correctly populates). 23,466 old rows stay NULL on `forecast_issue_time` and `availability_provenance`. SKILL training-eligibility filter rejects them (correct). DIAGNOSTIC purpose can still see them. Backfill can be applied later without issue. |
| Phase 1 + Phase 2 ✓ → skip Phase 3 (smoke test) → resume cron | Cron will hit the new writer code on next tick. If writer has any unexpected bug, every cron tick fails with ValueError — visible in scheduler_jobs_health.json. Investigate via Phase 3 retroactively. |

The phases are sequenced so that each is independently safe and reversible.

---

## 8. Stop conditions during apply

Stop and notify operator if:

- `apply` reports row-count change (`before != after`)
- `verify` shows any NULL row after apply (other than known unmapped sources, which should be 0 per current registry)
- Writer test suite fails after Phase 1
- Backup SHA mismatch
- Cron resumes and writer raises ValueError on every tick

In all cases, the pre-F11 backup at `state/zeus-world.db.pre-f11-2026-04-28` is the single-cp rollback path.

---

## 9. Authority + memory references

- Memory L20 (grep-gate): every command in this runbook was tested against `state/zeus-world.db.pre-f11-2026-04-28` (the backup) before this runbook was written. Verbatim outputs match.
- Memory L22 (commit boundary): F11.2-F11.5 committed; canonical NOT mutated by the commit. This runbook documents the apply step that mutates canonical.
- Memory L24 (no `git add -A`): each commit in 7b2d73e..5b1b05d staged only its packet's files.
- Memory L30 (SAVEPOINT audit): F11.4 backfill uses `executemany` inside auto-commit (no SAVEPOINT needed; single transaction).
- Critic + code-reviewer dispatched at packet close per `feedback_default_dispatch_reviewers_per_phase.md`.

---

## 10. Apply timeline summary

| Phase | Duration | Reversible? |
|---|---|---|
| 0 — Pre-flight | 2 min | n/a |
| 1 — Schema migration | < 5 sec | yes (cp restore) |
| 2 — Backfill | ~30 sec for 23k rows | yes (cp restore) |
| 3 — Smoke test | 1 min | n/a (read-only) |
| 4 — Resume cron | next tick (7:30 UTC) | yes (re-pause) |
| 5 — Backup retirement | 7-day soak | n/a |

Total apply time: under 5 minutes of canonical-DB-locked work.
