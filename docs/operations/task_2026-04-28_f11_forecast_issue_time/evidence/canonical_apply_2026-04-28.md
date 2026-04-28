# F11 Canonical Apply Evidence (2026-04-28)

Created: 2026-04-28
Last reused/audited: 2026-04-28
Authority basis: operator authorization 2026-04-28 ("无关runtime的backfill我已经授权了，继续推进")
Status: post-apply audit trail.

---

## 1. Apply window

| Phase | Action | Result |
|---|---|---|
| 0 | Pre-flight checklist | All 6 checks PASS (HEAD ok, LIVE PAUSED, fresh backup taken, schema column NOT yet present, row count 23,466, F11 antibodies 75/75 green) |
| 1 | F11.2 schema migration | ALTER TABLE forecasts ADD COLUMN availability_provenance TEXT (with CHECK constraint) — applied on canonical; row count 23,466 → 23,466 (no row mutation) |
| 2 | F11.4 backfill | UPDATE 23,466 forecasts rows with derived (forecast_issue_time, availability_provenance) in single transaction; 0 NULL rows remaining post-apply |
| 3 | Smoke test | 39 F11 antibody tests pass against post-apply canonical |
| 4 | Resume cron | DEFERRED to operator (LIVE remains PAUSED) |
| 5 | Backup retirement | DEFERRED to 7-day soak per runbook |

Apply duration: ~30 seconds for backfill (single executemany over 23,466 rows). LIVE PAUSED throughout.

---

## 2. Verbatim apply outputs

### Phase 1 — F11.2 schema migration

```
[apply] Target DB: /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
[apply] forecasts row count before: 23,466
[apply] forecasts row count after: 23,466
[apply] Column added; all 23,466 rows have NULL provenance (expected; backfill via F11.4).

[verify] Target DB: /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
[verify] availability_provenance column present.
[verify] Distribution: {None: 23466}
[verify] CHECK constraint enforces enum: ['derived_dissemination', 'fetch_time', 'reconstructed', 'recorded']
[verify] OK
```

### Phase 2 — F11.4 backfill

```
[apply] Target DB: /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
[apply] Rows requiring backfill: 23,466
[apply] Writing 23,466 updates in a single transaction...
[apply] Remaining NULL rows: 0

[verify] Target DB: /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
[verify] Provenance distribution: {'derived_dissemination': 9996, 'reconstructed': 13470}
[verify] NULL rows remaining: 0
[verify] Per-source × provenance distribution:
  ecmwf_previous_runs          derived_dissemination     4,998
  gfs_previous_runs            derived_dissemination     4,998
  icon_previous_runs           reconstructed             4,284
  openmeteo_previous_runs      reconstructed             4,998
  ukmo_previous_runs           reconstructed             4,188
[verify] OK — all rows backfilled.
```

Distribution matches the F11.4 dry-run prediction exactly (9,996 DERIVED + 13,470 RECONSTRUCTED + 0 unmapped).

---

## 3. SHA chain

| File | SHA-256 | Size | Role |
|---|---|---|---|
| `state/zeus-world.db.pre-f11-apply-20260428-0143` | `c21f375cc5151b5c636a93f417e2b6dc73f268111877705628bf113c88307bf2` | 1,819,426,816 B | Fresh pre-apply backup (canonical state immediately before F11.2 + F11.4) |
| `state/zeus-world.db` (post-apply) | `be66e31d4c6f37247487516aafa0a3ea18efe64933ae3316da56c9738e311d4e` | (similar) | Canonical post-F11 state |
| `state/zeus-world.db.pre-f11-2026-04-28` | `150ed910108c09e5dcc161d6f500c2c0f975742d56b24d2ff1214e66566315b1` | (similar) | Earlier backup; superseded — was used for F11.2/F11.4 dry-run + apply testing |

The `pre-f11-apply-20260428-0143` backup is the canonical rollback target. SHA chain provides audit trail.

---

## 4. Post-apply F11 antibody verification

```
tests/test_forecasts_schema_alignment.py ...........             [ 13%]
tests/test_forecasts_writer_provenance_required.py ........      [ 33%]
tests/test_dissemination_schedules.py ......................     [ 89%]
tests/test_replay_skill_eligibility_filter.py ...                [100%]

============================== 39 passed in 1.36s ==============================
```

