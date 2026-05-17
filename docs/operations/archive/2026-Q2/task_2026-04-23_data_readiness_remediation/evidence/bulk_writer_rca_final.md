# P-A Deliverable: Bulk Writer RCA — Final Evidence Doc

**Packet**: P-A
**Goal**: identify the writer of the 1,562 settlement rows in `state/zeus-world.db` that carry the impossible combination `authority='VERIFIED' AND winning_bin IS NULL`.
**Date**: 2026-04-23
**Executor**: team-lead
**Pending review**: critic-opus

---

## Section 1 — The Question

All 1,562 rows in `settlements` table share:
- `settled_at = '2026-04-16T12:39:58.026729+00:00'` (single microsecond, single transaction)
- `authority = 'VERIFIED'` (all)
- `winning_bin = NULL` (all)
- `market_slug = NULL` (all)

This signature is structurally impossible for `src/execution/harvester.py::_write_settlement_truth` (which SETS `winning_bin` at L550 before SETTING `authority='VERIFIED'` at **L563**). So the writer is NOT the registered settlement_write_route. **Who wrote these rows?**

*(Line-number correction applied per critic-opus F1: `grep -n "'VERIFIED'" src/execution/harvester.py` → L563, not L562.)*

---

## Section 2 — Decoded Writer Logic (from data signature)

### 2.1 Full signature census

Column-by-column NULL count (SQL run 2026-04-23T12:55:00Z):

| Column | Populated | NULL |
|---|---:|---:|
| city | 1562 | 0 |
| target_date | 1562 | 0 |
| market_slug | 0 | **1562** |
| winning_bin | 0 | **1562** |
| settlement_value | 933 | 629 |
| settlement_source | 1562 | 0 |
| settled_at | 1562 | 0 |
| authority | 1562 | 0 |
| pm_bin_lo | 1503 | **59** |
| pm_bin_hi | 1352 | **210** |
| unit | 1562 | 0 |
| settlement_source_type | 1562 | 0 |

### 2.2 Bin-shape breakdown in DB

| Shape | Count |
|---|---:|
| Point bins (pm_bin_lo = pm_bin_hi, both non-NULL) | 933 |
| Finite ranges (pm_bin_lo < pm_bin_hi) | 360 |
| Low shoulders (pm_bin_lo NULL, pm_bin_hi populated) | 59 |
| High shoulders (pm_bin_hi NULL, pm_bin_lo populated) | 210 |
| Both NULL | 0 |

### 2.3 Correlation: `settlement_value = pm_bin_lo` for all point bins

SQL (reproducible):

```sql
SELECT SUM(CASE WHEN settlement_value = pm_bin_lo THEN 1 ELSE 0 END) AS matches,
       SUM(CASE WHEN settlement_value != pm_bin_lo THEN 1 ELSE 0 END) AS mismatches,
       COUNT(*) AS total_point_bins
FROM settlements
WHERE pm_bin_lo IS NOT NULL AND pm_bin_lo = pm_bin_hi AND settlement_value IS NOT NULL;

→ matches=933 | mismatches=0 | total_point_bins=933
```

**100% of point-bin rows have settlement_value exactly equal to pm_bin_lo.**

### 2.4 Writer algorithm (inferred)

```python
# Reads pm_settlement_truth.json (1566 entries in current file)
for entry in json_entries:
    # Shoulder sentinel mapping
    pm_bin_lo = None if entry['pm_bin_lo'] == -999 else entry['pm_bin_lo']
    pm_bin_hi = None if entry['pm_bin_hi'] ==  999 else entry['pm_bin_hi']

    # Settlement value: ONLY for point bins (where lo == hi after sentinel-map)
    if pm_bin_lo is not None and pm_bin_hi is not None and pm_bin_lo == pm_bin_hi:
        settlement_value = pm_bin_lo
    else:
        settlement_value = None

    # Author bin label is NOT computed (winning_bin left NULL)
    # Market slug is NOT set
    INSERT INTO settlements (city, target_date, pm_bin_lo, pm_bin_hi, unit,
                             settlement_source, settlement_source_type,
                             settlement_value, authority, settled_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'VERIFIED', '2026-04-16T12:39:58.026729+00:00')
```

