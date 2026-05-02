# Identity-column `DEFAULT` repair — operator unblock procedure

**Status**: SCANNER LANDED (regression-locked); KERNEL DDL EDIT AWAITING `ARCH_PLAN_EVIDENCE`
**Filed**: 2026-05-01 by team-lead during ultrareview25_remediation P1-2
**Related**: `scripts/check_identity_column_defaults.py`, `tests/test_identity_column_defaults.py`

## TL;DR

Architect K-D found 4 `DEFAULT 'high'` sites on `temperature_metric` (an INV-14 identity column). Depth audit added 1 `DEFAULT 'v1'` site on `data_version` (same identity-default failure shape). The scanner + test wrapper landed today **lock the 5-site baseline** so a 5th occurrence cannot land silently. The actual DDL repair (drop the DEFAULT, replace with INSERT-side discipline + legacy-row migration) requires editing `architecture/2026_04_02_architecture_kernel.sql`, which is gated by the `pre-edit-architecture` hook. The hook needs `ARCH_PLAN_EVIDENCE=docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md` set on the operator's session.

## Sites cataloged in baseline

| # | Column | File | Line | Why dangerous |
|---|---|---|---|---|
| 1 | `temperature_metric` | `architecture/2026_04_02_architecture_kernel.sql` | 129 | Kernel-level DDL for `selection_family_fact`. New rows depend on INSERT-side discipline; DEFAULT silently routes any missing-value INSERT to HIGH. |
| 2 | `temperature_metric` | `src/state/db.py` | ~515 | `init_schema` runtime DDL for `selection_family_fact`. Same as #1 in the runtime path. |
| 3 | `temperature_metric` | `src/state/db.py` | ~1559 | `ALTER TABLE position_current ADD COLUMN`. **Has assert `row_count == 0`** at the migration site — the DEFAULT only applies during a Zero-Data Golden Window. **Practically safe today**, structurally lazy. |
| 4 | `temperature_metric` | `src/state/db.py` | ~1581 | `ALTER TABLE ensemble_snapshots ADD COLUMN`. **NO row count assertion**. **GENUINELY DANGEROUS**: legacy rows get silently labeled HIGH. |
| 5 | `data_version` | `src/state/db.py` | 513 | `init_schema` for `ensemble_snapshots`. New rows depend on INSERT-side discipline; DEFAULT silently labels any missing-value INSERT as `v1`. |

## Per-site repair recipe

### Site #1 — `architecture/2026_04_02_architecture_kernel.sql:129`

```sql
-- BEFORE
temperature_metric TEXT NOT NULL DEFAULT 'high' CHECK (temperature_metric IN ('high', 'low'))

-- AFTER
temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low'))
```

Add antibody: `tests/test_architecture_contracts.py::test_inv14_selection_family_fact_requires_explicit_temperature_metric` that runs the kernel schema, attempts an INSERT WITHOUT `temperature_metric`, and asserts `sqlite3.IntegrityError` with `NOT NULL constraint failed: selection_family_fact.temperature_metric`.

### Site #2 — `src/state/db.py:~515`

Same edit shape, runtime DDL string.

### Site #3 — `src/state/db.py:~1559`

This site is gated by the row-count assertion above it. Three options:
- **(a) Drop DEFAULT, keep assertion**: relies on the same NOT NULL semantic; safe because the assertion guarantees no rows exist.
- **(b) Leave as-is**: documented contract is "Zero-Data Golden Window only". Add a comment cross-reference to this file.
- **(c) Defer**: the migration logic is tied up with idempotent re-run guarantees; touching it has secondary risk.

Recommended: **(a)** — keeps consistency with sites #1 and #2, doesn't rely on the assertion as a load-bearing safety mechanism.

### Site #4 — `src/state/db.py:~1581` — **CLOSED-AS-FALSE-ALARM (operator ruling 2026-05-01)**

Original concern: this ALTER on `ensemble_snapshots` runs without a row-count assertion, so legacy rows would get DEFAULT-labeled silently.

**Operator review 2026-05-01 reasoning chain**:
1. `ensemble_snapshots` (legacy) has **zero runtime writers**. Every current snapshot write path goes to `ensemble_snapshots_v2`. `grep` confirms no `INSERT INTO ensemble_snapshots` (non-v2) anywhere in `src/`.
2. **All existing rows genuinely ARE high-track**. The `temperature_metric` column was added during P10D S3 (the commit that introduced HIGH/LOW duality / C2 inversion). Before that migration ran, only HIGH-track data existed. Every row already in `ensemble_snapshots` was written in a world where there was no LOW track. `DEFAULT 'high'` labeled them correctly, not silently-wrong.
3. The harvester already assumes `'high'` for these rows — `_snapshot_select_expr(columns, "temperature_metric", "'high'")` at `src/execution/harvester.py:1223` falls back to the literal `'high'` in SELECT when the column isn't present. The DEFAULT just made that explicit at the column level.
4. **No future INSERT can be mislabeled**. The table is write-frozen, so the DEFAULT can never fire again.

**Verdict**: **No fix needed**. The scanner correctly reports 1 known-baseline occurrence (this site). That's the steady state — a documented historical artifact, not an active hazard. If desired in the future, a cosmetic DDL pass could drop the DEFAULT, but it carries zero math benefit.

### Site #5 — `src/state/db.py:513` (`data_version DEFAULT 'v1'`)

Same shape as #1/#2 for `data_version` instead of `temperature_metric`.
- Drop the DEFAULT on `init_schema`.
- Add antibody: assert INSERT without `data_version` fails.
- Backfill: any existing rows where `data_version='v1'` was applied as the silent default → re-stamp from `model_version` lineage where derivable, or QUARANTINE.

## Operator-side procedure

```bash
# 1. Set plan evidence so the pre-edit-architecture hook unblocks
export ARCH_PLAN_EVIDENCE=docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md

# 2. Apply the kernel SQL edit (site #1 first; cleanest)
$EDITOR architecture/2026_04_02_architecture_kernel.sql  # delete `DEFAULT 'high'` on line ~129

# 3. Apply runtime DDL edits (sites #2, #3, #5; skip #4 until the migration is built)
$EDITOR src/state/db.py

# 4. Re-run scanner and test to confirm KNOWN_DEFAULTS shrinks
.venv/bin/python scripts/check_identity_column_defaults.py
.venv/bin/python -m pytest tests/test_identity_column_defaults.py -v

# 5. The test will fail with "P1-2 housekeeping: ... once known are now GONE".
#    Update scripts/check_identity_column_defaults.py:
#      - Remove the repaired entry from _BASELINE_KNOWN_DEFAULTS
#      - Decrement the matching count in _BASELINE_OCCURRENCE_COUNTS
#    AND remove the matching expectation in
#    tests/test_identity_column_defaults.py if needed (the test reads
#    from the script directly; no separate edit needed).

# 6. Re-run; should pass cleanly.
.venv/bin/python -m pytest tests/test_identity_column_defaults.py -v

# 7. Commit. The pre-commit hook now also enforces this (BASELINE_PASSED ↑).
```

## Why I did not do the kernel edit inline

`pre-edit-architecture.sh` blocks Edit/Write to `architecture/**` without `ARCH_PLAN_EVIDENCE`. The hook is correct — invariants/architecture are law-layer. I cannot set environment variables that persist across the Claude Code tool boundary, so the env var must be exported in the operator's shell before re-prompting. The scanner + regression test landed regardless; they are the K-D structural antibody (no 5th DEFAULT can slip in silently). The DDL repair itself is a small operator-side task once unblocked.
