# P-B Schema Migration Plan — Pre-Packet (awaiting critic-opus pre-review)

**Packet**: P-B (schema migration — INV-14 identity + provenance_json + trigger)
**Goal**: additively extend the `settlements` schema with INV-14 identity columns + `provenance_json` + authority-monotonic trigger, and retrofit-stamp the 1556 existing rows with bulk-writer provenance. Unblocks P-F (hard quarantine) and P-E (DELETE+INSERT reconstruction).
**Date**: 2026-04-23
**Executor**: team-lead
**Pending**: critic-opus PRE-REVIEW before any ALTER TABLE or trigger creation
**Planning-lock scope**: K0-adjacent schema change in `src/state/db.py` (K2_runtime per source_rationale.yaml, but canonical-DB schema is planning-locked per AGENTS.md §Planning lock)

---

## Section 1 — Scope and rationale

### 1.1 Current settlements schema (src/state/db.py:158-169 canonical + migrated ALTER TABLE columns)

```sql
CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    market_slug TEXT,
    winning_bin TEXT,
    settlement_value REAL,
    settlement_source TEXT,
    settled_at TEXT,
    authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
    -- Migrated via ALTER TABLE ADD COLUMN (already present in live DB):
    pm_bin_lo REAL,
    pm_bin_hi REAL,
    unit TEXT,
    settlement_source_type TEXT,
    UNIQUE(city, target_date)
);
```

**Reads** (grep src/): `src/engine/monitor_refresh.py:472` (`SELECT settlement_value`), and the 1562→1556 bulk rows consumed by P-C audit + replay/calibration.

**Writes** (grep src/): `src/execution/harvester.py:549,560` (`UPDATE` and `INSERT INTO settlements`). No other writers in src/. Ghost bulk writer (per P-A) has no source file.

**Triggers currently on `settlements`**: none (sqlite_master empty for this table). Other tables use `BEFORE DELETE / BEFORE UPDATE → RAISE(ABORT)` pattern (see `control_overrides_history_no_delete`, `trg_position_events_no_update`).

### 1.2 Required additions for downstream packets

Per P-0 first_principles.md §2 (INV-FP-1..10) and the P-G structural finding (HIGH/LOW metric-identity collision on 2026-04-15):

| Column                | Type / CHECK                                                                                       | Required for                       |
|-----------------------|----------------------------------------------------------------------------------------------------|------------------------------------|
| `temperature_metric`  | TEXT, CHECK IN ('high', 'low')                                                                     | **INV-14 identity spine**; P-G-exposed HIGH/LOW collision; P-E DELETE+INSERT MUST set this |
| `physical_quantity`   | TEXT (e.g., 'daily_maximum_air_temperature', 'daily_minimum_air_temperature')                      | INV-14 cross-track join safety     |
| `observation_field`   | TEXT, CHECK IN ('high_temp', 'low_temp')                                                           | INV-14 obs-field binding (maps to observations.high_temp / low_temp) |
| `data_version`        | TEXT (e.g., 'wu_icao_history_v1', 'hko_daily_api_v1')                                              | INV-14 versioning; P-E provenance chain |
| `provenance_json`     | TEXT (JSON blob)                                                                                   | **INV-FP-1 unbroken provenance**; P-A retrofit for 1556 bulk rows; P-E INSERT-time stamping |

All columns nullable (no NOT NULL) to permit the ALTER TABLE ADD COLUMN on pre-existing rows. Post-P-E every row carries these values populated; nullability is strict only via CHECK on P-E's INSERT path (enforced in code, not schema, to preserve ALTER-TABLE compatibility).

### 1.3 Authority-monotonic trigger (INV-FP-5)

Per first_principles.md §2 INV-FP-5: "Authority is monotonic and earned... QUARANTINED → VERIFIED only via explicit re-activation packet". Currently the schema has a CHECK on `authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')` but no transition rules. P-B adds a `BEFORE UPDATE` trigger that forbids:
- `VERIFIED → UNVERIFIED` (downgrading earned authority silently)
- `QUARANTINED → VERIFIED` without explicit marker in `provenance_json`

