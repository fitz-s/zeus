# P-B Execution Log

**Packet**: P-B (schema migration — INV-14 identity + provenance_json + trigger)
**Execution date**: 2026-04-23T18:21–18:22 UTC
**Executor**: team-lead
**Pre-review verdict**: critic-opus APPROVE_WITH_CONDITIONS (C1 LIKE→json_extract, C2 document reactivation contract; R1 optional recommended)
**Post-execution verdict pending**: critic-opus

---

## Section 1 — Applied pre-review conditions

### C1 — LIKE → json_extract (BLOCKING, applied)

Trigger DDL replaced:
```
OLD: NEW.provenance_json NOT LIKE '%reactivated_by%'
NEW: json_extract(NEW.provenance_json, '$.reactivated_by') IS NULL
```

Applied in 3 places:
- `evidence/pb_schema_plan.md` §1.3 and §2.1
- `evidence/scripts/pb_apply_schema_migration.sql`
- `evidence/scripts/pb_run_migration.py` (TRIGGER_DDL constant)
- `src/state/db.py` migration block

Verified: SQLite 3.43+ / 3.51+ both ship json1; critic-opus reference to existing `json_extract` usage at `src/riskguard/riskguard.py:377-383` confirms in-tree availability. No false-positive exposure remains.

### C2 — Reactivation contract documented (BLOCKING, applied)

`evidence/pb_schema_plan.md` §1.3 now explicitly documents the contract:
> to transition QUARANTINED→VERIFIED, the NEW row's `provenance_json` MUST contain a top-level JSON key `reactivated_by` with a non-null string value naming the authorizing packet or decision. Example: `{"reactivated_by": "R3-XY", "decision_doc": "...", "reactivated_at": "..."}`.

The trigger enforces this via `json_extract(NEW.provenance_json, '$.reactivated_by') IS NULL` (true = no marker = ABORT).

### R1 — provenance_json enrichment (NON-BLOCKING, applied)

Backfill constant now carries 9 fields (added 2 per R1):
- `writer_logic_inferred`: "settlement_value = pm_bin_lo if pm_bin_lo == pm_bin_hi else NULL; JSON sentinels -999/999 mapped to SQL NULL"
- `inferred_source_json_confidence`: 0.9

Cost: ~250 bytes × 1556 rows ≈ ~390 KB total; verified via `SELECT LENGTH(provenance_json) FROM settlements LIMIT 1` = 441 bytes per row.

R2 (CREATE TABLE hygiene), R3 (INSERT-time authority check), NH-B1 (json1 startup check), NH-B2 (snapshot cleanup policy): deferred as non-blocking per critic.

---

## Section 2 — Execution timeline

```
18:21:23Z  P-B migration runner starting
18:21:23Z  WAL checkpoint(TRUNCATE)
18:21:23Z  cp state/zeus-world.db → state/zeus-world.db.pre-pb_2026-04-23
18:21:28Z  first-run main == snap md5 = 827b370d22a2d552103c8c1c67f4d6f2
18:21:28Z  pre-count = 1556
18:21:28Z  ALTER ADD temperature_metric — OK
18:21:28Z  ALTER ADD physical_quantity — OK
18:21:28Z  ALTER ADD observation_field — OK
18:21:28Z  ALTER ADD data_version — OK
18:21:28Z  ALTER ADD provenance_json — OK
18:21:28Z  CREATE TRIGGER — FAIL: SQLite syntax error (Python-style string-literal concat in RAISE; not valid SQL)
           → Partial migration state: 5 columns present, no trigger, no backfill
```

**First-run failure mode**: `sqlite3.OperationalError: near "'...'": syntax error`. Root cause: the multiline RAISE message used Python adjacent-string-literal concatenation, which works in Python source but is two distinct argument tokens in SQL. SQLite's RAISE takes exactly one string argument.

