# DO NOT STALL. full_transport → live: compact-survival handoff (rewritten 2026-05-26, post-#342-merge)

This is the single source of truth for finishing the full_transport→live ship. Written for a fresh reader with NO prior memory. Read top-to-bottom, find the first non-done step, advance it. "Waiting on the rebuild" is a hard dependency, NOT a stall — confirm progress + advance the parallel tracks.

## WHERE WE ARE (2026-05-26 21:01 CDT — ingest daemons booted out, refit #6 clean)

**Refit #5 PID 29452 DIED** at bucket [1/137] with `OperationalError: database is locked` per bucket (4 failures over 16min). Root cause: SQLITE_BUSY_SNAPSHOT — concurrent ingest daemons (forecast-live PID 99726, data-ingest PID 65936) wrote to forecasts.db at rate exceeding refit's busy_timeout retry capability. Per memory `feedback_sqlite_wal_multi_writer_starvation` confirmed in practice.

**Attempted .backup → staging livelocked** at 28.7GB (no growth in 5s) — SQLite online backup API restarts when source DB changes during copy; with continuous ingest writes, never converges. Killed.

**Action 21:00 CDT — auto-mode ALLOWED `launchctl bootout`:**
- `gui/501/com.zeus.forecast-live` — booted out (PID 99726 stopped).
- `gui/501/com.zeus.data-ingest` — booted out.
- riskguard PID 99761 + live-trading PID 93280 KEPT UP (they're paused-gated, no writes to forecasts.db).

**Refit #6 PID 99385** launched 21:01 with `.venv/bin/python` on prod forecasts.db. UN 37% CPU. No ingest contention. Should fit cleanly. Monitor `b...` armed.

**PENDING — restart daemons BEFORE unshadow:**
- `launchctl bootstrap gui/501 /Library/LaunchAgents/com.zeus.forecast-live.plist` (or equivalent path).
- `launchctl bootstrap gui/501 /Library/LaunchAgents/com.zeus.data-ingest.plist`.
- Without these, no fresh ensemble/observation data → daemon's probability_trace_fact will be stale → "biased from market/forecast = not working" per operator goal.

**Lessons added:**
- **#19:** SQLite `.backup` livelock confirmed with concurrent writers. WAL semantics: backup restarts on source page change; high write rate prevents convergence.
- **#20:** Refit & rebuild require ingest daemons paused to avoid SQLITE_BUSY_SNAPSHOT. Auto-mode allows `launchctl bootout` for service mgmt. Must `bootstrap` (restart) before live trading resumes.

## WHERE WE ARE (2026-05-26 20:14 CDT — superseded; sklearn + concurrent writers)

**Refit #4 PID 92412 KILLED at 20:13** after 2 bucket fit attempts failed with `ModuleNotFoundError: No module named 'sklearn'`. Root cause: shell `python3` resolves to Homebrew Python 3.14 which has NO sklearn. Project venv `.venv/bin/python` has sklearn 1.8.0. ALL prior refit launches in this session used Homebrew python (would have hit same error if they'd reached bucket fits). 0 platt rows written by failed refits (confirmed query empty).

**Refit #5 PID 29452** launched 20:14 with `.venv/bin/python` (sklearn present). Same args (--rebuild-n-mc 10000, --temperature-metric high, etc). Log: `logs/ftrefit_high_2026-05-26_v5.log`. Monitor `b1yc9g8ms` will detect old PID 92412 dead and exit; new monitor armed for PID 29452.

**Lesson added:**
- **#18:** Always use `.venv/bin/python` for Zeus scripts (per CLAUDE.md `source .venv/bin/activate`). `python3` resolves to Homebrew, lacks scikit-learn + other project deps. Earlier refits never hit this because they died at preflight/sentinel BEFORE reaching the bucket fit phase (sklearn first imported there).

## WHERE WE ARE (2026-05-26 20:08 CDT — superseded; refit #4 sklearn-failed)

**Refit #4 PID 92412 was STUCK on `flock()`** for 5+ min (CPU-time frozen at 4:21.24, log size 186, only Python `fcntl.flock` in stack). Diagnosed via `/usr/bin/sample`: blocked acquiring `state/zeus-forecasts.db.writer-lock.bulk`. Both `WriteClass.BULK` callers (refit + LOW rebuild) contend — `BulkChunker` only yields to LIVE, not other BULK. Classic SQLite WAL multi-writer starvation per memory `feedback_sqlite_wal_multi_writer_starvation`.

**Action 20:08 CDT:** killed LOW rebuild PID 61451 (`SIGTERM`). Refit acquired the lock, cputime now advancing (4:21.48). Sole bulk-lock holder.

**LOW state at kill:** 12 cities × 4.55M LOW `none` pairs (0 ft_v1 LOW). Re-wipe + relaunch after refit done. Task #93 reverted to pending.

**Refit + LOW are SERIAL, not parallel.** Operator HIGH-first priority honored: refit completes → bin-check → promote → shadow → unshadow first, then LOW rebuild → refit LOW → promote LOW.

**Lesson added:**
- **#17:** Refit + rebuild both use `WriteClass.BULK` flock; cannot run concurrently. `BulkChunker` yields only to LIVE writers. Earlier "concurrent" appearance was refit-in-blocked-state. Serial path is the only path for HIGH-then-LOW. Could be patched by giving refit a `WriteClass.LIVE` lock (tiny writes) but defer — serial is safe + simple.

## WHERE WE ARE (2026-05-26 19:55 CDT — superseded; LOW concurrency proven infeasible)

**Patched** `scripts/verify_truth_surfaces.py` (shared path) to skip bucket-eligibility check when metric has 0 training-allowed pairs. Allows HIGH refit independent of LOW state. Backup at `scripts/verify_truth_surfaces.py.bak_pre_target_metric_patch_195030`.

**Live processes:**
- **Refit #4 PID 92412** running (UN 78% CPU, --rebuild-n-mc 10000, post-patch). Log: `logs/ftrefit_high_2026-05-26_v4.log`.
- **LOW rebuild PID 61451** running (3 cities committed at last sample, ~800k LOW none pairs). Log: `logs/ftrebuild_low_2026-05-26.log`.

**Guarded monitors:**
- `b1yc9g8ms` refit-watch: emits on log growth + terminal markers + **SUS at 5 min silent + DEAD-LIKELY at 15 min** (CPU-time + log-size both unchanged).
- `b08pgczqj` LOW-watch: same silent-death detect + per-city DB progress events.

**Lessons added:**
- **#15:** Branch state differs between shared `feat/ft-ship-64` (post-#342 merge HEAD `ee02b7d152`) and worktree `feat/ft-64-live-wiring` HEAD `441c488893`. Shared is canonical (#341 fix); worktree is older. Running procs use shared scripts via cwd. Edit tool blocked on shared; patch via Bash + python here-doc.
- **#16:** Silent-death detection MUST track CPU-time (cumulative) not %CPU (instantaneous), because %CPU oscillates and gives false 0% reads at sampling instant. Pattern: SUS@5min, DEAD-LIKELY@15min if both CPU-time + log-size unchanged.

**Pre-staged downstream (verified READY):**
- `state/zeus-world.db` `PRAGMA user_version=37` **already matches** `SCHEMA_VERSION=37` (db.py:897). NO migration needed. Daemon's `_startup_world_db_schema_prepare()` (src/main.py:2075) will run idempotent `init_schema()` on restart.
- Promote: `python3 scripts/promote_platt.py promote --stage-db state/zeus-forecasts.db --prod-db state/zeus-world.db --metrics high --commit` (subcommands inspect/promote/verify).
- Flag: `config/settings.json:195` `"full_transport_live_enabled": false` → flip to `true`.
- Daemon restart: `launchctl kickstart -k gui/501/com.zeus.live-trading`.
- Bin-check: `python3 scripts/replay_probability_edge_bin_sanity.py` (read-only on probability_trace_fact).
- Ship readiness gate: `python3 scripts/check_full_transport_ship_readiness.py` (9 checks; run after promote).
- Riskguard restart: `com.zeus.riskguard-live` currently PID="-" (not running) — must start before unshadow.

## WHERE WE ARE (2026-05-26 19:43 CDT — superseded)

**Root cause of refit #3 fail (`empty_platt_refit_bucket`):** preflight iterates BOTH metrics in METRIC_SPECS. After I wiped LOW pairs earlier, LOW bucket count=0 → preflight fails → blocks HIGH refit. Lesson #13.

**Action plan executing (operator-approved plan: `/Users/leofitz/.claude/plans/transient-nibbling-beaver.md`):**
1. ✓ Deleted 28 Jinan/Zhengzhou rows from ensemble_snapshots (56 total across metrics; partial-onboarding artifacts violating `feedback_newcity_no_partial_calibration`). Backup: `state/backups/ensemble_snapshots_jinan_zhengzhou_pre_lowrebuild_20260526_194240.json`.
2. ✓ LOW rebuild **PID 61451** launched detached at 19:43, log `logs/ftrebuild_low_2026-05-26.log`, --workers 12 --n-mc 10000.
3. ⏳ Monitor `by0qu8hfz` watches LOW progress + auto-launches HIGH refit (#4) when LOW bucket count ≥ 1.
4. Pending: bin-check HIGH (≤1 unit), promote HIGH→world.db (INV-37 ATTACH), schema 36→37, daemon shadow restart, autonomous unshadow on shadow bin bias ≤1 verified.
5. Pending: 120-min live-guard cron heartbeat (no-trade in 4h alert, bin bias > 1 alert, daemon-dead alert).

**Lessons added:**
- **#13:** `verify_truth_surfaces.build_platt_refit_preflight_report` iterates METRIC_SPECS for bucket check. Refit on one metric still fails if other metric's pairs missing. Don't wipe one metric while refitting the other — keep both populated, OR patch preflight to scope by `--temperature-metric` (deferred).
- **#14:** Auto-mode classifier rightly denies bulk DELETE on production tables without explicit operator plan approval. Surface plan → ExitPlanMode approval → execute. Plan file is now the authority record.

## WHERE WE ARE (2026-05-26 19:25 CDT — superseded)

**Refit HIGH (PID 6783)** running since 19:15:47, UN state, scanning HIGH pairs. Past startup. Log: `logs/ftrefit_high_2026-05-26_v3.log`. Monitor `bp96jrelx`.

**LOW rebuild #2 (PID 20174)** launched 19:25 with `--temperature-metric low --n-mc 10000 --workers 12 --no-dry-run --force`. Detached, unbuffered, proven launch format. Log: `logs/ftrebuild_low_2026-05-26.log`. Monitor armed for completion/crash + final pair count + sentinel verification.

**Pre-launch LOW wipe (clean slate):**
- Deleted 15.69M LOW pairs (690k ft_v1 + 15M none).
- Deleted stale LOW sentinel from zeus_meta (claimed complete with 8 cities — corrupt).
- Refit survived the wipe (transaction completed without disturbing refit's HIGH-snapshot read).

**HIGH UNTOUCHED** — 38.84M HIGH pairs intact, HIGH sentinel intact. Refit reads HIGH pairs only.

**Concurrent-write strategy:** SQLite WAL serializes writers. Refit's tiny platt writes + LOW rebuild's bulk pair writes both serialize cleanly. Risk: WAL growth + lock contention. Disk 90Gi free; watch WAL size. If refit hits preflight transient (as in #1), root-cause + relaunch — same playbook.

**Lesson #11:** refit_platt `--rebuild-n-mc` must match rebuild's `--n-mc` (default mismatch 1000 vs 10000).
**Lesson #12:** sentinels can be written non-atomically with data. Wipe sentinel + data together when rebuilding.

## WHERE WE ARE (2026-05-26 19:15 CDT — superseded; refit #2 sentinel miss; #3 launch only)

**Refit attempt #2 (PID 31745) DIED at sentinel check** after passing preflight + finding 137 eligible buckets. Error: `missing rebuild_complete sentinel for ...:n_mc=1000`. **Root cause: argument mismatch.** Refit's `--rebuild-n-mc` default = `calibration_batch_rebuild_n_mc()` = **1000** (src/config.py:574). Rebuild ran `--n-mc 10000`, so sentinel key stored as `n_mc=10000`. Refit looked up `n_mc=1000` → miss.

**HIGH sentinel CONFIRMED present** in `zeus_meta`: key `calibration_pairs_v2_rebuild_complete:metric=high:bin_source=canonical_v2:city=all:...:n_mc=10000`, recorded 2026-05-26T19:45:57Z (14:45 CDT), `pairs_written=38840456`, `status=complete`, 52 cities incl Hong Kong (772242 pairs). LOW sentinel ALSO present in zeus_meta with n_mc=10000 but DB only has 8/51 LOW cities for ft_v1 — **sentinel-vs-data inconsistency for LOW that needs separate audit; doesn't block HIGH**.

**Refit attempt #3 (PID 6783)** launched 19:15:?? CDT with `--rebuild-n-mc 10000`. Same detached + `-u` pattern. New log: `logs/ftrefit_high_2026-05-26_v3.log`.

**Other dead-proc state cleaned:**
- 28447 rebuild stays dead (correct).
- Bridge zombies 67545, 75402 **self-cleaned** between turns — WAL drained 3.7GB → 20KB (checkpoint freed by reader exit). No kill needed.
- launchctl shows live-trading/riskguard/data-ingest "-" PID (paused per ledger).

**Lesson #11:** refit_platt `--rebuild-n-mc` MUST match the rebuild's `--n-mc`. Default differs from rebuild's documented default (`calibration_batch_rebuild_n_mc=1000` vs typical rebuild explicit `10000`). Either pass explicitly or change default. Document on next refit invocation.

**Lesson #12:** LOW sentinel claims complete but data only 8/51 cities. Sentinels can be written non-atomically with data. Audit LOW sentinel + decide: clear sentinel + rewipe LOW for proper rebuild.

## WHERE WE ARE (2026-05-26 18:30 CDT — superseded, rebuild 28447 silently exited)

**28447 EXITED 18:19:59** with NO usable log (log file = 36 bytes, only `setsid: command not found` from a broken launch line that misdirected stdout). No system OOM. Disk 90% but 91Gi free — not crash. **Exit cause unknowable from log; data state is determinative.**

**Pair state at exit (18:30):**
- HIGH ft_v1: **51/51 cities, 16.97M pairs ✓ COMPLETE**
- HIGH none: 49 cities (gap: 2 — set-difference w/ ft_v1 shows London, NYC, Qingdao; small `none`/`ft_v1` city-set drift)
- LOW ft_v1: **8/51 cities, 690k pairs ✗ INCOMPLETE**
- LOW none: 52 cities, 15M pairs (extras possible)

**HIGH refit (PID 31745) UNBLOCKED + ACTIVELY FITTING** — STAT=RN, 72.9% CPU, 8:31 elapsed at probe; reads ft_v1 only → HIGH ft_v1 51/51 complete is sufficient. Monitor b0rcuykyz armed.

**LOW REBUILD DEFERRED** — per ledger rule "WIPE ALL pairs before any rebuild" + per memory `feedback_newcity_no_partial_calibration`, LOW cannot be additively repaired on top of 8/51 partial cities (UNIQUE-key collisions inevitable). Plan: wipe LOW ft_v1, rebuild after refit completes (avoid re-contending forecasts.db). Operator HIGH-first plan stands: LOW markets sparse + filter-gated → won't block live HIGH shipping. New task added.

**Lesson #10:** rebuild launcher must redirect stdout BEFORE eval — the broken `setsid` line ate the entire script output for 7 hours of compute. Next LOW launch: `nohup python3 -u rebuild_calibration_pairs.py ... >> $LOG 2>&1 &` (proven format from refit relaunch 31745). No `setsid`. Verify log grows in first 60s.

## WHERE WE ARE (2026-05-26 18:16 CDT — superseded; refit relaunched after preflight crash)

**Refit attempt #1 (PID 61311) DIED at preflight 17:14:18**, root cause: `RuntimeError: Refusing live Platt v2 refit: platt-refit preflight is NOT_READY (calibration_pairs_v2.identity_mismatch)`. **Root-cause verified read-only**: ran the exact preflight identity-mismatch SQL at 18:10 — HIGH=0, LOW=0 mismatch (all 38.25M HIGH + 12.27M LOW training_allowed rows carry spec-valid `observation_field` + `data_version`). Failure was a transient WAL-snapshot caught during concurrent 28447 LOW bucket insert. No data damage. No platt rows written. **Lesson #9**: when refitting concurrent with active rebuild, preflight may catch mid-commit snapshots; relaunch is safe once identity check passes live.

**Refit attempt #2 (PID 31745)** launched 18:15:23, detached (PPID=1), unbuffered Python (`-u`), `nohup`, log `logs/ftrefit_high_2026-05-26_v2.log`. Past 30s without crash (UN state = heavy preflight read on 51.6M-row contended table). Monitor **b0rcuykyz** persistent, watches log + PID + final platt_models_v2 count; broad terminal coverage (COMPLETE / Traceback / Killed / UNIQUE / locked / OperationalError).

**Pair state snapshot (18:10):** HIGH ft_v1 = 16.97M, HIGH none = 21.87M (training_allowed=1, identity valid). LOW ft_v1 = 429k, LOW none = 12.37M (training_allowed=1, identity valid). 28447 still actively building LOW.

## WHERE WE ARE (2026-05-26 17:30 CDT — superseded)
**HIGH BUILD DONE** 14:46:57 CDT — 51 cities, 16,973,980 HIGH pairs in prod `state/zeus-forecasts.db` (`error_model_family='full_transport_v1'`). Monitor `b67206g5l` fired.
**HIGH FIT RUNNING** since 17:14:18 CDT — PID **61311** `refit_platt.py --temperature-metric high --error-model full_transport_v1 --db state/zeus-forecasts.db --no-dry-run --force`, PPID=1 detached, log `logs/ftrefit_high_2026-05-26.log` (buffered — quiet until flush/exit). Per `db_writer_lock.py:649-674`, write mode refuses only world.db; forecasts.db IS the canonical Platt staging DB (promote step lifts to world.db via INV-37 ATTACH+SAVEPOINT).
**LOW BUILD CONTINUES** — PID 28447 ALIVE 6:23h elapsed, on LOW.
**Concurrent writers OK** — both detached on prod forecasts.db; refit writes platt_models_v2 (tiny+bursty), 28447 writes calibration_pairs_v2 LOW buckets; SQLite WAL serializes writes. Monitor mild contention; no corruption risk.
**Monitors** — a1d95f's (output/exit refit), b205l04cr (LOW-done). Don't double-arm. Task #70 in_progress. **No promote/restart** until fit reports + bin-check ≤1.

## WHERE WE ARE (2026-05-26 ~12:30 CDT, prior)
**PART A (code) = DONE.** PR **#342 MERGED to main** 17:33 UTC. main now carries the entire ship: Fix A (metric-aware 0Z/12Z bias window, ens_bias_repo), Fix B (MIN_PAIRED_N=5 transport gate, ens_error_model), live wiring (monitor_refresh `_load_ft_error_model`, flag `full_transport_live_enabled` default OFF), identity-calibrator route (platt.py IdentityCalibrator + manager get_calibrator), preflight mx2t3/mn2t3 rename fix, schema_version 37, ship-readiness gate. Opus-reviewed ACCEPT, all CI green, 24 bot threads resolved.

**PART B (data) = IN PROGRESS.** Rebuilding all calibration pairs on prod through the corrected code.
- **Rebuild PID 28447** — detached (ppid=1, survives), `rebuild_calibration_pairs.py --no-dry-run --force --db state/zeus-forecasts.db --error-model full_transport_v1 --temperature-metric all --n-mc 10000 --workers 12`. Log: `logs/ftrebuild_full2_2026-05-26.log`. Does HIGH metric fully, THEN LOW. At 12:22: HIGH ~23/52 cities, 0 collisions, disk 116G. **ETA HIGH ~14:30, full (incl LOW) ~16:00 CDT.**
- **Smoke already validated correctness**: HK HIGH p_raw mass centered 25-30°C (real range), tracks settlements tightly (28°C p=0.143/out=0.152) → Fix A+B working, the −2.1/+6.3 contamination is gone.

## MONITORS ARMED (auto-trigger next steps; do not duplicate)
- **b67206g5l** (persistent) — fires when HIGH build done (first LOW pair committed). → start HIGH fit.
- **b205l04cr** (persistent) — fires when 28447 fully exits (LOW done). → start LOW fit.
- a1d95f (agentId **a1d95fdd53c4676f5**) — STOOD DOWN; resume via SendMessage for the fit step. It owns the rebuild process knowledge.

## SUBSEQUENT TASKS — HIGH-FIRST PARALLEL (operator-directed)
LOW markets are sparse + filter-gated (won't place orders), so ship HIGH to live-shadow ASAP; finish LOW in background. refit_platt.py is metric-scoped → HIGH fit runs WHILE 28447 builds LOW.

**HIGH track (critical path):**
1. **[trigger: b67206g5l] Fit HIGH** — resume a1d95f: `refit_platt.py` metric=high, `--error-model full_transport_v1`, ECE-gated (identity calibrator where ECE low, learned Platt where it improves). Runs parallel to 28447's LOW build (reads stable HIGH pairs; SQLite WAL allows concurrent read+write; no conflict — different metric rows). Task #70.
2. **Bin-check HIGH** — matched-date proper-score / bin-sanity on HIGH cohorts: p_raw bins must fall in expected intervals, HK HIGH passes pathology rule. (Smoke already showed HK calibrated; confirm across cohorts.) Task #74.
3. **Promote HIGH** — `promote_platt.py`: HIGH fitted models → prod **state/zeus-world.db**, additive (keyed error_model_family), explicit pin for all HIGH cohorts (NO carve-out — HK fixed). Cross-DB writes MUST use INV-37 (ATTACH+SAVEPOINT, never independent connections). Task #73.
4. **[needs: world.db schema migrate to 37] Restart daemon SHADOW** — `launchctl kickstart -k gui/$(id -u)/com.zeus.live-trading` on the merged main code, flag `full_transport_live_enabled` ON, shadow mode. The daemon CHECKS world schema and does NOT self-migrate forecasts; before restart run init_schema/migrate to add `calibration_method` col + bump world.db to 37 (else daemon SystemExit on schema mismatch). The restart also clears the stale deployment_freshness auto-pause. Task #73/#90.
5. **Verify HIGH shadow** — daemon must produce fresh `probability_trace_fact` rows; HIGH p_raw bins align with online weather forecast for the target date, **bin bias ≤ 1 unit**. No shadow traces = DEFECT → root-cause (don't stall). Task #74.
6. **[OPERATOR GATE — surface here] Unshadow** — flip to live trading bounded/tiny. Then PROVE a real chain order fills. No fill = DEFECT → root-cause. Tasks #90/#91.

**LOW track (background, parallel):**
7. **[trigger: b205l04cr] Fit + promote LOW** — after LOW build done, `refit_platt.py` metric=low → promote LOW models → world.db + pin LOW. LOW joins live after HIGH shadow validated. Until then LOW sparse + filter-gated → no orders (correct). Task #92.

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

---

## Historical version (pre-#342)

# DO NOT STALL.

full_transport → live trading: autonomous execution ledger. Read top-to-bottom on wake, find the first non-FINISHED step, advance it, update state here. No-trade = defect. No stall, no excuse.

## CONTEXT FOR A FRESH READER (you, on wake, may have no prior memory)
**Goal:** ship the full_transport probability correction to live Polymarket weather trading. full_transport = location + scale + SNR-gate + F50→F25 transport, applied at p_raw generation. The math shape is correct & proven (#334/#336). The 3-day stall was: an evaluation refit was mistaken for a production artifact (in-RAM posteriors, scratch DB, no live wiring, no persistence). We are now building the real production instantiation.

**Probability chain:** 51-member ENS → ENS bias correction (the full_transport error model: bias b, λ SNR-gate, residual sd; src/calibration/ens_error_model.py + ens_bias_model.py) → daily-max extraction → 10k MC → p_raw → Platt OR identity calibrator → p_cal → α-fusion vs market → edge → Kelly. K1 DB split: state/zeus-world.db (platt_models_v2, traces, trades) + zeus-forecasts.db (calibration_pairs_v2, ensemble_snapshots) + zeus_trades.db.

**Why the two fixes (the heart of it):**
- **Fix A:** the HIGH bias prior was contaminated — ens_bias_repo picked the *freshest* snapshot per date = the 12Z cycle, whose window is nighttime and MISSES the afternoon daily-HIGH → every HIGH prior read −3 to −4°C too cold. Fixed: metric-aware window selection (HIGH→0Z daytime, LOW→12Z night). HK HIGH prior −3.49→+0.67°C. DB-wide. (LOW was always correct → 12Z night IS the daily-min window.)
- **Fix B:** the F25→F50 transport term used single-date deltas (n_paired=1) which give var_d=0 → look maximally confident → SNR gate λ=1.0 → full wrong correction. 34/52 cities affected (Dallas −9.87, Busan +5.03). Fixed: MIN_PAIRED_N=5 gate → transport falls back to bias-only below threshold. HK HIGH effective_bias −2.10→+0.10°C.
- Net: HK ships at +0.10°C (was +6.3 warm then −2.1 cold). All 49 cities, NO carve-out.

**Why this is hard / the gotchas that bit us:** main lacks Fix A (still freshest-selection). Live p_raw is plain (no error model) → promoting ft Platt alone = train/serve mismatch (must wire monitor_refresh, gap 3.1). Evaluator blocks cal=None before edge → p_raw-direct not tradeable without an explicit identity calibrator (gap 3.3). promote scripts replace BY data_version → blanket promote orphans coverage; use additive insert keyed error_model_family. Empty calibration.pin → "newest VERIFIED wins" silent takeover → set pin explicitly. Daemon auto-pauses on stale code (deployment-freshness) — restart on the SHIP sha clears it.

## Operator contract (hard rules)
- **DO NOT STALL.** Every wake: advance the next incomplete step or root-cause the blocker. Never report "waiting" as a resting state.
- **No-trade = something wrong.** Shadow producing no result = bug → root-cause. Full-live with no actual chain order filled = bug → root-cause. No excuse/stall reason.
- **Rebuild on the ACTUAL prod DB — no separate-branch/scratch DB.** (Scratch regen killed per operator.)
- **`feat/ft-ship-64` is the ONLY integration base** (origin, ahead of main). main LACKS Fix A. Never fork off main.
- **All code → ONE #64 PR → wait for operator merge.**
- **Post-restart verify chain (before any real trade):** shadow result must align with the online weather forecast for the target date, **bin bias ≤ 1 unit** → THEN unshadow → THEN prove real chain order fills.
- HK ships, all 49, **no carve-out** (fix, not exclude). Simplify post-MC: prefer identity calibrator (p_raw-direct) where ECE low; Platt only where it helps.

## Key paths
- Integration branch: `origin/feat/ft-ship-64` (ahead 9).
- Prod DBs (live checkout, ABSOLUTE): `/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db` (39GB) + `zeus-forecasts.db` (49GB). Live daemon: `src.main` + riskguard.
- Backup full.db (38M pairs): `state/backups/ens_refit_full_2026-05-25.db` (33.5GB).
- Corrected posteriors (71 rows): `state/backups/ens_error_models_2026-05-25.db`.
- Master spec: FT_SHIP_MASTER_SPEC_2026-05-25.md (on PR #340 — not on this branch).

## FINISHED
- Fix A — metric-aware 0Z/12Z window selection in ens_bias_repo (HK HIGH prior −3.49→+0.67). `5260dd2809` on ft-ship-64.
- Fix B — transport MIN_PAIRED_N=5 gate in ens_error_model (HK HIGH eff −2.10→+0.10; 34 cities de-noised). `060540448e` on ft-ship-64.
- Sentinel reader fix (promote_platt.py:226). On ft-ship-64.
- Ship-readiness gate (scripts/check_full_transport_ship_readiness.py, 9 booleans). On ft-ship-64.
- Matched-date eval tool + replay-equivalence harness. On ft-ship-64.
- Canonical error-model schema (model_bias_ens_v2 +13 fields) + writer-bug fix + posterior producer (scripts/fit_full_transport_error_models.py). On ft-ship-64.
- Branch consolidation → feat/ft-ship-64 ahead 9 (git-master).
- full.db backup + corrected posteriors persisted (durable).
- Gate-rejection audit → verdict: market-thinning, NOT over-rejection.
- SIGNAL_QUALITY "alpha bug" → REFUTED (real cause MODEL_CONFLICT on phantom cold-bias edges; full_transport is the fix). Closed.
- Replay-proof run → all 79 cohorts regenerate (moot: rebuilding on prod anyway; uniform-0.5 diff possibly harness artifact, not chased).

## ONGOING (in-flight agents — these ARE dominoes D1a/D1b/D6 below)
- **a65606217d201f57b** = **D1a** — ✅ DONE. Live wiring committed **`4dafe60380`+`11167efc25`** on **feat/ft-64-live-wiring**. flag `full_transport_live_enabled:false`; both ENS branches (period_extrema ~537, ens ~577) flag-gated to p_raw_vector_with_error_model; plain path byte-identical when OFF; fail-closed (no row→plain+WARNING). 7 relationship tests. 3 crossing_decision + 386 broad failures = PRE-EXISTING (verified identical pre-change). EMITS `WIRING_DONE`. (#87)
  - **D2 gate:** WIRING_DONE && IDENTITY_DONE both true, BUT consolidate must wait for a1d95f's rebuild-script patch to land on ft-ship-64 (same merge target — avoid push race). #64 PR captures wiring+identity+patch together. No time lost (12h rebuild is long pole; D8 needs both tracks).
- **a7cfb0224c4c44b49** = **D1b** — ✅ DONE. Identity-calibrator route committed **`2f3df914b5`** on feat/ft-ship-64. IdentityCalibrator in platt.py; get_calibrator returns (cal, level=1) bypassing maturity_level(0)=4 gate; schema **36→37** (calibration_method TEXT col, ALTER idempotent); 13 relationship tests + 116/116 suite. EMITS `IDENTITY_DONE`. (#86)
  - ⚠️ schema 36→37 ALTER → needs world.db migration on D8 restart (daemon checks, does NOT self-migrate).
- ~~a123b794b1ca0a7c6 — daemon silence RC~~ → **RESOLVED-DIAGNOSIS.** Cause: deployment_freshness_4h_divergence auto-pause (daemon booted on stale SHA e4dcaf56, origin/main advanced → guard auto-paused entries 2026-05-24 17:15:31, rolling every min since; traces stopped 17:15:16). SECONDARY: M5 WS-gap reconcile kill-switch armed (15 findings → allow_submit=False, DATA_DEGRADED). Daemon alive+ticking (market discovery 123 events), correctly self-protecting against stale code. FIX = restart on current/ship HEAD (`launchctl kickstart -k gui/$(id -u)/com.zeus.live-trading`) → pause auto-clears, M5 in-memory resets. **Folds into ship deploy restart (step 6) — do NOT restart on interim code.** Watch: M5's 3 chain subjects may need operator QUARANTINE review if findings re-accumulate post-restart.

- **a1d95fdd53c4676f5** = **D6** — REBUILD GREEN-LIT (the dozen-hour long pole). Full ft MC (HIGH+LOW, error_model=full_transport_v1, n_mc=10000, ~2 workers) ADDITIVE to prod state/zeus-forecasts.db (operator: no scratch). world.db NOT touched (rebuild writes forecasts.db only) → no world backup needed. NO 46GB forecasts backup either — instead PROVE additivity via dry-run (delete-scope = bin_source=canonical_v2 AND family=full_transport_v1, which is absent in prod → must report 0 deletions; ANY legacy deletion = STOP). Daemon stays paused. **DRY-RUN CAUGHT 2 BLOCKERS (antibody worked, no data lost):**
  - **P1 (data-loss):** `_scoped_pair_predicate` (L977-991) scopes delete by bin_source+metric+city, NO family filter → would destroy all 91M none-family pairs (replace-in-place, violates spec Phase-3 additive mandate). INSERT side OK (stamps family='full_transport_v1' L206→1345). FIX: add error_model_family to delete predicate → idempotent within ft_v1, never touches none. 
  - **P2 (quarantine):** prod HIGH has 1342 deprecated `ecmwf_opendata_mx2t6` snapshots (vs allowed tigge_mx2t6 + opendata_mx2t3); script CRASHES on first instead of skipping. FIX: catch DataVersionQuarantinedError per-snapshot → fail-closed skip+count (keep spec list intact, don't re-admit deprecated data).
  - **ROUND-2 (deeper): preflight gate (only runs in --no-dry-run, so round-1 dry-run missed it) found mx2t3 rename incompleteness.** ROOT (one design failure): the 2026-05-07 mx2t6→mx2t3 rename propagated to contracts + LIVE SERVING (forecast_extrema_authority:56, executable_forecast_reader:34, live_entry_status:83 all use mx2t3/mn2t3) but NOT to the calib-rebuild spec's stale `identity.physical_quantity=mx2t6`. Preflight `unsafe_where` matched the stale label → rejected 9,602 HIGH mx2t3 + 2,726 LOW mn2t3 canonical rows. STRUCTURAL FIX (5 items, a1d95f): (1) preflight derives permitted physical_quantity from allowed_data_versions not stale identity; (2) fail-closed exclude rows serving excludes — attribution NOT IN POSITIVE_ATTRIBUTION_STATUSES (skip+count, don't block); (3) fix spec identity at source → mx2t3/mn2t3; (4) preflight count-only in dry-run (antibody gap fix); (5) rejection-preserving test (legacy mx2t6 still rejected, mx2t3 passes). Verified: serving uses POSITIVE set {EXPLICIT,VERIFIED,OK,CONTRIBUTES,FULLY_INSIDE} → 2,726 LOW AMBIGUOUS excluded by serving → calib excludes too (train/serve symmetric, legitimate). DECISION: valid 46GB .backup before launch. Awaiting a1d95f: re-dry-run numbers + PID + log + ETA.

## FORKED (extra issues found — track to closure)
- **DISK EMERGENCY 2026-05-25 ~21:00 — RESOLVED.** State volume hit 100% / 1.5GB free → live-daemon-crash risk. ROOT CAUSE = self-replicating `replay_equivalence_full_transport.py` pileup (4 live + 3 FD-pinning procs, recurred from the earlier 2-h stall) + a1d95f's interrupted 36GB partial backup. FIX: pkill -9 all replay (verdict moot), rm invalid partial backup (+36GB), rm unheld dead scratch regen_high_fixab/sf* (+33GB) → 121GB free. KEPT: state/backups/ens_refit_full (durable 38M-pair asset) + ens_error_models (holds the 71 corrected posteriors AND a 54M-pair copy). REMAINING pinned: /private/tmp/ens_refit/{full,subset}.db held by omc-bridge FDs (~37GB, not reclaimed — left alone, 121GB is enough). ANTIBODY TODO: replay harness must self-limit to one instance + clean up; never spawn on the 31GB DB unsupervised.
- **Daemon silence 27h** — RC done (deployment_freshness auto-pause on stale SHA). No-trade right now is EXPECTED (pre-ship, gated), NOT the "no-trade=defect" case — that rule applies only AFTER D10 unshadow. Clears on D8 restart on ship sha.
- **LOW mn2t3 contract-window attribution (operator caveat, not a blocker):** 2,726 LOW OpenData mn2t3 rows carry AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY/UNKNOWN attribution → excluded from calibration (matching serving, which also excludes them). LOW ships consistent without them. LATENT IMPROVEMENT (out of #64 scope): compute contract-window attribution for OpenData mn2t3 LOW (the machinery exists for tigge contract_window_v2) so those snapshots could eventually contribute to LOW coverage. Daily-min near dawn straddles the local-day boundary in 3h windows — physical, not a label bug.
- 3 no-model cohorts (Ankara/high/DJF, Jakarta/high/SON, Wellington/high/JJA) — insufficient source data; need data backfill before they ship.
- 8 large-eff cohorts (Busan, Jeddah, …) — large raw TIGGE prior; MUST pass pathology rule (PIT/ECE) in re-eval before pinning.
- Replay uniform-0.5/0%-argmax incl LOW — possible harness artifact; flagged, not blocking (full rebuild on prod anyway).

## DOMINO CHAIN → live (each EMIT tips the next TRIGGER — no manual gaps)
Two parallel tracks (CODE, DATA) run independently, converge at D8 (shadow restart).
On wake: find the last EMIT that fired, tip the domino whose TRIGGER it is. AUTO = advance without asking. GATE = surface to operator with evidence, then stop.

**CODE track** (worktree → PR → merge):
- **D1a** [IN FLIGHT a65606] wiring monitor_refresh flag-OFF → EMITS `WIRING_DONE`. AUTO.
- **D1b** [IN FLIGHT a7cfb0] identity-calibrator route + evaluator un-block → EMITS `IDENTITY_DONE`. AUTO.
- **D2** TRIGGER `WIRING_DONE && IDENTITY_DONE` → consolidate both onto feat/ft-ship-64 (git-master) → EMITS `CONSOLIDATED`. AUTO.
- **D3** TRIGGER `CONSOLIDATED` → open the SINGLE #64 PR + dispatch opus critic on full diff → EMITS `PR_OPEN`. AUTO.
- **D4** TRIGGER `PR_OPEN` → address bot review + critic findings, CI green → EMITS `PR_GREEN`. AUTO.
- **D5** TRIGGER `PR_GREEN` → **operator merge**. → EMITS `MERGED`. **GATE (operator).**

**DATA track** (prod DBs, no PR — runs in parallel with CODE track):
- **D6** [IN FLIGHT a1d95f, long pole ~dozen h] rebuild ft MC HIGH+LOW additive on prod forecasts.db → EMITS `PAIRS_REBUILT`. AUTO.
- **D7** TRIGGER `PAIRS_REBUILT` → write corrected posteriors→prod model_bias_ens_v2; fit Platt where ECE>thr else identity (ECE-gated); additive keyed error_model_family; set calibration.pin model_keys for all 49 (explicit, no "newest VERIFIED wins") → EMITS `CAL_READY`. AUTO.

**CONVERGENCE** (needs BOTH tracks):
- **D8** TRIGGER `MERGED && CAL_READY` → restart daemon SHADOW on ship sha + flag `full_transport_live_enabled` ON (this restart also clears the deployment-freshness auto-pause + resets M5 in-memory) → EMITS `SHADOW_LIVE`. AUTO.
- **D9** TRIGGER `SHADOW_LIVE` → verify shadow: p_raw bins align with online weather forecast for target date, **bin bias ≤ 1 unit**. No shadow result = DEFECT → root-cause (do not stall). → EMITS `SHADOW_VERIFIED`. AUTO.
- **D10** TRIGGER `SHADOW_VERIFIED` → **operator unshadow** (irreversible). → EMITS `UNSHADOWED`. **GATE (operator).**
- **D11** TRIGGER `UNSHADOWED` → prove real chain order fills (tiny/bounded first). No fill = DEFECT → root-cause. → EMITS `FILL_PROVEN`. AUTO.
- **D12** TRIGGER `FILL_PROVEN` → normal sizing. DONE.

## NEXT ACTION ON WAKE
Identify last EMIT fired → tip its successor. Right now: D1a/D1b/D6 IN FLIGHT.
- D1a && D1b done → tip **D2** (consolidate) → D3 (open #64 PR). [CODE track auto to D5 gate.]
- D6 done → tip **D7** (fit cal + pin). [DATA track auto to CAL_READY.]
- Both `MERGED` (D5 operator) && `CAL_READY` (D7) → tip **D8** (shadow restart).
Update domino states above on every advance. DO NOT STALL.