**Reactivation contract (per critic-opus C2)**: to transition QUARANTINED→VERIFIED, the NEW row's `provenance_json` MUST contain a top-level JSON key `reactivated_by` with a non-null string value naming the authorizing packet or decision. Example:

```json
{
  "reactivated_by": "R3-XY",
  "decision_doc": "docs/operations/.../evidence/reactivation_xy.md",
  "reactivated_at": "2026-MM-DDThh:mm:ssZ"
}
```

The trigger enforces this via `json_extract(NEW.provenance_json, '$.reactivated_by') IS NULL` (true means "no reactivation marker" → ABORT). Substring-LIKE is avoided per critic-opus C1: `LIKE '%reactivated_by%'` would false-match against `{"not_reactivated_by": ...}` and other literal substring occurrences.

```sql
CREATE TRIGGER IF NOT EXISTS settlements_authority_monotonic
BEFORE UPDATE OF authority ON settlements
WHEN OLD.authority = 'VERIFIED' AND NEW.authority = 'UNVERIFIED'
  OR (OLD.authority = 'QUARANTINED' AND NEW.authority = 'VERIFIED'
      AND (NEW.provenance_json IS NULL
           OR json_extract(NEW.provenance_json, '$.reactivated_by') IS NULL))
BEGIN
    SELECT RAISE(ABORT,
        'settlements.authority transition forbidden: VERIFIED→UNVERIFIED not allowed; '
        'QUARANTINED→VERIFIED requires provenance_json.reactivated_by marker');
END;
```

### 1.4 One-time backfill for 1556 existing rows (P-A closure)

Per P-A verdict and first_principles.md §6: every bulk-batch row gets `provenance_json` stamped with the unregistered-writer reason. Atomically, via a single `UPDATE settlements SET provenance_json = ? WHERE provenance_json IS NULL`. Content:

```json
{
  "writer": "unregistered_bulk_writer_2026-04-16",
  "rca_doc": "docs/operations/task_2026-04-23_data_readiness_remediation/evidence/bulk_writer_rca_final.md",
  "reason": "provenance_hostile_bulk_batch",
  "inferred_source_json": "data/pm_settlement_truth.json",
  "inferred_source_json_confidence": 0.9,
  "writer_logic_inferred": "settlement_value = pm_bin_lo if pm_bin_lo == pm_bin_hi else NULL; JSON sentinels -999/999 mapped to SQL NULL",
  "audit_packets": ["P-A", "P-C", "P-G"],
  "settled_at_all_rows": "2026-04-16T12:39:58.026729+00:00",
  "retrofit_date": "2026-04-23"
}
```

R1 (per critic-opus recommendation, applied): `writer_logic_inferred` + `inferred_source_json_confidence` embedded so that a future reader with only a DB snapshot — no repo access — can reconstruct the writer's behavior and know its identification confidence. Cost: ~200 B × 1556 rows = ~312 KB total. Negligible.

This retrofit does NOT touch any other column. It does NOT change `authority`. It does NOT introduce new semantic claims. It records the (already-known) provenance hole explicitly so downstream (P-F, P-E) can filter / requalify.

### 1.5 Explicitly NOT in P-B scope