### 2.5 Cross-check DB signature vs JSON source shape

| Category | JSON `pm_settlement_truth.json` | DB `settlements` |
|---|---:|---:|
| Total entries | 1566 | 1562 |
| Point bins | 936 | 933 |
| Finite ranges | 360 | 360 **(exact)** |
| Low shoulders (lo=-999) | 59 | 59 null_lo **(exact)** |
| High shoulders (hi=999) | 211 | 210 null_hi |

Differences explained:
- JSON has 5 duplicate (city, date) entries for 2026-04-15 (London, NYC, Seoul, Tokyo, Shanghai) — documented in scientist R3-D4. Unique JSON keys = 1561. DB has 1562 → +1 row not in JSON = **Denver 2026-04-15** (scientist R3-D7 — confirmed Denver exists in DB but NOT in pm_settlement_truth.json).
- JSON 936 point bins vs DB 933 point bins: differential 3 falls within the 5 duplicates' point-bin subset (London, Shanghai, Tokyo 2026-04-15 all have duplicate entries where entry 1 is point-bin-typed while entry 2 is lower-value point; writer loaded only one per key).

**Writer used `pm_settlement_truth.json` as its source** (not `pm_settlements_full.json` which has 1567 entries with different schema). The exact JSON schema (pm_high / resolution_source as WU URL) matches the DB signature (settlement_source populated with WU URL strings).

---

## Section 3 — Hypotheses evaluated

### H1: `scripts/rebuild_settlements.py` (deleted in commit d99273a 2026-04-16 09:22 CDT)

**Ruled OUT**. Read the content at `git show d99273a^:scripts/rebuild_settlements.py`:
- Writes `settlement_source = f"{obs_source}_rebuild"` — suffix pattern
- SQL: `SELECT COUNT(*) FROM settlements WHERE settlement_source LIKE '%_rebuild%'` → **0 rows**
- rebuild_settlements.py reads `observations`, not `pm_settlement_truth.json` — wrong source
- rebuild_settlements.py uses SettlementSemantics.assert_settlement_value for ALL rows — would produce settlement_value for F-cities too (DB has 0 F settlement_value)

### H2: `scripts/audit_polymarket_city_settlement.py` (deleted in d99273a)

**Ruled OUT**. Read at `git show d99273a^:scripts/audit_polymarket_city_settlement.py`:
- Purpose: audit (reads Polymarket Gamma API, compares to cities.json)
- Writes to `state/city_settlement_audit.json` — NOT to DB
- No `INSERT INTO settlements` statement

### H3: `scripts/smoke_test_settlements.py` (deleted in d99273a)

**Ruled OUT**. Read at `git show d99273a^:scripts/smoke_test_settlements.py`:
- Purpose: smoke-test (fetches closed Gamma events, compares to obs)
- No DB write statements; outputs to stdout

### H4: `scripts/_build_pm_truth.py` (CREATED in d99273a)

**Ruled OUT**. Current content grep:
- `grep -nE "sqlite|conn\.execute|INSERT" scripts/_build_pm_truth.py` → **0 matches**
- Script only writes `data/pm_settlements_full.json` (line 187)
- No DB connection code at all

### H5: `src/execution/harvester.py::_write_settlement_truth`

**Ruled OUT structurally**. L550 SETS `winning_bin`; L563 stamps `authority='VERIFIED'` only after winning_bin is set. Bulk DB has `winning_bin=NULL` universally. Cannot be this writer.

### H6: `scripts/onboard_cities.py::scaffold_settlements`