**Recovery**: fixed TRIGGER_DDL to single-string message + fixed script's snapshot-integrity check (the original check compared main-md5 vs snap-md5 even on re-run, which correctly fails because main has mutated; corrected to verify snapshot-md5 vs recorded-hash-file on re-run, permitting main to differ).

```
18:22:19Z  P-B migration runner re-starting (idempotent re-run)
18:22:19Z  Snapshot exists; verify integrity only
18:22:21Z  snap md5 827b370d22a2d552103c8c1c67f4d6f2 matches recorded hash ✓
18:22:21Z  pre-count = 1556
18:22:21Z  ALTER: all 5 already_exists (idempotent try/except absorbed)
18:22:21Z  TRIGGER: settlements_authority_monotonic created
18:22:21Z  backfill pre-verify: 1556 NULL
18:22:21Z  UPDATE changes()=1556
18:22:21Z  backfill post-verify: 0 NULL (expected 0) ✓
18:22:21Z  COMMIT backfill
18:22:21Z  === TRIGGER VALIDATION ===
18:22:21Z  scratch row id=88058 inserted with authority='VERIFIED'
18:22:21Z    Case V→U : ABORT (expected ABORT) ✓
18:22:21Z    Case V→Q : SUCCESS (expected SUCCESS) ✓
18:22:21Z    Case Q→V without marker : ABORT (expected ABORT) ✓
18:22:21Z    Case Q→V with reactivated_by marker : SUCCESS (expected SUCCESS) ✓
18:22:21Z  scratch row cleaned up; COMMIT
18:22:21Z  post-count = 1556 ✓
18:22:21Z  post_null_count = 0 ✓
```

---

## Section 3 — Self-verify

| AC | Command | Expected | Actual |
|---|---|---|---|
| AC-P-B-1 | row count pre/post = 1556 | 1556 | 1556 ✓ |
| AC-P-B-2 | 5 new columns present via `.schema settlements` | 5 listed | 5 listed ✓ (temperature_metric / physical_quantity / observation_field / data_version / provenance_json) |
| AC-P-B-3 | CHECK constraints survived ALTER | permissive (IS NULL OR IN ...) | confirmed in `.schema` output ✓ |
| AC-P-B-4 | Trigger exists | name='settlements_authority_monotonic' | SELECT from sqlite_master returned 1 row ✓ |
| AC-P-B-5 | 1556 rows with provenance_json populated | 1556 / 1556 | `SELECT COUNT(*) … WHERE provenance_json IS NOT NULL` = 1556 ✓ |
| AC-P-B-6 | Trigger Case 1: V→U ABORT | IntegrityError | IntegrityError with expected message ✓ |
| AC-P-B-7 | Trigger Case 2: V→Q SUCCESS | row updated | row updated; no error ✓ |
| AC-P-B-8 | Trigger Case 3: Q→V without marker ABORT | IntegrityError | IntegrityError ✓ |
| AC-P-B-9 | Trigger Case 4: Q→V with marker SUCCESS | row updated | row updated; no error ✓ |
| AC-P-B-10 | Baseline pytest PASS pre-migration | 9 passed + 7 subtests | 9 passed + 7 subtests ✓ |
| AC-P-B-11 | Post-migration pytest PASS (0 regression) | 9 passed + 7 subtests | 9 passed + 7 subtests (identical) ✓ |
| AC-P-B-12 | Post-src/state/db.py-update import clean | no ImportError | `from src.state import db; db.get_world_connection()` returns cleanly ✓ |
| AC-P-B-13 | 18 columns total (13 + 5) | 18 | `PRAGMA table_info` returned 18 columns ✓ |
| AC-P-B-14 | Snapshot rollback path intact | md5 matches recorded hash | 827b370d2...matches ✓ |

---

## Section 4 — What P-B did NOT do (deferred per plan §1.5)

- No DELETE or INSERT of existing 1556 rows (P-E)
- No population of `temperature_metric` / `observation_field` / `data_version` on existing rows (P-E on re-insert)
- No changes to harvester write path (DR-33 or P-E-adjacent packet)
- No NOT NULL constraint on new columns (deferred until all writers updated)
- No CREATE TABLE hygiene update (canonical L158-169 unchanged per R2 deferral — fresh-init goes through the ALTER block and reaches the same end state)

