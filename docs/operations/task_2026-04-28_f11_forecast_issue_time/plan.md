# F11 — `forecasts.forecast_issue_time` Hindsight Antibody

Created: 2026-04-28
Last reused/audited: 2026-04-28
Authority basis: `architecture/invariants.yaml` (INV-06 point-in-time truth, INV-15 forecast cycle identity), `docs/operations/task_2026-04-27_backtest_first_principles_review/01_backtest_upgrade_design.md` §5 (D4 typed provenance), forensic finding F11 ("forecast available_at may be reconstructed → hindsight leakage risk"), live SQL probe 2026-04-27/28
Status: planning evidence; not authority. No DB / schema mutation in this plan packet.
Branch: `claude/mystifying-varahamihira-3d3733` (continuation; F11 implementation lands as follow-up commits)

---

## 0. Why this packet exists separately

F11 is the realized, on-disk form of the hindsight-leakage risk:
- 23,466 rows in `state/zeus-world.db::forecasts` have `forecast_issue_time = NULL` (verified live SQL 2026-04-28).
- `src/data/forecasts_append.py:236` explicitly sets `forecast_issue_time=None` because Open-Meteo's Previous Runs API does not expose run issue times in the response payload.
- Schema columns `raw_payload_hash`, `captured_at`, `authority_tier` are also NULL on every row — same writer never populates them.
- D4 from packet 2026-04-27 §5 introduces `AvailabilityProvenance` typed enum that downstream consumers (backtest skill/economics) MUST honor. F11 fix is the WRITER side of that contract.

This is a **medium slice**, not a single small change:
- Touches a runtime data-ingestion writer (medium blast).
- Has 4 forecast sources each with their own dissemination schedules to verify.
- Includes a 23,466-row backfill of existing data.
- May or may not require a schema column (design Q1 below).

It needs its own packet with critic + verifier review.

---

## 1. Disk-verified problem state (2026-04-28)

```
forecasts: 23,466 rows
  source distribution:
    openmeteo_previous_runs : 4,998
    gfs_previous_runs       : 4,998
    ecmwf_previous_runs     : 4,998
    icon_previous_runs      : 4,284
    ukmo_previous_runs      : 4,188
  forecast_issue_time : NULL on every row
  raw_payload_hash    : NULL on every row
  captured_at         : NULL on every row
  authority_tier      : NULL on every row
```

`src/data/forecasts_append.py:236` constructs `ForecastRow(forecast_issue_time=None, ...)` because the Open-Meteo Previous Runs payload (line 134-159 `_fetch_previous_runs_chunk`) returns hourly arrays keyed by `time`, not per-run metadata.

---

## 2. Structural framing (Fitz Constraint #1)

This is **not 4 fixes** (one per NULL column). It is **one structural decision**: what level of decision-time-truth provenance do these rows carry? Once that's typed, all four columns either get values or are explicitly stamped UNKNOWN.

**The decision (Q1 below) is**: where does `availability_provenance` live in the schema?

---

## 3. Design questions (operator decision needed)

### Q1. Schema treatment for `availability_provenance`

| Option | Cost | Pros | Cons |
|---|---|---|---|
| **A. Use existing `data_source_version` column** | none — already exists, NULL today | Zero migration | Overloads a name; breaks if any consumer parses `data_source_version` as version-only |
| **B. Add `availability_provenance` TEXT column** | small migration, planning lock | Cleanest; matches D4 type contract | DB schema change; requires a schema slice |
| **C. Pack into existing `raw_payload_hash` JSON-extended field** | none | No new column | Conceptually wrong — provenance ≠ hash |

**Recommendation: B** — minimal column add (`ALTER TABLE forecasts ADD COLUMN availability_provenance TEXT`), explicit semantic, gates training-eligibility queries.

### Q2. Dissemination derivation per source

Each source needs its own deterministic `(base_time, lead_day) → available_at` function. Today only ECMWF is verified (packet 2026-04-27 §C1):