**Ruled OUT**. L383 `INSERT OR IGNORE INTO settlements (city, target_date)` — only writes 2 columns, leaves `unit` NULL. Bulk DB has `unit` populated for all 1,562 rows.

### H7: Interactive Python REPL (IPython) session

**Ruled OUT**. IPython history:
```sql
SELECT source FROM history WHERE source LIKE '%INSERT INTO settlements%' OR source LIKE '%pm_settlement%' OR source LIKE '%pm_bin_lo%'
→ 0 rows
```
No IPython session ever executed a settlements-INSERT or pm_bin_lo statement. Shell history (`.zsh_history`, `.bash_history`) only shows SELECT queries (bookkeeping/debugging), no INSERT.

### H8: Jupyter notebook execution

**Ruled OUT**. `find . -name "*.ipynb" -not -path "./.git/*"` → **0 results**. No notebooks exist in the repo.

### H9: Git stash contents

**Ruled OUT**. `git stash list` has 1 stash. `git stash show -p stash@{0}` — contents are about calibration_pairs_v2 + invariant test activation, NOT a settlements writer.

### H10: Archived dual-track refactor packages

**Ruled OUT**. 
- `zeus_dual_track_refactor_package_2026-04-16/` (deleted in commit a46fb36): archive file `05_code_skeletons.py` is for schema, not bulk-loading.
- `zeus_dual_track_refactor_package_v2_2026-04-16/` (still in docs/archives): contains test files (`test_schema_dual_track.py`) and code snippets (`rebuild_settlements_v2.py`, etc.) for the v2 schema. None match the bulk-writer signature.

### H11: Cron / launchd scheduled job

**Ruled OUT for this bulk**. `~/.openclaw/cron/jobs.json` has one settlement-related cron (`city-settlement-audit`) — weekly Monday 9am CT. 2026-04-16 was a Thursday. Also it calls smoke_test_settlements.py (a read-only tool, not a writer). No cron fired at 07:39 CDT on 2026-04-16.

### H12: Unregistered local-only script — PROBABLE

**Cannot be fully verified, BUT most consistent with evidence**:
- `data/pm_settlements_full.json` mtime = Apr 16 07:39:07 CDT
- Bulk DB write at 07:39:58 CDT (51 seconds later)
- This ~1-minute gap strongly implies a single session: fetch Gamma → write JSONs → write DB
- No such combined fetch+write script currently exists in repo or git history
- Hypothesis: writer was a local `.py` (or `.sh`/`.ipynb`) file that existed at execution time, was run once, and was deleted/moved before commit d99273a landed at 09:22 CDT
- Supporting fact: commit d99273a is the first git appearance of `_build_pm_truth.py` (the JSON-write half). The DB-write half was never committed.

### H13: Dangling git objects (critic-opus extension)

**Ruled OUT**. `git fsck --lost-found` returned 2 dangling commits (b3012c19 2026-04-14, ed01c082 2026-04-20); content scan shows no settlement writer in either.

### H14: OMC session transcripts (NH-A1 closure)

**Ruled OUT**. `grep -l 'INSERT INTO settlements\|pm_bin_lo\|pm_settlement_truth' .omc/sessions/*.json` → **0 matches** across 66 session JSON files.

---

## Section 4 — Verdict

**The writer of the 1,562 rows is unidentifiable from current git + filesystem + history state.**

However, the writer LOGIC is fully decoded (Section 2.4). We know exactly what the writer did even though we don't know the exact script file. This is sufficient for downstream packets because:

1. The rows violate **INV-03** (canonical authority must be append-first/projection-backed; this writer bypassed the ledger/projection path)
2. The rows violate **INV-FP-6** (no registered write_route in `architecture/source_rationale.yaml::write_routes::settlement_write`; only owner is `src/execution/harvester.py`)
3. The rows violate **INV-FP-1** (provenance chain broken — writer identity unknown)
4. The `authority='VERIFIED'` stamp is earned by process, not by fiat (INV-FP-5, NC-02); these rows received the stamp without the process

