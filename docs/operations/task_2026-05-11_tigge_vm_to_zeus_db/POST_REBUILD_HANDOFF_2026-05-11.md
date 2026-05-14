# Task 6 v3 — Post-Rebuild Handoff
**Owner**: any operator continuing this work
**Status**: rebuild in-flight; all preparation complete
**Date**: 2026-05-11
**Source**: this session (Copilot/Sonnet)

---

## What's running

| Item | Value |
|---|---|
| Background rebuild PID | `cat /tmp/zeus_task6_v3_pid.txt` |
| Background rebuild log | `cat /tmp/zeus_task6_v3_log.txt` |
| STAGE_DB | `state/tigge_stage_20260511T175548Z.db` |
| Rebuild script | `tmp/task6_v3_full_rebuild_2026_05_11.sh` |
| Refit script (queued) | `tmp/task6_v3_platt_refit_2026_05_11.sh` |
| Config | workers=8, n_mc=10000, seed_base=42 |

**Progress check** (one-liner):
```bash
PID=$(cat /tmp/zeus_task6_v3_pid.txt); LOG=$(cat /tmp/zeus_task6_v3_log.txt); \
  ps -p $PID -o etime= 2>/dev/null; \
  sqlite3 state/tigge_stage_20260511T175548Z.db \
    "SELECT data_version, COUNT(*), COUNT(DISTINCT city) FROM calibration_pairs_v2 GROUP BY data_version;"; \
  tail -5 "$LOG"
```

**Expected progression**: HIGH (~7-8h, 52 cities, ~70M pairs) → LOW (~1.5h, ~14M pairs) → LOW_contract_window (~0.8h, ~7M pairs). Total ETA ~10h. Look for `ALL REBUILDS COMPLETE` in log.

## Branches state

- `fix/harvester-paginator-bound-2026-05-11` @ `dfbd36c5` — parallel rebuild patch
- `fix/calibration-tigge-opendata-bridge-2026-05-11` @ `cd93c1bd` — TIGGE→OpenData runtime fallback (HIGH-only)

Both verified independently:
- Bridge probe: 5/5 HIGH cities resolve via TIGGE rescue, 5/5 LOW correctly blocked per `_low_purity_doctrine_2026_05_07`
- Parallel rebuild: critic PASS w/ notes, HIGH confidence; smoke 30144=30144

## When `ALL REBUILDS COMPLETE` appears in log

### Step 1 — Refit Platt models (~1-2h)
```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
nohup bash tmp/task6_v3_platt_refit_2026_05_11.sh \
  > tmp/task6_v3_platt_refit_wrapper.log 2>&1 &
echo $! > /tmp/zeus_task6_v3_refit_pid.txt
```
Refit script has precondition gate — refuses to run unless rebuild log shows `ALL REBUILDS COMPLETE`. Logs to `tmp/task6_v3_platt_refit_<TS>.log`.

### Step 2 — Promote STAGE_DB → production (Phase A)
**Not yet scripted.** Manual operator action required: copy or merge `calibration_pairs_v2` and `platt_models_v2` rows from STAGE_DB to `state/zeus-world.db`. Verify integrity before/after via `PRAGMA integrity_check;`.

### Step 3 — Re-run training-readiness verifier
```bash
python3 scripts/verify_truth_surfaces.py --mode training-readiness
```

### Step 4 — Merge bridge fix into harvester branch
```bash
git checkout fix/harvester-paginator-bound-2026-05-11
git merge --no-ff fix/calibration-tigge-opendata-bridge-2026-05-11
# resolve any conflicts (none expected; disjoint files)
```

## Critical path: rebuild-complete → live-active

| # | Action | Wall-time | Owner |
|---|---|---|---|
| A | Wait for `ALL REBUILDS COMPLETE` in rebuild log | (in progress) | (auto) |
| 0 | Run refit script (step 1 above) | ~1-2h | shell-async |
| 1 | Promote STAGE_DB → production calibration_pairs_v2 + platt_models_v2 | minutes | operator |
| 2a | **CRITICAL — diagnose ECMWF open-data ingest leg.** Last `producer_readiness` row written 2026-05-04. Daemon `com.zeus.data-ingest` (PID `19635`) running but open-data branch silent 6+ days. TIGGE leg unaffected. | unknown | operator |
| 2b | Trigger one fresh ECMWF open-data ingest cycle. This writes new `producer_readiness` rows (`expires_at` future) — clears both `PRODUCER_READINESS_EXPIRED` and `SOURCE_RUN_HORIZON_OUT_OF_RANGE` (HORIZON requires longer-step run; verify `--max-step` covers full required horizon) | one ECMWF cycle | operator |
| 3 | Run `python scripts/live_readiness_check.py` from project root. Expect exit 0. Capture printed `g1_evidence_id` filename. | minutes | operator |
| 4 | Use the new operator CLI (commit `4d661014`):<br>`python -m src.control.cli.promote_entry_forecast propose --operator-approval-id OPS-2026-MM-DD-NN --g1-evidence-id <path-from-step-3> [--canary-success-evidence-id ID] --commit`<br>Without `--commit` it dry-runs and prints proposed JSON for review. With `--commit` atomically writes `state/entry_forecast_promotion_evidence.json`. | minutes | operator |
| 5 | Flip rollout mode in `config/settings.json` (canonical source — **NOT an env var**; `ZEUS_ENTRY_FORECAST_ROLLOUT_MODE` is not wired today). Use:<br>`python -m src.control.cli.promote_entry_forecast flip-mode canary` (dry-run; prints what to change + daemon kickstart command). Edit `config/settings.json` `entry_forecast.rollout_mode="canary"`, restart `launchctl kickstart -k gui/$(id -u)/com.zeus.live-trading`. After canary success: re-run `propose --commit` with `--canary-success-evidence-id`, then `flip-mode live` (CLI refuses without canary evidence). | seconds + daemon restart | operator |
| 6 | Live-trading daemon's next evaluator tick (~30s cycle): reads fresh evidence (atomic-write inode rotation invalidates `lru_cache` per `entry_forecast_promotion_evidence_io.py:146-256`). `evaluate_entry_forecast_rollout_gate` returns `LIVE_ELIGIBLE`. Orders submit. | seconds | (auto) |

