# DO NOT STALL. full_transport → live: compact-survival handoff (rewritten 2026-05-26, post-#342-merge)

This is the single source of truth for finishing the full_transport→live ship. Written for a fresh reader with NO prior memory. Read top-to-bottom, find the first non-done step, advance it. "Waiting on the rebuild" is a hard dependency, NOT a stall — confirm progress + advance the parallel tracks.

## WHERE WE ARE (2026-05-26 ~12:30 CDT)
**PART A (code) = DONE.** PR **#342 MERGED to main** 17:33 UTC. main now carries the entire ship: Fix A (metric-aware 0Z/12Z bias window, ens_bias_repo), Fix B (MIN_PAIRED_N=5 transport gate, ens_error_model), live wiring (monitor_refresh `_load_ft_error_model`, flag `full_transport_live_enabled` default OFF), identity-calibrator route (platt.py IdentityCalibrator + manager get_calibrator), preflight mx2t3/mn2t3 rename fix, schema_version 37, ship-readiness gate. Opus-reviewed ACCEPT, all CI green, 24 bot threads resolved.

**PART B (data) = IN PROGRESS.** Rebuilding all calibration pairs on prod through the corrected code.
- **Rebuild PID 28447** — detached (ppid=1, survives), `rebuild_calibration_pairs_v2.py --no-dry-run --force --db state/zeus-forecasts.db --error-model full_transport_v1 --temperature-metric all --n-mc 10000 --workers 12`. Log: `logs/ftrebuild_full2_2026-05-26.log`. Does HIGH metric fully, THEN LOW. At 12:22: HIGH ~23/52 cities, 0 collisions, disk 116G. **ETA HIGH ~14:30, full (incl LOW) ~16:00 CDT.**
- **Smoke already validated correctness**: HK HIGH p_raw mass centered 25-30°C (real range), tracks settlements tightly (28°C p=0.143/out=0.152) → Fix A+B working, the −2.1/+6.3 contamination is gone.

## MONITORS ARMED (auto-trigger next steps; do not duplicate)
- **b67206g5l** (persistent) — fires when HIGH build done (first LOW pair committed). → start HIGH fit.
- **b205l04cr** (persistent) — fires when 28447 fully exits (LOW done). → start LOW fit.
- a1d95f (agentId **a1d95fdd53c4676f5**) — STOOD DOWN; resume via SendMessage for the fit step. It owns the rebuild process knowledge.

