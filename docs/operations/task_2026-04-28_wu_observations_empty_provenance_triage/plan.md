# WU Observations — 39,431 Empty-Provenance Rows Triage (Q5)

Created: 2026-04-28
Last reused/audited: 2026-04-28
Authority basis: forensic finding F5 (CRITICAL), `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md` §H4 + §C3.A, packet 2026-04-27 §02 §3.A (Q5 deferred), live SQL probe 2026-04-27/28
Status: planning evidence; not authority. No DB / schema mutation in this plan packet.

---

## 0. Why this packet exists separately from F11

F11 fixed `forecasts` table provenance (forecast issue_time NULL → typed). This packet fixes `observations` table provenance (provenance_metadata empty on 99% of WU rows). They are **different tables, different writers, different consumers** — combining would explode blast radius.

## 1. Disk truth (verified 2026-04-27/28)

```
observations table:
  total: 42,749 rows
  VERIFIED: 42,743

  Per-source distribution + empty-provenance rate:
    wu_icao_history    : 39,437 total, 39,431 empty  (99% empty — defective writer)
    ogimet_metar_uuww  :    837 total,      0 empty  (clean)
    ogimet_metar_llbg  :    837 total,      0 empty  (clean)
    hko_daily_api      :    821 total,      0 empty  (clean)
    ogimet_metar_ltfm  :    756 total,      0 empty  (clean)
    ogimet_metar_vilk  :     59 total,      0 empty  (clean)
    ogimet_metar_fact  :      2 total,      0 empty  (clean)
```

**Pattern**: WU writer historically did NOT stamp `provenance_metadata`; ogimet + HKO writers always did. Single-writer defect, not systemic data-quality problem.

---

## 2. Structural framing (Fitz Constraint #1)

Not 39,431 fixes (one per row). Two structural decisions:

| Decision | Effect |
|---|---|
| **A. Going-forward writer hardening** | Ensure `wu_icao_history` writer NEVER produces empty-provenance rows again. Code change. |
| **B. Historical row treatment** | Decide what to do with the 39,431 existing empty-provenance rows. Operator decision (Q5 from packet 02). |

A is a small code slice. B has 3 sub-options (A.1 / B.1 / C.1 below).

---

## 3. Backfill feasibility analysis (cross-referenced with oracle_shadow_snapshots)

**Oracle shadow snapshots** at `raw/oracle_shadow_snapshots/{city}/{date}.json` are recent (post-2026-04-15) authoritative WU API captures with full payload + sha256 derivable.

| Dimension | settlements | observations (WU) | oracle_shadow | Overlap with empty-provenance rows |
|---|---|---|---|---|
| Date range | 2025-12-30 → 2026-04-16 | 2023-12-27 → 2026-04-19 | 2026-04-15 → 2026-04-26 | only 2 dates × 48 cities = 96 rows |
| % of empty-provenance rows that overlap | n/a | n/a | n/a | **0.24%** |

**Conclusion**: oracle_shadow can recoverably backfill ~96 rows authoritatively. The other 39,335 rows have no payload-source-of-truth on disk.

---

## 4. Three handling options

### Option A.1 — Quarantine all 39,431 rows
- `UPDATE observations SET authority='QUARANTINED', quarantine_reason='empty_provenance_wu_daily_pre_2026-04-15' WHERE source='wu_icao_history' AND (provenance_metadata IS NULL OR provenance_metadata = '' OR provenance_metadata = '{}')`
- Effect: training-eligible WU obs drops from 39,437 → 6 rows (the 6 that already had provenance).
- Pros: simplest, fully reversible (UPDATE authority back to VERIFIED).
- Cons: scale loss in training data (2.3 years of WU obs effectively excluded).

### Option B.1 — Partial backfill from oracle_shadow + quarantine rest (RECOMMENDED)
- For the 96 rows where (city, target_date) matches a shadow file:
  - Read shadow JSON
  - Verify city + station_id + date match
  - Compute provenance_metadata from shadow's `wu_raw_payload`:
    ```json
    {
      "source_url": "https://api.weather.com/...",
      "payload_sha256": "<sha256 of wu_raw_payload>",
      "parser_version": "wu_icao_history_v1",
      "station_id": "<KLGA/etc>",
      "imported_via": "oracle_shadow_backfill_2026-04-28"
    }
    ```
  - UPDATE `provenance_metadata` on the matching observation rows
- For the remaining 39,335 rows: option A.1 quarantine
- Pros: salvages the 96 audit-grade rows; explicit provenance for going-forward training; the 39,335 quarantine is reversible if log-replay (option C.1) ever lands.
- Cons: 0.24% recovery rate; bulk of rows still excluded.

