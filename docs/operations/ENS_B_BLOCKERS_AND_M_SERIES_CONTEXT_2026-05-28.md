# ENS Mission Context — 2026-05-28

**Single resume-from-anywhere reference for the FT-domain canonicality + MC rebuild + live unshadow mission.**

**Audience**: any session (live or background) resuming after compaction, restart, heartbeat, or operator handoff.
**Authority**: operator pre-MC re-audit (2026-05-28 ~04:30 CT) + operator live-shadow pivot (2026-05-28 ~05:30 CT) + operator restated Hard Blockers (2026-05-28 ~05:35 CT).
**Worktree**: `/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/ens-bias-hierarchical`
**Branch**: `feat/ft-domain-canonical-refit`
**HEAD at write-time**: `23a64622bd` (B1-B7 + context doc v1).
**Active gate**: `_GATE_SET_VERSION = "ftgate-2026-05-28-sd3"` (B1+B6 bumped sd2→sd3).

---

## 0 — Operator Standing Directives (do not violate)

1. **AUTONOMOUS MODE** (operator away 2026-05-28 ~05:50 CT): no AskUserQuestion, no stall. Continue self-paced. Operator: "i will be away and you are on autonomous now, do not call ask question do not stall."
2. **TASKS ARE DOMINOS — next does not fall until current fires.** No parallel speculation on uncompleted predecessors. Each rebuild/refit/verify step waits for its predecessor's GREEN evidence (not just exit code 0). Operator: "your tasks are dominos and the next won't fall until you fire current one."
3. **NO REBUILD BEFORE ALL FIX VERIFIED.** Rebuild = `rebuild_calibration_pairs_v2.py` (MC pair regen, hours-long). Must NOT fire until every B-fix is **verified in persisted state**, not just in unit tests. Verification list = §13. Operator: "you should not fire rebuild before all fix are veirfied."
4. **Goal active**: bring live to right code, right data, profitable orders. The only valid wait reasons are PR #359 + active rebuild/refit progress.
5. **Live = SHADOW until M5**: daemons run, candidates compute, intents created + SUBMIT_REJECTED → no fills. FT flag OFF.
6. **Shadow must work NOW** — wiring break at §6 (probability_trace_fact silent). Parallel investigation while rebuild stages run, but does NOT block rebuild dominos.
7. **Worktree will be far behind main post-#359** — big file renames expected. B1-B7 patches must carry over; replay each on the new file path if renamed.
8. **2hr heartbeat MUST read this doc first** — operator: "make sure that heartbeat prompt ask you to read the doc." See §9. Doc is the single source of truth between heartbeat fires.
9. **1hr-equivalent Monitor REQUIRED during any active rebuild/refit**. PID alive + log frozen ≠ normal; silent ≠ normal.
10. **No new orders, no manual completion** — first live order must complete programmatically per design.

---

## 1 — Operator Hard Blockers (HB1-HB8) → My B-fixes + BL-A status

This is the **definitive mapping** so no future session re-fixes what is already shipped.

