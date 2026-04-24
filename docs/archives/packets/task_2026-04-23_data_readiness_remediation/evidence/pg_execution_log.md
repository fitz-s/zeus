# P-G Execution Log

**Packet**: P-G (pre-existing corrections — DB-mutating)
**Execution date**: 2026-04-23T17:58:53Z
**Executor**: team-lead
**Pre-review verdict**: critic-opus APPROVE (2026-04-23, per work_log P-G section)
**Post-execution verdict pending**: critic-opus

---

## Section 1 — Applied pre-review findings

### F1 — Explicit ROLLBACK on mismatch (wrapped in Python)

Evidence: `evidence/scripts/pg_delete_wrongmetric_and_synthetic.py` (247 lines). Each transaction:
1. `BEGIN IMMEDIATE` (acquires RESERVED lock; serializes against any concurrent writer)
2. Pre-verify SELECT inside the transaction
3. Assert `pre_rows == expected_rows`; ROLLBACK + exit(2) on mismatch
4. DELETE
5. Assert `cursor.rowcount == expected_rows`; ROLLBACK + exit(3) on mismatch
6. Post-verify SELECT COUNT inside the transaction
7. Assert `remaining == 0`; ROLLBACK + exit(4) on mismatch
8. COMMIT only on all-green path
9. Catch `sqlite3.Error` at the outer `try`; ROLLBACK + exit(5)

No raw `sqlite3` CLI used for the DELETE; the wrapper makes every failure mode explicitly recoverable and logs every step with ISO8601 timestamps.

### F2 — Gamma slug verification for HK WU 2026-03-13 / 2026-03-14

Direct slug queries (2 API calls, 2026-04-23T17:50):

```
GET /events?slug=highest-temperature-in-hong-kong-on-march-13-2026
  → 1 event, title="Highest temperature in Hong Kong on March 13?"
    9 resolved markets; YES-won on "15°C or below on March 13" (low shoulder)

GET /events?slug=highest-temperature-in-hong-kong-on-march-14-2026
  → 1 event, title="Highest temperature in Hong Kong on March 14?"
    9 resolved markets; YES-won on "16°C or below on March 14" (low shoulder)
```

DB rows for Hong Kong WU 2026-03-13/14 carry `pm_bin=(NULL, 15.0)` and `pm_bin=(NULL, 16.0)` respectively — matching Gamma's YES-won low-shoulder bins **exactly**. Confirms:
- These 2 rows are NOT synthetic orphans (unlike Denver).
- Polymarket did open and settle WU-Hong-Kong markets on these 2 dates before switching to HKO from 2026-03-16.
- The `settlement_source_type='WU'` label is historically correct.
- **P-G does NOT touch these 2 rows. P-E decides backfill-or-QUARANTINE per row** (no WU obs exists for HK Mar 13-14 in current observations table; may require Option A backfill of WU or Option B QUARANTINE).

### F3 — Post-P-G P-C re-audit (predictions vs actual)

Ran `python3 docs/operations/.../evidence/scripts/pc_agreement_audit.py > evidence/pc_agreement_audit_postPG.json`.

**Predictions** (pg_corrections_plan.md §5):

| Metric                    | Pre-P-G | Predicted | Actual | match |
|---------------------------|--------:|----------:|-------:|-------|
| total_settlements         | 1562    | 1556      | 1556   | ✓    |
| audited                   | 1513    | 1507      | 1507   | ✓    |
| matches                   | 1481    | 1476 (est)| 1480   | ±1¹  |
| mismatches                | 32      | 27        | 27     | ✓    |
| no_obs                    | 42      | 42        | 42     | ✓    |
| station_remap_needed      | 7       | 7         | 7      | ✓    |
| VERIFIED buckets          | 37      | 40        | 40     | ✓    |
| QUARANTINE buckets        | 13      | 10        | 10     | ✓    |
| NO_OBS buckets            | 3       | 3         | 3      | ✓    |
| STATION_REMAP buckets     | 1       | 1         | 1      | ✓    |
| partition sum (buckets)   | 54      | 54        | 54     | ✓    |

¹ matches delta: plan predicted "5 fewer mismatches" but not explicit about match count; actual 1481→1480 (−1) reflects loss of Denver's coincidence-match (obs=68°F, bin=[68,69]F matched by accident; post-DELETE the row is gone so it doesn't contribute to matches either). No surprise; noted.

**Bucket-level verification** — buckets that shifted:

- **London WU/C**: pre 57 audited / 1 mism / QUARANTINE → post 56 audited / 0 mism / **VERIFIED** ✓ (2026-04-15 LOW-contamination row DELETEd)
- **Shanghai WU/C**: pre 33 audited / 1 mism / QUARANTINE → post 32 audited / 0 mism / **VERIFIED** ✓
- **Tokyo WU/C**: pre 36 audited / 1 mism / QUARANTINE → post 35 audited / 0 mism / **VERIFIED** ✓

