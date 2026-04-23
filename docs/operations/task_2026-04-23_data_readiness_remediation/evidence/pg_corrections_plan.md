# P-G Corrections Plan — Pre-Packet (awaiting critic-opus pre-review)

**Packet**: P-G (pre-existing corrections)
**Goal**: resolve Denver synthetic orphan + 5 LOW-market-contaminated rows on 2026-04-15 + clarify the "AP-4 relabel" misframing from P-C §5/§11
**Date**: 2026-04-23
**Executor**: team-lead
**Pending**: critic-opus PRE-REVIEW before any DB mutation
**DB-mutation boundary**: FIRST DB-mutating packet in the workstream. 6 DELETEs total, each tightly scoped.

---

## Section 1 — Major scope revision vs P-C/P-D assumptions

### 1.1 The "2026-04-15 duplicate" is not a duplicate — it's a HIGH/LOW identity collision

P-C §4.2 and scientist R3-D4 framed the 5 London/NYC/Seoul/Tokyo/Shanghai 2026-04-15 anomaly as "JSON duplicate entries where bulk writer loaded the wrong one". Gamma probe 2026-04-23 decisively refines this to **separate HIGH and LOW markets being conflated**:

**Gamma UMA-resolved 2026-04-15 HIGH markets** (probe + YES_WON markers):
| city      | HIGH winner | `pm_settlement_truth.json` EARLY entry | match |
|-----------|-------------|----------------------------------------|-------|
| London    | 17°C        | idx 1513 bin=[17,17] pm_high=17        | ✓    |
| NYC       | 86-87°F     | idx 1520 bin=[86,87] pm_high=86.5      | ✓    |
| Seoul     | 21°C+ (high shoulder) | idx 1517 bin=[21,999] pm_high=21 | ✓    |
| Tokyo     | 22°C        | idx 1530 bin=[22,22] pm_high=22        | ✓    |
| Shanghai  | 18°C        | idx 1532 bin=[18,18] pm_high=18        | ✓    |

**Gamma UMA-resolved 2026-04-15 LOW markets** (separate "Lowest temperature in …" events):
| city      | LOW winner | `pm_settlement_truth.json` LATE entry | match |
|-----------|------------|---------------------------------------|-------|
| London    | 11°C       | idx 1557 bin=[11,11] pm_high=11       | ✓    |
| NYC       | 68-69°F    | idx 1559 bin=[68,69] pm_high=68.5     | ✓    |
| Seoul     | 10°C       | idx 1558 bin=[10,10] pm_high=10       | ✓    |
| Tokyo     | 15°C       | idx 1560 bin=[15,15] pm_high=15       | ✓    |
| Shanghai  | 15°C       | idx 1561 bin=[15,15] pm_high=15       | ✓    |

**Conclusion**: `pm_settlement_truth.json` silently contains BOTH high-temperature AND low-temperature market settlements, using the same field names (`pm_bin_lo`, `pm_bin_hi`, `pm_high`) and **no `temperature_metric` distinguishing field**. The bulk writer iterated the JSON, hit each (city, date) key twice for these 5 cities, and — because `settlements` has a `UNIQUE(city, target_date)` constraint — only the last (LOW) entry survived. The surviving DB rows are LOW-market bins labeled as HIGH-market settlements.

This is **INV-14 (metric-identity spine) failure**: settlements table lacks the `temperature_metric ∈ {high, low}` column that P-B schema migration introduces. In the current pre-P-B schema, there is literally no way to store both HIGH and LOW markets for the same (city, date). P-G's role here is to DELETE the wrong-metric rows so P-E's reconstruction doesn't inherit them.

**Scope-limit check**: `pm_settlement_truth.json` has exactly 5 duplicate (city, date) pairs, all on 2026-04-15. No other dates have this duplication in the truth JSON. LOW-market contamination is bounded to these 5 DB rows.

### 1.2 The 27 "NO_OBS" AP-4 rows are NOT mislabels — they are source-handoff history

P-C §5 and P-C §11 proposed relabeling 27 NO_OBS rows (HK WU → HKO, Taipei NOAA → authoritative, Tel Aviv WU → NOAA) on the assumption that the labels were wrong. SQL query on target-date ranges per source_type shows these are **cleanly disjoint date ranges**, not overlapping labels:

```
Taipei     CWA  Mar 16 → Mar 22 (7 dates)
Taipei     NOAA Mar 23 → Apr 04 (12 dates)
Taipei     WU   Apr 05 → Apr 15 (11 dates)

Tel Aviv   WU   Mar 10 → Mar 22 (13 dates)
Tel Aviv   NOAA Mar 23 → Apr 15 (23 dates)

Hong Kong  WU   Mar 13 → Mar 14 (2 dates)
Hong Kong  HKO  Mar 16 → Apr 15 (29 dates; one gap on Mar 20)
```