**Therefore the 1,562 rows MUST NOT be treated as trusted truth**. Downstream packets (P-E reconstruction) must treat them as legacy-hostile:
- **P-E DELETE + INSERT** (not UPDATE — per P-0 §8 rule 10). Rebuild every row from `observations + SettlementSemantics + canonical_bin_label`
- For rows that can't be rebuilt (HK HKO Apr gap, Taipei CWA/NOAA mismatch, null pm_bin): `authority='QUARANTINED'` with explicit provenance_json reason

The unidentifiable writer does NOT block downstream work. It DOES dictate that the quarantine/reconstruction protocol must be maximally conservative.

---

## Section 5 — Evidence artifacts (file paths)

All SQL/grep outputs above are reproducible via the commands shown inline. Additional supporting materials:

- **Deleted scripts' pre-deletion content** (for rule-out hypotheses): `git show d99273a^:scripts/rebuild_settlements.py`, `git show d99273a^:scripts/audit_polymarket_city_settlement.py`, `git show d99273a^:scripts/smoke_test_settlements.py`
- **Current `_build_pm_truth.py`** (for rule-out): `scripts/_build_pm_truth.py` at HEAD
- **IPython history DB**: `~/.ipython/profile_default/history.sqlite` — no settlement INSERT rows
- **Shell history**: `~/.zsh_history`, `~/.bash_history` — only SELECTs
- **pm_settlement_truth.json**: `data/pm_settlement_truth.json` mtime Apr 16 06:28:31 CDT
- **pm_settlements_full.json**: `data/pm_settlements_full.json` mtime Apr 16 07:39:07 CDT
- **settlement column schema**: `src/state/db.py:~150` (existing settlements table)

---

## Section 6 — Downstream packet impact

| Packet | How P-A findings inform |
|---|---|
| P-D (Harvester Gamma probe) | harvester write path is structurally separate from the bulk writer; probe can independently assess `winningOutcome` field availability. P-A's finding that harvester has NEVER written successfully reinforces need for P-D (separate from the bulk writer mystery). |
| P-C (WU product audit) | settlement_source URLs in the 1562 rows are WU-website URLs. If WU audit (P-C) shows WU website != WU API hourly product, P-A's finding reinforces that the bulk data is using a different product than our current `observations.wu_icao_history` source. |
| P-G (pre-existing corrections) | Denver 2026-04-15 orphan (scientist R3-D7) is in DB but not in JSON truth — P-G must handle. 2026-04-15 duplicates (scientist R3-D4) must be resolved before P-E. |
| P-B (schema migration) | settlements must grow INV-14 identity fields + provenance_json, because all 1562 rows carry NO provenance. Migration retrofit must stamp every existing row with `provenance_json={"reason":"unregistered_bulk_writer_2026-04-16_RCA_unidentifiable","rca_doc":"evidence/bulk_writer_rca_final.md"}` before P-F/P-E. |
| P-F (hard quarantine) | Quarantine protocol must mark EVERY current row (not just the hard-quarantine 30) with the "unregistered provenance" reason, because every row is provenance-hostile. |
| P-E (reconstruction) | DELETE+INSERT confirmed as correct approach. Original 1562 (city, target_date) pairs are the universe (minus 5 corrections for 2026-04-15 duplicates after P-G; plus 1 decision on Denver after P-G). Writer identity unknown → no "preserve existing row metadata" tactic is valid. |
| P-H (atomicity refactor) | Not directly informed by P-A, but reinforces need for a registered write_route single-transaction pattern going forward. |

---

## Section 7 — Residual uncertainty

The following are UNKNOWN and documented as permanent unknowns (no further investigation planned):

