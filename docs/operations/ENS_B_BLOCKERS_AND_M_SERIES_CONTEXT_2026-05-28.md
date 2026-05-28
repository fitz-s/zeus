# ENS B-Blockers + M-Series Mission Context — 2026-05-28

**Audience**: any session (live or background) resuming this work after compaction or a restart.
**Authority**: operator pre-MC re-audit (2026-05-28) + operator live-shadow pivot (2026-05-28 ~05:30 CT).
**Worktree**: `/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/ens-bias-hierarchical`
**Branch**: `feat/ft-domain-canonical-refit`
**HEAD at write-time**: `3ce02984b3` (B1+B2+B3+B6+B7 patches)
**Active gate**: `_GATE_SET_VERSION = "ftgate-2026-05-28-sd3"`

---

## 1 — What just shipped (durable, do not redo)

PR #358, commit `3ce02984b3`, closes the operator's pre-MC re-audit:

| ID | Defect | Fix | Files | Tests |
|----|--------|-----|-------|-------|
| **B1** | Producer iterated calendar `_SEASONS`; reader uses `season_from_date(... lat=city.lat)` with `_SH_FLIP` → 4 SH cities (Buenos Aires, Cape Town, Sao Paulo, Wellington) had VERIFIED rows orphaned. | New `_iter_seasons_for_city(city)` in `scripts/fit_full_transport_error_models.py` yields `(label, months)` with label flipped per `city.lat`. Producer loop iterates the hemisphere-aware helper. | `scripts/fit_full_transport_error_models.py` | `tests/test_b1_producer_hemisphere_aware_season.py` |
| **B2** | `_native_error_params_for_snapshot` cache_key was `(city.name, season_label, metric)` — first snapshot's None poisoned later covered-month snapshots OR a covered-month row got reused for off-coverage months, bypassing `read_bias_model` month-scope guard. | Cache_key now `(city.name, season_label, metric, target_month)`. | `scripts/rebuild_calibration_pairs_v2.py` | `tests/test_b2_native_error_params_cache_includes_target_month.py` |
| **B3** | A-cohort replay used `error_model_source="recompute"` (MIN_LIVE_N=5) vs producer (MIN_LIVE_N=20). `_load_error_model_from_db` ran raw SELECT with no family/authority/gate/month filter. | Switched selective driver to `db` mode; `_load_error_model_from_db` rewritten to call `read_bias_model(error_model_family='full_transport_v1', authority='STAGING', require_gate_set_hash=current_gate_set_hash(), target_month=<per_snapshot>)`. `_evaluate_cohort` defers acquisition to per-snapshot in db mode. | `scripts/replay_equivalence_full_transport.py`, `scripts/selective_refit_from_manifest.py` | `tests/test_b3_replay_uses_persisted_staging.py` |
| **B6** | Producer wrote `training_cutoff=today_str` but loaders received `settled_before=None`. Stored cutoff was a label only — two-row reproducibility could not reproduce. | Threaded `today_str` into all 4 loaders (`load_bucket_residuals`, `fit_city_predictive_error`, `paired_delta_coverage`, `_effective_coverage_months`). | `scripts/fit_full_transport_error_models.py` | `tests/test_b6_training_cutoff_threaded_to_loaders.py` |
| **B7** | `compute_final_regen(... gate_changed=True)` silently returned `set(all_cohorts)` → selective rebuild degraded to full reproduce when manifest itself was stale under the new gate. | Raises `SystemExit` with explicit "regenerate ROW_ACTION_MANIFEST under current gate" guidance. | `scripts/selective_refit_from_manifest.py` | `tests/test_b7_gate_changed_aborts_selective_refit.py` |

