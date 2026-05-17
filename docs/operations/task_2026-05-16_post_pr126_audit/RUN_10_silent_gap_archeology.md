# Run #10 — Silent-gap archeology + F32-class sibling inventory

## 1. Run metadata

| Field | Value |
|---|---|
| Date (UTC) | 2026-05-17T12:46Z |
| Baseline | main HEAD `4d8888eaea` (post-PR-#126, post-PR-#130/#132/#133, post hotfix/live-runtime-artifact-and-cycle-backpressure) |
| Worktree HEAD pre-run | `d3c3e91b53` (Run #9 commit) |
| Operator trigger (verbatim) | "Continue Run #10 — investigate other 'designed but never wired' silent gaps (F32-class). Also find where these missing data live — I CONFIRM they used to exist and were being generated correctly." |
| Run shape | Archeology + sibling sweep |
| Mode | READ-ONLY intent; one accidental write (disclosed §2.5) |

---

## 2. Phase A — `data/oracle_error_rates.json` archeology

### 2.1 Git history

| Probe | Result |
|---|---|
| `git log --all --diff-filter=A -- '*oracle_error_rates*'` | **empty** — file was never committed on any branch |
| `git log --all --diff-filter=D -- '*oracle_error_rates*'` | **empty** — no deletion event |
| `git log --all -- 'data/oracle*'` | **empty** — no commit ever touched the `data/oracle_*` path |

**Verdict**: The artifact has **NEVER been git-tracked** on any branch in this repo. There is no recoverable blob.

### 2.2 Filesystem sweep

`find /Users/leofitz -name '*oracle_error*' -type f` → **empty** across all of `~/.openclaw` (backups, backup-archives, all 5 worktrees, claude/openclaw config trees).

The path resolves (via `src.state.paths.oracle_error_rates_path()` with `ZEUS_STORAGE_ROOT` unset) to `<repo>/data/oracle_error_rates.json`. The `<repo>/data/` directory itself **did not exist** at run-start.

### 2.3 Bridge dry-run + live re-run

Running `scripts/bridge_oracle_to_calibration.py --dry-run` (and accidental non-dry, see §2.5):

```
2026-05-17T07:40:18Z INFO [DRY RUN] Would update .../data/oracle_error_rates.json with 0 cities
2026-05-17T07:40:18Z INFO Bridge complete: 0 cities, 0 comparisons, 0 mismatches
```

**The bridge CAN run today and exits clean — but it produces an empty `{}` artifact** because its settlements input is empty.

### 2.4 Root cause — operator memory IS CORRECT (2-layer F32)

The operator remembers oracle data being generated correctly. **They are right.** Evidence:

| Table | Verified rows |
|---|---|
| `settlements` (live, bridge reads here) | **0** |
| `settlements_v2` (live) | **0** |
| `settlements_v2_archived_2026_05_11` | **3987 VERIFIED** rows |
| Max `target_date` in archive | **2026-05-07** (now 10 days stale) |
| Cities present | Amsterdam, Ankara, Beijing, Karachi, London, NYC … (the full Zeus city list) |
| Sample | `NYC|2025-01-22|high|VERIFIED`, `London|2025-01-23|high|VERIFIED` |

The data WAS being generated correctly through **2026-05-07**, then on **2026-05-11** a migration moved all rows from `settlements_v2` → `settlements_v2_archived_2026_05_11`, and the live writers (`src/execution/harvester.py:1300`, `src/ingest/harvester_truth_writer.py:551`, `src/state/db.py:4397`) have produced **zero** new VERIFIED rows in the 6 days since.

Two stacked F32s explain the missing `data/oracle_error_rates.json`:

- **F32-A (already filed in Run #9)**: bridge is unscheduled — no cron, no launchd, no jobs.json entry — so even with input data the artifact would never be written.
- **F32-B (new this run)**: settlements live tables are empty because the harvester settlement-write path has been dormant since 2026-05-07 (post-archive). The bridge's `_load_settlements()` SQL `FROM settlements` does not consult the `_archived_2026_05_11` table either, so even an out-of-band manual bridge run yields `{}`.

### 2.5 Disclosure: accidental write

While probing, I ran `scripts/bridge_oracle_to_calibration.py --help` expecting argparse to print help and exit. The script does **not** implement `--help` and instead treated unknown args as a normal run, **writing** `data/oracle_error_rates.json` (2 bytes, `{}`) and `data/oracle_error_rates.heartbeat.json`. I then cleaned both files and the `data/` directory:

```
rm -fv data/oracle_error_rates.json data/oracle_error_rates.heartbeat.json
rmdir data
```

Post-cleanup `ls data/` returns `No such file or directory` — repo is back to pre-run state for this path. Operator action required: none. Antibody: see LEARNINGS.md Cat-K entry "argparse-absent CLI".

### 2.6 Can it be regenerated today?

| Layer | Status | Action needed |
|---|---|---|
| Snapshots | OK — 268 files under `raw/oracle_shadow_snapshots/`, cron `0 10 * * *` listener firing daily (last run 2026-05-16 10:20Z, 28 captured / 20 failed) | none |
| Settlements input | **BROKEN** — `settlements` table empty | repoint bridge SQL at `settlements_v2_archived_2026_05_11` OR restart harvester settlement-write path OR backfill from archive |
| Bridge schedule | **MISSING** — no cron/launchd entry | add cron `5 10 * * *` (5 min after listener) per Run #9 §6 |
| Output reader | OK — `src/strategy/oracle_penalty.py` would consume it | none |

Fastest path to a real artifact today: temporarily set `_load_settlements` to read from the archive table, run the bridge once, verify a non-empty payload, then ship the schedule + the harvester restart together.

---

## 3. Phase B — F32-class sibling inventory

### 3.1 Scheduler universe (full)

**crontab**:
- `*/30 * * * *` `heartbeat_dispatcher.py`
- `0 10 * * *` `oracle_snapshot_listener.py`

**launchd** (loaded; PID = running, `-` = idle waiting next interval):

| Label | PID | Last exit |
|---|---|---|
| `com.zeus.venue-heartbeat` | 78899 | 0 |
| `com.zeus.calibration-transfer-eval` | `-` | 0 |
| `com.zeus.live-trading` | 4242 | -15 (SIGTERM) |
| `com.zeus.forecast-live` | 10397 | 1 |
| `com.zeus.riskguard-live` | 14356 | -15 |
| `com.zeus.data-ingest` | 34316 | 0 |
| `com.zeus.heartbeat-sensor` | `-` | 0 |

**OpenClaw `cron/jobs.json` zeus jobs** (7 total): heartbeat-001, daily-audit-001, antibody-scan-001, source-contract-protocol weekly Mondays, source-auto-conversion-canary annual, plus 2 generic-id jobs.

**Total scripts**: 186 under `scripts/`. Above schedulers reference ≈ 12 of them.

### 3.2 New F32-class candidates (designed but unwired OR runs but silent-degraded)

| ID | Script / writer | Self-claim | Schedule | Last successful useful run | Tier |
|---|---|---|---|---|---|
| **F35** | `scripts/bridge_oracle_to_calibration.py` | "ONLY writer to oracle_error_rates.json" | **NONE** | never produced non-empty artifact (no on-disk evidence anywhere) | TIER-1 SILENT-OFF (Cat-K) |
| **F36** | Settlement writers (`src/execution/harvester.py:1300`, `src/ingest/harvester_truth_writer.py:551`, `src/state/db.py:4397`) | live truth-recording chain | implicit via harvester daemon + ingest daemon (both loaded) | last VERIFIED row target_date `2026-05-07` — 10 days stale | TIER-2 SILENT-DEGRADED (Cat-J pre-flag — could be expected post-archive, see §5 question Q1) |
| **F37** | `com.zeus.calibration-transfer-eval` (weekly Sunday 04:00) | Phase-B re-eval of `validated_calibration_transfers` | loaded + firing | runs but reports `target domains in calibration_pairs_v2: []` — table is empty, evaluator iterates 0 active Platt models | TIER-2 SILENT-DEGRADED (Cat-K) |
| **F38** | `calibration_pairs_v2` table population path | calibration evaluator input | unknown writer; investigate further | **0 rows** — entire downstream OOS-staleness antibody is no-op | TIER-1 SILENT-OFF (Cat-J pre-flag — INVESTIGATE-FURTHER) |
| **F39** | `com.zeus.calibration-transfer-eval` plist header text | "PROPOSED — DO NOT LOAD AUTOMATICALLY" with explicit "DO NOT load at initial launch — zero cross-domain rows exist until ECMWF calibration_pairs_v2 accumulate (~2-4 weeks post-launch)" | plist IS loaded; ran successfully 2026-05-17 04:00 | comment is stale OR loaded prematurely | TIER-3 OBSOLETE-DOC (Cat-N — doc lies vs reality) |

### 3.3 Confirmed-OK (not F32-class)

- `oracle_snapshot_listener.py` — scheduled + recently ran (28 captured 5/16 10:20Z).
- `heartbeat_dispatcher.py` — scheduled every 30 min, `state/daemon-heartbeat.json` mtime = now.
- `com.zeus.forecast-live` / `data-ingest` / `riskguard-live` / `live-trading` — running PIDs.
- `state/scheduler_jobs_health.json` — recently written, showing OK status for harvester/forecast/source-health jobs.

### 3.4 Stale state files (mtime audit)

| File | Age | Notes |
|---|---|---|
| `state/cutover_guard.json` | 372 h (15.5 d) | likely expected — set-and-forget guard |
| `state/source_contract_quarantine.json` | 329 h | weekly Mon job — recently due |
| `state/assumptions.json` | 218 h | depends on assumptions-refresh job — none found |
| `state/forecasts_schema_ready.json` | 76 h | schema-readiness marker — should refresh with schema change |
| `state/entry_forecast_promotion_evidence.json` | 40 h | mid-stale |
| Others | <2 h | OK |

None of the stale state files are immediately F32-class without more context; flag for next run as "stale-feed sweep".

---

## 4. Phase C — Tier classification of new findings

| Finding | Tier | Cat |
|---|---|---|
| F35 | TIER-1 SILENT-OFF | **Cat-K** (designed-but-never-scheduled) |
| F36 | TIER-2 SILENT-DEGRADED | **Cat-J pre-flag** (could be intended post-archive pause) |
| F37 | TIER-2 SILENT-DEGRADED | Cat-K (runs but input empty) |
| F38 | TIER-1 SILENT-OFF | Cat-J pre-flag — INVESTIGATE-FURTHER |
| F39 | TIER-3 OBSOLETE-DOC | **Cat-N** (doc says "do not load"; it IS loaded; runs fine — doc is stale) |

---

## 5. TOP-3 most consequential silent gaps (operator priority)

1. **F36 — settlement live tables empty for 10 days** → bridge artifact cannot regenerate, calibration-transfer cannot evolve, oracle-penalty stays at 0.5×/0.0× fallback. **One-line fix**: confirm with operator whether the `_archived_2026_05_11` migration was intended to be permanent or rolling; if rolling, the harvester settlement-write path needs to be restarted / debugged for why no new VERIFIED rows have landed since 5/7.

2. **F35 — bridge unscheduled** → even after F36 is fixed, no artifact ever lands without explicit schedule. **One-line fix**: add cron entry `5 10 * * * cd /Users/leofitz/.openclaw/workspace-venus/zeus && .venv/bin/python scripts/bridge_oracle_to_calibration.py >> /Users/leofitz/.openclaw/logs/oracle-bridge.log 2>&1` (5 min lag after listener so snapshots are flushed).

3. **F38 — `calibration_pairs_v2` empty** → OOS-staleness antibody is no-op; the entire weekly `calibration-transfer-eval` daemon is performance theatre. **One-line fix**: identify the script/path that populates `calibration_pairs_v2` (`rebuild_calibration_pairs_v2.py` and `_rebuild_calibration_pairs_v2_parallel.py` are candidates — check their last run + schedule).

---

## 6. Karachi 2026-05-17 impact assessment

Karachi GO position is `c30f28a5-d4e` `day0_window`, ship-imminent (per Run #9 §1 live context).

**Direct impact of these silent gaps on the 5/17 Karachi position**: **NONE within this run's evidence**. Reasoning:

- F35/F36 cause oracle multiplier to be 0.5 (MISSING-fallback) for high/0.0 (METRIC_UNSUPPORTED) for low across **all** cities including Karachi. This was already the runtime state when Karachi was sized — sizing decisions already account for the conservative 0.5× cut. The position is GO **despite** the penalty, not because we'll suddenly remove it.
- Restoring the data later would generally make Karachi sizing MORE aggressive (mult → 1.0 if Karachi historically low-error), not less. Restoring before settlement is risk-neutral or favorable.
- F37/F38 affect cross-domain Platt-transfer calibration, which is upstream of forecast probabilities, not sizing. No same-day Karachi probability would shift without a rebuild + reload, neither of which is happening before settlement.

**Conclusion**: Karachi 5/17 ship is unblocked by these findings. Treat F35-F38 as post-settlement work for the next operating window.

---

## 7. New questions opened for operator

- **Q1 (F36)**: Was the `_archived_2026_05_11` settlement migration intended to fully drain the live `settlements`/`settlements_v2` tables, or should some "rolling tail" have remained? If full drain was intended, what was the planned cutover for the new settlement-write path?
- **Q2 (F36/F38)**: Has the harvester ever produced a `settlements`/`settlements_v2` VERIFIED row since 2026-05-07? If not, what changed on/around 5/7?
- **Q3 (F39)**: Was loading `com.zeus.calibration-transfer-eval` intentional, or did it auto-load against the plist's "DO NOT LOAD" comment? If intentional, update the comment to remove the trap.

---

## 8. Items INVESTIGATE-FURTHER (next run)

- F38 root cause: who/what populates `calibration_pairs_v2` and why has it not run?
- Stale state files (`assumptions.json` 218 h, `cutover_guard.json` 372 h) — confirm whether stale-by-design.
- Other "writers" pattern: search `# ONLY consumer of X` / `# single reader` patterns to find the inverse — files that exist but nobody reads — these may be cheap deletes.
