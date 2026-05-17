# Run #11 — F36 root cause + Q1/Q2/Q3 + F40/F41 new findings

## 1. Run metadata

| Field | Value |
|---|---|
| Date (UTC) | 2026-05-17T13:30Z |
| Baseline | main HEAD `4d8888eaea` (post-PR-#126/#130/#132/#133, includes PR #114 K1 forecast DB split merged 2026-05-14) |
| Worktree HEAD pre-run | `148efe98e7` (Run #10) |
| Operator trigger | "drive F36 to a definitive root cause + fix, answer Q1/Q2/Q3, catch other F32-class siblings" |
| Run shape | Targeted F36 root cause + post-hoc Run #10 correction |
| Mode | READ-ONLY (no production writes; one diagnostic `.venv/bin/python` invocation, no DB writes) |

**Headline reversal**: Run #10's F36/F38 are **DEFECT-INVALID-PROVENANCE**. The harvester is healthy and writing settlements daily; the calibration_pairs_v2 table has 91 M rows. Run #10 looked at the wrong DB (`zeus-world.db`) because it did not trace where the K1 forecast-DB split (PR #114, merged 2026-05-14) moved the live data. Two NEW reader-side regressions surfaced as a result: **F40** (oracle bridge reads dead K1 source DB) and **F41** (calibration-transfer OOS evaluator reads dead K1 source DB). Both regressions are real and SEV-1.

---

## 2. Task 1 — F36 definitive root cause

### 2.1 What Run #10 missed

`src/ingest/harvester_truth_writer.py:9` and :395 explicitly state:
> Writes ONLY to forecasts_conn (settlements, settlements_v2, market_events_v2).

Harvester truth-write path uses `get_forecasts_connection()` → `state/zeus-forecasts.db`. Run #10 queried `state/zeus_trades.db` and `state/zeus-world.db` only; never opened `state/zeus-forecasts.db` (the actual K1 target post-2026-05-11 migration).

### 2.2 Hard evidence (read 2026-05-17T07:50Z)

`state/zeus-forecasts.db` (49 GB):

| Table | Total | VERIFIED | Max recorded_at |
|---|---|---|---|
| settlements (v1) | 5599 | 5292 | 2026-05-17T05:46:44+00:00 |
| settlements_v2 | 4016 | 3634 | 2026-05-17T05:46:44+00:00 |
| settlements_v2 VERIFIED since 2026-05-07 | — | **3634** | — |

Most recent rows (sampled): Munich/Warsaw/London/Madrid/Paris/Milan/Ankara/Karachi/Sao Paulo for `target_date=2026-05-15`, all written at 2026-05-17T05:46:44+00:00.

### 2.3 Verdict

**F36 → DEFECT-INVALID-PROVENANCE (Run #10 looked at wrong DB).** The harvester is healthy; it has written 3634 VERIFIED settlements since 2026-05-07; last write today (Karachi included). The empty `settlements` / `settlements_v2` tables in `zeus-world.db` and `zeus_trades.db` are **expected post-K1**: PR #114 moved the 7 forecast-class tables off zeus-world.db and renamed the source tables to `_archived_2026_05_11`. The live tables on the old DB are post-archive empty shells.

### 2.4 Fix recommendation

**None for the harvester.** Update FINDINGS_REFERENCE_v2 F36 status to `DEFECT-INVALID-PROVENANCE` with cross-reference to PR #114 K1 migration. Audit-side antibody filed (see §6, LEARNINGS Cat-J-PROV new entry).

---

## 3. Task 2 — Q1 verdict (archive intent)

### 3.1 Source-of-truth read

`scripts/migrate_world_to_forecasts.py` (commit `0059a78d91`, 2026-05-11; merged via PR #114 / commit `eba80d2b9d` on 2026-05-14):

Docstring header (verbatim):
> K1 forecast DB migration: copy 7 forecast-class tables from zeus-world.db to zeus-forecasts.db with checkpoint-resume (§5.4.1).

Line 305-309:
```python
suffix = "_archived_2026_05_11"
for table in _TABLES:
    archived = f"{table}{suffix}"
    conn.execute(f"ALTER TABLE {table} RENAME TO {archived}")
    logger.info("Renamed %s → %s on world DB", table, archived)
```

Tables moved (per `_TABLES`): `source_run`, `settlements_v2`, `settlements`, `market_events_v2`, `observations`, `ensemble_snapshots_v2`, `calibration_pairs_v2`.

### 3.2 Verdict

**FULL-DRAIN — intentional.** The K1 split is a permanent migration: source tables on `zeus-world.db` are renamed to `_archived_2026_05_11` (kept for rollback), new home is `zeus-forecasts.db`, writers (harvester_truth_writer, harvester) were updated to use `get_forecasts_connection()` / `get_forecasts_connection_with_world()`. Caller-routes were updated in the PR #114 commit (`eba80d2b9d`) per the docstring rollback note ("git revert <K1 commit> (caller routes + lock topology + boot supplement)").

### 3.3 New-writer path status

- Writer side (`src/ingest/harvester_truth_writer.py`, `src/execution/harvester.py`): **implemented + wired + running**. Daemons `com.zeus.data-ingest` and `com.zeus.forecast-live` are loaded. Settlements have flowed every day since 5/11.
- Reader side: **partially migrated**. Several `get_world_connection`-using readers were not repointed. F40 + F41 below are confirmed cases; ~30 files use `get_world_connection` and need a sweep (F42).

### 3.4 Cross-link with F36

F36 was the SUSPECTED-coupled regression. It is **decoupled** — the writer path is healthy. The actual coupled regressions are reader-side (F40/F41).

---

## 4. Task 3 — Q2 hard answer

Probed every DB in `state/`:

| DB | settlements_v2 total | settlements_v2 VERIFIED | settlements_v2 VERIFIED since 5/7 | Max recorded_at |
|---|---|---|---|---|
| `state/zeus-forecasts.db` | **4016** | **3634** | **3634** | 2026-05-17T05:46:44+00:00 |
| `state/zeus-world.db` (live tables, post-archive) | 0 | 0 | 0 | — |
| `state/zeus_trades.db` | 0 | 0 | 0 | — |
| `zeus_trades.db` (repo-root copy) | 0 | 0 | 0 | — |
| `state/zeus-world.db` `settlements_v2_archived_2026_05_11` | 3987 | 3605 | n/a (archive) | 2026-05-11T19:59:13+00:00 |

**Answer: 3634 VERIFIED settlements_v2 rows written since 2026-05-07, exclusively to `state/zeus-forecasts.db`.** Run #10's "settlements live tables empty since 5/7" claim is wrong; the live tables they consulted were zeus-world.db (post-archive shell) and zeus_trades.db (settlements never lived there). Most recent write 2026-05-17T05:46Z — well under 1 hour stale.

---

## 5. Task 4 — Q3 verdict (calibration-transfer-eval plist intent)

### 5.1 Plist text (`~/Library/LaunchAgents/com.zeus.calibration-transfer-eval.plist`)

Header comment (verbatim):
> PROPOSED — DO NOT LOAD AUTOMATICALLY.
> …
> To activate after Phase B trigger conditions are met (see architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml §5):
>   launchctl load ~/Library/LaunchAgents/com.zeus.calibration-transfer-eval.plist
> …
> DO NOT load at initial launch — zero cross-domain rows exist until ECMWF calibration_pairs_v2 accumulate (~2-4 weeks post-launch).

### 5.2 Reality check

- `launchctl list | grep calibration` → loaded, ran 2026-05-17T04:00 (Sunday weekly cadence per plist `StartCalendarInterval Weekday=0 Hour=4`).
- `logs/calibration-transfer-eval.log` last entry: `active_platt_models_iterated=0`, `rows_written=0`.
- Plist file mtime: 2026-05-06T03:34.
- `git log --all -- scripts/evaluate_calibration_transfer_oos.py`: created 2026-05-05 (`514f30b81a feat(calibration): Issue 2.2 X.2 — OOS evaluator writes validated_calibration_transfers [skip-invariant]`).
- No `git log` entry for the `~/Library/LaunchAgents/…plist` itself (LaunchAgents are outside the repo); cannot trace who ran `launchctl load`.

### 5.3 Verdict

**ACCIDENTAL / PREMATURE LOAD (Cat-N).** Plist comment is the source-of-truth intent: DO NOT load at initial launch; activate only after Phase B trigger conditions accumulate ECMWF rows. It IS loaded and firing weekly. There is no operator note in the repo or logs corresponding to a "Phase B activation" event. The runtime evidence — `target domains in calibration_pairs_v2: []` on 2026-05-17 — confirms Phase B condition is NOT met.

Note: F41 below means even if Phase B were met, the eval is currently impotent (reads dead DB).

### 5.4 Operator decision required

- (a) UNLOAD until Phase B genuinely arrives, OR
- (b) keep loaded (treat the comment as stale; remove "DO NOT LOAD"), AND fix F41 so it actually evaluates against zeus-forecasts.db.

---

## 6. Task 5 — F40, F41, F42 opportunistic catches (F32-class siblings)

### 6.1 F40 — Oracle bridge reads retired K1 source DB

- File: `scripts/bridge_oracle_to_calibration.py:71`
  ```python
  DB_PATH = ROOT / "state" / "zeus-world.db"
  ```
  Line 86: `FROM settlements` (bare table name → resolves to `zeus-world.db.settlements`, the post-archive empty shell).
- Effect: Even if F35 (unscheduled) were fixed, the bridge would always emit `{}` because the table it reads has been empty by design since 2026-05-11.
- Sev: SEV-1 (compounds with F35; same blast-radius — oracle-penalty stays at 0.5×/0.0× fallback forever).
- One-line fix: repoint to `state/zeus-forecasts.db` (or switch to `get_forecasts_connection()` from `src.state.db` and remove the hardcoded `DB_PATH`).

### 6.2 F41 — Calibration-transfer OOS evaluator reads retired K1 source DB

- File: `scripts/evaluate_calibration_transfer_oos.py:684`
  ```python
  from src.state.db import get_world_connection
  conn = get_world_connection(write_class="bulk")
  ```
  Reads `calibration_pairs_v2` (bare name) → resolves to `zeus-world.db.calibration_pairs_v2`, which has **0 rows** post-archive; the live data (**91 040 450 rows**) lives in `zeus-forecasts.db`.
- Live regression evidence (logs/calibration-transfer-eval.log):
  - 2026-05-10 10:26: `target domains in calibration_pairs_v2: [('tigge_mars', '00'), ('tigge_mars', '12')]`
  - 2026-05-17 04:00: `target domains in calibration_pairs_v2: []`
  - Aligned exactly with K1 migration 2026-05-11 / PR #114 merge 2026-05-14.
- Sev: SEV-1.
- One-line fix: replace `get_world_connection(...)` with `get_forecasts_connection_with_world(...)` (context manager that ATTACHes world onto forecasts; both bare names resolve correctly).

### 6.3 F42 — META: PR #114 reader-side audit gap

- Live runtime probe via `.venv/bin/python -c "...get_world_connection..."` confirms `SELECT COUNT(*) FROM settlements` and `SELECT COUNT(*) FROM calibration_pairs_v2` both return **0** when called through `get_world_connection(write_class='bulk')`.
- `grep -rln get_world_connection src/ scripts/` returns **~30 files** that import it; many of those also reference the 7 forecast-class tables. PR #114 migrated the WRITER side (harvester_truth_writer + harvester) and the `get_forecasts_connection_with_world()` helper, but did NOT migrate all READER callers.
- Confirmed broken: F40, F41. Suspected broken (NOT verified this run — INVESTIGATE-FURTHER):
  - `scripts/etl_diurnal_curves.py` (reads observations)
  - `scripts/etl_historical_forecasts.py`
  - `scripts/baseline_experiment.py`
  - `scripts/etl_forecast_skill_from_forecasts.py`
  - `scripts/migrate_world_observations_to_forecasts.py` (probably-OK, ETL transitional)
  - `src/data/hole_scanner.py`, `src/data/observation_client.py`, `src/data/ingestion_guard.py`
  - `src/control/control_plane.py`
  - `src/state/connection_pair.py`
- Sev: SEV-1 META (one root cause; N silent failures).
- One-line fix: planned audit pass — for each of the ~30 callers, classify as (i) world-data only (OK), (ii) forecast-class table reader (must switch to `get_forecasts_connection_with_world`), (iii) ETL/historical (case-by-case).

---

## 7. Updated TOP-3 fix priority (incorporating F36 correction + F40/F41/F42)

1. **F42 — K1 reader-side audit pass (META)**. One PR that sweeps the ~30 `get_world_connection` callers; reclassify forecast-class table readers to `get_forecasts_connection_with_world`. F40 + F41 are the first two known cases; doing them in isolation leaves N more silent. Owner action: scripted audit + targeted PR.
2. **F40 — bridge `state/zeus-world.db` repoint** (sub-task of F42 — but the most operationally consequential of the readers since it gates the oracle penalty for every city). After F40 fix is in, F35 (scheduling) becomes the next-step finishing move.
3. **F41 — cal-transfer OOS evaluator** (sub-task of F42). Required before Q3-(b) (keep plist loaded) makes sense.

Demoted (compared to Run #10 TOP-3):
- F36 (no longer a finding).
- F38 (no longer a finding).
- F35 (still SEV-1 but now ranks behind F40, since fixing F35 without F40 produces an artifact of `{}`).

---

## 8. Karachi 2026-05-17 impact (re-affirmed)

**No new impact.** Run #10's Karachi-no-impact stance holds, with a stronger basis: the harvester is verified writing today (Karachi target_date 2026-05-15 settlement landed at 2026-05-17T05:46Z). F40/F41 affect upstream oracle-penalty and OOS-eval analytics paths, both of which were already in fallback at the time Karachi sizing was set. Restoring them would generally be size-favorable, not blocking. Ship Karachi on the prior decision.

---

## 9. Items INVESTIGATE-FURTHER (next run)

- F42 caller sweep: enumerate the ~30 `get_world_connection` callers; classify each as forecast-reader vs world-only. Open one finding per confirmed silent reader. (Cheap mechanical pass.)
- Confirm no SECOND archive operation: `zeus-world.db` has `_archived_2026_05_11` table suffixes only; verify no fresher `_archived_*` suffix appears after Phase 1 of the K1 follow-up (`scripts/migrate_world_observations_to_forecasts.py` exists separately; check its application status).
- Audit whether other plists carry stale "DO NOT LOAD" comments while being loaded (a cheap launchd-vs-doc cross-check would catch repeats of F39).
- Re-examine `validated_calibration_transfers` table location and row count: with F41 broken, has it received any rows? (Likely 0 since K1.)