**Buckets that stayed QUARANTINE** (expected — only their 2026-04-15 row was DELETEd, but they have other mismatches):

- **NYC WU/F**: pre 60 / 2 mism / QUARANTINE → post 59 / 1 mism / QUARANTINE (2026-03-08 DST row remains; delta=12)
- **Cape Town WU/C**: pre 7 / 1 mism / QUARANTINE → post 7 / 1 mism / QUARANTINE (bin=[21,21] obs=20; 2026-04-15 row stays because P-G did NOT delete Cape Town — Cape Town 2026-04-15 is a real HIGH-market with ±1°C obs drift, not a LOW-metric collision)
- **Atlanta / Chicago / Dallas / Miami / Seattle / Seoul / Shenzhen / Kuala Lumpur**: all unchanged — no 2026-04-15 DELETE touched them

**Delta histogram** (mismatches only):
- Before: `{1-2: 20, 2-3: 2, 3-4: 1, 4-5: 1, 6-7: 1, 7-8: 1, 9-10: 1, 11-12: 1, 12-13: 1, 17-18: 1, 18-19: 1, 28-29: 1}`
- After:  `{1-2: 20, 2-3: 2,       4-5: 1,         9-10: 1,        12-13: 1, 17-18: 1,       28-29: 1}`
- Removed: 3-4 (Shanghai 2026-04-15), 6-7 (London), 7-8 (Tokyo), 11-12 (Seoul), 18-19 (NYC) — 5 entries, all 2026-04-15 LOW-contamination rows ✓

### New hazard — Snapshot hash verification

Pre-execution snapshot taken via `PRAGMA wal_checkpoint(TRUNCATE)` then `cp`. Both files produced the same md5 (`8bece7701a1eafe57c451567c9335841`); hash file recorded at `state/zeus-world.db.pre-pg_2026-04-23.md5` for audit trail. Rollback path is intact.

### F1-POST (critic-opus post-execution review) — md5 sidecar filename fix

Script line 42 originally used `SNAPSHOT_PATH.with_suffix(".db.pre-pg_2026-04-23.md5")`. Python's `Path.with_suffix` replaces only the last dotted suffix, which for `zeus-world.db.pre-pg_2026-04-23` is `.pre-pg_2026-04-23` — producing `zeus-world.db.db.pre-pg_2026-04-23.md5` (doubled `.db.db`). The rollback path was unaffected because the md5 file did exist, just at the doubled-path location.

Fix applied post-execution:
1. Script: replaced with explicit concatenation `SNAPSHOT_PATH.parent / (SNAPSHOT_PATH.name + ".md5")` (L42-46 in the post-fix script)
2. Filesystem: renamed `state/zeus-world.db.db.pre-pg_2026-04-23.md5` → `state/zeus-world.db.pre-pg_2026-04-23.md5` to match documented path

Md5 content unchanged. Documentation in §1 above was always correct about the intended path.

---

## Section 2 — Execution timeline

```
2026-04-23T17:58:53Z  P-G delete runner starting
2026-04-23T17:58:53Z  WAL checkpoint (TRUNCATE)
2026-04-23T17:58:53Z  cp state/zeus-world.db → state/zeus-world.db.pre-pg_2026-04-23
2026-04-23T17:58:58Z  main md5 == snap md5 == 8bece7701a1eafe57c451567c9335841
2026-04-23T17:58:58Z  pre-mutation row count: 1562
2026-04-23T17:58:58Z  === TXN1_denver_synthetic ===
2026-04-23T17:58:58Z  pre-verify: 1 row [Denver 2026-04-15 bin=[68,69]F WU id=88035]
2026-04-23T17:58:58Z  DELETE changes()=1 (expected 1)
2026-04-23T17:58:58Z  post-verify: 0 rows remain
2026-04-23T17:58:58Z  COMMIT TXN1_denver_synthetic
2026-04-23T17:58:58Z  === TXN2_low_market_contamination ===
2026-04-23T17:58:58Z  pre-verify: 5 rows [London[11,11]C WU id=88049; NYC[68,69]F WU id=88051;
                                           Seoul[10,10]C WU id=88050; Shanghai[15,15]C WU id=88053;
                                           Tokyo[15,15]C WU id=88052]
2026-04-23T17:58:58Z  DELETE changes()=5 (expected 5)
2026-04-23T17:58:58Z  post-verify: 0 rows remain
2026-04-23T17:58:58Z  COMMIT TXN2_low_market_contamination
2026-04-23T17:58:58Z  post-mutation row count: 1556 (expected 1556)
```

**Rows removed**: 6 (sum of TXN1 changes=1 + TXN2 changes=5). All expected. Zero ROLLBACKs. Zero SQLite errors.

---

## Section 3 — Self-verify table

