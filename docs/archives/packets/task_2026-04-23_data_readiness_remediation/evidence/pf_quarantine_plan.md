# P-F Hard Quarantine Plan — Pre-Packet

**Packet**: P-F (hard quarantine)
**Goal**: mark 74 specifically-identified rows as `authority='QUARANTINED'` with enumerable `provenance_json.quarantine_reason`, using the P-B trigger + provenance_json column. This is the last packet before P-E reconstruction; P-E will see VERIFIED vs QUARANTINED and honor the split.
**Date**: 2026-04-23
**Executor**: team-lead
**Pending**: critic-opus PRE-REVIEW

---

## Section 1 — Scope

74 rows UPDATE, partitioned by enumerable reason (from P-C §6.4):

| reason_id                                                      | rows | source                                   |
|----------------------------------------------------------------|-----:|------------------------------------------|
| `pc_audit_source_role_collapse_no_source_correct_obs_available`|   27 | HK WU (2) + Taipei NOAA (12) + Tel Aviv WU (13) — P-C §5 NO_OBS buckets; date-range disjoint per P-G §1.2 |
| `pc_audit_shenzhen_drift_nonreproducible`                      |   26 | Shenzhen WU whole-bucket (10 mismatches + 16 apparent-matches per P-C §4.3 + critic-opus whole-bucket caveat) |
| `pc_audit_dst_spring_forward_bin_mismatch`                     |    7 | 2026-03-08 DST cluster (Atlanta/Chicago/Dallas/Miami/NYC/Seattle F + Toronto C); obs consistently below pm_bin |
| `pc_audit_station_remap_needed_no_cwa_collector`               |    7 | Taipei CWA Mar 16-22 (no CWA obs collector exists) |
| `pc_audit_seoul_station_drift_2026-03_through_2026-04`         |    5 | Seoul WU ±1°C drift specific dates (not whole-bucket; 53 of 59 Seoul rows remain VERIFIED-reconstructable in P-E) |
| `pc_audit_1unit_drift`                                         |    2 | Kuala Lumpur 2026-04-10 + Cape Town 2026-04-15 (single-row 1°C drift per city) |
| **TOTAL**                                                      | **74** | |

Mapping persisted to `evidence/pf_quarantine_mapping.json` (74 entries, each `{city, target_date, reason_id}`).

Post-P-F expected state:
- `authority='VERIFIED'` rows: 1556 − 74 = **1482** (still carrying bulk-writer provenance from P-B; not yet re-derived)
- `authority='QUARANTINED'` rows: **74** (explicit reason in provenance_json)

P-E then DELETE+INSERTs against both populations, restamping `authority` per row-level evidence.

## Section 2 — Mutation design

### 2.1 SQL per row

For each `(city, target_date, reason_id)` in the mapping:

```sql
UPDATE settlements
SET authority = 'QUARANTINED',
    provenance_json = json_set(
        json_set(
            json_set(provenance_json,
                '$.quarantine_reason', :reason_id),
            '$.quarantined_at', :now_iso),
        '$.quarantined_by_packet', 'P-F')
WHERE city = :city AND target_date = :target_date;
```

- Uses `json_set` (SQLite json1; verified available in P-B) to augment without destroying the existing bulk-writer retrofit keys.
- Transitions VERIFIED→QUARANTINED (all 74 rows currently carry `authority='VERIFIED'` per the bulk-writer). This is explicitly allowed by `settlements_authority_monotonic` trigger (only V→U and unmarked Q→V are forbidden).

### 2.2 Transaction boundary

All 74 UPDATEs in **ONE** `BEGIN IMMEDIATE ... COMMIT`:
- Pre-verify: SELECT COUNT matching `(city, target_date)` list with `authority='VERIFIED'` → expect 74
- Loop: execute 74 parameterized UPDATEs
- Post-verify:
  - SELECT COUNT WHERE authority='QUARANTINED' AND provenance_json LIKE '%quarantine_reason%' → expect 74
  - SELECT COUNT WHERE city=? AND target_date=? AND authority='QUARANTINED' per row → each = 1
