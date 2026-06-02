# MASTER SYNTHESIS + STAGED IMPLEMENT PLAN — GOAL#36 — 2026-06-02

Created: 2026-06-02 | HEAD e5191441 | branch edli-correctness-recover-2026-06-02 | daemon SHADOW PID 21616, arm OFF, $0 capital.
Authority: this session's 4 audits (exec+exit, probability-integrity, pre-arm safety) + calibration bake-off + coverage/churn re-derivation + blind-spot scouts. GOAL#36 = 3 e2e-correctness-checked FILLED orders + 120-min 守護.

> STATUS: COMPLETE. All 5 blind-spot scouts + the bake-off critic landed and are folded in. Headline: **the bake-off scorecard is SUPERSEDED (critic trust=FALSE — RAW handicapped, EMOS-49 is an artifact)** → calibration selection unknown until the scorer is rebuilt; and **the #1 coverage lever is the unloaded `src.ingest_main` obs daemon (operational, not code)**.

---

## 0. THE CORRECTION THAT REFRAMES EVERYTHING

Earlier this session I claimed "35 cities are dark (no forecast)". **WRONG — re-verified:** all 54 configured cities have recent `ensemble_snapshots` (target_date≥2026-05-28, contributes_to_target_extrema=1), and 51 cities have markets (`market_events` 51 cities; `executable_market_snapshots`=223k rows in **zeus_trades.db** — my "empty" reads queried the wrong DB twice). **The operator was right: forecast data + markets exist for ~50 cities.**

The real collapse is **downstream event-emission**: `source_run.status` = **SUCCESS 19 / PARTIAL 56 / FAILED 3**. `FORECAST_SNAPSHOT_READY` opportunity-events fire only for SUCCESS source-runs → **19 cities reach the reactor**, of which 14 form candidates. The 35-city gap = **PARTIAL source-runs** (incomplete ensemble member/step ingest) gating FSR emission. THIS is the #1 alpha lever — not a forecast-dark problem, an ingest-completeness problem.

---

## 1. MONEY-PATH COVERAGE TABLE (no blind spot)