| Source | Schedule (verified?) | Action |
|---|---|---|
| `ecmwf_previous_runs` | ✓ Day 0 = base + 6h40m, Day N = +4min/day; cite https://confluence.ecmwf.int/display/DAC/Dissemination+schedule | Use `src.backtest.decision_time_truth.ecmwf_ens_available_at` |
| `gfs_previous_runs` | ✗ unverified; NOAA NCEP typically issues GFS 00/06/12/18 UTC ~3.5h after base | Verify primary source before stamping |
| `icon_previous_runs` | ✗ unverified; DWD ICON | Verify primary source |
| `ukmo_previous_runs` | ✗ unverified; UKMO Met Office | Verify primary source |
| `openmeteo_previous_runs` | ✗ Open-Meteo is a re-distributor; the `best_match` model could be any of the above | Treat as `UNKNOWN` until clarified — mark `RECONSTRUCTED` rather than `DERIVED_FROM_DISSEMINATION` |

### Q3. Backfill strategy for the 23,466 existing rows

| Option | Action | Effort |
|---|---|---|
| **A. Single migration script** | `scripts/backfill_forecast_issue_time.py` populates all 23,466 in one pass; per-source dissemination derivation; mark `availability_provenance = "DERIVED_FROM_DISSEMINATION"` (or RECONSTRUCTED for openmeteo `best_match`) | medium |
| **B. Quarantine via authority_tier** | Stamp existing rows with `authority_tier = "QUARANTINED_PRE_F11"` and only populate going forward | low |
| **C. Both (B as fallback for any rows option A can't derive)** | Combined | medium |

**Recommendation: C** — derive what's verifiable; quarantine the rest with explicit reason.

### Q4. SettlementSemantics-style assertion at writer site?

D4 antibody requires the writer to STAMP an `availability_provenance` value. Should the writer's `_insert_rows` raise if the provenance is NULL?

**Recommendation: yes** — single-line `assert` after row construction; matches the typed contract direction. Wrong code becomes unwritable.

---

## 4. Slices

### Slice F11.1 — `_dissemination_lag` registry + ECMWF wiring (CODE ONLY, additive)

Scope:
- New file `src/data/dissemination_schedules.py` enumerating per-source `(base_time, lead_day) → datetime`.
- ECMWF entry uses existing `src.backtest.decision_time_truth.ecmwf_ens_available_at`.
- Other sources stub `RECONSTRUCTED` until verified (Q2).

Files: 1 new src + 1 test (~100 lines total).

Tests:
- `tests/test_dissemination_schedules.py` — relationship test that `derive(ecmwf, base, lead)` matches the cited Confluence wiki schedule + URL in test docstring.

Blast radius: low (additive only).

### Slice F11.2 — Schema migration + `availability_provenance` column

Scope:
- One ALTER TABLE migration adding `availability_provenance TEXT`.
- Migration script under `scripts/migrate_forecasts_availability_provenance.py`.
- Per memory L24, run on a backup of `state/zeus-world.db` first; verify integrity.

Triggers planning lock (schema change). Operator must approve Q1 = B.

Files: 1 new script + 1 schema test.

Blast radius: low-medium (schema add is reversible; existing reads continue working with NULL).

### Slice F11.3 — Writer modification

Scope:
- `src/data/forecasts_append.py:_rows_from_payload` derives `forecast_issue_time = base_datetime + dissemination_lag(source)` and `availability_provenance` accordingly.
- `_INSERT_SQL` extended to write the new column.
- ForecastRow dataclass extended.
- Writer assertion: row constructed without `availability_provenance` raises (Q4).

Tests:
- `tests/test_forecasts_writer_provenance_required.py` — antibody that NULL-provenance row inserts fail; OpenMeteo best_match rows get RECONSTRUCTED; ECMWF rows get DERIVED_FROM_DISSEMINATION.

Blast radius: medium (touches live ingest writer that runs every cron tick). Must confirm cron job continues to succeed.

### Slice F11.4 — Backfill 23,466 existing rows

Scope:
- `scripts/backfill_forecast_issue_time.py` reads existing rows, applies `dissemination_schedules.py` per source, writes `forecast_issue_time` + `availability_provenance` in a single transaction.
- Dry-run / apply / report modes (per Zeus script convention).
- Rows where derivation fails (e.g., openmeteo best_match) get `availability_provenance="RECONSTRUCTED"` + `authority_tier="QUARANTINED_PRE_F11"`.

Files: 1 new script + 1 evidence doc with row-class breakdown.

Blast radius: medium (mutates 23,466 existing rows). MUST run on `state/zeus-world.db.backup-pre-f11` first; verify; then on canonical.

### Slice F11.5 — Consumer gating

Scope:
- `src/backtest/skill.py` and any future `decision_time_truth` consumer of `forecasts` reads `availability_provenance` and routes through the per-purpose `gate_for_purpose`.
- Training-eligibility view: only rows with `availability_provenance IN ('FETCH_TIME', 'RECORDED', 'DERIVED_FROM_DISSEMINATION')` are training-eligible.

Tests: relationship test that a query in SKILL purpose excludes RECONSTRUCTED rows.

Blast radius: low (additive guard).

---

## 5. Acceptance gates per slice

Each slice packet must:

1. Run new tests + existing forecasts regression baseline → 0 new failures.
2. `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit` clean.
3. F11.2 + F11.4 require operator approval before applying to canonical DB; F11.4 requires verified backup.
4. Critic-opus review (con-nyx primary; surrogate code-reviewer-opus parallel).
5. Verifier rerun on a clean checkout.

---

## 6. Out of scope (this packet)

- Modifying SettlementSemantics or other K0_frozen_kernel surfaces.
- Touching `ensemble_snapshots` (TIGGE-sourced, separate path with already-correct provenance — see [task_2026-04-27_backtest_first_principles_review/evidence/vm_probe_2026-04-27.md](../task_2026-04-27_backtest_first_principles_review/evidence/vm_probe_2026-04-27.md) §6.A).
- Polymarket subgraph ingestion (`market_events_v2` populate) — separate packet.
- LOW-track settlements writer.
- Removing `src/engine/replay.py` (S5 is downstream of S2 + this packet).

---

## 7. Estimated effort

| Slice | Type | Estimate | Calendar |
|---|---|---|---|
| F11.1 dissemination registry | small additive | 4-6h | 1-2 days with critic |
| F11.2 schema migration | small (planning lock) | 2-4h + operator approval | 1 day after Q1 answered |
| F11.3 writer modification | medium | 6-8h | 2 days |
| F11.4 backfill 23,466 rows | medium | 4-6h + DB backup verify | 1-2 days |
| F11.5 consumer gating | small | 2-4h | 1 day |
| **TOTAL** | medium packet | **~25-30h focused** | **~1 week calendar** |

---

## 8. Open operator decisions (gates)

| # | Question | Blocks |
|---|---|---|
| Q1 | Schema treatment for availability_provenance (A/B/C) | F11.2 |
| Q2 | Verify GFS / ICON / UKMO dissemination schedules from primary sources | F11.1 (can ship ECMWF-only first) |
| Q3 | Backfill option A / B / C | F11.4 |
| Q4 | Writer raises on NULL provenance? | F11.3 |

Slice F11.1 (dissemination registry, ECMWF-only) can ship without any decision — it's pure additive code. Other slices wait on Q1 / Q3 / Q4.

---

## 9. Memory rules

- L20 grep-gate: every file:line citation here verified within writing window 2026-04-28.
- L22 commit boundary: F11.3 + F11.4 implementation MUST NOT autocommit before critic dispatch (these are runtime + canonical-DB-mutating slices).
- L24 git scope: stage only files inside `task_2026-04-28_f11_forecast_issue_time/**` for plan commit; subsequent slice commits stage their slice files.
- L30 SAVEPOINT audit: F11.4 backfill writes via `executemany` inside a transaction — verify no `with conn:` collision in scripts.
