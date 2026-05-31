# Zeus Live-Shadow Seam-Hunt — Handoff 2026-05-29

## WHERE WE ARE (one line)
The statistical-layer redesign is MERGED to `main` and the **live daemon is running it in SHADOW**. We are now hunting decision-pipeline wiring breaks one seam at a time until a real decision forms end-to-end → then a real live trade (the only proof of correctness). The redesign/PR/merge backstory is DONE; this handoff is the forward live state only.

## HARD OPERATING RULES (carry verbatim — operator directives)
1. **no-trade / no-events = DEFECT, not normal** — even in shadow. Shadow blocks SUBMIT only; the decision/compute/trace pipeline MUST produce positive activity. Root-cause silence; assert POSITIVE activity (fresh opportunity_events / decision_events / probability_trace_fact / candidates), never "no errors / no orders = healthy."
2. **Only a real live trade (real fill + real P&L) proves correctness.** Offline/historical/recompute verification is a checkpoint, NEVER proof.
3. **Expect MANY wiring breaks** from the redesign's large diff — fixing one gate is just the first; hunt+fix each seam.
4. **Reuse subagents on similar-context missions** (SendMessage active agents by agentId; for completed agents, hand their report to the next agent — don't re-derive).
5. **Do NOT call advisor** — current context-doubling bug, it has interrupted sessions.
6. **First live order completes PROGRAMMATICALLY — never manual completion.**
7. **cutover_guard arm/disarm is operator-token-gated** (HMAC vs OPERATOR_TOKEN_SECRET_ENV) — operator-only by design.
8. Don't `git stash` in shared worktrees; ALWAYS `git rev-parse HEAD` before a live daemon restart (multi-session collision risk).

## CURRENT LIVE STATE (verified 2026-05-29 ~11:51 Chicago)
- **Live checkout:** `/Users/leofitz/.openclaw/workspace-venus/zeus`, branch **`main` @ `bedff16832`** (merged redesign; switched off `pr3-deadcode`).
- **Daemon:** pid 36514, mode **`edli_shadow_no_submit`**, healthy (no crash). launchd domain `gui/501`, jobs `com.zeus.{live-trading,forecast-live,riskguard-live,venue-heartbeat,heartbeat-sensor,calibration-transfer-eval}`.
- **DBs** (`state/`, K1 split): `zeus-world.db`, `zeus-forecasts.db`, `zeus_trades.db`. forecasts.db is migrated: `dataset_id` (renamed from data_version by #362), integer-free values, **D-S1 `settlement_station`/`settlement_unit` columns added** this session. Schema fingerprint pin `d251312a…` matches main; daemon boot asserts (`assert_schema_current_forecasts` + world) PASS.
- **SHADOW gates (reversible):**
  - `control_plane:global:entries_paused` (indefinite, issued_by=control_plane) — set this session via `_apply_command('pause_entries', …)`.
  - `edli_v1.real_order_submit_enabled=false` (shadow no-submit).
  - `cutover_guard.json` = `LIVE_ENABLED` (operator-token-gated; entries_paused + no_submit are the ACTIVE shadow blocks. To go live: operator re-confirms / lifts pause / cutover transition).
- **Config flip:** `edli_v1` `legacy_cron`→`edli_shadow_no_submit` tuple (`reactor_mode=live_no_submit`, `enabled/event_writer_enabled/forecast_snapshot_trigger_enabled=true`, `real_order_submit_enabled=false`) applied to the LIVE `config/settings.json` (**UNCOMMITTED** working copy; backup `config/settings.json.pre_edli_flip.bak`). REVERT shadow→legacy = restore backup + restart.

## PIPELINE STATE (the seam-hunt — this is the active work)
```
ensemble_snapshots (FRESH 11:23) ✓
  → source_run_coverage (682 LIVE_ELIGIBLE fresh; +212 HORIZON_OUT_OF_RANGE = designed 5-day-cap block) ✓
    → opportunity_events (20, all FORECAST_SNAPSHOT_READY, fresh) ✓   [fixed seam-1: was 0]
      → decision_events = 0   ←★ CURRENT DEAD SEAM
        → candidate → (would-submit, correctly blocked by shadow)
```
- **Seam-1 (FIXED):** daemon was in `legacy_cron` (deliberate PR332 opt-in gate, NOT a regression) → EDLI reactor never scheduled → 0 opportunity_events all-time. Flipped to `edli_shadow_no_submit` → reactor scheduled → opportunity_events 0→20.
- **Seam-2 (ROOT-CAUSED — APPLY THIS FIX FIRST on resume):** `_edli_event_reactor_cycle` (`src/main.py:2810`) emits+commits opportunity_events, then throws **`NameError: name 'forecasts_conn' is not defined`** at `main.py:2870` (also 2871/2890/2891/2902) while building the submit adapter. The fail-open `_scheduler_job` decorator (`main.py:494-521`) swallows it → `state/scheduler_jobs_health.json::edli_event_reactor.status=FAILED, last_failure_reason="name 'forecasts_conn' is not defined"`. So opportunities accumulate, decisions never run. (Introduced by the redesign diff, merge `a85beffbdd` lineage.)
  **EXACT FIX:** in `_edli_event_reactor_cycle`, after `trade_conn` is opened (~line 2836) add `forecasts_conn = get_forecasts_connection_read_only()` (same fn used at 2945), and add `forecasts_conn.close()` to the `finally` (2918-2920, which currently closes only conn+trade_conn — don't leak). Then restart `com.zeus.live-trading` + verify `scheduler_jobs_health.json::edli_event_reactor.status` flips OK and `decision_events`/regret rows appear.
  **DOWNSTREAM SEAMS (latent, masked behind the crash — fix/probe in one pass; operator expects many):**
  1. executable-snapshot gate (`src/engine/event_reactor_adapter.py:172-173`) returns False when `topology_conn is None` OR no fresh `executable_market_snapshots` / 0 active Polymarket weather market (`imminent_open_capture monitors: 0`). Probe `executable_market_snapshots` freshness + active-market count in `market_topology_state` right after the fix.
  2. `build_event_bound_no_submit_receipt` (`event_reactor_adapter.py:469-475`) raises if forecast/topology/calibration conn is None — the fix must pass a REAL forecasts conn, not None.
  3. H4: `read_executable_forecast` may return `EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA` (the pre-existing `test_collect_open_ens_cycle…readable_by_live_reader` failure) once the gate is reachable.
  4. `reject=lambda _event,_stage,_reason: None` (`main.py:2906`) silently drops rejections → no_trade_events won't populate even when healthy; wire it to the no_trade/regret writer so shadow rejections are visible (per no-events rule).

## LIVENESS PROBES (re-run to check the pipeline; READ-ONLY)
```sh
LIVE=/Users/leofitz/.openclaw/workspace-venus/zeus
sqlite3 "file:$LIVE/state/zeus-world.db?mode=ro" "SELECT COUNT(*),MAX(created_at) FROM opportunity_events;"
sqlite3 "file:$LIVE/state/zeus-world.db?mode=ro" "SELECT COUNT(*),MAX(created_at) FROM decision_events;"
sqlite3 "file:$LIVE/state/zeus-forecasts.db?mode=ro" "SELECT readiness_status,COUNT(*),MAX(computed_at) FROM source_run_coverage GROUP BY readiness_status;"
launchctl list | grep com.zeus ; tail -n 40 "$LIVE/logs/zeus-live.log"
# restart decision daemon (picks up config): launchctl kickstart -k gui/501/com.zeus.live-trading
```

## AGENTS (status at handoff)
- `ad78facbc7e2e036e` decision-tracer — DONE (seam-2 root cause above).
- `a4b7ab6f326d91eee` drop-edli-v1 — DONE → branch `fix/drop-edli-v1` `f7425a8791`.
- `af6c1d8c7eac825d6` platt-gate — #27 Platt P0-P7 tests (branch `fix/platt-oos-gate`); may still be running on resume — collect its result.

## REVIEW-READY BRANCHES (off main `bedff16832`; merge after review)
- `fix/d5-bias-keying` (#29) — model_bias_ens PK + product/cycle/lead-bucket + migration; 11 RED + 84 pass.
- `fix/evidence-pairing-harden` (#30) — residual_key loud-fallback + closed-vocab {F,C} + unverified flags; 636 pass.
- `test/analytic-pcal-equivalence-coverage` (#31) `fe009fff19` — 42 pass, all 4 rounding rules exact.
- `fix/drop-edli-v1` (`f7425a8791`) — `edli_v1`→`edli` config-key + code-consumer rename, 473 pass. **DEPLOY HAZARD:** FF-ing this to live would set the config key to `edli` with the branch's committed `legacy_cron` values → **WIPES the uncommitted edli_shadow_no_submit flip and drops the daemon back to legacy_cron (re-breaks the pipeline).** When deploying: carry the shadow tuple under the new `edli` key (or re-apply the flip + restart). Data-level `edli_v1` strings (strategy_key values, policy ids, cert payloads) deliberately left — need a DB migration, separate.

## REMAINING TASKS — ALL IMPORTANT (full context in TaskList #9–#33)
**Critical path to the proof (a live trade):** #33 decision seam-hunt (ACTIVE) → decisions form in shadow → end-to-end forecast-error confirm (#24; forecast/p_raw layer already 9/10 offline-verified, East-Asia cold RESOLVED, Tokyo/RJTT coastal grid-cell = follow-up) → #11 gate-hash re-bump → #12/#25 unshadow + **first programmatic live trade**.

- **#10 REFIT (LOW re-extract) — operator-go.** ETA ~2-3 hr to an ISOLATED db copy (`state/backups/zeus_refit_low_iso_*.db`, never live). Config: extract `-j14` / ingest 1 proc / calib `--workers 12`; 54 cities, 9 local GRIB dates, ~743K LOW rows. Code already committed (D1-LOW 3h-window + drop>144h tail). Extractor-replace (`.claude/worktrees_patch_copies/extract_open_ens_localday.PATCHED.py`) + re-ingest are operator-gated. NOT a live blocker (LOW `apply_to_metrics:["high"]` — LOW not traded; this is training-corpus correction).
- **#27 Platt** P0–P7 (identity-default + full-chain OOS gate; spec `docs/operations/STAT_WAVE_REPORT_AND_PLATT_TASK_SPEC_2026-05-29.md`). Blocked by #10/#29/#31 (p_raw domain freeze).
- **#29/#30/#31** merge after review; **`edli_v1`→`edli`** deploy (coordinated config rename + restart).
- **#28** OpenData cap 144→156 (future, only if Polymarket reopens >5-day markets — Western D+5 currently fail-closed-blocked, fine).
- **Tokyo/RJTT** coastal grid-cell audit (the one #24 bin-bias exceedance; non-blocker).

## DANGER NOTES
- A concurrent session was on `pr3-deadcode`; we moved the live checkout to `main`. Verify HEAD before any restart.
- The `edli` flip is uncommitted live config — survives restart (daemon reads it) but a `git checkout`/reset could wipe it; backup exists.
- `check_schema_fingerprint.py` fresh-`init_schema_forecasts` errors on the `market_events` `temperature_metric` index (main `v2_schema` fresh-init bug) — NOT the daemon boot path (asserts, doesn't init; live table HAS the column). Separate cleanup.

## UPDATE 2026-05-29 PM — refined Break-1 (dual-authority design failure)
Seam-1 (legacy_cron) + Seam-2 (NameError forecasts_conn main.py:2837/2921) FIXED; reactor status=OK.
**Break-1 root (two layers, both confirmed with live data):**
1. **Emit starvation** — `forecast_snapshot_ready.py::scan_committed_snapshots` `ORDER BY c.computed_at DESC` + `forecast_emit_limit=20` → the limit window fills with BLOCKED rows (top-25 by computed_at = ~24 BLOCKED + 1 LIVE_ELIGIBLE). All 20 emitted opportunity_events were target 2026-06-04 (6d-out HORIZON_OUT_OF_RANGE, designed 5d cap #28). The 682 LIVE_ELIGIBLE near-date coverage rows (2026-05-28..06-03) emit ZERO. FIX: prepend `CASE WHEN c.readiness_status='LIVE_ELIGIBLE' THEN 0 ELSE 1 END ASC,` to the ORDER BY (line 289).
2. **Dual-authority gate** — for all 682 LIVE_ELIGIBLE rows: coverage.completeness_status=COMPLETE + readiness=LIVE_ELIGIBLE + observed_steps present, BUT joined source_run.completeness_status=PARTIAL/status=PARTIAL (source_run.observed_members=0 — accounting orphaned from the snapshot write; ensemble_snapshots HAS 51 members). `classify_forecast_snapshot` `source_complete` requires BOTH source_run AND coverage to say COMPLETE → PARTIAL source_run forces PARTIAL_ALLOWED → `edli_source_truth_gate` (reads payload.completeness_status==COMPLETE, event_reactor_adapter.py:143) BLOCKS. **DESIGN FAILURE: two completeness authorities (whole-run source_run vs window-scoped coverage) AND-combined.** FIX-B (decided): make coverage the single authority — `source_complete = coverage.completeness_status=='COMPLETE' and coverage.readiness_status=='LIVE_ELIGIBLE'`; drop the source_run conjuncts. SAFE: COMPLETE branch still requires required_steps_present + observed_members>=expected(51) + reader_live (executable-reader). Rejected FIX-A (fix source_run accounting) — papers over, keeps dual authority.
**Remaining downstream gate chain (expect to hit next, in order):** reader_live / EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA (H4) → executable_market_snapshots=0 (Break-2: ingest_market_scan FAILED 2d DB-lock + market_discovery FakePolymarketClient wiring; snapshot_repo.py:106 writer) → market-binding → decision.
**Agents:** tracer ad78 implementing Fix-1+Fix-2 (TDD, restart, report next gate). v1-drop a4b7 DONE (branch fix/drop-edli-v1 @187185501d, 480 tests pass, isolated worktree; live deploy deferred to coordinated step). platt af6c1d8c running (#27, separating its OOS tests from pre-existing topology_doctor base drift).
**Next:** opus critic on the Fix-B gate-semantics change before any unshadow.

## UPDATE 2026-05-29 ~17:40 — Break-1 FIXED (verified live), Break-2 now active
Applied to src/events/triggers/forecast_snapshot_ready.py (UNCOMMITTED live working copy):
- Fix-1 (line ~289): ORDER BY prepend `CASE WHEN c.readiness_status='LIVE_ELIGIBLE' THEN 0 ELSE 1 END ASC`. Live-data top-20 now all LIVE_ELIGIBLE.
- Fix-2 (~line 107): source_complete = coverage.completeness_status=='COMPLETE' and coverage.readiness_status=='LIVE_ELIGIBLE' (dropped source_run.status/completeness conjuncts). Standalone RED→GREEN + guards (window-steps, reader_live, coverage-BLOCKED all still block COMPLETE).
- 3 relationship tests added to tests/events/test_forecast_snapshot_ready.py (currently runnable only via standalone repro — see #34 suite-blocker).
LIVE RESULT (daemon pid 68752 restarted): opp 20→40; near-date 2026-05-29..06-03 now emit; completeness COMPLETE=20 + PARTIAL_BLOCKED=20; the 20 COMPLETE PASS source-truth gate, now block at executable-snapshot gate → decision_compile_failures EXECUTABLE_QUOTE/EXECUTABLE_SNAPSHOT_BLOCKED=20.
**Break-2 (now the live blocker, task #35):** executable_market_snapshots=0. market_topology_state=3938 but none materialized. Writer snapshot_repo.py:106. ingest_market_scan FAILED ~2d 'database is locked'; market_discovery 'FakePolymarketClient() takes no arguments'. Gate event_reactor_adapter.py:161.
**#34 SUITE-BLOCKER:** init_schema_forecasts crashes on fresh build (market_events temperature_metric index) → whole pytest suite + fingerprint down (daemon unaffected). Blocks committing Break-1 fixes with green tests + re-pin.
**Pending before unshadow (operator hard gate):** real shadow decisions/traces flowing + shadow p_raw vs online-forecast bias ≤1 (#24). opus critic on Fix-2 gate-semantics. NONE committed yet.

## UPDATE 2026-05-29 — Fix-2 critic verdict: SAFE_TO_PROCEED (opus)
Adversarial review of the coverage-authority change. NO genuinely-incomplete forecast can now classify COMPLETE:
- coverage.completeness_status=COMPLETE is set ONLY when evaluate_producer_coverage→LIVE_ELIGIBLE (ecmwf_open_data.py:904-913; forecast_target_contract.py:241-251): in-window steps complete + observed_members>=51 + source linkage + match. Excludes FAILED/MISSING source_run AT WRITE TIME.
- coverage.observed_members = _usable_member_count(members_json) — independent recount, NOT the orphaned source_run.observed_members (=min per-step, dragged to 0 by failed >144h tail steps).
- The one real failure mode source_run==PARTIAL could catch (corrupt/partial member set) is caught TWICE by retained guards: observed_members>=51 (independent recount) + reader_live (executable_forecast_reader.py:677-697 enforces authority=VERIFIED, causality=OK, non-ambiguous boundary, fail-closed extrema-authority) — none of which the dropped source_run conjuncts ever provided.
- Live: 1014 unexpired COMPLETE/LIVE_ELIGIBLE rows all 51/51 members + exact in-window steps + NULL reason; 0 unexpired incomplete. 312 FAILED-join rows all EXPIRED + failed only on >144h tail (NET_ConnectionError on steps the cap drops).
**2 LOW conditions before the IRREVERSIBLE UNSHADOW (not before continued shadow):**
1. Repair #34 (_create_market_events fresh-init) + re-pin fingerprint → run tests/events/test_forecast_snapshot_ready.py green in-suite (2/3 verified out-of-suite; Test-3 blocked only by #34).
2. Operator's existing gate: real shadow decisions flowing + shadow-vs-online bias ≤1 (#24).
LOW finding: tail-step semantics are time-bound (if OpenData cap reverts without re-running coverage, a stale coverage=COMPLETE could carry a tail-incomplete run; mitigated by +24h expires_at + live step recompute). Track with #28.