### Option C.1 — Log-replay reconstruction
- Walk historical fetcher logs (if any persist)
- For each log line, derive (city, target_date, station_id, payload_hash) and UPDATE the matching observation row
- Open question (forensic §14 #2): do logs persist? Operator must answer before this option is feasible.
- Pros: highest authority; could recover all 39,431 rows
- Cons: requires log persistence the operator has NOT confirmed; effort 1-2 weeks; likely partial coverage.

**Recommendation: B.1 now + flag C.1 for future if logs exist.**

---

## 5. Slices (assuming B.1)

### Slice Q5.1 — Going-forward writer hardening (CODE ONLY, additive)

Scope:
- Identify the WU writer (likely `src/data/observation_client.py` or `src/data/daily_observation_writer.py`).
- Extend writer to require non-empty `provenance_metadata` on every insert.
- Add assertion `if not provenance_metadata: raise ValueError(...)` at writer site (matches F11.3 pattern).
- Source URL + payload SHA256 + parser version are mandatory fields.
- Antibody test: `tests/test_wu_observations_writer_provenance_required.py`.

Files: 1-2 modified writer + 1 new test.

Blast radius: low-medium (touches live ingest writer; not currently running due to LIVE PAUSED).

### Slice Q5.2 — Schema migration (provenance_metadata NOT NULL CHECK)

Scope:
- Add CHECK constraint: `provenance_metadata IS NOT NULL AND provenance_metadata != ''`.
- Currently column allows NULL/empty.
- Migration script + rollback path.
- Triggers planning lock (schema change).

Files: 1 new migration script + 1 schema test.

Blast radius: low (new constraint; existing rows must already be cleaned via Q5.3 first or constraint will fail).

### Slice Q5.3 — Backfill from oracle_shadow (96 rows)

Scope:
- New script `scripts/backfill_wu_obs_provenance_from_oracle_shadow.py`.
- Reads `raw/oracle_shadow_snapshots/*/*.json`.
- For each (city, target_date), match observation rows.
- UPDATE `provenance_metadata` with derived JSON bundle.
- Dry-run + apply + verify modes (matching F11.4 pattern).

Files: 1 new script + 1 antibody test verifying derived bundle shape.

Blast radius: low (96 rows, fully reversible via backup restore).

### Slice Q5.4 — Quarantine the remaining 39,335 rows

Scope:
- New script `scripts/quarantine_wu_obs_empty_provenance.py`.
- UPDATE 39,335 rows to `authority='QUARANTINED'` with explicit reason.
- Single transaction, dry-run + apply + verify.
- DB backup verified before apply.

Files: 1 new script + 1 antibody test.

Blast radius: medium (mutates 39,335 existing canonical rows). Requires DB backup + operator confirmation.

### Slice Q5.5 — Consumer gating

Scope:
- Training-eligibility view / filter rejects QUARANTINED rows with this reason (Zeus already filters on `authority='VERIFIED'` for calibration training; verify the filter holds).
- Antibody test: assert calibration manager refuses to read QUARANTINED rows.

Files: 1 new test (or extension of existing authority-filter test).

Blast radius: low.

---

## 6. Ordering + dependencies

```
Q5.1 writer hardening        ─→ first; must precede Q5.4 to ensure no NEW empty rows
Q5.2 schema CHECK constraint ─→ blocked on Q5.3 + Q5.4 (constraint would fail with empty rows present)
Q5.3 oracle_shadow backfill  ─→ runs first on the 96 row subset
Q5.4 quarantine the rest     ─→ runs after Q5.3 cleans the recoverable subset
Q5.5 consumer gating         ─→ last; verifies the contract end-to-end
```

Apply order on canonical DB:
1. Q5.1 (writer commit) — hardens going-forward
2. Q5.3 (oracle_shadow backfill apply) — 96 rows
3. Q5.4 (quarantine apply) — 39,335 rows
4. Q5.2 (schema CHECK constraint) — locks the going-forward contract
5. Q5.5 (consumer gating commit + tests)

---

## 7. Open operator decisions

| # | Question | Blocks |
|---|---|---|
| Q5-A | Confirm B.1 over A.1 (recovery 96 rows + quarantine 39,335)? | Q5.3 + Q5.4 |
| Q5-B | Do historical WU fetcher logs exist? If yes, Q5.6 (option C.1 log-replay) becomes a future opportunity. | Q5.6 future |
| Q5-C | Authorize canonical DB writes for Q5.3 / Q5.4? | Apply step |

---

## 8. Estimated effort

| Slice | Type | Estimate |
|---|---|---|
| Q5.1 writer hardening | small code | 4-6h |
| Q5.2 schema CHECK | small | 2-4h + operator approval |
| Q5.3 oracle_shadow backfill | medium | 6-8h |
| Q5.4 quarantine 39,335 | medium | 4-6h + DB backup verify |
| Q5.5 consumer gating | small | 2-4h |
| **TOTAL** | medium packet | **~20-25h focused, ~1 week calendar** |

---

## 9. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| oracle_shadow JSON SHA256 mismatch (operator captured vs original WU response) | low | Verify shadow capture has integrity checks; if not, mark backfilled rows as `oracle_shadow_backfill_v1` provenance with explicit caveat |
| Quarantine breaks downstream calibration that already trained on these rows | medium | Audit calibration pairs / Platt models for downstream provenance — they should not have been trained on empty-provenance rows already (forensic ruling: training BLOCKED). If they were, the Platt models also need re-training. |
| Writer hardening (Q5.1) breaks live ingest if a code path exists that doesn't have provenance_metadata available | medium | Run regression with the writer change; verify live cron job continues to succeed in next tick after Q5.1 commit (live still paused; safe window) |
| Operator confirms historical WU logs exist (option C.1) — sunk cost on Q5.4 quarantine | low | Quarantine is reversible: UPDATE authority back to VERIFIED. The work is not wasted. |

---

## 10. Out of scope

- Polymarket subgraph ingestion (`market_events_v2` populate) — separate packet
- LOW-track settlements writer — separate packet
- Modifying `SettlementSemantics` or other K0_frozen_kernel surfaces
- Touching `forecasts` table — F11 packet
- Touching observations table for non-WU sources — they're already clean

---

## 11. Memory rules

- L20 grep-gate: every row count probed live within 30 minutes of writing.
- L22 commit boundary: each Q5.* slice MUST NOT autocommit before critic dispatch.
- L24 git scope: stage only `task_2026-04-28_wu_observations_empty_provenance_triage/**` files for this plan packet.
- L30 SAVEPOINT audit: Q5.4 backfill writes via `executemany` inside auto-commit transaction; no SAVEPOINT collision.