---

## Section 5 — New findings during execution

### 5.1 Root cause of first-run trigger-creation failure

The multiline RAISE message used Python-style adjacent string literal concatenation:
```python
"SELECT RAISE(ABORT, "
"    'settlements.authority transition forbidden: ' "
"    'QUARANTINED->VERIFIED requires ...');"
```

In Python, this is one string. In SQL, the SQLite parser sees the two separate string literals as two tokens, producing `near "'...'" : syntax error` because RAISE accepts exactly one message argument.

**Hygiene lesson**: when composing multi-clause error messages in SQL, concatenate via `||` or use a single-string literal — never rely on Python's adjacent-literal concatenation inside triple-quoted SQL heredocs. Adding this as **NH-B3** to §8 non-blocking hazards for guidance_kernel consideration.

### 5.2 Hash check logic refinement during re-run

The original snapshot integrity check compared `main_hash == snap_hash` on every run. On first run, this is correct (just-taken snapshot must match main). On re-run after partial migration, this check falsely fails because main has mutated by design. Corrected: on re-run, verify only `md5(snapshot) == recorded_hash_file`. Hash file is written on first successful snapshot take; subsequent runs inspect the hash file to confirm the rollback artifact hasn't been tampered with.

---

## Section 6 — R3-## closure requests for critic-opus post-execution review

- **R3-01** (architect P0-1 AP-5 axis mirage): request **CLOSED-BY-P-B** — `data_version` column now exists as the concrete axis for metric-identity alignment. P-E will populate on INSERT.
- **R3-06** (architect P0-5 dual-track cross-packet coordination): request **CLOSED-BY-P-B** — `temperature_metric` + `observation_field` columns implement the dual-track separation in schema. P-E writes preserve HIGH/LOW identity.
- **R3-07** (architect P0-6,P0-7 AP-16 zone laundering): **NOT closed by P-B** — P-B does not touch `src/contracts/`, `src/types/`, or relocate K0 atoms. The K0 schema_packet concern remains for future governance.
- **R3-10** (critic P0-3 AP-7 deprecated API proliferation): **NOT directly closed by P-B** — this is about test-suite patterns (deprecated helper mandates); P-B is schema-only. Hand to a separate test-topology packet.
- **R3-15** (architect P1-2, scientist D6 AP-15 decision_time_snapshot_id): **PARTIALLY addressed** — `provenance_json` is the vehicle; P-E writers will embed `decision_time_snapshot_id` in the JSON blob. Full closure at P-E.
- **R3-18** (architect P1-6 AP-15 trigger body unspecified): request **CLOSED-BY-P-B** — `settlements_authority_monotonic` trigger is specified, created, and validated against 4 transition cases.
- **R3-19** (architect P1-7 AP-15 NC-13 enforcement deferred): **NOT closed by P-B** — NC-13 is a different constraint; P-B implements INV-FP-5 via trigger but NC-13 remains for its owning packet.

---

## Section 7 — Non-blocking hazards (new)

- **NH-B3** (SQL multi-string RAISE pitfall): during P-B execution, Python-style adjacent-string-literal concatenation silently produced invalid SQL for RAISE. Add to guidance_kernel's SQL-pattern antibodies: "RAISE takes exactly one string argument; use `'foo; bar'` (single string) or `'foo' || 'bar'` (SQL ||), never adjacent Python literals inside triple-quoted SQL."
- **NH-B4** (snapshot integrity re-run asymmetry): first-run expects main_md5 == snap_md5; re-run expects main_md5 ≠ snap_md5 (main has mutated) BUT snap_md5 == recorded_hash. Future schema-migration runners should encode this asymmetry explicitly to avoid false failures on re-run.

---

**Packet P-B ready for critic-opus post-execution review. Closure request to follow.**
