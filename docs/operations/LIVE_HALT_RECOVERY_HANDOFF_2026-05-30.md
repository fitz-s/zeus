# Zeus Live Halt-Recovery Handoff — 2026-05-30

## ONE LINE
Forecast pipeline + decision pipeline walked from a 30h silent halt through ~12 seams to ONE remaining gate (entry-gate 30s price-freshness). Fix it → first candidate. Everything UNCOMMITTED on live; SHADOW ON; 0 fills.

## GOAL (#36, active)
Live earns alpha: **3 fully-verified FILLED orders e2e** (track logic, no bugs) → then **120-min heartbeat 守护** (verify exits + P&L vs correct math/stats). Per-strategy unshadow needs its shadow **bias test (#24, bin bias ≤1)**. no-trade/stall/no-candidate = DEFECT, never "normal".

## HARD RULES (carry verbatim)
- NO advisor (it 400s + kills agents). NO AskUserQuestion (decide autonomously).
- SHADOW stays ON until #24 bias passes. real_order_submit_enabled=false + entries_paused. First order PROGRAMMATIC. cutover_guard operator-token-gated.
- Agents DIE constantly on transient model 400/socket (every 30–100 calls) → DRIVE DIRECTLY or salvage+resume; don't rely on delegation.
- `git rev-parse HEAD` before any live daemon restart. Don't git stash in shared worktree.
- Output to operator = terse caveman; subagent prompts = precise.

## STATE (2026-05-30 ~17:43Z)
- Live checkout `/Users/leofitz/.openclaw/workspace-venus/zeus`, branch **main @ 6dbc71ce3e** (#363 integrated; #29 subsumed/closed).
- Live-trading daemon: restarted, pid ~51930, `edli_shadow_no_submit`, reactor OK + ticking.
- forecast-live daemon: restarted pid ~44078 with the #38 fix loaded.
- opportunity_events: HIGH now emitting (~20+); pending|130 processed|10. decision_events=**0**.
- DBs (K1): state/zeus-world.db, zeus-forecasts.db, zeus_trades.db. executable_market_snapshots live in **zeus_trades.db** (71,842 rows).

## WHAT WAS FIXED THIS SESSION (ALL UNCOMMITTED — nothing committed/submitted)
1. **#38 CRITICAL ROOT — forecast halt (DONE+VERIFIED).** ECMWF OpenData fetched fine (ok=48) but every `ensemble_snapshots` write REFUSED: `data_version='ecmwf_opendata_mx2t3_local_calendar_day_max_v1' not in canonical allowlist`. The extractor bakes legacy `_v1` into extracted grib→JSON `data_version`; #362 version-eradication dropped `_v1` from `CANONICAL_ENSEMBLE_DATA_VERSIONS` (ensemble_snapshot_provenance.py:77,103) but NOT the writer. Frozen ~30h (HIGH since 05-24, LOW 05-29). Also the forecast-live daemon ran 25h-stale code.
   - **FIX (applied, uncommitted):** `scripts/ingest_grib_to_snapshots.py:520` — normalize `ecmwf_opendata_*_v1 → drop _v1` before `assert_data_version_allowed` (NC-12 guard). Same data, #362-redundant tag; TIGGE `_v1` unaffected; accepts existing+new extractions without re-extract.
   - **VERIFIED:** forced `_opendata_mx2t6_cycle` wrote 362 fresh HIGH rows (ens_max=2026-05-30T00:00); coverage recomputed fresh; 20 HIGH events emitted; 308 LIVE_ELIGIBLE HIGH coverage.
   - **STILL TODO (cleanup, not blocking):** fix the EXTRACTOR `extract_open_ens_localday` to stop emitting `_v1` (so artifacts are clean at source).
2. **Decision pipeline ~10 seams (Break-1/Break-2), uncommitted in:** `config/settings.json` (shadow flip), `src/events/triggers/forecast_snapshot_ready.py` (Break-1: ORDER BY LIVE_ELIGIBLE priority + coverage-authoritative classify), `src/main.py` (Break-2: `_refresh_pending_family_snapshots` targeted fetch + reactor wiring + seam-2 forecasts_conn), `src/data/market_scanner.py` (capture-illiquid `tolerate_missing_book` → illiquid snapshot top_ask=None executable_allowed=False, threaded into refresh @3513), tests.
3. **capture enable_orderbook (DONE+VERIFIED):** `src/main.py _refresh_pending_family_snapshots` discovery switched from per-family `/events?slug=` (under-enriched, missing enableOrderBook) → `find_weather_markets(min_hours_to_resolution=0.0)` (fully enriched via _get_active_events; downstream gamma_by_family filter keeps CLOB capture bounded to pending families). VERIFIED: `refresh_pending: status=refreshed, inserted=66, failed=0` (enable_orderbook error gone).

## THE ONE REMAINING GATE → FIRST CANDIDATE
`EXECUTABLE_SNAPSHOT_BLOCKED` persists despite capture working. ROOT: the **entry/FDR gate requires 30s PRICE-freshness** — `event_reactor_adapter.py:2941` `predicates=["freshness_deadline >= ?"]` (fresh_at=now), via `_latest_snapshot_rows_for_event_family` called from `build_event_bound_no_submit_receipt` (line ~485, fresh_at=decision_time). Capturing a full family (66 bins: find_weather_markets list + per-bin CLOB) takes >30s, so early-captured bins EXPIRE before the FDR-full-family check → never all-fresh-simultaneously → blocked.

**This is the operator's freshness principle: "freshness 针对价格不针对市场; 市场捕捉了不会突然消失."**

**FIX (operator-aligned, the immediate next action):** the ENTRY/FDR gate must bind on **IDENTITY-present** (a snapshot row exists for every family condition_id, ANY age) — drop/replace the `freshness_deadline >= now` predicate at the ENTRY gate (`event_reactor_adapter.py:2941` in `_latest_snapshot_rows_for_event_family`; the FDR family-completeness check at `build_event_bound_no_submit_receipt:490` should require identity coverage, not fresh price). Keep PRICE-freshness ONLY at submission — `assert_snapshot_executable` (`src/contracts/executable_market_snapshot.py:388`) already enforces it correctly there. The decision still needs CURRENT price for odds/edge: the targeted fetch refreshes the decided family's bins just-in-time before the read, so at decision time the prices ARE fresh; the gate just shouldn't REJECT on the 30s window across a multi-bin capture. TDD: family fully captured but bin captured_at spread >30s → entry gate passes (identity), submission still rejects a >30s-stale price.

After this: full-captured families pass FDR → `decision_events>0` = FIRST CANDIDATE → walk economics/edge/risk → #24 bias → unshadow → fills.

## CHAIN STATUS
✅forecast(recovered) ✅coverage ✅HIGH events ✅open HIGH markets ✅capture(66 inserted) ❌ **entry-gate 30s price-freshness** → FDR → decision → #24 bias → unshadow → 3 fills → heartbeat.

## OTHER OPEN ITEMS
- **M5 reconcile** loop: `M5 reconcile finding refresh kept blockers: reason=reconcile_findings_remain` firing every ~37s (unshadow-reconcile blocker; benign in shadow; clear before unshadow).
- **#34** SUITE-BLOCKER: `init_schema_forecasts` fresh-init crash (world `market_events` ghost). Fix VERIFIED in worktree `.claude/worktrees/agent-af3da32d60f3ea852` (db.py `_WORLD_ATTACH_EXCLUDED={"market_events"}` + test). Currently APPLIED to live src/state/db.py → causes fingerprint drift (computed 35342b7335 ≠ pin 1e667af0). Decide: revert db.py (git checkout) OR keep + `--write-pin 35342b73` (verify #363 test_replay_schema_fingerprint.py doesn't hardcode the old pin). Off critical path (daemon asserts table-set).
- **#27 Platt OOS**: branch `fix/platt-oos-gate` rebased onto #363 (tip 7f3107790b), harness 28/28. OOS before/after numbers NOT produced (agents died at degF→F normalize). #29 precondition met. Run scripts/score_platt_candidates.py → identity-vs-Platt LogLoss/RPS/bin-bias; improve→promote (operator rule), regress→diagnose data-vs-process. Identity-default live-safe meanwhile.
- **#11** gate-hash rebump (pre-unshadow). **#24** shadow-vs-online bias ≤1 (unshadow gate, needs decisions flowing). **#23** multi-surface readiness audit.
- The 130 pending events grinding 10/tick — a fully-covered family may decide once the entry-gate freshness fix lands.

## COMMIT/PR HYGIENE (before unshadow)
Nothing is committed. The uncommitted set spans many files across forecast-ingest + decision pipeline + capture + #34. Needs: separate the #38 ingest fix, the Break-1/2 + capture fixes, the find_weather_markets fix into coherent commits; opus critic on the Fix-2 coverage-authority + the entry-gate-freshness change (Tier-0 decision gate) BEFORE unshadow; restore canonical fingerprint pin.

## IMMEDIATE NEXT (fresh context)
1. Apply the entry-gate identity-vs-price-freshness fix (event_reactor_adapter.py:2941 + the FDR check). TDD. Restart live-trading. Watch next reactor cycle: `refresh_pending inserted>0` (already true) → FDR passes → `decision_events>0` = FIRST CANDIDATE (or next gate economics/edge — report which).
2. Then #24 bias on the formed candidates' p_raw vs online forecast (≤1) → the unshadow gate.

---

## UPDATE 2026-05-30 ~18:10Z — chain walked to the TRUE wall (kernel)
Drove the receipt path on live data (python harness on `build_event_bound_no_submit_receipt`, family Wuhan|2026-05-31|high which has BOTH a pending forecast event AND fresh snapshots). Cleared THREE more gates; hit the real bottom.

**FIXED + PROVEN this session (TDD, all uncommitted; `tests/engine/test_event_reactor_no_bypass.py` 31 passed/44 xfail):**
- **#35 freshness decouple (DONE).** `_latest_snapshot_rows_for_event_family` (event_reactor_adapter.py:2927) gained `require_fresh` param; entry callers — gate (`executable_snapshot_gate_from_trade_conn`:~192) and receipt (`build_event_bound_no_submit_receipt`:~481) — pass `require_fresh=False` (bind IDENTITY). Price-freshness stays at submission (`assert_snapshot_executable`, wired at `venue_command_repo.py:1025`). New relationship test + 2 repurposed active tests + shadow canary→money-guard contract. Operator law: freshness=price-not-market. **LIVE-PROVEN:** receipt advanced past EXECUTABLE_SNAPSHOT.
- **#39 unit provenance (DONE).** `_bin_from_market_event` derives Bin unit from `SettlementSemantics.for_city(city).measurement_unit` (new helper `_settlement_unit_for_payload_city`) instead of defaulting payload→'F'. Fixed every Celsius city (was `EVENT_BOUND_MARKET_TOPOLOGY_INVALID: …26°C… is Celsius but unit='F'`). 2 TDD tests. **LIVE-PROVEN:** families now `family_complete=True`.

**THE TRUE WALL — #40 (Break-4): EDLI q/FDR kernel UNAUTHORED.** `_live_yes_probabilities`:2367 dispatches both event types to the fail-closed STUB `_forecast_snapshot_probability_and_fdr_proof`:2403 (returns empty q → `LIVE_INFERENCE_INPUTS_MISSING: missing q_live for condition`). The real `_canonical_probability_and_fdr_proof`:2450 is unauthored: its assembly loop is now wired to the real `hypothesis_by_label_direction` (done this session), but it references two **unwritten** helpers — `_forecast_snapshot_row_for_event` + `_market_analysis_from_event_snapshot`. Authoring them (mirror `_generate_candidate_proofs`:2243 snapshot fetch + legacy `src/engine/evaluator.py` MarketAnalysis assembly; building blocks `_snapshot_p_raw`/`_snapshot_p_cal` exist), then repoint dispatch 2368/2377→canonical, un-xfail the 44 acceptance tests = the path to FIRST CANDIDATE. **Tier-0 statistical core (wrong q = silent mis-trade) — author in a full-context session with opus critic; SHADOW until #24.** Dispatch reverted to stub → daemon clean fail-closed (no candidates, no crash, no capital risk).

**CHAIN NOW:** ✅forecast ✅coverage ✅events ✅markets ✅capture ✅freshness(#35) ✅topology/unit(#39) ✅family_complete → ❌ **q/FDR kernel corrupted (#40)** → FIRST CANDIDATE → economics/FDR/Kelly → #24 bias → unshadow → fills.

### #40 PRECISE RECONSTRUCTION SPEC (the kernel is a corrupted abandoned refactor, not "unwritten" — masked by the fail-closed stub). All in `src/engine/event_reactor_adapter.py`. Fix all 5, repoint dispatch, un-xfail the 44 tests in `tests/engine/test_event_reactor_no_bypass.py` (assert `q_live>0.60`, `fdr_hypothesis_count==4`):
1. `_live_yes_probabilities` dispatch (~2375/2384) → currently the fail-closed STUB `_forecast_snapshot_probability_and_fdr_proof` (empty q). Repoint BOTH event types → `_canonical_probability_and_fdr_proof` **after** 2–5. (Left on stub this session = safe fail-closed.)
2. `_canonical_probability_and_fdr_proof`:2450 — assembly loop now reads the real `hypothesis_by_label_direction` (FullFamilyHypothesis p_posterior/p_value/ci_lower/passed_prefilter) — **DONE this session**. It calls two helpers by names that don't match the defs (3,4).
3. `_canonical_probability_rows`:2544 **is** the intended `_forecast_snapshot_row_for_event` (sig matches the 2471 call) but its **body is corrupted**: undefined `select_fields` (2579), undefined `row` (2588), and a spurious probability-fact query (`required={…direction,p_posterior}`, `direction IN ('buy_yes','buy_no')`) against `ensemble_snapshots` (no such columns). Reconstruct as a clean `ensemble_snapshots` causal/latest fetch (SELECT * WHERE city/target_date/temperature_metric + `CAST(snapshot_id AS TEXT)=causal_snapshot_id` unless allow_latest, + available_at<=dt + authority/causality/boundary guards, ORDER recorded_at DESC, fetchone→dict, `_forecast_snapshot_reader_block_reason` check, return). Rename → `_forecast_snapshot_row_for_event`.
4. `_canonical_hypothesis_rows`:2602 **is** the intended `_market_analysis_from_event_snapshot` (body builds `MarketAnalysis` correctly via existing `_snapshot_p_raw`/`_snapshot_p_cal` + native_costs p_market). Drop the vestigial `conn` param (the 2482 call omits it), **delete dead code 2673-2680** (orphan hypothesis-rows dict builder after the `return`), rename → `_market_analysis_from_event_snapshot`.
5. `_generate_candidate_proofs`:2308-2309 references **undefined** `canonical_p_values`/`canonical_prefilter` → should be `generated_p_values`/`generated_prefilter` (returned at 2258-2259).

Tier-0 alpha core — wrong q = silent mis-trade. Author in a full-context session + opus critic; SHADOW until #24. The loop-wiring fix (2) is the only kernel edit kept this session (harmless; canonical uncalled).

---

## UPDATE 2026-05-30 ~19:00Z — KERNEL RECONSTRUCTED + VALIDATED (#40 DONE)
The "TRUE WALL" was a corrupted abandoned refactor, not unwritten code — fully reconstructed this session. All edits in `src/engine/event_reactor_adapter.py` (uncommitted). ~10 fixes: dispatch repointed stub→`_canonical_probability_and_fdr_proof`; `_canonical_probability_rows`→`_forecast_snapshot_row_for_event` (reconstructed corrupted SELECT/fetchone body); `_canonical_hypothesis_rows`→`_market_analysis_from_event_snapshot` (dropped vestigial conn + dead code); `_generate_candidate_proofs` `canonical_*`→`generated_*`; module import `+edge_n_bootstrap,+settings`; canonical loop wired to real `hypothesis_by_label_direction`, q from `analysis.p_posterior`; day0 `_apply_day0_mask_to_probability_vector`+`_day0_absorbing_mask` authored, day0 branch arity 4→2; **ALPHA-CORRECTNESS BUG**: `q_lcb_5pct` was fed the bootstrap **edge**-LCB where `robust_trade_score` wants q's LCB (cost double-subtracted → spurious negative) → fixed `q_lcb = edge_lcb + p_market` (exact, c_b fixed); buy_no made optional (scan omits non-executable side).

**LIVE-PROVEN** (python harness on real DBs): Wuhan|2026-05-31|high → `q_live=0.98`, computes market cost + trade score, correctly **declines** negative-edge (TRADE_SCORE_NON_POSITIVE). **ACCEPTANCE**: stripped 44 stale "kernel unauthored" xfail markers (kernel IS authored now) + fixed fixture `data_version`→`dataset_id` for ensemble_snapshots/source_run (live schema) → `test_event_reactor_no_bypass.py` + substrate + canary = **107 passed / 1 xfail** (the 1 = `depth_at_best_ask` quote-book feature, genuinely separate, re-xfailed with accurate reason). **ZERO regressions** — broader engine+money_path 5 failures are all pre-existing (3 legacy passive-maker `test_crossing_decision`; 2 shadow-config-flip `test_edli_online_invariants`, same class as the canary).

Daemon restarted with the kernel (pid ~85234, boots clean). decision_events still 0 because the reactor's 10/cycle budget (no_submit_proof_limit) is consumed by the 160 pending events that are mostly **market-less LOW/future families** (correct `EXECUTABLE_SNAPSHOT_BLOCKED`) — it doesn't reach the market-backed Wuhan/Wellington-high families the kernel can decide. **The remaining gap to a FIRST LIVE candidate is reactor throughput/prioritization, NOT the kernel** (decision-first emission, or raise proof_limit, or deprioritize market-less families).

**CHAIN:** ✅forecast ✅coverage ✅events ✅markets ✅capture ✅freshness ✅unit ✅family_complete ✅**q/FDR kernel** → (reactor throughput) → first candidate → #24 bias → unshadow → fills.

**Pre-existing defect (exit/PnL — affects GOAL exit-verification):** `position_current` missing `chain_avg_price` column → Traceback in `src/execution/exchange_reconcile.py::_apply_exit_fill_projection_and_execution_fact`. Add the column / migration.

**Before unshadow:** opus critic on the kernel reconstruction (Tier-0 alpha); commit the uncommitted set as coherent units; update the 2 shadow-config tests + canary to the shadow contract.

**Also flagged:** `position_current` table missing `chain_avg_price` column (`sqlite3.OperationalError` in boot/position-write path) — exit/PnL schema drift, investigate (affects GOAL exit-verification). reactor `reject` lambda is log-silent — rejection reasons ARE persisted in `no_trade_regret_events`(zeus-world.db) + decision-cert compile failures (query those to diagnose live stalls). 160 pending opp events are mostly LOW/future-dated families with no near market — they burn the 10/cycle proof budget (decision-first emission is the operator-principled redesign).