| AC | Command | Result |
|---|---|---|
| AC-P-G-1 | DB row count pre-DELETE = 1562 | 1562 ✓ |
| AC-P-G-2 | snapshot md5 matches main DB md5 pre-DELETE | 8bece770… == 8bece770… ✓ |
| AC-P-G-3 | TXN1 deletes exactly 1 row (Denver 2026-04-15) | changes()=1; id=88035 confirmed ✓ |
| AC-P-G-4 | TXN2 deletes exactly 5 rows (2026-04-15 LOW-metric) | changes()=5; ids 88049-88053 confirmed ✓ |
| AC-P-G-5 | DB row count post-DELETE = 1556 | 1556 ✓ |
| AC-P-G-6 | DB row count reproducible post-DELETE | `sqlite3 state/zeus-world.db "SELECT COUNT(*) FROM settlements"` → 1556 |
| AC-P-G-7 | No Denver 2026-04-15 row remains | `SELECT COUNT(*) FROM settlements WHERE city='Denver' AND target_date='2026-04-15'` → 0 ✓ |
| AC-P-G-8 | No 2026-04-15 rows for the 5 LOW-contam cities | `SELECT COUNT(*) FROM settlements WHERE target_date='2026-04-15' AND city IN ('London','NYC','Seoul','Tokyo','Shanghai')` → 0 ✓ |
| AC-P-G-9 | P-C re-audit mismatches = 27 (5 fewer) | pc_agreement_audit_postPG.json totals.mismatches=27 ✓ |
| AC-P-G-10 | London/Shanghai/Tokyo WU/C shifted to VERIFIED | post JSON dispositions confirmed ✓ |
| AC-P-G-11 | NYC WU/F + Cape Town WU/C + all 2026-03-08 DST QUARANTINE stay | still QUARANTINE ✓ |
| AC-P-G-12 | 54-bucket partition sum preserved | 40 VERIFIED + 10 QUARANTINE + 3 NO_OBS + 1 STATION_REMAP = 54 ✓ |
| AC-P-G-13 | HK WU Mar 13/14 Gamma-confirmed real markets (F2) | both slug queries returned resolved UMA events matching DB bins ✓ |
| AC-P-G-14 | snapshot rollback path intact | `ls -la state/zeus-world.db.pre-pg_2026-04-23` exists; md5 file present ✓ |

---

## Section 4 — What remains for P-B / P-F / P-E (not P-G territory)

- **27 AP-4 rows** (HK WU 2 / Taipei NOAA 12 / Tel Aviv WU 13): P-E decides backfill-or-QUARANTINE. Labels historically correct; don't relabel.
- **Cape Town 2026-04-15** (±1 °C obs drift, R3-22-adjacent): P-E QUARANTINE with `pc_audit_1unit_drift`.
- **2026-03-08 DST cluster** (7 rows, 4–28 °F deltas): P-E QUARANTINE with `pc_audit_dst_spring_forward_bin_mismatch`. Diagnostic deferred to NH-C6 lane.
- **Shenzhen whole-bucket** (26 rows): P-E QUARANTINE with `pc_audit_shenzhen_drift_nonreproducible`.
- **Seoul 5 ± 1 °C drift rows** (2026-03 & 2026-04): P-E QUARANTINE with `pc_audit_seoul_station_drift_2026-03_through_2026-04`.
- **Kuala Lumpur 2026-04-10** (±1 °C, NH-C7 canary): P-E QUARANTINE with `pc_audit_1unit_drift`.
- **Taipei CWA 7 rows**: P-E QUARANTINE with `pc_audit_station_remap_needed_no_cwa_collector`.

Enumerable provenance_json reason set for P-E is fixed in P-C §6.4 (7 entries, now reduced scope since LOW-contamination reason no longer applies — those 5 rows are gone).

---

## Section 5 — R3-## closure requests for critic-opus post-execution review

- **R3-21** (2026-04-15 duplicate market identity): request **RESOLVED-BY-P-G** — reframed as HIGH/LOW metric collision, resolved by 5-row DELETE. HIGH-market re-insertion happens in P-E using EARLY-set JSON entries (indices 1513/1520/1517/1530/1532).
- **R3-23** (Denver orphan): already CLOSED-BY-P-D in App-C; P-G's DELETE is the execution of that closure. No status change.
- **R3-22** (obs_v2 Cape Town): P-G touches only the metadata row; the obs row is out of scope. Remains PENDING for P-G sub-packet OR P-E.
- **R3-20** (Tel Aviv AP-4): P-G's Q9 retires the relabel framing; P-E now owns backfill-or-QUARANTINE disposition. Status stays ADDRESSED-BY-P-C-TO-BE-CLOSED-BY-P-G → should now read **ADDRESSED-BY-P-C-REFRAMED-BY-P-G-CLOSES-IN-P-E**.

---

**Packet P-G ready for critic-opus post-execution review. Closure request to follow.**