**Already-shipped pre-B work (do not redo, in same PR #358):** SD1–SD6 + BL-B + BL-C + BL-D + BL-E. See completed task IDs #126-#130, #141-#144.

**B4, B5**: already closed in PR #358 before B-batch (BL-C task #142, BL-B task #141).

**`_GATE_SET_VERSION` bump**: sd2 → sd3. B1 + B6 change the fit-time semantics encoded in every stored row, so the gate hash MUST advance — pre-sd3 STAGING rows are no longer reproducible under the new contract. Stats unchanged (MIN_PRIOR_N=5, MIN_PAIRED_N=5, DEFAULT_MIN_LIVE_N=20, CONSERVATIVE_RESIDUAL_FLOOR_C=3.0). **Do not touch any stat threshold** — each change re-bumps the hash and re-invalidates the manifest that gets regenerated downstream.

**Regression**: 65/65 B+SD+BL tests green; 109/112 ens/ft/replay tests green (3 pre-existing carveout-yaml fails in `test_check_full_transport_ship_readiness.py` unrelated, predate B-batch).

---

## 2 — Operator's working-state pivot (2026-05-28 ~05:30 CT)

**Verbatim**: "在rebuild和refit完成前live只能shadow运行，你必须等到pr359 merge之后对齐main，然后基于最新的main进行对应的代码修改，有大量文件改名，然后再跑rebuild和refit，你必须设置monitor等待他们结束，同时在rebuild和refit期间必须有1小时heartbeat... 在最新的数据上restart shadow live，查看bin bias对于主流天气预报的区别是否改善，用测试证明前后区别，然后再正确的unshadow live让live在正确代码上运行."

**Distilled into M-series task graph (#150-#155)**:

```
                          [M0 #150] monitor PR #359 open + merge
                                       |
                                       v
                          [M1 #152] rebase worktree → latest main
                              (big file renames expected;
                               B1-B7 patches must carry over)
                                       |
                                       v
                          [M2 #153] rebuild + refit at sd3
                              (Monitor + 1hr heartbeat REQUIRED)
                                       |
                                       v
   [M3 #151] live SHADOW    +    [M4 #154] before/after bin-bias test
   (parallel during M0-M2          (vs mainstream forecast, must prove
    wait; candidates flow,          DIRECTIONAL IMPROVEMENT)
    NO orders submitted)                |
                                       v
                          [M5 #155] unshadow live
                              (only after M4 GREEN; post-unshadow
                               4h zero-trade = DEFECT, not expected)
```

Live trading stays in SHADOW until M5. FT flag stays OFF until M5.

---

## 3 — Where each task stands RIGHT NOW

State as of 2026-05-28 05:33 CT (re-verify on resume — see §7 heartbeat).

| Task | Status | Blocker / next action |
|------|--------|-----------------------|
| **B1-B7** (#145-#149) | ✅ Completed | Shipped in `3ce02984b3` on PR #358. |
| **R1** (#131) Merge PR #358 | ⏸️ Operator-gated | Operator authorization required. PR #358 CI was GREEN at last push; new commit `3ce02984b3` will re-run CI. |
| **R2-R8** (#132-#138) | ⏸️ Superseded / re-sequenced under M-series | M-series is the new master sequence per operator pivot. R-series tasks remain on the list for traceability; R7 (delete stale-gate VERIFIED rows) merges naturally into M2's rebuild phase. R8 (live reader enforcement of gate + month) requires operator decision before M5. |
| **R9** (#139) Live unblock | ⏸️ Parked on -pr3 reconcile/DATA_DEGRADED | Other session. Not in this worktree's critical path. |
| **BL-A** (#140) Regen ROW_ACTION_MANIFEST under current gate | ⏸️ Pending | Required BEFORE M2 — manifest from sd2 is stale under sd3. Regenerate after #359 merges + main alignment (so the audit script runs against post-rename layout). |
| **M0** (#150) Monitor PR #359 | 🔵 In flight | Monitor task `bvo7624iz` armed (persistent, 10-min poll). Will emit `#359 state: NO_PR_YET` → `OPEN:null` → `MERGED:<ts>`. Branch `claude/refactor-auth-econ-split-pr3` (SHA `844d82294e`) exists on origin but no PR opened yet. |
| **M1** (#152) Rebase to main | ⏸️ Blocked by M0 | Big renames expected. B1-B7 patched files: `scripts/fit_full_transport_error_models.py`, `scripts/rebuild_calibration_pairs_v2.py`, `scripts/replay_equivalence_full_transport.py`, `scripts/selective_refit_from_manifest.py`, `src/calibration/ens_error_model.py`. Verify each path still exists post-rename; if renamed, replay the B-edits onto the new file. |
| **M2** (#153) Rebuild + refit | ⏸️ Blocked by M1 | Run `scripts/fit_full_transport_error_models.py --metric high --commit` then `scripts/fit_full_transport_error_models.py --metric low --commit` on an isolated staging DB (not prod). Then `scripts/rebuild_calibration_pairs_v2.py --no-dry-run --force --db <staging> --error-model full_transport_v1 --temperature-metric <high|low> --n-mc 10000 --workers <CPU-2>`. Then `scripts/selective_refit_from_manifest.py --manifest <fresh manifest> --db <staging> --execute`. **MUST set Monitor with 1hr heartbeat** during this — per memory `project_ft_ship_autonomous_workflow_2026_05_26.md`: PID alive + log frozen ≠ normal; silent ≠ normal; CPU%+RSS+log-byte+temp-file delta per minute required. |
| **M3** (#151) Live shadow | 🟢 Eligible now (parallel to M0/M1 wait) | forecast-live daemon (PID 85201) confirmed alive + ingesting at 05:13 (`zeus-forecast-live.log` loop_progress). live-trading (74152) + riskguard (85199) alive. FT flag OFF. Need: confirm probability_trace_fact writer not silent (task #120 in_progress — verify recent rows on `zeus_trades.db` `.probability_trace_fact`). Need: confirm candidates flow to evaluator (not just ingest). Then formally declare shadow-active. |
| **M4** (#154) Bin-bias test | ⏸️ Blocked by M2 + M3 | Write test: (a) pre-sd3 rows + plain p_raw vs (b) post-sd3 rows + ft-corrected p_raw, compare bin-bias against mainstream forecast (open-meteo + TIGGE consensus). Metric: bin-bias mean (signed), RPS, ECE. Must show DIRECTIONAL IMPROVEMENT in ≥2 of 3 metrics on ≥75% of cities. Open question: exact baseline definition — operator decision. |
| **M5** (#155) Unshadow | ⏸️ Blocked by M4 | Flip `config/settings.json:195 "full_transport_live_enabled": true`. Restart monitor + live-trading. Per memory: 4h post-unshadow zero-trade = DEFECT cascade (daemon → candidates → gates → edge → bias). |

**Other in-flight tasks (background, not on critical path)**:
- #109 (in_progress) P0: market scanner ingests 1 of 11 sub-markets per event.
- #110 (pending) Per-outcome HTTP overhead in capture_executable_market_snapshot.
- #116 (pending) test_ens_predictive_pipeline.py fixture: missing issue_time column.
- #120 (in_progress) probability_trace_fact silent writer investigation — **convert this into hard verification for M3**.
- #121 (pending) portfolio_quarantined blocker (post-RiskGuard fix).
- #124 (in_progress) Post-restart: candidates flow + trace fires + first order — superseded by M3.
- #105 (pending) F7b: trace writer must tag p_raw_domain — should land before M3 unshadow.
- #107 (pending) promote_model_bias_ens_v2 missing conn.commit — feeds M2 promote phase.
- #29 (pending) test_market_phase_dispatch 3 fails — stale, not critical.

---

## 4 — How to resume work

**On any resume (compaction recovery, new session, heartbeat fire)**, run §7 heartbeat script first. Do **not** skip — operator's standing rule: "must check latest instead of just wake up." The state in this doc is a snapshot; the heartbeat refreshes it.

Then:
1. Read the heartbeat output.
2. Compare against §3 table above.
3. Identify the lowest-ID task that is BOTH unblocked AND not-in-progress; advance it.
4. If a Monitor event fired during the wait (look for `#359 state:` entries), act on it — Monitor's persistent task `bvo7624iz` watches PR #359.
5. If M2 (rebuild/refit) is in flight, additional 1hr heartbeat to it is required (above and beyond the universal 2hr heartbeat scheduled in §7).

---

## 5 — Key invariants and gotchas — read before any edit

**From accumulated memory (do not violate):**
- **`launchctl list` STATUS column = LAST exit code, not current health.** Verify daemon via PID column + `ps -p <PID>` + log byte-delta. `feedback_launchctl_status_is_last_exit_not_current.md`.
- **Live root is multi-session.** Other session can flip HEAD to `claude/refactor-auth-econ-split-pr3` mid-flight. `git rev-parse HEAD` BEFORE any live daemon restart. Memory: `feedback_live_root_multisession_collision_2026_05_28.md`.
- **No partial calibration on new cities.** Strict order: register+blacklist → full historical TIGGE + ECMWF ingest → calibration once at full depth → Platt → 14-day shadow. Jinan/Zhengzhou warnings in forecast-live.err are expected pre-onboarding. Memory: `feedback_newcity_no_partial_calibration.md`.
- **Disk currently 14 GiB free / 99% full.** Blocker for full STAGING reproduce; clean before M2 OR scope staging DB.
- **Cross-DB writes adhere to INV-37** — ATTACH + SAVEPOINT, never independent connections.
- **Never write to production DBs from the producer.** `scripts/fit_full_transport_error_models.py::_refuse_prod_db` is the antibody (BL-E). Always run on isolated staging copy.
- **Auto-mode denies unaudited writes (correct guardrail).** Plan write phases as a programmatic sequence with operator pre-authorization.

**M2-specific:**
- BULK lock serial across forecasts.db; LOW + HIGH cannot rebuild concurrently. Sequence: HIGH fit → HIGH MC rebuild → HIGH selective_refit → LOW fit → LOW MC rebuild → LOW selective_refit → audit.
- The rebuild is compute-in-workers / write-in-main (single writer). Workers = `CPU_count - 2` (12 on a 14-core box). No WAL multi-writer contention.
- Replay equivalence runs at production `--n-mc` to validate A-cohort reuse (BL-D fix).

**M3-specific:**
- Shadow = daemon alive + candidates compute + probability_trace_fact writes + NO orders submitted.
- The `live-trading` daemon's submission path is governed by kill-switch + `full_transport_live_enabled` flag. FT flag OFF + no kill-switch arm means plain p_raw flows but orders may STILL submit — confirm by reading `chain_orders` (or its current-schema equivalent) emptiness over the shadow window.
- `probability_trace_fact` columns (verified 2026-05-28): `trace_id`, `decision_id`, `decision_snapshot_id`, `candidate_id`, `city`, `target_date`, `range_label`, `direction`, `mode`, `strategy_key`, `discovery_mode`, `entry_method`, `selected_method`, `trace_status`, `missing_reason_json`, `bin_labels_json`, `p_raw_json`, `p_cal_json`, `p_market_json`, `p_posterior_json` (world-DB only). **No `captured_at` column** — use `decision_id` join + `decision_log` if temporal queries needed.

**M4-specific:**
- Baseline-definition decision is operator-gated. Until they clarify, default to (open-meteo `tt2m_max` daily) as mainstream baseline + measure bin-bias = (P(stored bin) − P(observed bin)). Compare pre-sd3 vs post-sd3 distributions.

---

## 6 — Quick command reference (use absolute paths)

```bash
# Verify worktree HEAD (NEVER `cd` away from worktree absolute path)
cd /Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/ens-bias-hierarchical
git rev-parse HEAD                            # expected post-B: 3ce02984b3 or newer

# Active PR state
gh pr view 358 --json state,url,statusCheckRollup
gh pr list --state all --head "claude/refactor-auth-econ-split-pr3" --json number,state

# Daemon liveness (PID column matters; STATUS is historical)
launchctl list | grep com.zeus
ps -p <PID> -o pid,etime,rss,command

# Disk
df -h /Users/leofitz/.openclaw/workspace-venus/zeus/state

# Run regression sweep before any commit
/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest \
  tests/test_b1_producer_hemisphere_aware_season.py \
  tests/test_b2_native_error_params_cache_includes_target_month.py \
  tests/test_b3_replay_uses_persisted_staging.py \
  tests/test_b6_training_cutoff_threaded_to_loaders.py \
  tests/test_b7_gate_changed_aborts_selective_refit.py \
  tests/test_selective_refit_replay_consumption_sd3.py \
  tests/test_effective_coverage_sd1.py \
  tests/test_insufficient_prior_conservative_sigma.py \
  tests/test_producer_schema_preflight_sd5.py \
  tests/test_pair_batch_domain_sd4.py \
  tests/test_mc_entry_gate_sd6.py \
  tests/test_rebuild_months_scope_bl_b.py \
  tests/test_producer_no_prod_write_bl_e.py \
  --no-header -q
```

---

## 7 — 2-hour heartbeat: refresh latest state, then orient

This MD is a snapshot. The heartbeat fires every 2hr and forces a fresh fetch
before any "still waiting" claim. Per operator's explicit standing rule:
"must check latest instead of just wake up." Silent ≠ normal; PID alive + log
frozen ≠ normal.

Script the heartbeat fires:

```bash
OUT=$CLAUDE_JOB_DIR/heartbeat_$(date +%s).txt
{
echo "=== TS ==="; date
echo "=== PR #358 + #359 state ==="
gh pr view 358 --json state,statusCheckRollup,mergedAt --jq '{s:.state,m:.mergedAt,checks:([.statusCheckRollup[]?|.conclusion]|group_by(.)|map({k:.[0],n:length}))}'
gh pr list --state all --head "claude/refactor-auth-econ-split-pr3" --json number,state,mergedAt
echo "=== main HEAD ==="
git ls-remote origin main | awk '{print $1}'
echo "=== worktree HEAD + ahead/behind ==="
cd /Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/ens-bias-hierarchical
git rev-parse --short HEAD
git fetch origin main --quiet
git rev-list --count HEAD..origin/main
git rev-list --count origin/main..HEAD
echo "=== daemons ==="
launchctl list | grep com.zeus
echo "=== active rebuild/refit PIDs ==="
pgrep -afl "rebuild_calibration_pairs_v2|fit_full_transport|selective_refit_from_manifest|refit_platt_v2"
echo "=== log byte delta (forecast-live last hour) ==="
LATEST=/Users/leofitz/.openclaw/workspace-venus/zeus/logs/zeus-forecast-live.log
[ -f "$LATEST" ] && { stat -f "%Sm %z" "$LATEST"; }
echo "=== disk ==="
df -h /Users/leofitz/.openclaw/workspace-venus/zeus/state | head -2
echo "=== FT flag ==="
grep -nE 'full_transport_live_enabled' /Users/leofitz/.openclaw/workspace-venus/zeus/config/settings.json | head -1
} > "$OUT" 2>&1
/bin/cat "$OUT" | base64
```

**Then orient against §3 table and act**.

---

## 8 — Failure modes that demand immediate cascade (do not normalize)

- **M2 rebuild silent for >2hr** (PID alive + no log byte-delta): silent-death suspect. Sample stack via `/usr/bin/sample`; root-cause; do NOT claim "still running."
- **Post-M5 unshadow 4h zero-trade**: DEFECT cascade — check daemon → candidates → gates → edge → bias.
- **#359 merged + my worktree fails to rebase cleanly**: do NOT force-push; isolate conflicts on a new branch, ask operator before reconciling.
- **#359 merged + post-rename, B1-B7 patches don't apply**: re-apply edits manually, re-run B-test suite to confirm, push. Antibody preserved.
- **Disk falls below 5 GiB**: HALT M2; clean before proceeding (operator memory: `project_deep_housekeeping_2026_05_23.md`).
- **forecast-live PID disappears (not just status=1)**: actual death. Read `zeus-forecast-live.err`, root-cause; do not `launchctl kickstart` blindly.
- **Live SHADOW emits orders during M3 wait**: structural failure of the FT-flag-OFF + kill-switch contract. Pause live-trading immediately, root-cause.

---

## 9 — Living document policy

- Each heartbeat that materially changes state → update §3 inline (one line per task).
- B-series antibody once-only: do not re-do.
- If operator changes direction, replace §2 entirely; preserve §1 (history).
- Memory: any new feedback worth surviving compaction → `~/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/memory/`, link from MEMORY.md.

---

**End of doc. Resume work in §3 task table.**