| Stage | Audited by | Verdict / key finding |
|---|---|---|
| contract semantics | blind-spot scout | **CORRECT, no blockers.** WMO half-up + HKO truncate dispatched by for_city(); assert_kelly_safe wired at every Kelly boundary; BOUND_ENVELOPE_REQUIRED = fail-closed rejection (NOT silent drop — downgrades exec-exit concern); fx_classification fail-closed (not hot-path). |
| source truth / freshness | blind-spot scout | Price feed HEALTHY (0.3s); book-before-order correctly gated (GATE #84 JIT /book primary, DB fallback <1000ms else raise). **BUT obs+TIGGE ingest STOPPED 2026-05-28 (data-ingest daemon down)**; freshness gate is **fail-OPEN for submission** (main.py:2818 logs, trades continue on stale data) — SEV1 when armed. |
| forecast coverage | this session (re-derived) | **ROOT (operational, not code): the `com.zeus.data-ingest` daemon is NOT loaded** (only com.zeus.forecast-live runs). source_run SUCCESS=19 (fresh today, ecmwf forecast cities) / PARTIAL=56 (FROZEN at 2026-05-30) / FAILED=3. FSR fires only for SUCCESS → 19 cities reach reactor. The 35 PARTIAL cities froze when obs+TIGGE ingest stopped (~2026-05-30); obs sources stale since 2026-05-28. **#1 alpha lever = restore data-ingest (+TIGGE/OpenData cap #28).** |
| calibration | bake-off (OOS 2025) + critic | **SCORECARD SUPERSEDED — critic trust=FALSE.** "EMOS wins 49" is an artifact: RAW LogLoss inflated 1.3-4.5× (non-reproducible; scorer doesn't query the calibration_pairs vector RAW actually serves). On its real served vector **RAW beats EMOS in ~half of spot-checks**. Leakage unverifiable; LOW-metric EMOS unlicensed (no metric key). **Rebuild the scorer before any per-city selection.** |
| edge / q | probability-integrity audit | NO-CI haircut bypassed 84.8% (**#129**); units CORRECT; q-domain #91 CORRECT; #105 q-faithfulness UNCERTAIN (live-verify needed). |
| execution (fill) | exec+exit audit | **#92 tick_size rejects 100% pre-venue** (executor.py:1746). Programmatic path OK; silent-drop risks (envelope=None, fill_tracker quarantine). |
| monitoring / exit | exec+exit audit | **#127 FLASH_CRASH_PANIC** dumps on bare −15¢/hr move (no belief gate); **#113 CI-separation 守護 unwired dead code**; live exit = flat 2-confirm. |
| settlement | exec+exit + this session | **#128 no durable realized_pnl column**; harvester gated OFF (settle→redeem dead, Shanghai stuck 4d); **settlement_outcomes backfilled** (6488 rows, commit ed8f6f22) — readers now fed, but forward-write still missing. |
| learning | blind-spot scout | **WIRED but NOT SCHEDULED**: settlement_attribution.py (INV-37 compliant) never scheduled → regret_decompositions + calibration_pairs only fill on manual run → settlement→calibration feedback is DEAD. settlement_outcomes forward-write now ACTIVE (dual-write harvester_truth_writer:616). |
| riskguard / control | blind-spot scout | **CORRECT.** Limits (8% daily/15% weekly/Brier 0.35/95% heat) enforced at cycle entry-gate on non-GREEN; kill-switch (ZEUS_KILL_SWITCH/RISK_HALT/SETTLEMENT_FREEZE) non-bypassable; 5-min staleness→RED fail-closed; auto-pause=15min (NOT 4h — memory stale), operator-pause indefinite. GAPS: force-exit MARKS-not-cancels (orders stay live in cycle gap); FLASH_CRASH #127; #113 unwired. PID 81524 healthy. |
| arm / canary | blind-spot scout | **FAIL-CLOSED** — no order without all 20 preconditions (boot raises if any contract flag missing). Full checklist in §5. |
| safety (flood/conc) | pre-arm audit | **#99 flood-cap** (1000×$185, no rate-limiter); bankroll stale-sizing + bridge-id collision SOFT. |

---

## 2. FINAL CALIBRATION STATE — BLOCKED: scorecard SUPERSEDED (critic trust=FALSE)

The bake-off "EMOS wins 49 cities" **cannot be trusted** (critic ae0e509c). Defects:
1. **RAW handicapped / RawLL non-reproducible** — published RAW LogLoss inflated 1.3-4.5× (London 2.22 published vs 1.50 proper; Amsterdam 7.20 vs 1.60). The scorer's RAW path does NOT query the calibration_pairs MC vector RAW actually serves live; scored as a 0.5-floored mixture vs EMOS's single fitted Gaussian (different families = sharpness artifact, not skill).
2. **Decisive:** scoring RAW on its real served vector, **RAW beats EMOS in 4-5 of 9 spot-checked cities** (Amsterdam, NYC, Chicago, Miami, Tokyo; London tie). The 49-sweep was the inflation.
3. **Leakage UNVERIFIABLE** — fit≤2024 is only a header comment; no machine-checkable per-cell fit date / asserted OOS gate.
4. **LOW-metric EMOS unlicensed** — emos_calibration.json keys City|SEASON (no metric) → serves HIGH params to LOW rows; every "EMOS-wins-low" line is invalid.

**REQUIRED before any per-city calibration selection (implement-plan step 3 prerequisite):** rebuild ONE scorer that runs ALL methods (incl RAW on its real live-served calibration_pairs MC vector) through one identical pipeline; add per-cell fit-date provenance + an asserted OOS gate (fail if fit window overlaps scored date); fit a separate low-metric EMOS or exclude LOW. Then re-derive the per-city winner. EMOS may still win ~half — but it is NOT a 49-sweep, and **the final calibration state is UNKNOWN until the corrected bake-off runs.** Do NOT wire any selection live until trust=TRUE.

Wiring (once trusted): serve the chosen calibrator's q + honest CI per city, shadow-first behind a flag (override `lcb_by_direction`@event_reactor_adapter.py:3138 + q serving), universal, never weakening the gate. Old scorecard files marked SUPERSEDED.

---

## 3. PRE-ARM BLOCKER MAP (severity-ranked)

**SEV1 — block a correct armed fill/exit:**
1. **DATA-INGEST DAEMON DOWN (operational, #1 alpha lever)** — `com.zeus.data-ingest` not loaded; obs+TIGGE stale since 2026-05-28; 35 cities' source_runs PARTIAL-frozen since 2026-05-30 → only 19/54 cities reach the reactor. ALSO makes source_health.json stale → blocks arm precondition #13 AND the freshness gate trades fail-open on stale data. Restore the daemon (+TIGGE/OpenData cap #28). This is infra, not code.
2. **#92 tick_size** — rejects 100% of orders pre-venue (executor.py:1746). No fill possible until fixed.
3. **#99 flood-cap** — 1000×$185/day, no rate-limiter decoupled from notional (live_cap.py:211-224). HARD pre-arm.
4. **#127 FLASH_CRASH_PANIC** — spurious exit on bare price move (exit_triggers.py:98, portfolio.py:995). GOAL#36 守護 violation.
5. **#113 CI-separation 守護 unwired** — the 120-min 守護 guarantee is false in live (event_reactor_adapter.py:3192 dead code; live exit flat 2-confirm).
6. **#128 no durable P&L** — a filled order leaves no queryable P&L record (position_current has no realized_pnl/exit_price).
7. **#129 NO-CI haircut bypassed 84.8%** — NO trades get no CI discount (market_analysis.py:823-898 dual-Platt estimator mismatch).
8. **harvester gated OFF** — settle→redeem can't fire (ZEUS_HARVESTER_LIVE_ENABLED unset).

**SEV1b — armed-safety (new from scouts):**
8b. **Freshness gate fail-OPEN** — trades continue on stale data when armed (main.py:2818). Should fail-closed (skip) on stale obs/forecast.
8c. **Force-exit MARKS-not-cancels** — riskguard RED sweep marks positions but leaves pending orders LIVE at venue in the cycle gap until M4. Close the gap (immediate cancel).

**SEV2 — correctness/durability:**
9. **#105 q-faithfulness** — UNCERTAIN; needs a live-family q-vector == p_raw_vector_from_maxes reproduction.
10. **Learning loop NOT SCHEDULED** — settlement_attribution + calibration_pairs rebuild are manual-only → settlement→calibration feedback dead. Schedule them. (settlement_outcomes forward-write itself is ACTIVE — dual-write wired, NOT a blocker.)
11. **churn** — 78% of evaluations on phase-closed markets; wasted reactor budget (#51).
12. **harvester-era latent** — harvester passes forecasts-only conn to write_settlement; UMA_OO_V2 era would query world.uma_resolution on wrong conn + silently fail. Safe today (all settlements ≥2026-02-21 INTERNAL era) but a backfill antibody.

**SEV3 — pre-scale:**
12. #96 bankroll 1800s stale-sizing + bridge position_id 28-bit collision.
13. #107 single-Kelly vs full bankroll (not a blocker at $43).

**FIXED this session:** settlement_outcomes backfill (ed8f6f22); #94 chain_shares; #103 Kelly variance; units; #91 q-domain.

---

## 4. STAGED IMPLEMENT PLAN (for the implement workflow — each step TDD + shadow-gated, NOTHING arms)

Ordering by dependency + alpha impact. Each step: {goal · files · RED test · shadow-gate · verify probe · invariant-not-to-break}.

1. **Coverage — unblock the 35 PARTIAL cities.** Investigate per-city `source_run` observed vs expected members/steps; fix the ingest completeness (download gap) OR — only if the snapshots are genuinely usable — a justified completeness re-grade (NEVER a blind gate-relaxation). RED: a PARTIAL-but-sufficient source_run with ≥N members emits FSR. Verify: FSR distinct cities rises toward 51. Invariant: no incomplete/causal-invalid run trades.
2. **Churn — stop evaluating phase-closed markets** (#51): prune settled/post-trading from the re-decision queue. Verify: phase-closed % of regret drops; live-open evaluations rise.
3. **Calibrator consolidation** — TWO sub-steps. **3a (prerequisite): REBUILD the bake-off scorer** — all methods (RAW on its real live-served calibration_pairs MC vector) through ONE identical pipeline; per-cell fit-date provenance + asserted OOS gate; separate low-metric EMOS or exclude LOW; opus critic must return trust=TRUE. RED: a deliberately-leaked cell fails the OOS assert; RAW LogLoss reproduces the live-served vector. **3b:** wire the re-derived per-city winner shadow-first behind a flag; retire flag-maze. RED: per-city served calibrator == re-derived scorecard; q-vector MECE. Verify: shadow edges clear. Invariant: universal, no future-data, buy_yes/no preserved.
4. **#92 tick_size** — align intent.tick_size to snapshot.min_tick_size at the executor (executor.py:1746). RED: a candidate with snapshot tick passes pre-venue. Verify: REJECTED tick_size count → 0.
5. **#99 rate-limiter** — add a real per-day order-emission rate-limit decoupled from the notional cap; restore a sane canary daily cap. RED: N+1th order/day blocked regardless of notional. Update test_edli_online_invariants.
6. **#127 FLASH_CRASH belief-gate** — require belief-CI confirmation (or N consecutive cycles + sanity bound) before FLASH_CRASH_PANIC exits. RED: price wiggle w/o belief change does NOT exit.
7. **#113 wire CI-separation 守護** — re-wire screen_exit using the cycle connection (no 2nd-connection SAVEPOINT deadlock) OR move CI-separation into the live evaluate_exit; + claimed-done-is-wired CI guard. RED: exit fires only when belief CI disjoint-below entry CI.
8. **#128 durable P&L** — add realized_pnl/exit_price/settlement_price/settled_at columns to position_current; write in compute_settlement_close via canonical path. RED: a settled position has queryable realized_pnl.
9. **#129 NO-CI haircut** — compute q_lcb with the SAME current Platt params as q_live (resample members only, market_analysis.py:853); guarantee q_lcb ≤ q_live. RED: q_lcb_no ≤ q_live_no for every bin.
10. **harvester enable** + **settlement_outcomes forward-write** — turn on settle→redeem (ZEUS_HARVESTER_LIVE_ENABLED) under control; wire harvester_truth_writer to also write settlement_outcomes (canonical). Verify: Shanghai resolves; settlement_outcomes stays current.
11. **#105 live-verify** — drive one live family, assert traded q-vector == p_raw_vector_from_maxes(members,bins). Close or fix.
12. **Arm-readiness checklist** (from scout a77bf0cb) — assemble the exact operator preconditions for a SAFE first canary. **[IN-FLIGHT]**

---

## 5. ARM SEQUENCE (operator-only, AFTER all SEV1 closed) — FAIL-CLOSED, verified

20-point fail-closed checklist (scout a77bf0cb; boot raises if any contract flag missing):
- **Config flags (settings.json edli_v1):** live_execution_mode `edli_shadow_no_submit`→`edli_live_canary`; reactor_mode `live_no_submit`→`live`; enabled/event_writer_enabled/forecast_snapshot_trigger_enabled=true; **market_channel_ingestor_enabled false→true**; **edli_user_channel_reconcile_enabled (G29) false→true**; **real_order_submit_enabled false→true**; live_canary_enabled→true; edli_live_scope=forecast_only; taker_fok_fak_live_enabled keep false.
- **Stage evidence files (<15min):** state/loaded_sha.json (==boot SHA), state/source_health.json, state/status_summary.json. ⚠ source_health is currently STALE (data-ingest down) → MUST restore ingest first.
- **DB state:** edli_live_order_projection.pending_reconcile=0; edli_live_cap_usage RESERVED=0.
- **Canary artifact:** state/edli_live_canary_artifact.json absent → WAITING_FOR_QUALIFYING_EVENT (first order fires + writes it); present-with-FAIL → boot raises.
- **Bridge/bankroll:** no FILL_CONFIRMED orphans; wallet reachable; restart daemon (verify PID + log delta).
- **Force-taker 5c floor:** while confirmed_fills < min_canary_count, candidates forced TAKER only if post_cross_edge ≥ 0.05 (no loss-making cross forced).

Then: operator flips `real_order_submit_enabled=true` → tight canary → first FILL → e2e-correctness-check → 3 fills → 120-min 守護. NEVER flipped without explicit operator "arm it".

---

## RESIDUAL / OPEN
- Bake-off critic trust (gates §2).
- 5 scout verdicts (gates §1 contract/source/riskguard/arm/learning rows).
- Implement workflow to execute §4 (operator sign-off pending).