1. **Exact file name of the writer script**: unidentifiable from current state
2. **Whether writer was Python, Bash, or another language**: evidence strongly suggests Python (based on signature detail + timing with JSON writes), but not provable
3. **Whether writer was manually invoked or automated**: no evidence of automation (no cron, no launchd); likely manual
4. **Whether writer wrote other artifacts we haven't discovered**: state/ files from around Apr 16 don't show obvious traces, but a silent-only writer is possible

These unknowns do NOT block downstream work because the QUARANTINE+RECONSTRUCT approach (per P-0 §8 rule 10) doesn't depend on writer identity — it depends only on the logical conclusion that the rows aren't canonical-authority-grade.

### 7.1 Non-blocking hazards (critic-opus review — NH-A1/2/3)

Recorded for future reference; do not block P-A closure:

- **NH-A1 `.omc/sessions` audit closed (post-critic)**: 66 session files at `.omc/sessions/*.json` (file count was wrong in original execution log which said 12). `grep -l 'INSERT INTO settlements\|pm_bin_lo\|pm_settlement_truth' .omc/sessions/*.json` → **0 matches**. This lane is now NULL-result, not "deferred". Adds one more hypothesis ruleout to §3 (implicit: not in any OMC session transcript).
- **NH-A2 Time Machine / fs_usage lanes**: `fs_usage` is real-time-only (useless). Time Machine is low-probability; no operator-confirmed snapshot exists for the 2026-04-16 07:39–09:22 CDT window where the ghost script would have been on disk pre-deletion. Lane TOMBSTONED pending operator confirmation of snapshot availability.
- **NH-A3 Source-file identification confidence**: evidence supports writer used `pm_settlement_truth.json` at ~90% confidence (sentinel mapping + point-bin exact match + duplicate handling align with that file's specific shape). The remaining 10% covers the hypothesis that writer took either JSON and applied the same logic. Downstream packets do not depend on exact source-file identification.

### 7.2 git fsck --lost-found sweep (critic-opus extension, H13)

critic-opus independently ran `git fsck --lost-found` during P-A review. Result:

- 2 dangling commits found:
  - **b3012c19** (2026-04-14): WIP stash-equivalent, no settlement writer in content
  - **ed01c082** (2026-04-20): test-only changes, no settlement writer in content
- Both ruled out as ghost writer candidates.

Lane H13 (dangling commit content scan) is NULL.

---

## Section 8 — Self-verification (team-lead pre-critic checks)

Every SQL count re-run just before doc submission:

```
SELECT COUNT(*) FROM settlements                                        → 1562 ✓
SELECT COUNT(*) FROM settlements WHERE authority='VERIFIED'             → 1562 ✓
SELECT COUNT(*) FROM settlements WHERE winning_bin IS NULL              → 1562 ✓
SELECT COUNT(*) FROM settlements WHERE market_slug IS NULL              → 1562 ✓
SELECT DISTINCT settled_at FROM settlements                             → 1 value ✓
SELECT COUNT(*) FROM settlements WHERE pm_bin_lo=pm_bin_hi
                              AND settlement_value=pm_bin_lo            → 933 ✓
SELECT COUNT(*) FROM settlements WHERE settlement_source LIKE '%_rebuild%' → 0 ✓
```

Timing:
- `stat -f '%Sm' data/pm_settlement_truth.json` → Apr 16 06:28:31 CDT ✓
- `stat -f '%Sm' data/pm_settlements_full.json` → Apr 16 07:39:07 CDT ✓
- bulk settled_at = 2026-04-16T12:39:58 UTC = 07:39:58 CDT ✓

Git:
- `git log --all --diff-filter=D --pretty='%h %ci' | grep d99273a` → d99273a 2026-04-16 09:22:32 -0500 ✓
- `git show d99273a --stat | head` → adds _build_pm_truth.py, deletes 3 scripts ✓
- `git stash show -p stash@{0} | grep 'INSERT INTO settlements'` → 0 matches ✓

---

**Packet P-A ready for critic-opus review. Closure request to follow.**