## SUBSEQUENT TASKS — HIGH-FIRST PARALLEL (operator-directed)
LOW markets are sparse + filter-gated (won't place orders), so ship HIGH to live-shadow ASAP; finish LOW in background. refit_platt_v2.py is metric-scoped → HIGH fit runs WHILE 28447 builds LOW.

**HIGH track (critical path):**
1. **[trigger: b67206g5l] Fit HIGH** — resume a1d95f: `refit_platt_v2.py` metric=high, `--error-model full_transport_v1`, ECE-gated (identity calibrator where ECE low, learned Platt where it improves). Runs parallel to 28447's LOW build (reads stable HIGH pairs; SQLite WAL allows concurrent read+write; no conflict — different metric rows). Task #70.
2. **Bin-check HIGH** — matched-date proper-score / bin-sanity on HIGH cohorts: p_raw bins must fall in expected intervals, HK HIGH passes pathology rule. (Smoke already showed HK calibrated; confirm across cohorts.) Task #74.
3. **Promote HIGH** — `promote_platt_models_v2.py`: HIGH fitted models → prod **state/zeus-world.db**, additive (keyed error_model_family), explicit pin for all HIGH cohorts (NO carve-out — HK fixed). Cross-DB writes MUST use INV-37 (ATTACH+SAVEPOINT, never independent connections). Task #73.
4. **[needs: world.db schema migrate to 37] Restart daemon SHADOW** — `launchctl kickstart -k gui/$(id -u)/com.zeus.live-trading` on the merged main code, flag `full_transport_live_enabled` ON, shadow mode. The daemon CHECKS world schema and does NOT self-migrate forecasts; before restart run init_schema/migrate to add `calibration_method` col + bump world.db to 37 (else daemon SystemExit on schema mismatch). The restart also clears the stale deployment_freshness auto-pause. Task #73/#90.
5. **Verify HIGH shadow** — daemon must produce fresh `probability_trace_fact` rows; HIGH p_raw bins align with online weather forecast for the target date, **bin bias ≤ 1 unit**. No shadow traces = DEFECT → root-cause (don't stall). Task #74.
6. **[OPERATOR GATE — surface here] Unshadow** — flip to live trading bounded/tiny. Then PROVE a real chain order fills. No fill = DEFECT → root-cause. Tasks #90/#91.

**LOW track (background, parallel):**
7. **[trigger: b205l04cr] Fit + promote LOW** — after LOW build done, `refit_platt_v2.py` metric=low → promote LOW models → world.db + pin LOW. LOW joins live after HIGH shadow validated. Until then LOW sparse + filter-gated → no orders (correct). Task #92.

## LESSONS FROM PART A (errors hit + how to avoid — do NOT repeat)
1. **Rebuild must WIPE ALL pairs first.** The rebuild writes BOTH `error_model_family='full_transport_v1'` AND `'none'` pairs (TIGGE snapshots without ft params → none). The per-city `--force` delete is scoped to ft_v1 only, so leftover `none` pairs (e.g. from a smoke run) cause `UNIQUE` collisions mid-run (the v2 unique key has NO error_model_family). Run #1 (PID 9138) died at 16/52 cities this way. FIX: `DELETE FROM calibration_pairs_v2` (full wipe) before any full rebuild.
2. **Always --workers 12, never 2.** 14 cores, daemon paused. workers=2 → 40h; workers=12 → ~5h. Per-(city,metric) bucket commits keep SQLite WAL contention low even at 12; the bottleneck is serial pre-compute/write between MC bursts (~40-75% avg CPU is normal/bursty). Compute parallelism (ProcessPoolExecutor, workers never touch SQLite — main writes) scales near-linearly.
3. **Launch detached (setsid/ppid=1).** A rebuild launched by a subagent dies when that agent's session is reaped. Launch fully detached from the coordinator shell.
4. **ONE process owner — never double-launch.** Twice the coordinator AND a1d95f relaunched simultaneously → two `--force` writers racing on the same DB. Assign one owner; verify `pgrep` shows a single tree before walking away.
5. **Verify the feature-matrix after any merge/rebase.** A "canonical schema" commit (f44cf21261) silently regressed ens_error_model.py + ens_bias_repo.py on ft-ship-64, dropping Fix B + _CANONICAL_EXTENSION_COLUMNS; an ort merge then inherited the regression. Always `git grep -c` the key symbols (MIN_PAIRED_N, _CANONICAL_EXTENSION_COLUMNS, IdentityCalibrator, _load_ft_error_model, Fix-A window) on the integration HEAD. The complete branch was feat/ft-64-live-wiring.
6. **Schema bump 36→37 touches 7 CHECK allowlists.** db.py:1396/2661/2697 + no_trade_events_schema.py:73/93/232 + phase6_evidence_schema.py:44. Miss one → IntegrityError on canonical inserts. test_boot_migration_v28_antibody catches it.
7. **Disk: kill replay_equivalence zombies on sight.** The replay harness self-replicates and filled the state volume to 100% twice (live-daemon-crash risk). `pkill -9 -f replay_equivalence_full_transport`; the verdict is moot. Don't let scratch (/private/tmp/ens_refit) accumulate.
8. **Do NOT run the post-merge worktree-cleanup suggestions** while the rebuild runs — `ens-bias-hierarchical` hosts the live rebuild process + this session's cwd.

## HARD RULES
- Prod DBs only (no scratch eval DBs). **main** is now the integration base (post-#342-merge).
- One process owner + detached launch. Wipe-all before any rebuild.
- Post-restart: shadow bin bias ≤ 1 unit BEFORE unshadow. no-trade after unshadow = defect.
- Surface to operator only at the unshadow / irreversible live-trade gate, or a genuine blocker.

## KEY PATHS
- Prod (ABSOLUTE): `/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db` (models/traces/trades) + `zeus-forecasts.db` (pairs/snapshots, rebuilding). Daemon: src.main PID 98627, riskguard 99761 (PAUSED).
- Backup/restore: `state/backups/zeus-forecasts_pre_ftrebuild_2026-05-25.db` (49GB, the pre-clear 91M none pairs, integrity_check=ok — restore path if rebuild fails). `ens_refit_full_2026-05-25.db` (eval asset). `ens_error_models_2026-05-25.db` (71 posteriors).
- Rebuild log: `logs/ftrebuild_full2_2026-05-26.log`. PID tracker: `/tmp/ftrebuild_pid.txt`.
- Worktree (rebuild + session): `.claude/worktrees/ens-bias-hierarchical` (branch fix/onboarding... but the merged ship code is on main; the rebuild runs Fix A+B from this tree which has them).

## NEXT ACTION ON WAKE
Check 28447: alive? HIGH cities count? LOW started? — `pgrep -fl rebuild_calibration_pairs`; `sqlite3 forecasts.db "SELECT temperature_metric,COUNT(DISTINCT city),COUNT(*) FROM calibration_pairs_v2 WHERE error_model_family='full_transport_v1' GROUP BY 1"`.
- If HIGH done (LOW pairs > 0 or b67206g5l fired) → resume a1d95f for HIGH fit (step 1) → bin-check → promote → shadow restart (after world.db migrate). 
- If full exited cleanly → also do LOW fit (step 7).
- If 28447 DEAD with HIGH incomplete → read the log tail for the cause, wipe-all, relaunch detached at --workers 12.
Advance the lowest non-done step. DO NOT STALL.