## TIME-WINDOW RISK

`producer_readiness.expires_at` TTL is observed ≈ 24h. **Steps 3-5 above must complete inside that 24h window after step 2b lands**, or `PRODUCER_READINESS_EXPIRED` recurs and the rollout-gate falls back to BLOCKED. Mitigation: do steps 3-5 back-to-back in one session.

## Already-armed state (Phase D)

- `state/cutover_guard.json` `state="LIVE_ENABLED"` since 2026-05-02
- `arm_live_mode.sh` was last run 2026-05-02T00:28:18Z
- All 3 daemons loaded: `com.zeus.live-trading` (PID 33214), `com.zeus.data-ingest` (PID 19635), `com.zeus.riskguard-live` (PID 90763)
- **No re-arm needed.** No restart needed for promotion-evidence cache (inode rotation).

## Known gotchas

1. `arm_live_mode.sh` bypasses `transition_to_live_enabled()` → no G1 evidence check enforced by the script. Current armed state was set without that check; not a bug today but worth knowing if you ever re-arm cleanly.
2. `Tokyo`/`Singapore` LOW Platt rows are `authority='UNVERIFIED'` (pre-existing data quality), so bridge probe shows them as `False` for LOW+tigge. Not a bridge bug; address separately.
3. The bridge fix only affects HIGH+ecmwf_opendata. LOW path stays strict per `_low_purity_doctrine_2026_05_07`. If LOW live serving is ever needed, the doctrine must be re-litigated.
4. STAGE_DB has 36GB working file. Disk space: confirm before promoting.
5. Concurrent agents in this workspace observed (DT team). Use `git add <file>` per memory hygiene; never `git commit -am`. Run `git diff --stat <file>` before every commit.

## Rollback paths

- **Rebuild died/fails**: STAGE_DB is isolated; production untouched. Re-run `tmp/task6_v3_full_rebuild_2026_05_11.sh` after diagnosing.
- **Bridge fix regression**: `git revert cd93c1bd` on bridge branch; live system continues to load uncalibrated probabilities (the pre-fix state) — no data corruption.
- **Live promotion gone wrong**: rewrite `state/cutover_guard.json` `state="NORMAL"`, restore `state/auto_pause_failclosed.tombstone`, `launchctl unload` the live-trading job, write a NEW promotion-evidence with `live_promotion_approved=False` (or set rollout_mode env back to `shadow`).

## Files generated this session

- [scripts/_rebuild_calibration_pairs_v2_parallel.py](scripts/_rebuild_calibration_pairs_v2_parallel.py) — committed in `dfbd36c5`
- [scripts/rebuild_calibration_pairs_v2.py](scripts/rebuild_calibration_pairs_v2.py) — modified, committed in `dfbd36c5`
- [src/calibration/manager.py](src/calibration/manager.py) — modified, committed in `cd93c1bd`
- [tests/test_calibration_manager.py](tests/test_calibration_manager.py) — added, committed in `cd93c1bd`
- [src/control/cli/promote_entry_forecast.py](src/control/cli/promote_entry_forecast.py) — operator CLI, committed in `4d661014`
- [tests/test_promote_entry_forecast_cli.py](tests/test_promote_entry_forecast_cli.py) — committed in `4d661014` (8/8 pass)
- [tmp/task6_v3_full_rebuild_2026_05_11.sh](tmp/task6_v3_full_rebuild_2026_05_11.sh) — rebuild orchestration script (gitignored)
- [tmp/task6_v3_platt_refit_2026_05_11.sh](tmp/task6_v3_platt_refit_2026_05_11.sh) — refit orchestration script (gitignored)
- This handoff doc

## Open / NOT done

- ECMWF open-data ingest leg root-cause (silent 6+ days)
- STAGE_DB → production calibration promotion script
- ~~Operator CLI wrapper for `write_promotion_evidence`~~ ✅ done in `4d661014`
- `ZEUS_ENTRY_FORECAST_ROLLOUT_MODE` env var is NOT wired today — `entry_forecast_config()` reads `config/settings.json` only. CLI prints the env var line as a hint AND a `# also update config/settings.json` reminder. If env override is wanted, requires change in `src/config.py:entry_forecast_config()`.
- GCP refund opener — drafted at `~/.openclaw/ops/google_cloud_billing_refund_appeal_2026-05-11.md` but not sent