All 39 invariant antibodies green against the post-apply canonical schema. The full 75-test backtest+F11 suite was green pre-apply and the schema alignment subset is what's most affected by canonical state changes.

---

## 5. Per-source apply outcome summary

| Source | Rows | Provenance tier | Notes |
|---|---|---|---|
| `ecmwf_previous_runs` | 4,998 | `derived_dissemination` | ECMWF ENS via Open-Meteo previous_runs; lag formula = `base + 6h40m + lead_day×4min` (confluence wiki verified) |
| `gfs_previous_runs` | 4,998 | `derived_dissemination` | NOAA GFS via Open-Meteo previous_runs; lag = `base + 4h14m` (NCEP production status MOS-completion verified) |
| `icon_previous_runs` | 4,284 | `reconstructed` | DWD ICON; primary-source lag not yet verified — conservative `+12h` placeholder; downstream SKILL gate excludes |
| `ukmo_previous_runs` | 4,188 | `reconstructed` | UK Met Office; same as ICON |
| `openmeteo_previous_runs` | 4,998 | `reconstructed` | Open-Meteo `best_match` redistributor; upstream model varies; SKILL gate excludes |
| **TOTAL** | **23,466** | — | 9,996 SKILL-eligible + 13,470 DIAGNOSTIC-only |

---

## 6. What's now live on canonical

After F11.2 + F11.4:
- `forecasts` table has `availability_provenance` column with CHECK constraint enforcing the 4-tier enum
- All 23,466 historical rows carry typed provenance and non-NULL `forecast_issue_time`
- Live writer (`forecasts_append.py:_insert_rows`) will fail-fast on any new row with NULL provenance (F11.3 antibody)
- Replay queries (`src/engine/replay.py:_forecast_rows_for`) automatically exclude RECONSTRUCTED rows from SKILL backtest output (F11.6 SQL filter wired)
- Training-eligibility helpers (`src/backtest/training_eligibility.py`) ready for downstream consumer migration (F11.5-migrate slice — `etl_historical_forecasts.py` + `etl_forecast_skill_from_forecasts.py`)

---

## 7. Rollback path (still available)

If any post-apply behavior deviates unexpectedly:

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
# Stop any live writes first (LIVE PAUSED, but verify daemon)
cp -p state/zeus-world.db state/zeus-world.db.post-rollback-2026-04-28
cp -p state/zeus-world.db.pre-f11-apply-20260428-0143 state/zeus-world.db
# Restart cron / daemon as desired
```

Single `cp` restores pre-F11 state. Backup file size 1.82 GB; restore takes ~5 seconds.

---

## 8. Remaining F11 follow-ups

- **F11.5-migrate**: wire `SKILL_ELIGIBLE_SQL` into `etl_historical_forecasts.py:71` and `etl_forecast_skill_from_forecasts.py:120` so training-only ETLs filter at the SQL layer (per `forecasts_consumer_audit_2026-04-28.md`).
- **Backup retirement**: 7-day soak before retiring `pre-f11-apply-20260428-0143` to long-term archive (per runbook §6).
- **Live resume**: operator decision; LIVE_LOCK still says PAUSED. After resume, next `k2_forecasts_daily` cron tick will write F11-typed rows via `forecasts_append.py` (verified writer change committed at HEAD `8dbe7c2`).
- **Q5 WU obs triage**: separate packet, separate operator decisions Q5-A/B/C.

---

## 9. Memory rules applied

- L20 grep-gate: every command line in this evidence verified within the writing window 2026-04-28.
- L22 commit boundary: this evidence file is the post-apply audit trail; it does NOT autocommit before writing — it documents what already happened.
- L24 git scope: this commit stages only `task_2026-04-28_f11_forecast_issue_time/evidence/canonical_apply_2026-04-28.md` plus the runbook update.
- L28 critic-baseline: critic + code-reviewer adversarial passes already ran pre-apply; their BLOCKERs were addressed in commit `7b46003` and `57fdc81`.
- L30 SAVEPOINT audit: F11.4 backfill uses `executemany` inside a single SQLite implicit transaction; no nested SAVEPOINT collision.