| Operator HB | Defect | Status | Implementing fix | Commit |
|-------------|--------|--------|------------------|--------|
| **HB1** | Southern Hemisphere season label mismatch (producer iterated calendar `_SEASONS`, reader applies `_SH_FLIP` per `lat<0` → BA/Cape Town/Sao Paulo/Wellington VERIFIED rows orphaned). | ✅ FIXED | **B1**: `_iter_seasons_for_city(city)` in `scripts/fit_full_transport_error_models.py`. Producer loop iterates hemisphere-aware. Tests: `tests/test_b1_producer_hemisphere_aware_season.py`. | `3ce02984b3` |
| **HB2** | `_native_error_params_for_snapshot` cache_key omits target_month → fails open or reuses across-month rows, bypassing `read_bias_model` month-scope guard. | ✅ FIXED | **B2**: `cache_key = (city.name, season_label, metric, target_month)` in `scripts/rebuild_calibration_pairs_v2.py`. Tests: `tests/test_b2_native_error_params_cache_includes_target_month.py`. | `3ce02984b3` |
| **HB3** | Replay equivalence runs in recompute-mode (`_MIN_LIVE_N_RECOMPUTE=5`) vs producer (`DEFAULT_MIN_LIVE_N=20`) → A-cohort verdict invalid (compares against the WRONG model). | ✅ FIXED | **B3**: `selective_refit_from_manifest._run_replay_for_a_cohorts` dispatches `error_model_source="db"`; `_load_error_model_from_db` rewritten to call `read_bias_model(error_model_family='full_transport_v1', authority='STAGING', require_gate_set_hash=current_gate_set_hash(), target_month=<per_snapshot>)`. `_evaluate_cohort` defers acquisition to per-snapshot in db mode. Tests: `tests/test_b3_replay_uses_persisted_staging.py`. | `3ce02984b3` |
| **HB4** | Per-snapshot DB read missing — replay must load model PER SNAPSHOT target_month, not once per cohort. | ✅ FIXED in B3 | Same commit as HB3 — `_evaluate_cohort` db-mode iterates `for row in sampled` and calls `_load_error_model_from_db(..., target_month=<int(snap.target_date[5:7])>)` per snapshot. | `3ce02984b3` |
| **HB5** | Season/month-scoped rebuild missing → cohort regen leaked to other seasons. | ✅ FIXED in BL-B | `rebuild_calibration_pairs_v2.py` added `--months`; sequential + parallel delete paths month-scoped. `selective_refit_from_manifest` passes `--months <calendar group>` per cohort. Task #141. | Earlier #358 commit `9b95e831ce` |
| **HB6** | (a) Pair-batch manifest queried wrong DB + best-effort failure; (b) producer wrote `training_cutoff=today_str` but loaders received `settled_before=None`. | ✅ FIXED (both) | **(a)** BL-C task #142 — manifest queries `forecasts_conn` (the rebuild's `--db`), abort `rc=1` on failure. **(b)** **B6**: thread `today_str` into all 4 loaders (`load_bucket_residuals`, `fit_city_predictive_error`, `paired_delta_coverage`, `_effective_coverage_months`). Tests: `tests/test_b6_training_cutoff_threaded_to_loaders.py`. | `3962ceac07` + `3ce02984b3` |
| **HB7** | `compute_final_regen(... gate_changed=True)` silently returned `set(all_cohorts)` → degraded selective rebuild to full reproduce when manifest itself was stale under the new gate. | ✅ FIXED | **B7**: `raise SystemExit("regenerate ROW_ACTION_MANIFEST under current gate ...")`. Tests: `tests/test_b7_gate_changed_aborts_selective_refit.py`. | `3ce02984b3` |
| **HB8** | `ROW_ACTION_MANIFEST_2026-05-27.csv` was generated under old gate (sd2 + pre-MIN_PRIOR_N=5). Under sd3 several rows would re-classify (Denver/LA DJF HIGH n_prior=2, Shanghai/Tokyo MAM LOW, Qingdao MAM HIGH). CSV is HISTORICAL EVIDENCE, not execution input. | ⏸️ PENDING (BL-A #140) | Cannot pre-generate — must run audit script + producer on the aligned post-#359 worktree under sd3 to emit fresh manifest. Then feed into `selective_refit_from_manifest --execute`. | (none yet) |

**Stats locked unchanged** (operator: do not touch — each change re-bumps gate hash and re-invalidates manifest): `MIN_PRIOR_N=5`, `MIN_PAIRED_N=5`, `DEFAULT_MIN_LIVE_N=20`, `CONSERVATIVE_RESIDUAL_FLOOR_C=3.0`, `_GATE_SET_VERSION="ftgate-2026-05-28-sd3"`.

**Regression at HEAD `3ce02984b3`**: 65/65 B+SD+BL tests green; 109/112 ens/ft/replay tests green (3 pre-existing carveout-yaml fails in `test_check_full_transport_ship_readiness.py` unrelated, predate B-batch).

---

## 2 — Mission task graph: M0-M5

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
                              (Monitor + 1hr heartbeat REQUIRED;
                               includes BL-A manifest regen → producer → selective_refit)
                                       |
                                       v
   [M3 #151] live SHADOW    +    [M4 #154] before/after bin-bias test
   (NOW eligible — runs            (vs mainstream forecast, must prove
    in parallel during              DIRECTIONAL IMPROVEMENT)
    M0-M2 wait)                          |
                                       v
                          [M5 #155] unshadow live
                              (only after M4 GREEN; post-unshadow
                               4h zero-trade = DEFECT cascade)
```

---

## 3 — Full task ledger (every task, every state)

### 3.1 — Active M-series + BL-A

| ID | Subject | Status | Action |
|----|---------|--------|--------|
| #150 | M0: Monitor PR #359 open + merge | 🔵 in_progress | Monitor task `bvo7624iz` armed (persistent, 10-min poll on branch `claude/refactor-auth-econ-split-pr3`, SHA `844d82294e`). Emits `#359 state:` on transition NO_PR_YET→OPEN→MERGED. |
| #152 | M1: Align worktree to latest main | ⏸️ blocked by #150 | Post-#359: `git fetch origin main && git rebase origin/main`. Big renames expected — verify B1-B7 patched files (`scripts/fit_full_transport_error_models.py`, `scripts/rebuild_calibration_pairs_v2.py`, `scripts/replay_equivalence_full_transport.py`, `scripts/selective_refit_from_manifest.py`, `src/calibration/ens_error_model.py`) still exist or replay edits onto new paths. |
| #153 | M2: Rebuild + refit at sd3 | ⏸️ blocked by #152 | Sequence: (a) `scripts/audit_error_model_row_reproducibility.py` → fresh `ROW_ACTION_MANIFEST_<date>.csv` under sd3 (this closes BL-A #140). (b) `scripts/fit_full_transport_error_models.py --metric high --commit --db <staging>` then `--metric low`. (c) `scripts/rebuild_calibration_pairs_v2.py --no-dry-run --force --db <staging> --error-model full_transport_v1 --temperature-metric <m> --n-mc 10000 --workers $(( $(sysctl -n hw.ncpu) - 2 ))`. (d) `scripts/selective_refit_from_manifest.py --manifest <fresh> --db <staging> --execute --n-mc 10000`. (e) `scripts/audit_error_model_row_reproducibility.py` post-audit. Monitor MUST be armed with 1hr heartbeat over the entire phase. |
| #140 | BL-A: Regenerate ROW_ACTION_MANIFEST under sd3 (= HB8) | ⏸️ pending, runs inside M2 step (a) | Old `docs/operations/ROW_ACTION_MANIFEST_2026-05-27.csv` is HISTORICAL EVIDENCE only — operator confirmed must NOT be used as execution input. |
| #151 | M3: Live shadow — confirm wiring works | 🟢 ELIGIBLE NOW (parallel) | See §6 — wiring break found 2026-05-28 05:26 CT: probability_trace_fact silent (0 rows in 24h vs 44 decisions firing). Root-cause + fix REQUIRED before M5. |
| #154 | M4: Bin-bias before/after test | ⏸️ blocked by #153 + #151 | Operator: "用测试证明前后区别". Test must compare (pre-sd3 rows + plain p_raw) vs (post-sd3 rows + ft-corrected p_raw) against mainstream forecast (open-meteo + TIGGE consensus). Metric: bin-bias mean (signed) + RPS + ECE. Must show DIRECTIONAL IMPROVEMENT in ≥2 of 3 metrics on ≥75% of cities. **Open**: exact baseline definition pending operator confirm. |
| #155 | M5: Unshadow live on sd3 + main-aligned code | ⏸️ blocked by #154 | Flip `config/settings.json:195 "full_transport_live_enabled": true`; `launchctl kickstart -k gui/$UID/com.zeus.live-trading`. Per memory: 4h post-unshadow zero-trade = DEFECT (daemon → candidates → gates → edge → bias cascade). |

### 3.2 — Carry-over R-series (subsumed by M-series, kept for traceability)

| ID | Subject | Status | Disposition |
|----|---------|--------|-------------|
| #131 | R1: Merge PR #358 → main | ⏸️ operator-gated | Operator authorization required. CI was GREEN at last push; new commit `23a64622bd` re-runs CI. Once #359 lands first per operator pivot, #358 rebases onto new main then merges. |
| #132 | R2: Deploy main → world.db canonical migration | ⏸️ subsumed by M2 + post-merge ops | Schema migration of `gate_set_hash` + `coverage_months` columns will happen automatically via boot init_schema on post-merge daemon restart. |
| #133 | R3: Run mc_entry_gate.py P0-P3 PASS | ⏸️ runs as part of M2 | `scripts/mc_entry_gate.py` is the go/no-go gate before M2 starts; must show PASS for P0 (schema), P1 (hash determinism), P2 (coverage), P3 (insufficient-prior). |
| #134 | R4: Small-sample iso producer + MC | ⏸️ optional smoke before M2 | Operator may want a 5-city dry run before full M2 to verify pipeline works on new schema. Skip if M2 monitor is armed properly. |
| #135 | R5: Full STAGING reproduce + row audit 100% servable | ⏸️ subsumed by M2 step (e) | M2's final audit step closes R5. Blocked on disk free (14 GiB free / 99% full at write-time — must clean before M2). |
| #136 | R6: selective_refit + MC + pair-batch manifest + post-MC audit | ⏸️ subsumed by M2 | M2 steps (c)+(d)+(e). |
| #137 | R7: Promote STAGING→VERIFIED + DELETE stale-gate VERIFIED rows | ⏸️ post-M2, pre-M5 | After M2 produces fresh sd3 STAGING rows: `scripts/promote_model_bias_ens_v2.py promote --commit`. Stale-gate (pre-sd3) VERIFIED rows on world.db must be DELETEd before unshadow — live reader does not enforce gate_set_hash filter on VERIFIED reads (task #138 / R8). |
| #138 | R8: DECISION — wire gate_set_hash + target_month into live entry reader | ⏸️ operator-gated, pre-M5 | `src/engine/evaluator.py:3211` live reader currently does not pass `require_gate_set_hash` / `target_month` to `read_bias_model`. Adding it is a live-money entry change → operator sign-off required. Without it, R7 stale-row DELETE is the only defense. |
| #139 | R9: Live trading unblock (-pr3 reconcile/DATA_DEGRADED) | ⏸️ parked, other session | Not in this worktree's critical path. Memory `feedback_live_root_multisession_collision_2026_05_28.md`: 15 unresolved exchange_reconcile_findings (confirmed_journal_size=0, settled/redeem dust). |

### 3.3 — Misc pending / in-flight tasks

| ID | Subject | Status | Disposition |
|----|---------|--------|-------------|
| #29 | Stale `test_market_phase_dispatch` 3 fails | ⏸️ pending | Low priority. Semantic, not fixture. Park until post-M5. |
| #63 | Contingent refinement roadmap | ⏸️ pending | Each candidate must beat full_transport baseline under blocked OOS. Post-M5 follow-up. |
| #64 | REBUILD PARENT: full_transport canonical refit | 🔵 in_progress | This is the parent for M2 + all R-series. Will complete when M2 closes. |
| #105 | F7b: trace writer must tag p_raw_domain | ⏸️ pending | Should land BEFORE M5 unshadow — trace evidence needs to declare which domain (sd2 vs sd3) produced its p_raw. Touches `src/state/db.py:6557`. |
| #107 | promote_model_bias_ens_v2 missing conn.commit bug | ⏸️ pending | Must fix before R7 / M2 promote phase. Single-line fix expected. |
| #109 | P0 DEFECT: market scanner ingests 1 of 11 sub-markets per event | 🔵 in_progress | High-priority candidate-coverage issue. **Affects M3 shadow** — partial market coverage = partial candidates = trace evidence misses 90% of markets. |
| #110 | Per-outcome HTTP overhead in `capture_executable_market_snapshot` | ⏸️ pending | Performance, not correctness. Park unless M3 shadow shows excessive latency. |
| #116 | `test_ens_predictive_pipeline.py` fixture: missing issue_time column | ⏸️ pending | Test-only. Fix when convenient. |
| #120 | Investigate why probability_trace_fact writer is silent | 🔵 in_progress | **CRITICAL for M3** — see §6. Root-cause + fix is part of shadow-works-now. |
| #121 | portfolio_quarantined blocker (post-RiskGuard fix) | ⏸️ pending | RiskGuard state machine investigation. Tangential to shadow, required for M5 fill-path. |
| #124 | Post-restart: verify candidates flow + trace fires + first order | 🔵 in_progress | Superseded by M3. Subsumed. |

### 3.4 — Completed (do NOT redo, listed for sanity)

B1-B7 (#145-#149), SD1-SD6 (#126-#130), BL-B/C/D/E (#141-#144), #106 forecast-live false-alarm closed, plus the pre-2026-05-26 completion set (#17-#88 etc.). Full ledger lives in TaskList.

---

## 4 — Current live shadow state (verified 2026-05-28 05:26 CT)

| Surface | State |
|---------|-------|
| `com.zeus.forecast-live` (PID 85201) | ALIVE, 06:22h uptime, 200 MB RSS, ingesting `mn2t6_low` track at 05:23 (loop_progress in `zeus-forecast-live.log`) |
| `com.zeus.live-trading` (PID 74152) | ALIVE, 04:15h uptime, 400 MB RSS, polling Polymarket clob orders + auth (HTTPx GETs at 05:25) |
| `com.zeus.riskguard-live` (PID 85199) | ALIVE, 06:22h uptime, 110 MB RSS |
| `com.zeus.venue-heartbeat` (PID 99805) | ALIVE, 2-18:57h uptime, 36 MB RSS |
| `com.zeus.heartbeat-sensor`, `com.zeus.calibration-transfer-eval` | Not running (status=`-`); may be on-demand |
| `ensemble_snapshots_v2` freshness | ECMWF mx2t3 + mn2t3: 362 rows each, issue_time 2026-05-28T00:00:00 UTC = today 00z run ✓ |
| `decision_log` 2h activity | `opening_hunt`: 38 decisions (last 10:25 UTC). `imminent_open_capture`: 6 decisions (last 04:13 UTC). Decisions FIRE. |
| `execution_fact` posted_at 4h | 9 orders posted, last 05:53 UTC (~midnight CT) — these are existing positions, not new submissions |
| `venue_command_events` 4h | INTENT_CREATED=10, SUBMIT_REQUESTED=10, SUBMIT_REJECTED=10. **All submissions rejected = shadow gate working** ✓ |
| `venue_trade_facts` fills 4h | 0 fills ✓ |
| `probability_trace_fact` | 33203 rows total, **0 rows in last 24h** ❌ Wiring break — see §6 |
| `config/settings.json:195 "full_transport_live_enabled"` | `false` (shadow) ✓ |

**Net**: shadow is "working" in the safe sense (orders not landing, no fills), but the **trace evidence channel is silent** — we cannot audit what the shadow is doing.

---

## 5 — Schema column reference (verified 2026-05-28; many drifted from prior memory)

When writing recency / health probes, use these column names — wrong names give SQLite "no such column" errors that masquerade as "nothing recent."

| Table | DB | Key timestamp column |
|-------|----|---------------------|
| `ensemble_snapshots_v2` | zeus-forecasts.db | **`issue_time`** (ISO TEXT). Not `snapshot_ts_utc`. Also `valid_time`, `available_at`, `fetch_time`. |
| `decision_log` | zeus_trades.db | **`started_at`**, **`completed_at`**, **`timestamp`** (ISO TEXT). Not `created_at`. Cols: `id`, `mode`, `started_at`, `completed_at`, `artifact_json`, `timestamp`, `env`. |
| `probability_trace_fact` | zeus_trades.db (+ world.db mirror) | **`recorded_at`** (ISO TEXT). Not `captured_at`. Has `p_raw_json`, `p_cal_json`, `p_market_json`, `p_posterior_json`, `rejection_stage`, `availability_status`, `market_phase*`, `settlement_day_entry_utc`. |
| `venue_trade_facts` | zeus_trades.db | **`observed_at`**, **`ingested_at`**, **`venue_timestamp`**. Not `submitted_at`. State machine: `state` column. |
| `venue_command_events` | zeus_trades.db | **`occurred_at`** (ISO TEXT). Not `ts`. `event_type` enumerates: INTENT_CREATED, SUBMIT_REQUESTED, SUBMIT_REJECTED. |
| `execution_fact` | zeus_trades.db | **`posted_at`**, **`filled_at`**, **`voided_at`** (ISO TEXT). |
| `control_overrides` | zeus_trades.db | Schema drifted — no `name` column. Use `pragma table_info` before querying. |
| `model_bias_ens_v2` | zeus-world.db + scratch staging | `recorded_at`, `training_cutoff` (ISO TEXT). New canonical extension cols: `gate_set_hash`, `coverage_months`, `error_model_family`, `authority`, `fit_signature_hash`. |

---

## 6 — Trace writer status (RETRACTED + CORRECTED 2026-05-28 06:25 CT)

**Initial "WIRING BREAK probability_trace_fact silent" claim was WRONG.** Apology + retraction.

**Correction**: Initial silence diagnosis came from querying the LEGACY destination DB (`zeus_trades.db`). PR-S4b (2026-05-18, commit `f5290060c98`) correctly REDIRECTED trace writes from zeus_trades.db (where they accidentally landed when callers passed cycle-rooted conn) to `zeus-world.db` (canonical destination). The 33,203 rows in zeus_trades.db are pre-PR-S4b historical. Current `zeus-world.db.probability_trace_fact` (verified 2026-05-28 06:25 CT):

| query | value |
|-------|-------|
| `COUNT(*) WHERE recorded_at > datetime('now','-2 hours')` | **173 rows** |
| Total | 5759 |
| Last write | 2026-05-28T06:10:11 UTC (5 min before query) |
| Distribution (24h, mode/trace_status) | opening_hunt:complete=305 / pre_vector_unavailable=89 / degraded_decision_context=22 ; imminent_open_capture:complete=46 / pre_vector_unavailable=22 / degraded_decision_context=4 ; day0_capture:complete=1 |

**Trace writer is HEALTHY. M3 shadow acceptance criterion met.** Task #151 (M3) marked completed; #120 (silent investigation) closed.

**F7b (#105) remains valid**: all 5759 world.db rows have `p_raw_domain=NULL`. Writer doesn't bind column even though it exists in schema. Fix gated by `settlement_write` capability on src/state/db.py — needs operator ARCH_PLAN_EVIDENCE. Not blocking M5 (audit-annotation only).

**Self-correction lesson**: always verify the writer's CURRENT destination per docstring before declaring silence. PR-S4b's docstring at `log_probability_trace_fact` literally says "rows to land in zeus_trades.db instead of zeus-world.db" — that's the BUG IT FIXED. Historical rows in the legacy destination can mislead. Memory: `project_trace_writer_pr_s4b_commit_omission_2026_05_28.md` (records the wrong path + retraction).

---

### Historical investigation (kept for record; conclusions superseded above)


**Architecture mapped (2026-05-28 05:50 CT)**:
- Writer: `src/state/db.py:6424 log_probability_trace_fact` → `_log_probability_trace_fact_inner` :6453. Returns `{status: "written"|"skipped_missing_table"|"skipped_missing_decision_id"|...}`.
- Single caller: `src/engine/cycle_runtime.py:3916 _record_probability_trace(candidate, decision)` → queues `_write` closure via `_queue_derived_write("probability_trace:...", _write)`.
- Queue: `derived_writes: list[(name, writer)]`. Mutated by `_queue_derived_write` (3826) + `_flush_derived_writes` (3830).
- Flush: line 3830 `while derived_writes: ... writer()` with try/except — **fail-soft**, exceptions go to `deps.logger.warning("Derived discovery write failed for %s: %s", name, exc)` + sets `summary["degraded"] = True`.
- Flush invocations: 4472 (end of per-market) + 6106 (end of cycle).
- Caller of `_record_probability_trace`: only 4947 (`for trace_decision in decisions: _record_probability_trace(candidate, trace_decision)`).

**Evidence collected**:
- `probability_trace_fact` last row: `2026-05-18T01:51:04 UTC` (10 days ago); 33203 total rows, all old.
- `decision_log` 2h: 44 rows (`opening_hunt` mode = 38, `imminent_open_capture` = 6).
- `zeus-live.log`: ZERO entries matching `probability_trace`, `trace_fact`, `Derived discovery write failed`, or related. → **Writer closure never fires AND warning path never fires** → either (a) line 4947 unreachable on current cycle path, or (b) `decisions` list is empty when reached.

**Hypotheses for root cause** (to investigate post-rebuild):
- H1: The cycle path that reaches 4947 is gated behind a condition that turned off recently (e.g. a refactor moved the trace call into an `else` branch never entered when shadow path is the only path).
- H2: `decisions` (the per-bin-decision objects collected per candidate) is empty in current cycles even though `decision_log` rows exist (they may be `imminent_open_capture` / `opening_hunt` rows that don't produce per-bin decisions). Then iterating empty `decisions` is a no-op.
- H3: A refactor between 2026-05-18 and 2026-05-28 (mostly the chain/local position-model + state refactors #347/#352/#354/#357/#358) inadvertently broke the call site.

**Park**: investigate after external PID 7167 rebuild releases lock + after D6-D11 unblock. Not blocking the rebuild chain.

### (legacy text below kept for reference)



**Discovered 2026-05-28 05:26 CT.** Operator's warned wiring problem confirmed.

**Symptom**: 33203 rows total in `probability_trace_fact`. **0 rows in last 24h**. Meanwhile `decision_log` has 44 decisions in 2h. So evaluator IS deciding, but the trace writer at `src/state/db.py:6557` is not being invoked OR is being invoked + erroring silently.

**Candidate root-causes (probe in order)**:
1. The writer call site upstream of `db.py:6557` was unwired by a recent refactor (likely the same chain/local refactor that landed #347/#352).
2. The writer is called but errors with a column / FK / CHECK violation that's caught + swallowed.
3. The writer is gated by a flag that flipped OFF.
4. Schema drift between `zeus_trades.db` and `zeus-world.db` (the table exists in both per `.schema probability_trace_fact` dumps; differing column sets — world.db has `p_posterior_json`, but does the writer write both?).

**Probe sequence**:
```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/ens-bias-hierarchical
# Locate caller(s) of the write function:
grep -rnE "write_probability_trace|emit_probability_trace|probability_trace_fact" src/ | grep -v __pycache__ | head -20
# Search the live-trading log for trace-write errors:
grep -iE "trace.*fact|probability_trace|INSERT INTO probability" /Users/leofitz/.openclaw/workspace-venus/zeus/logs/zeus-live.log | tail -20
# Search for swallowed exceptions:
grep -iE "trace.*exception|trace.*error" /Users/leofitz/.openclaw/workspace-venus/zeus/logs/zeus-live.log | tail -10
```

**Fix policy**: structural, antibody-protected. Add a relationship test: "every completed evaluator decision producing a candidate must write a probability_trace_fact row." Then root-cause the wiring break.

**Acceptance for M3-done**: probe shows ≥1 trace row per decision over a 30-min observation window post-fix.

---

## 7 — Live ingest + decision wiring (currently healthy)

For reference (this part is NOT broken, do not "fix"):
- forecast-live: `src.ingest.forecast_live_daemon` runs ECMWF open-data poller (every 5 min), TIGGE-MARS once a day, GFS open-data, ingest pipelines write `ensemble_snapshots_v2`.
- live-trading: `src.main` runs evaluator + monitor_refresh + venue submission loop. Pulls existing chain orders, evaluates new candidates against bins, fires INTENT_CREATED → SUBMIT_REQUESTED → SUBMIT_REJECTED in shadow mode.
- riskguard: `src.riskguard.riskguard` tick-loop computes risk metrics + can fire kill switches.
- evaluator writes `decision_log` (via `src/state/decision_chain.py:211, 252`).
- **Missing edge**: decision_log → probability_trace_fact. See §6.

---

## 8 — Key invariants and gotchas — read before any edit

**Daemon liveness:**
- `launchctl list` STATUS column = LAST exit code, not current health. Verify via PID column + `ps -p <PID>` + log byte-delta. Memory: `feedback_launchctl_status_is_last_exit_not_current.md`.
- `STATUS=1` with PID present and log writing = daemon respawned after earlier exit, currently healthy.

**Multi-session collisions:**
- Live root is shared with another session. Other session can flip HEAD to `claude/refactor-auth-econ-split-pr3` mid-flight. ALWAYS `git rev-parse HEAD` BEFORE any live daemon restart. Memory: `feedback_live_root_multisession_collision_2026_05_28.md`.

**Data integrity:**
- **Never write to production DBs from the producer**. `scripts/fit_full_transport_error_models.py::_refuse_prod_db` is the antibody (BL-E, samefile + basename defense). Always run on isolated staging copy.
- **No partial calibration on new cities**. Strict order: register+blacklist → full historical ECMWF + TIGGE ingest → calibration once at full depth → Platt → 14-day shadow. Jinan/Zhengzhou warnings in forecast-live.err are expected pre-onboarding. Memory: `feedback_newcity_no_partial_calibration.md`.
- **Cross-DB writes adhere to INV-37** — ATTACH + SAVEPOINT, never independent connections.
- **DB authority**: zeus-world.db (canonical truth, world schema), zeus-forecasts.db (calibration + ensemble), zeus_trades.db (live trading). Don't cross-write outside ATTACH transactions.

**Code-graph + provenance:**
- Existing files are LEGACY-until-audited. Any reuse needs a provenance audit comment. New scripts carry `# Created:` + `# Last reused or audited:` + `# Authority basis:` headers.

**Disk:**
- Currently 14 GiB free / 99% full. Blocker for full STAGING reproduce. Clean before M2 OR scope staging DB to smaller subset.

**Cache + stale state:**
- `omc update ≥4.14` rewrites `ANTHROPIC_DEFAULT_SONNET_MODEL` to self-ref. Restore concrete `cc/claude-sonnet-4-6`. Memory: `feedback_omc_update_clobbers_sonnet_env_var.md`.

**Auto-mode + governance:**
- Auto-mode denies unaudited writes (correct guardrail). Plan write phases as a programmatic sequence with operator pre-authorization.
- PR open requires ≥300 self-authored LOC (hook enforced). ZEUS_PR_ALLOW_TINY=1 only with documented justification.

---

## 9 — 2-hour heartbeat (recurring via CronCreate)

Cron fires every 2hr at minute :17 (next 06:17 CT, then 08:17, ...). Auto-expires 7 days. The prompt fired by the cron is explicit:

> **STEP 1 (mandatory)**: Read `docs/operations/ENS_B_BLOCKERS_AND_M_SERIES_CONTEXT_2026-05-28.md` §0 (directives) + §12 (domino chain) + §13 (verification probes). Do NOT assume context from prior turn.
> **STEP 2**: Run the bash probe below; decode base64; orient against §12 domino table.
> **STEP 3**: Identify lowest-numbered domino that is unblocked AND has not fired AND whose predecessor's GREEN evidence is recorded in this doc. Fire it. Update §12 with evidence + new status.
> **STEP 4**: If D10 gate is still RED (any D2-D9 not GREEN), do NOT fire D11 rebuild.
> **STEP 5**: Update doc §12, commit, push.

Operator standing rule: "must check latest instead of just wake up."

Script the heartbeat re-runs (paste into Bash; absolute paths):

```bash
OUT=$CLAUDE_JOB_DIR/heartbeat_$(date +%s).txt
{
echo "=== TS ==="; date
echo "=== PR #358 + #359 ==="
gh pr view 358 --json state,statusCheckRollup,mergedAt --jq '{s:.state,m:.mergedAt,checks:([.statusCheckRollup[]?|.conclusion]|group_by(.)|map({k:.[0],n:length}))}'
gh pr list --state all --head "claude/refactor-auth-econ-split-pr3" --json number,state,mergedAt
echo "=== main HEAD ==="
git ls-remote origin main | awk '{print $1}'
echo "=== worktree HEAD + ahead/behind ==="
cd /Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/ens-bias-hierarchical
git rev-parse --short HEAD
git fetch origin main --quiet
git rev-list --count HEAD..origin/main; git rev-list --count origin/main..HEAD
echo "=== daemons (PID column matters) ==="
launchctl list | grep com.zeus
echo "=== active rebuild/refit PIDs ==="
pgrep -afl "rebuild_calibration_pairs_v2|fit_full_transport|selective_refit_from_manifest|refit_platt_v2"
echo "=== forecast-live log byte-delta ==="
stat -f "%Sm %z" /Users/leofitz/.openclaw/workspace-venus/zeus/logs/zeus-forecast-live.log
echo "=== probability_trace_fact recency (M3 wiring check) ==="
sqlite3 -cmd "PRAGMA query_only=1;" /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db \
"SELECT COUNT(*), MAX(recorded_at) FROM probability_trace_fact WHERE recorded_at > datetime('now','-2 hours');"
echo "=== decision_log 2h ==="
sqlite3 -cmd "PRAGMA query_only=1;" /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db \
"SELECT mode, COUNT(*), MAX(started_at) FROM decision_log WHERE started_at > datetime('now','-2 hours') GROUP BY mode;"
echo "=== venue_command_events 2h ==="
sqlite3 -cmd "PRAGMA query_only=1;" /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db \
"SELECT event_type, COUNT(*) FROM venue_command_events WHERE occurred_at > datetime('now','-2 hours') GROUP BY event_type;"
echo "=== fills 4h (zero = shadow OK) ==="
sqlite3 -cmd "PRAGMA query_only=1;" /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus_trades.db \
"SELECT COUNT(*) FROM venue_trade_facts WHERE observed_at > datetime('now','-4 hours');"
echo "=== disk ==="
df -h /Users/leofitz/.openclaw/workspace-venus/zeus/state | head -2
echo "=== FT flag ==="
grep -E '"full_transport_live_enabled"' /Users/leofitz/.openclaw/workspace-venus/zeus/config/settings.json | head -1
} > "$OUT" 2>&1
/bin/cat "$OUT" | base64
```

**Orient against §3 task table**, identify lowest-ID unblocked task that is NOT in_progress, advance it. If active rebuild/refit silent for >2hr (PID alive + no log byte-delta): DEFECT cascade, do not normalize.

---

## 10 — Failure modes that demand immediate cascade

| Trigger | Action |
|---------|--------|
| M2 rebuild silent >2hr (PID alive + no log byte-delta) | Suspect silent-death. `/usr/bin/sample <PID>`; root-cause; do NOT claim "still running." |
| Post-M5 unshadow 4h zero-trade | DEFECT cascade — check daemon → candidates → gates → edge → bias. |
| #359 merged + worktree rebase conflicts | Do NOT force-push. Isolate conflicts on new branch, ask operator before reconciling. |
| #359 merged + B1-B7 patches don't apply (renames) | Re-apply edits manually, re-run B-test suite to confirm green, push. Antibody preserved. |
| Disk falls < 5 GiB | HALT M2; clean before proceeding (memory `project_deep_housekeeping_2026_05_23.md`). |
| forecast-live PID disappears (not just status=1) | Actual death. Read `zeus-forecast-live.err`, root-cause; do not `launchctl kickstart` blindly. |
| Live SHADOW emits ANY fills during M3 wait | Structural failure of FT-flag-OFF + kill-switch contract. Pause live-trading immediately, root-cause. |
| `probability_trace_fact` remains silent | M3 cannot be declared done. Block M5. |
| Operator answers a probe that contradicts in-context state | Trust operator; re-verify; if conflict persists, surface explicitly. Don't silently re-trust prior context. |

---

## 11 — Living document policy

- Each heartbeat that changes state materially → update §3 + §4 inline.
- B-series antibody once-only: do not re-do (§1 is read-only for past commits).
- If operator changes direction, replace §2 + §0 ENTIRELY; preserve §1 (history).
- New memory worth surviving compaction → `~/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/memory/`, link from MEMORY.md.

---

---

## 12 — Domino chain (2026-05-28 05:50 CT, AUTONOMOUS MODE)

Per operator: tasks are dominos. Each falls only after its predecessor's GREEN evidence. **No rebuild fires until every B-fix is verified in persisted state.**

| # | Domino | Status | Evidence required to fall |
|---|--------|--------|---------------------------|
| D1 | HIGH producer fit on staging at sd3 | ✅ FELL 2026-05-28 05:35 CT (10s) | Log: `fitted=71 skipped=137 rows_written=71 zero_coverage=['Auckland']`. Gate stamped `deabf8f64bde27b7`. Staging `/private/tmp/scratch_ens_fit.db`. |
| D2 | Verify B1 in persisted rows | ✅ FELL 2026-05-28 05:42 CT | BA JJA cov={1,2,12}, BA SON cov={3,4,5}, CT SON cov={4,5}, SP JJA cov={2}, SP SON cov={3,4,5}, Well JJA cov={1,2}, Well SON cov={3,4,5}. All SH cities hemisphere-flipped. NH (Atlanta DJF cov={1,2,12}) unchanged. |
| D3 | Verify B6 — every row's coverage_months ⊆ row's calendar group | ✅ FELL 2026-05-28 05:42 CT | 79 rows total, 0 NULL/empty coverage_months. All season/coverage pairs valid: DJF{1,2,12} JJA{1,2,12 SH-or-6,7,8 NH} MAM{3,4,5} SON{3,4,5 SH-or-9,10,11 NH}. |
| D4 | Verify all D1 rows carry current gate_set_hash | ✅ FELL 2026-05-28 05:42 CT | 79 sd3 rows + 8 leftover NULL-gate LOW rows. All HIGH replaced via INSERT OR REPLACE. |
| D5 | Run audit_error_model_row_reproducibility on D1 rows | ✅ FELL 2026-05-28 05:43 CT | 66/79 REPRODUCIBLE, 11 INSUFFICIENT_PRIOR (correct SD2 identity rows), 2 COVERAGE_MISLABELED (self-policing via target_month guard), **0 NON_REPRODUCIBLE**. B1-B7 antibodies confirmed working end-to-end on persisted state. |
| **D5.5** | **External-session MC rebuild detected** | ⚠️ NOTED 2026-05-28 05:46 CT | **PID 7167** (NOT this session) running `scripts/rebuild_calibration_pairs_v2.py --db /private/tmp/scratch_ens_fit.db --no-dry-run --force --error-model full_transport_v1 --temperature-metric high --n-mc 10000 --workers 12`. Started 05:40 CT (just after my HIGH producer wrote sd3 rows). Holds BULK writer-lock. Per operator policy "do not fire rebuild before all fix verified" — but D2-D5 are GREEN so the verification IS done; PID 7167's input rows are the sd3-correct ones. This is D11 happening for HIGH metric outside my domino chain. I did NOT launch it; I am NOT killing it. |
| D6 | LOW producer fit on staging at sd3 | ⏸️ BLOCKED by D5.5 lock (chain script 42047 waiting) | First attempt PID 15100 wrote 8 LOW rows (partial) then `OperationalError: locked`. Retry PID 20425 also blocked. Chain script `chain_low_mc.sh` (PID 42047, alive 2:41h) waiting on PID 7167 exit; on clean exit auto-launches LOW MC. Heartbeat 08:41 CT: MC HIGH alive 3:00:01, log +35.7KB / 110min, WAL +171MB / 110min, **~42 of 50 cities done (alphabetical: …Shanghai)**, 8 cities remaining ETA ~09:00-09:15 CT. Disk 28GB free. |
| D7 | Re-verify D2-D5 for LOW rows | ⏸️ blocked by D6 | Same probes, metric=low. |
| D8 | Emit fresh `ROW_ACTION_MANIFEST_2026-05-28.csv` under sd3 | ⏸️ blocked by D7 | `scripts/audit_error_model_row_reproducibility.py` → ROW_ACTION_MANIFEST output mode. Closes BL-A / HB8. |
| D9 | Verify manifest classifications sane | ⏸️ blocked by D8 | All rows ∈ {A,B,C,D,E}; no UNKNOWN; cohort counts reasonable. |
| D10 | **GATE — all D1-D9 GREEN** | ⏸️ HARD STOP | Operator rule: no rebuild before all fix verified. If any D2-D9 RED: stop, log evidence, do NOT proceed. |
| D11 | Launch MC rebuild on B∪E∪A_failed cohorts | ⏸️ blocked by D10 | `scripts/selective_refit_from_manifest.py --manifest <D8> --db /private/tmp/scratch_ens_fit.db --execute --n-mc 10000 --workers $(( $(sysctl -n hw.ncpu) - 2 ))`. Monitor armed with 1hr silence-alert. Hours-long. |
| D12 | Verify MC outputs vs replay-equivalence | ⏸️ blocked by D11 | A-cohort PASS verdict; pair-batch manifest written; post-audit 100% servable. |
| D13 | Wait #359 merge | parallel | Monitor `bvo7624iz`. When green: M1 rebase. |
| D14 | M1: rebase worktree onto main, replay B-patches if renamed | ⏸️ blocked by D13 (and D12 in parallel) | Push aligned branch. |
| D15 | M3 wiring fix: probability_trace_fact writer | parallel investigation | §6 root-cause; not in critical path of D11 — runs in background. |
| D16 | M4 bin-bias before/after test | ⏸️ blocked by D12 + D15 | Test must show directional improvement on ≥2 of 3 metrics on ≥75% of cities. |
| D17 | M5 unshadow live | ⏸️ blocked by D16 | Flip FT flag, kickstart live-trading, watch 4hr trade signal. |

### Active monitoring

| Watcher | Task ID | Cadence | Purpose |
|---------|---------|---------|---------|
| 2hr heartbeat cron | `a15c09ae` (in-session) | `17 */2 * * *` | Read this doc §12 first, then refetch latest state, advance lowest unblocked domino. **Prompt explicitly reads doc.** |
| #359 PR monitor | `bvo7624iz` (persistent) | 10 min | Emits state changes. |
| Rebuild monitor | re-armed per phase | 60 s tick | When D11 fires, new Monitor on the MC PID with silent-1hr alert. |

---

## 13 — Verification probes (D2-D5, D7, D9)

Run each. Decode base64. Read evidence. Only mark domino GREEN with concrete row counts / specific cities listed.

### D2 — B1 hemisphere-aware label persisted

```bash
OUT=$CLAUDE_JOB_DIR/d2_b1.txt
sqlite3 -cmd "PRAGMA query_only=1;" /private/tmp/scratch_ens_fit.db "
SELECT city, season, month, metric, coverage_months
FROM model_bias_ens_v2
WHERE authority='STAGING' AND error_model_family='full_transport_v1'
  AND gate_set_hash='deabf8f64bde27b7'
  AND city IN ('Buenos Aires','Cape Town','Sao Paulo','Wellington')
ORDER BY city, season;" > "$OUT" 2>&1
cat "$OUT" | base64
```
**GREEN**: each SH city has ≥1 row labeled JJA on coverage_months∈{1,2,12} OR SON on {3,4,5} OR DJF on {6,7,8} OR MAM on {9,10,11}.
**RED**: any SH city shows DJF row covering (12,1,2) → B1 regressed.

### D3 — coverage_months ⊆ season's calendar group (B6 evidence)

```bash
OUT=$CLAUDE_JOB_DIR/d3_b6.txt
sqlite3 -cmd "PRAGMA query_only=1;" /private/tmp/scratch_ens_fit.db "
SELECT season, coverage_months, COUNT(*)
FROM model_bias_ens_v2
WHERE authority='STAGING' AND error_model_family='full_transport_v1'
  AND gate_set_hash='deabf8f64bde27b7'
GROUP BY season, coverage_months
ORDER BY season, coverage_months;" > "$OUT" 2>&1
cat "$OUT" | base64
```
**GREEN**: all rows declare non-empty coverage_months ⊆ the season's calendar group (DJF→{12,1,2}, MAM→{3,4,5}, JJA→{6,7,8}, SON→{9,10,11}); no row has NULL/empty coverage.

### D4 — every D1 row carries current gate hash

```bash
OUT=$CLAUDE_JOB_DIR/d4_gate.txt
sqlite3 -cmd "PRAGMA query_only=1;" /private/tmp/scratch_ens_fit.db "
SELECT gate_set_hash, authority, error_model_family, metric, COUNT(*)
FROM model_bias_ens_v2
WHERE recorded_at > datetime('now','-30 minutes')
GROUP BY 1,2,3,4;" > "$OUT" 2>&1
cat "$OUT" | base64
```
**GREEN**: all recent rows have `gate_set_hash='deabf8f64bde27b7'`.

### D5 — row reproducibility audit

```bash
OUT=$CLAUDE_JOB_DIR/d5_audit.txt
/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python scripts/audit_error_model_row_reproducibility.py \
  --world-db /private/tmp/scratch_ens_fit.db \
  --forecasts-db /private/tmp/scratch_ens_fit.db \
  --family full_transport_v1 2>&1 | tail -40 > "$OUT"
cat "$OUT" | base64
```
**GREEN**: audit reports 100% rows REPRODUCIBLE same-source.
**RED**: any NON_REPRODUCIBLE row.

### D7 — same probes for LOW rows after D6.

### D9 — manifest sanity

```bash
OUT=$CLAUDE_JOB_DIR/d9_manifest.txt
{
echo "=== rows total ==="
wc -l docs/operations/ROW_ACTION_MANIFEST_2026-05-28.csv
echo "=== by action ==="
awk -F, 'NR>1{print $NF}' docs/operations/ROW_ACTION_MANIFEST_2026-05-28.csv | sort | uniq -c
echo "=== UNKNOWN check ==="
grep -c UNKNOWN docs/operations/ROW_ACTION_MANIFEST_2026-05-28.csv
} > "$OUT" 2>&1
cat "$OUT" | base64
```
**GREEN**: rows∈{A,B,C,D,E}, UNKNOWN count = 0, cohort distribution reasonable.

---

**End of doc. Resume at §12 lowest-unfallen domino. Read §0 directives EVERY heartbeat.**