- Assertion-guarded ROLLBACK on any mismatch

### 2.3 Idempotency

Re-run safety: if any row already carries `authority='QUARANTINED'` AND `json_extract(provenance_json, '$.quarantine_reason') = :reason_id`, the UPDATE is effectively no-op (same values). SQLite UPDATE always "succeeds" even if values don't change.

On first re-run, pre-verify would count 0 VERIFIED rows matching (they're already QUARANTINED) — and the script should report "no-op, all rows already quarantined" rather than fail. The runner handles this case by distinguishing empty-baseline (idempotent re-run) from partial-state (partial failure — halt).

### 2.4 Safety protocol

1. Pre-flight: snapshot `cp state/zeus-world.db state/zeus-world.db.pre-pf_2026-04-23` + md5 record
2. Baseline pytest: `tests/test_schema_v2_gate_a.py` + `tests/test_canonical_position_current_schema_alignment.py` (should pass, since P-F doesn't alter schema)
3. Execute runner `pf_mark_quarantine.py` in one transaction
4. Post-migration pytest: same suite; expect 0 regression
5. Post-migration validation queries:
   - `SELECT COUNT(*) FROM settlements WHERE authority='QUARANTINED'` → 74
   - `SELECT COUNT(*) FROM settlements WHERE authority='VERIFIED'` → 1482
   - Per-reason count matches the §1 table
   - Sample row: `SELECT json_extract(provenance_json, '$.quarantine_reason') FROM settlements WHERE city='Shenzhen' AND target_date='2026-03-24'` → `pc_audit_shenzhen_drift_nonreproducible`
   - Existing provenance_json P-A retrofit keys preserved (json_set augments, doesn't replace): `json_extract(..., '$.writer')` still returns `unregistered_bulk_writer_2026-04-16`

## Section 3 — Q1-Q10

**Q1 (invariant)**:
- INV-FP-5 (authority monotonic): V→Q is ALLOWED by the P-B trigger. P-F exercises this allowed downgrade path.
- INV-FP-1 (provenance unbroken): json_set PRESERVES existing keys + adds quarantine_reason. P-A retrofit content survives.
- INV-FP-9 (NULL first-class / QUARANTINE is first-class): 74 rows move from VERIFIED-by-lie to explicitly QUARANTINED-with-reason. Authority signal becomes honest.

**Q2 (fatal_misread)**:
- `code_review_graph_answers_where_not_what_settles`: not touched; P-F uses SQL + enumerable reasons, not graph output.
- `hong_kong_hko_explicit_caution_path`: HK WU 2 rows go to QUARANTINE with source-role-collapse reason — respects the caution.

**Q3 (single-source-of-truth)**:
- 74 rows verified via `pc_agreement_audit_postPG.json` (post-P-G audit)
- Per-reason partition: 27+26+7+7+5+2 = 74 ✓
- Reason set: enumerable, closed list per P-C §6.4 (revised post-P-G — the `pc_audit_2026_04_15_pm_truth_json_duplicate` reason is retired since those 5 rows were DELETEd)

**Q4 (first-failure)**:
- Pre-verify VERIFIED count mismatch: ROLLBACK, report "DB state unexpected; investigate"
- Any individual UPDATE affects 0 rows (row moved/deleted): ROLLBACK, HALT
- Post-verify per-reason count mismatch: ROLLBACK, HALT
- Trigger ABORT (shouldn't fire on V→Q, but guard anyway): ROLLBACK, HALT

**Q5 (commit boundary)**: ONE transaction, BEGIN IMMEDIATE → 74 UPDATEs → post-verify asserts → COMMIT or ROLLBACK. Atomic.

**Q6 (verification)**:
- Each UPDATE returns `changes() == 1` inside the txn
- SELECT COUNT authority='QUARANTINED' = 74 post-commit
- Per-reason count via `SELECT json_extract(provenance_json, '$.quarantine_reason'), COUNT(*) FROM settlements GROUP BY 1` matches expected distribution
- Sample row: existing retrofit keys still present
- critic-opus independently re-runs SQL + inspects sample rows

**Q7 (new hazards)**:
- **Hazard 1**: 74 UPDATEs in one transaction may be slow on 1.8GB DB. Mitigation: each UPDATE uses UNIQUE(city, target_date) index → O(log N) lookup; 74 × ~1ms = <100ms total.
- **Hazard 2**: json_set on malformed provenance_json would error. Mitigation: post-P-B all 1556 rows have valid JSON (P-B backfill wrote `json.dumps(...)`); verified via `SELECT COUNT(*) WHERE json_valid(provenance_json)` should be 1556.
- **Hazard 3**: UPDATE trigger fires per-row. For 74 rows that's 74 trigger evaluations. Each evaluates the WHEN clause → OLD.authority=VERIFIED, NEW.authority=QUARANTINED → first OR clause false (NEW=Q not U), second OR clause false (NEW=V not V, wait — OLD=V not Q so second clause also false) → WHEN=false → no ABORT. Safe.

**Q8 (closure)**:
- `evidence/pf_quarantine_plan.md` (this doc)
- `evidence/pf_quarantine_mapping.json` (74 entries)
- `evidence/pf_execution_log.md` (post-execution)
- `evidence/scripts/pf_mark_quarantine.py` (runner)
- critic-opus APPROVE on pre-review + post-execution

**Q9 (decision reversal)**: N/A. P-F implements what P-C §6.4 prescribed + P-G §6 disposed. No prior decision reversed.

**Q10 (rollback)**:
- Snapshot path: `state/zeus-world.db.pre-pf_2026-04-23` (cp + md5)
- Per-row reversal: `UPDATE settlements SET authority='VERIFIED', provenance_json = json_remove(provenance_json, '$.quarantine_reason', '$.quarantined_at', '$.quarantined_by_packet') WHERE city=? AND target_date=?` — but this would be blocked by trigger (Q→V requires reactivation marker). Use snapshot restore instead.
- Forward-compat: P-E reads QUARANTINED rows, re-derives from evidence, DELETE+INSERT. If re-derivation can't produce a clean VERIFIED row, P-E keeps it QUARANTINED with possibly-refined reason. QUARANTINE is the safe state.

---

## Section 4 — What critic-opus should pre-verify

1. **Reason set closure**: the 6 enumerable reasons in §1 are the final set (post-P-G, after the 2026-04-15 JSON-duplicate reason retired). Confirm no rows fall outside this set.
2. **Row count arithmetic**: 27+26+7+7+5+2 = 74. Post-P-F: 1482 VERIFIED + 74 QUARANTINED = 1556. ✓
3. **Trigger allows V→Q**: walk through the WHEN clause for OLD=V, NEW=Q. First OR (NEW=U)? No. Second OR (OLD=Q)? No. WHEN = false → no ABORT. Should pass.
4. **json_set preserves P-A retrofit keys**: critic-opus should sample 1 row post-mutation and confirm `json_extract(..., '$.writer')` still returns the P-A retrofit value.
5. **Toronto 2026-03-08 inclusion in DST cluster**: Toronto WU bucket is VERIFIED per P-C (58/59 match, max_delta 1), but the 1 mismatch row IS on 2026-03-08 and belongs to the DST category. Is row-level quarantine the right call, or should this row be left VERIFIED and let P-E decide?

My inclination on Q5: Toronto 2026-03-08 should be QUARANTINED because obs 9°C doesn't land in bin [10,+∞). Row-level evidence trumps bucket-level disposition. P-E will DELETE+INSERT everything; QUARANTINE just flags "don't re-derive this as VERIFIED without fresh evidence".

---

**Awaiting critic-opus PRE-REVIEW. No UPDATE until APPROVE.**