These are **source-handoff histories**: Polymarket switched settlement source over time for these cities. The labels reflect historical routing, not bulk-writer confusion. Per fatal_misreads.yaml `api_returns_data_not_settlement_correct_source` + P-0 INV-FP-7 (source role boundaries are strict), substituting obs from a different source family for these rows would introduce AP-4 (source role collapse) — the very anti-pattern P-C used to detect the mismatch.

**Correct handling** for the 27 rows:
- **Option A** (backfill): attempt to collect source-family-correct obs for the historical dates (e.g., `ogimet_metar_llbg` already has Tel Aviv coverage since 2024; `wu_icao_history` starts 2026-04-10 so can't backfill Tel Aviv Mar 10-22).
- **Option B** (QUARANTINE): P-E inserts these rows with `authority='QUARANTINED'` and `provenance_json.reason` in the enumerable set from P-C §6.4, specifically the new reason `pc_audit_source_role_collapse_no_source_correct_obs_available`.

P-G itself does **not** relabel these rows. The "relabel" language in P-C §5 is retired. The rows stay as-is in the current DB until P-E's reconstruction pass decides Option A or B per row.

**Note on Tel Aviv**: `ogimet_metar_llbg` (NOAA) obs DOES exist from 2024-01 for Tel Aviv, covering the 13 WU-labeled dates. But using it for WU-labeled rows would be AP-4. The settlement source label reflects the authority that resolved the market; the obs collector gap is the issue. If Polymarket actually used NOAA for Tel Aviv Mar 10-22 but the bulk writer mislabeled it WU, that's relabel territory — but we have no evidence of that; the historical pattern suggests Polymarket switched at Mar 22 ↔ Mar 23 boundary. Without current_source_validity.md evidence contradicting this, the labels stand.

---

## Section 2 — Minimal DB mutations

### 2.1 Denver 2026-04-15 DELETE (1 row)

**Evidence**:
- P-D §9.1: 250-event Gamma scan, 0 Denver matches
- pm_settlement_truth.json: NO Denver 2026-04-15 entry (searched; verified in §1 of this doc)
- pm_settlements_full.json: HAS Denver 2026-04-15 with `pm_exact_value=None` (synthetic-planned-but-never-opened)
- DB row: `bin=[68,69] F settlement_source_type=WU` — matches pm_settlements_full.json shape

**SQL**:
```sql
BEGIN;
SELECT id, city, target_date, pm_bin_lo, pm_bin_hi, unit, settlement_source_type, settled_at
  FROM settlements WHERE city='Denver' AND target_date='2026-04-15';
-- expect 1 row
DELETE FROM settlements WHERE city='Denver' AND target_date='2026-04-15';
-- verify 1 row deleted
SELECT changes();
-- confirm 0 rows remain for this key
SELECT COUNT(*) FROM settlements WHERE city='Denver' AND target_date='2026-04-15';
-- expect 0
COMMIT;
```

### 2.2 LOW-market-contaminated DELETE (5 rows, 2026-04-15)

**Evidence**: §1.1 Gamma probe + JSON duplicate enumeration confirms these 5 DB rows carry LOW-market bins mislabeled as HIGH-market settlements.

**SQL** (one transaction, atomic):
```sql
BEGIN;
-- Pre-verify: show the 5 rows with their (wrong-metric) bins
SELECT city, target_date, pm_bin_lo, pm_bin_hi, unit, settlement_source_type
  FROM settlements
  WHERE target_date='2026-04-15'
    AND city IN ('London','NYC','Seoul','Tokyo','Shanghai');
-- expect 5 rows with the LOW-market bins: London=11, NYC=[68,69], Seoul=10, Tokyo=15, Shanghai=15

DELETE FROM settlements
  WHERE target_date='2026-04-15'
    AND city IN ('London','NYC','Seoul','Tokyo','Shanghai');
-- verify 5 rows deleted
SELECT changes();

-- Post-verify: 0 rows remain for these 5 (city, 2026-04-15) pairs
SELECT COUNT(*) FROM settlements
  WHERE target_date='2026-04-15'
    AND city IN ('London','NYC','Seoul','Tokyo','Shanghai');
-- expect 0

COMMIT;
```

### 2.3 Nothing else mutates in P-G

- Cape Town 2026-04-15: stays in DB. It is a ±1 °C obs-drift row (R3-22-adjacent). P-E handles via `pc_audit_1unit_drift` reason (§6.4 enumerable set).
- 27 AP-4 rows (HK WU / Taipei NOAA / Tel Aviv WU): stay in DB. P-E decides backfill-or-QUARANTINE.
- 2026-03-08 DST-cluster rows: stay in DB. P-E handles via `pc_audit_dst_spring_forward_bin_mismatch` reason.
- Shenzhen whole-bucket 26 rows: stay in DB. P-E QUARANTINEs via `pc_audit_shenzhen_drift_nonreproducible`.

All of these are DELETE-and-re-INSERT candidates in P-E, not P-G. Keeping P-G narrow: only rows we have structural evidence must NOT exist at all (Denver synthetic) or must be entirely wrong-metric (5 LOW-contaminated).

---

## Section 3 — Safety protocol

1. **Pre-mutation snapshot**: `cp state/zeus-world.db state/zeus-world.db.pre-pg_2026-04-23` (recoverable baseline)
2. **Idempotent by design**: DELETEs match on UNIQUE keys; re-running is a no-op (0 rows affected)
3. **Atomic transactions**: each DELETE wrapped in `BEGIN; … COMMIT;`; any SELECT-verify failure → ROLLBACK
4. **No other-agent interference**: confirm `git status state/zeus-world.db` shows only WAL changes immediately before mutation; if state/zeus-world.db shows a content diff from a concurrent writer, HALT and report

---

## Section 4 — Q1-Q10

**Q1 (invariant)**: INV-03 append-first projection (we're not inserting — we're DELETEing ghost rows that were themselves append-first-violating); INV-FP-1 provenance chain (removal of wrong-metric rows and synthetic-orphan restores provenance cleanliness); INV-FP-9 NULL first-class (after DELETE, the (city, target_date) key simply doesn't exist, which is the correct truth — P-E will re-INSERT or leave absent per evidence).

**Q2 (fatal_misread)**: `api_returns_data_not_settlement_correct_source` — Gamma API is used ONLY as UMA-resolution-vote-authority evidence (per P-D §5.3), not as settlement source substitution. We're not treating Gamma-resolved bins as our settlement source; we're using Gamma's resolution to DISPROVE the existing DB rows. `hong_kong_hko_explicit_caution_path` — not touched here; HK WU rows stay as-is pending P-E.

**Q3 (single source of truth)**:
- Denver synthetic: P-D §9.1 (250-event scan 0 matches) + pm_settlement_truth.json inspection
- 5 LOW-contaminated: Gamma probe 2026-04-23 (outputs embedded in §1.1) + `pm_settlement_truth.json` duplicate enumeration (indices 1513/1520/1517/1530/1532 for HIGH; 1557/1559/1558/1560/1561 for LOW)

**Q4 (first-failure)**:
- Pre-verify SELECT returns wrong count → HALT, report, do not execute DELETE
- DELETE `changes()` returns wrong count → ROLLBACK, report
- Any SQLite error → ROLLBACK, report
- Other-agent modified state/zeus-world.db during P-G → HALT, reconcile

**Q5 (commit boundary)**: 2 transactions (one for Denver, one for the 5-row batch). Each is a single `BEGIN; … COMMIT;` with verifications inside.

**Q6 (verification)**:
- Pre: SELECT row identity + bin values match evidence
- DELETE: `changes()` == expected
- Post: SELECT COUNT(*) == 0 for the removed keys
- Post-P-G: re-run `sqlite3 state/zeus-world.db "SELECT COUNT(*) FROM settlements"` expect 1562 − 6 = 1556
- critic-opus independent verification: re-probe Gamma; re-read pm_settlement_truth.json to confirm the 5 duplicates and their LOW-market identities

**Q7 (new hazards)**:
- Hazard 1: DB snapshot backup may lag the WAL commit if WAL-mode is active. Mitigation: `sqlite3 state/zeus-world.db "PRAGMA wal_checkpoint(TRUNCATE)"` before `cp` to merge WAL.
- Hazard 2: other agent may add a new row that references the deleted keys. Mitigation: foreign-key check before DELETE (`SELECT … FROM any_table WHERE city=? AND target_date=?`). `settlements.id` is the PK; nothing else references it FK-wise per `CREATE TABLE settlements` (no FROM references found in schema).
- Hazard 3: P-E not yet ready; between P-G DELETE and P-E INSERT, the 6 (city, target_date) pairs have no settlement row. Mitigation: downstream consumers (calibration, replay) must already be gated on `winning_bin IS NOT NULL` per P-0 §1 "baseline fact" (all 1562 bulk rows have `winning_bin=NULL`); absence of a row is strictly safer than presence of a wrong-metric row.

**Q8 (closure)**: `evidence/pg_corrections_plan.md` (this file) + `evidence/pg_execution_log.md` (post-execution log with SQL output) + critic-opus APPROVE on both pre-review and post-execution.

**Q9 (decision reversal)**:
- Reverses P-C §5/§11's proposed "relabel 27 NO_OBS rows". Rationale: date-range SQL (§1.2 here) shows the 27 rows are historically-correct source-handoff labels, not mislabels. P-C's prescription was based on a label↔collector framing; the date-range evidence here clarifies it's a collector-gap problem, not a label-error problem. P-E decides backfill-or-QUARANTINE per row, not P-G relabel.
- Reframes R3-D4 "JSON duplicates" as HIGH/LOW-metric-identity failure (per §1.1). Does NOT overrule R3-D4's empirical observation; clarifies root cause.
- Reverses nothing else. Denver DELETE is endorsed by P-D §9.1. 5-row DELETE is endorsed by Gamma probe + JSON enumeration.

**Q10 (rollback)**:
- Per-transaction: ROLLBACK within BEGIN/COMMIT
- Post-packet: restore `state/zeus-world.db.pre-pg_2026-04-23` snapshot
- Forward-compat: if P-B/P-E re-run P-G after DELETE, the DELETE is idempotent (changes()=0 on 2nd run)

---

## Section 5 — Post-P-G P-C re-audit

Per P-C §11, a P-C re-run SHOULD happen between P-G and P-E. After P-G's 6-row DELETE:
- Settlements count: 1562 → 1556
- P-C audit will re-compute: 1556 audited/no_obs partition (expect 6 fewer rows in mismatch list for 2026-04-15; 1 fewer NO_OBS if Denver was counted in no_obs — but Denver was in bin_unavailable=0 since it had bin populated; actually Denver was VERIFIED in P-C because obs.high_temp=68 matched bin=[68,69] — hmm, but then why would we DELETE a VERIFIED Denver row?)

Wait — Denver 2026-04-15 was actually a MATCH per P-C (obs=68°F, bin=[68,69]F). The match was coincidence (obs exists + bin exists + containment happens to hold). The reason to DELETE is P-D §9.1 proved Polymarket never opened the market; our obs is fetched anyway; the pm_bin came from pm_settlements_full.json's speculative entry. So Denver's DB row is VERIFIED-BY-COINCIDENCE but doesn't correspond to a real market. Still DELETE — we're honoring Polymarket reality over accidental DB convergence.

**Expected P-C re-audit shift**:
- Total 1556 settlements (was 1562)
- Mismatches: 32 → 27 (5 of the 2026-04-15 mismatches gone; Cape Town 2026-04-15 remains)
- No_obs: 42 unchanged
- Station_remap: 7 unchanged
- VERIFIED buckets: 37 unchanged
- QUARANTINE buckets: 13 → 12 (NYC WU loses both its mismatches; becomes VERIFIED; actually no — NYC WU had 2 mismatches on 03-08 and 04-15, so losing 04-15 leaves 1 mismatch, still QUARANTINE. London / Shanghai / Tokyo WU each had only 1 mismatch on 04-15, so they become VERIFIED. That's 3 buckets shifting to VERIFIED.)

**Decision**: re-run P-C post-P-G is SHORT (script is 5 seconds) and produces a fresh `evidence/pc_agreement_audit_postPG.json`. Do it. Document the shift in `evidence/pg_execution_log.md`.

---

## Section 6 — What critic-opus should pre-verify

1. §1.1 Gamma probe output: re-run the paginated fetch (offset 0..1930, 100 per page, tag_id=103040 closed=true) + filter to endDate='2026-04-15' + 5 disputed cities. Confirm YES_WON markers for HIGH at 17/86-87/21+/22/18 AND LOW at 11/68-69/10/15/15.
2. §1.2 date-range disjointness: `sqlite3 state/zeus-world.db "SELECT settlement_source_type, GROUP_CONCAT(target_date) FROM settlements WHERE city IN ('Hong Kong','Taipei','Tel Aviv') GROUP BY city, settlement_source_type"` — confirm no date overlaps.
3. §2.1/2.2 SQL: walk through each statement mentally and confirm the pre-verify and post-verify counts match expectation. Flag any edge case (e.g., does `DELETE WHERE city='X' AND target_date='Y'` hit MORE than the expected UNIQUE match? No — UNIQUE(city, target_date) guarantees 1).
4. Q9 decision-reversal language on the P-C §5/§11 relabel: is reverting that prescription sound given the date-range evidence?
5. Post-P-G plan (§5): is it acceptable to re-run P-C with the same script, or does the DELETE of 6 rows require any script modifications?

---

**Awaiting critic-opus PRE-REVIEW. No DB mutation until APPROVE.**