- DELETE/INSERT reconstruction of the 1556 rows → **P-E**
- Population of `temperature_metric` / `observation_field` / `data_version` for existing rows → **P-E** (on re-insert; retrofitting here would claim identity we haven't earned)
- Hard-quarantine policy writes → **P-F**
- Harvester atomicity refactor → **P-H**
- Updates to `src/execution/harvester.py::_write_settlement_truth` to stamp new columns → deferred to DR-33 implementation packet (P-E-adjacent)
- Adding INSERT-time CHECK for non-null of new columns → deferred until all writers updated

---

## Section 2 — Execution design

### 2.1 Migration location: `src/state/db.py` migration list

Following the established pattern at `src/state/db.py:748-819` (dozens of `try: conn.execute(ALTER...); except sqlite3.OperationalError: pass` idempotent migrations), P-B adds its own block after the existing trade_decisions/platt_models/calibration_pairs migrations.

```python
# P-B (2026-04-23): INV-14 identity spine + provenance_json + authority-monotonic trigger.
# See docs/operations/task_2026-04-23_data_readiness_remediation/evidence/pb_schema_plan.md
for ddl in [
    "ALTER TABLE settlements ADD COLUMN temperature_metric TEXT "
        "CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low'));",
    "ALTER TABLE settlements ADD COLUMN physical_quantity TEXT;",
    "ALTER TABLE settlements ADD COLUMN observation_field TEXT "
        "CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp'));",
    "ALTER TABLE settlements ADD COLUMN data_version TEXT;",
    "ALTER TABLE settlements ADD COLUMN provenance_json TEXT;",
]:
    try:
        conn.execute(ddl)
    except sqlite3.OperationalError:
        pass

# Authority-monotonic trigger (INV-FP-5). Idempotent via IF NOT EXISTS.
try:
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS settlements_authority_monotonic
        BEFORE UPDATE OF authority ON settlements
        WHEN (OLD.authority = 'VERIFIED' AND NEW.authority = 'UNVERIFIED')
          OR (OLD.authority = 'QUARANTINED' AND NEW.authority = 'VERIFIED'
              AND (NEW.provenance_json IS NULL
                   OR json_extract(NEW.provenance_json, '$.reactivated_by') IS NULL))
        BEGIN
            SELECT RAISE(ABORT,
                'settlements.authority transition forbidden: VERIFIED->UNVERIFIED not allowed; '
                'QUARANTINED->VERIFIED requires provenance_json.reactivated_by marker');
        END;
    """)
except sqlite3.OperationalError:
    pass
```

**Why CHECK uses `IS NULL OR value IN (...)`**: to permit NULLs on pre-existing rows while still forbidding invalid non-null values. Post-P-E every row must be NOT NULL, but the schema itself stays permissive to allow the staged migration.

**Why CREATE TABLE settlements isn't rewritten to include the new columns in the canonical definition**: SQLite ALTER TABLE ADD COLUMN is idempotent via try/except, and modifying CREATE TABLE wouldn't help fresh-init (ALTER runs after CREATE). In a later hygiene pass (not P-B), the canonical CREATE TABLE at L158-169 should be updated to match for clarity. I do NOT touch it in P-B to keep the diff minimal and focused.

### 2.2 One-time backfill script

Separate script `docs/operations/task_2026-04-23_data_readiness_remediation/evidence/scripts/pb_backfill_provenance.py`:

```python
PROVENANCE_JSON = json.dumps({
    "writer": "unregistered_bulk_writer_2026-04-16",
    "rca_doc": "docs/operations/task_2026-04-23_data_readiness_remediation/evidence/bulk_writer_rca_final.md",
    "reason": "provenance_hostile_bulk_batch",
    "inferred_source_json": "data/pm_settlement_truth.json",
    "audit_packets": ["P-A", "P-C", "P-G"],
    "settled_at_all_rows": "2026-04-16T12:39:58.026729+00:00",
    "retrofit_date": "2026-04-23",
})

# Single transaction with pre/post verify, identical safety pattern to P-G runner
# 1. BEGIN IMMEDIATE
# 2. SELECT COUNT(*) WHERE provenance_json IS NULL → expect 1556
# 3. UPDATE settlements SET provenance_json = ? WHERE provenance_json IS NULL
# 4. changes() == 1556 → else ROLLBACK
# 5. SELECT COUNT(*) WHERE provenance_json IS NULL → expect 0
# 6. COMMIT
```

### 2.3 Snapshot + test + run

Before any migration run:
1. `cp state/zeus-world.db state/zeus-world.db.pre-pb_2026-04-23` + md5 record
2. Run `pytest -q tests/test_schema_v2_gate_a.py tests/test_canonical_position_current_schema_alignment.py` on the snapshot-equivalent DB → baseline PASS expected
3. Execute P-B migration (import `src/state/db.py:init_db` OR run the `ALTER TABLE` statements directly via `sqlite3`). **Prefer direct sqlite3 execution** to keep the migration idempotent and independent of app startup (safer for this high-stakes packet).
4. Run `pb_backfill_provenance.py` (1 UPDATE, 1556 rows)
5. Run the same pytest suite again → should still PASS (additive migration; no regression expected)
6. Run `python3 -c "import sqlite3; c = sqlite3.connect('state/zeus-world.db'); c.executescript('SELECT name FROM sqlite_master WHERE tbl_name=\\\"settlements\\\";')"` and verify trigger exists
7. Run `sqlite3 state/zeus-world.db ".schema settlements"` → confirm 5 new columns present

### 2.4 Authority-monotonic trigger validation (post-migration)

After trigger creation, validate behavior via 4 test UPDATEs against a TEMPORARY row (or a test DB):

- `UPDATE ... SET authority='UNVERIFIED' WHERE authority='VERIFIED'` → should ABORT
- `UPDATE ... SET authority='VERIFIED' WHERE authority='QUARANTINED' AND provenance_json IS NULL` → should ABORT
- `UPDATE ... SET authority='VERIFIED' WHERE authority='QUARANTINED' AND provenance_json LIKE '%reactivated_by%'` → should SUCCEED
- `UPDATE ... SET authority='QUARANTINED' WHERE authority='VERIFIED'` → should SUCCEED (downgrades are allowed; upgrades restricted)

These 4 validations run against a scratch row created + deleted for the purpose in the execution log. No production row touched.

---

## Section 3 — Q1-Q10

**Q1 (invariant)**:
- INV-14 (metric identity 4 fields): the 5 new columns implement this directly.
- INV-FP-1 (provenance chain unbroken): `provenance_json` is the provenance vehicle.
- INV-FP-5 (authority monotonic and earned): trigger enforces the constraint.
- INV-03 (append-first projection): P-B is schema-evolution, not a violation. Retrofit UPDATE writes to an existing column that was NULL; doesn't violate append semantics.

**Q2 (fatal_misread)**: `code_review_graph_answers_where_not_what_settles` — the migration adds identity COLUMNS for the metric-identity distinction; it does NOT encode what settles (that's per-row data for P-E). No fatal misread risk.

**Q3 (single-source-of-truth)**:
- Migration DDL is copied verbatim from §2.1; reviewer can grep-diff against `src/state/db.py` post-commit.
- 1556 backfill count matches post-P-G row count; verified via `SELECT COUNT(*) FROM settlements`.
- PROVENANCE_JSON content is fixed constant in the script; content matches RCA doc path.

**Q4 (first-failure)**:
- ALTER TABLE fails: try/except pattern swallows `sqlite3.OperationalError` (e.g., column already exists) — this is INTENDED (idempotent migration).
- Trigger creation fails with non-"already exists" error: raises; ROLLBACK.
- Backfill pre-count mismatch: ROLLBACK, HALT, investigate.
- Backfill UPDATE changes() != 1556: ROLLBACK, HALT.
- Post-count mismatch: ROLLBACK, HALT.
- Tests fail post-migration: HALT, investigate. Snapshot rollback viable via `state/zeus-world.db.pre-pb_2026-04-23`.

**Q5 (commit boundary)**: 2 transaction boundaries:
1. DDL (5 ALTER + 1 TRIGGER) — run sequentially; each ALTER is implicitly atomic by SQLite semantics. Trigger creation is idempotent via IF NOT EXISTS.
2. Backfill UPDATE — single BEGIN/COMMIT with pre/post verify.

DDL and backfill are separate because failed DDL (column-already-exists) is expected and non-fatal, while failed backfill is a real signal.

**Q6 (verification)**:
- Pre-migration: pytest baseline (test_schema_v2_gate_a + test_canonical_position_current_schema_alignment); snapshot + md5
- Per-column: `sqlite3 .schema settlements` shows 5 new columns with correct types/CHECKs
- Trigger: `sqlite3 "SELECT name FROM sqlite_master WHERE type='trigger' AND name='settlements_authority_monotonic'"` → 1 row
- Backfill: `SELECT COUNT(*) FROM settlements WHERE provenance_json IS NOT NULL` → 1556
- Post-migration: pytest re-run → 0 regressions
- Trigger behavior: 4 UPDATE cases on scratch row → 2 ABORT + 2 SUCCEED per §2.4
- critic-opus independent re-run of all verifications

**Q7 (new hazards)**:
- **Hazard 1**: `provenance_json LIKE '%reactivated_by%'` in the trigger uses string LIKE. If provenance_json uses a different key or the JSON is minified/re-encoded, LIKE could fail to match. **Mitigation**: document the reactivation marker convention explicitly (reactivated_by is a top-level key in provenance_json JSON object); use `json_extract(NEW.provenance_json, '$.reactivated_by') IS NOT NULL` instead of LIKE IF SQLite json1 extension is compiled in. **Design decision**: LIKE is simpler and json1 may not be compiled in all environments. Accept LIKE with clear contract. Alternative: use an explicit column `reactivation_marker` TEXT NOT NULL on INSERT when reactivating. Deferred; LIKE is good enough for v1 but critic-opus should weigh in.
- **Hazard 2**: app startup (`src/state/db.py::init_db`) runs the migration automatically. If a live process is running when I commit + push + redeploy, the migration runs on startup. **Mitigation**: do not redeploy during P-B execution; execute via direct sqlite3 CLI (independent of app startup). The `src/state/db.py` change lands in the SAME commit, but only takes effect on next boot.
- **Hazard 3**: 1556 rows is a lot; UPDATE in one transaction might be slow on a 1.8GB DB. **Mitigation**: the rows are tiny (provenance_json is <512 bytes each), index on (city, target_date) present, no WHERE clause traversal needed — should complete in <1s. Measure on snapshot DB first.
- **Hazard 4**: CHECK constraints on ALTER TABLE ADD COLUMN require SQLite ≥ 3.35.0 for column-level CHECK to be enforced on existing rows. Older SQLite silently allowed violations in pre-existing rows. **Mitigation**: we're ADDING columns with NULL on all existing rows, so existing rows always satisfy `CHECK (col IS NULL OR col IN (...))`. No hazard in practice.
- **Hazard 5**: midstream agent coordination. T6.1/T6.2 want to READ settlements for P&L regression. My ADD COLUMN is strictly additive; existing reads keep working. No conflict.

**Q8 (closure)**:
- `evidence/pb_schema_plan.md` (this doc) — plan
- `evidence/pb_execution_log.md` — execution trail
- `evidence/pb_trigger_validation_log.md` — the 4 trigger test cases + outputs
- `evidence/scripts/pb_backfill_provenance.py` — backfill runner
- `src/state/db.py` diff — the ALTER TABLE + CREATE TRIGGER migration block
- critic-opus APPROVE on pre-review + post-execution

**Q9 (decision reversal)**: N/A. P-B adds columns; does not revive any removed pattern. Reviewing first_principles.md §3 AP-11 — no reinstated removed architecture. The P-E "DELETE+INSERT" architectural decision (P-0 §8 rule 10) is preserved; P-B provides the columns P-E will populate.

**Q10 (rollback)**:
- DDL additions: reversible by `ALTER TABLE settlements DROP COLUMN` (SQLite 3.35+) OR via snapshot restore. Per SQLite docs, DROP COLUMN is supported since 3.35.0 but is a table-rewrite operation (slow on 1.8GB DB).
- Trigger: reversible by `DROP TRIGGER IF EXISTS settlements_authority_monotonic;`
- Backfill UPDATE: reversible by `UPDATE settlements SET provenance_json = NULL` (but this destroys the provenance record; prefer snapshot restore).
- **Preferred rollback path**: `cp state/zeus-world.db.pre-pb_2026-04-23 state/zeus-world.db` (snapshot overwrite). This reverses all P-B effects in one operation.
- Forward-compat: if P-F/P-E re-run, ALTER TABLE is idempotent (try/except) and backfill is idempotent (`WHERE provenance_json IS NULL` matches 0 rows on re-run).

---

## Section 4 — Safety protocol (runbook)

```
STEP 1: Pre-flight
  - git status --short (verify clean workspace for P-B slice)
  - sqlite3 state/zeus-world.db "SELECT COUNT(*) FROM settlements" → expect 1556
  - ls state/zeus-world.db.pre-pg_2026-04-23 → confirm previous snapshot exists (rollback chain intact)

STEP 2: Snapshot
  - PRAGMA wal_checkpoint(TRUNCATE)
  - cp state/zeus-world.db state/zeus-world.db.pre-pb_2026-04-23
  - md5 verify; record hash file

STEP 3: Baseline test run
  - pytest -q tests/test_schema_v2_gate_a.py tests/test_canonical_position_current_schema_alignment.py
  - record exit code + output; if fail → HALT (baseline broken; investigate before migration)

STEP 4: DDL migration (direct sqlite3, NOT via app init)
  - sqlite3 state/zeus-world.db < DDL_COMMANDS (5 ALTER + 1 TRIGGER)
  - Each ALTER inside a try (idempotent — existing column produces error; acceptable)
  - verify: sqlite3 ".schema settlements" contains the 5 new columns
  - verify: sqlite3 "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='settlements'" contains settlements_authority_monotonic

STEP 5: Backfill (Python wrapper; pre/post verify + assertion-guarded ROLLBACK)
  - python3 evidence/scripts/pb_backfill_provenance.py
  - verify: changes()=1556, post-count NULL=0

STEP 6: Trigger validation
  - INSERT a scratch row with authority='VERIFIED'
  - UPDATE → UNVERIFIED → expect sqlite3.IntegrityError ("ABORT")
  - UPDATE → QUARANTINED → expect SUCCESS
  - UPDATE → VERIFIED with provenance_json='{}' → expect ABORT
  - UPDATE → VERIFIED with provenance_json='{"reactivated_by":"test"}' → expect SUCCESS
  - DELETE scratch row

STEP 7: Post-migration test run
  - pytest -q tests/test_schema_v2_gate_a.py tests/test_canonical_position_current_schema_alignment.py
  - compare exit code to baseline; if diverges → HALT + snapshot rollback

STEP 8: src/state/db.py code change
  - Add the ALTER TABLE + CREATE TRIGGER block to src/state/db.py migration list
  - Grep-verify: `grep 'settlements_authority_monotonic' src/state/db.py` finds 1 occurrence

STEP 9: Closure request to critic-opus
  - evidence docs + MD5s
  - trigger validation outputs
  - pytest before/after outputs

STEP 10: On critic APPROVE:
  - Update App-C (R3-01, R3-02, R3-04, R3-07, R3-10, R3-15, R3-18, R3-19 → review case-by-case)
  - Commit + push (5 files; snapshot binary excluded via existing gitignore pattern)
  - Mark P-B completed; move to P-F
```

---

## Section 5 — What critic-opus should pre-verify

1. **Migration idempotency**: does the try/except pattern safely handle the case where some columns already exist (e.g., if an earlier partial run happened)?
2. **Trigger logic**: does the `WHEN` clause correctly encode the 4 transition cases? Specifically:
   - VERIFIED→UNVERIFIED: trigger ABORTs ✓
   - VERIFIED→QUARANTINED: trigger allows (implicit; not in WHEN) ✓
   - QUARANTINED→VERIFIED without marker: trigger ABORTs ✓
   - QUARANTINED→VERIFIED with marker: trigger allows ✓
   - VERIFIED→VERIFIED (no-op): trigger allows ✓
   - UNVERIFIED→VERIFIED: trigger allows (implicit)
   - UNVERIFIED→QUARANTINED: trigger allows
3. **LIKE vs json_extract**: does `NEW.provenance_json LIKE '%reactivated_by%'` suffice, or should I use json_extract for robustness? Risk: if provenance_json contains `"not_reactivated_by": "..."` or literal `"reactivated_by_test"`, LIKE still matches; but this is unlikely given the reactivation marker is a structured top-level JSON key.
4. **Backfill content**: is the PROVENANCE_JSON constant sufficient for P-A retrofit? Should it include more fields (e.g., original JSON index sample, DB ids span)?
5. **CREATE TABLE not updated**: acceptable to leave the canonical CREATE TABLE at L158-169 unchanged and rely on migration-ADD-COLUMN for existing + fresh DBs? Alternative: modify CREATE TABLE too (would only affect fresh-init, which is unlikely in production).

---

**Awaiting critic-opus PRE-REVIEW. No ALTER TABLE, TRIGGER, or UPDATE until APPROVE.**
