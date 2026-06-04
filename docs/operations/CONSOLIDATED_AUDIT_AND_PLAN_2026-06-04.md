# Zeus Live — Full Audit Synthesis, Evidence Catalog & Implementation Plan
**Date:** 2026-06-04 · **Author:** orchestrator (Opus) · **Scope:** live Polymarket weather-derivatives trading daemon (`com.zeus.live-trading`), disarmed (`real_order_submit_enabled=false`).

**Method.** Eight independent deep-audit agents (Opus), all read-only, each required to PROVE findings against the live DBs (`state/zeus-world.db`, `state/zeus-forecasts.db`, `state/zeus_trades.db`), the daemon logs, and the Polymarket gamma/CLOB API — not assert. Combined ~1.4M audit tokens / ~370 tool calls. Agents:
1. **Universe debugger** — root-cause the 3-city collapse.
2. **Session critic** — adversarially re-review this session's EMOS/calibration changes.
3. **Data-semantics auditor** — metric/unit/timezone/station/provenance crossings.
4. **Empty-store / fail-open auditor** — silent emptiness, residual-masking, throttles.
5. **Boundary-mismatch auditor** — cross-module key/instance/domain/enum loss.
6. **Direction/sign/extremum auditor** — wrong-side / sign / min-max / comparator.
7. **Money-path falsification** — falsify the load-bearing beliefs against live data.
8. **Fresh-eyes adversarial** — see the system new, find what doesn't make sense.

This document preserves **every** finding with its evidence, maps the ~24 findings onto **6 structural roots**, separates FIXED-this-session from OPEN, and gives a concrete, sequenced, arm-aware implementation plan to the first real FILL.

---

# PART I — STRUCTURAL SYNTHESIS (the K roots, K≪N)

The ~24 findings are symptoms of **6 structural decisions**. Fixing the root kills the category (iron rule #4/#5); patching symptoms is whack-a-mole.

### R1 — Coverage emission is order-biased (the visible "3-city universe")
The forecast-snapshot trigger emits `ORDER BY (LIVE_ELIGIBLE), computed_at DESC, snapshot_id DESC LIMIT N`. All families share ONE batch `computed_at`, so the effective tie-break is `snapshot_id DESC`, which deterministically emits only the **alphabetic tail (M–W, ~25 cities)** every cycle; **A–L (38 cities) never emit → never pending → never refreshed → incomplete families → 0 candidates**. The purpose-built coverage-fairness round-robin existed but was inert: gated by `coverage_fairness_emit_enabled=false`, and even when ON its window never advanced because the plain emit passed no `cycle-N` source (`cycle_index` frozen at 0).
**Symptoms:** "3–13 city universe"; A–L cities dark for days.
**Status:** ✅ **FIXED & LIVE-CONFIRMED** — FSR coverage went **25 → 49 cities** (A–L present). Commit `2965c88` + `42800-restart`.

### R2 — The evaluable universe is sized off a 30s price-freshness TTL, not market identity
Two throttles compound: `_FAMILY_REFRESH_CAP` (was 8) limits JIT `/book` price-refreshes per cycle; `FRESHNESS_WINDOW_DEFAULT=30s` (`executable_market_snapshot.py:27`) expires snapshots. The reader (`event_reactor_adapter.py:5605`, `freshness_deadline >= now`) therefore sees only the ~8–16 families warmed in the last 30s and returns `[]` for everything else — **indistinguishable from "no market."** Raising the cap to 50 overran the 60s reactor cycle ("max running instances reached"). Full discovery genuinely reaches 48 cities (proven: Tokyo has 11 live markets), but their snapshots expire before the throttled warm re-touches them.
**Symptoms:** even with 49 cities emitting, only ~8–16 are *simultaneously* tradeable; treadmill.
**Status:** ◐ **PARTIAL** — cap 8→16 (safe, no overrun). Proper decouple = task #178.

### R3 — Settlement-day is an un-owned dead zone (the dominant 0-fill cause)
Two scopes were designed disjoint+exhaustive over a market's life: `FORECAST_SNAPSHOT_READY` admits **only `PRE_SETTLEMENT_DAY`** (`market_phase.py:287` `FORECAST_ONLY_ADMIT_PHASES={PRE_SETTLEMENT_DAY}`); `DAY0_EXTREME_UPDATED` was to own the entire `SETTLEMENT_DAY` window. But day0 is **`RuntimeError("DAY0_OUT_OF_SCOPE_FOR_PR332")`-gated** — it was never finished. So the instant a market enters its local target day (city-local 00:00, `market_phase.py:160`), **no scope can trade it** → 64% of rejections are `EVENT_BOUND_MARKET_PHASE_CLOSED:settlement_day` (5,855 today).
**Symptoms:** ≥half of all evaluations can never trade by construction; the rejection-reason flood.
**Status:** ❌ **OPEN — structural build (PR332), NOT a flag.** Verified 2026-06-04: flipping `day0_extreme_trigger_enabled=true` **crashed boot** with the RuntimeError; reverted; daemon recovered.

### R4 — Swallowed DB/schema errors become trade-rejections
DB write faults surface as no-trade reasons on otherwise-tradeable candidates: `platt_models` UNIQUE-constraint collision (858×, ongoing, on PRE_SETTLEMENT candidates), and `edli_no_submit_receipts has no column named mainstream` (37×). Both puncture the only currently-tradeable (D-1) window.
**Status:** ❌ OPEN (tasks #174, #175).

### R5 — One belief computed on two domains (the metric/season/unit-crossing class)
Catastrophic-but-silent value mismatches at module boundaries (iron rule #4): the metric-crossing (HIGH forecast graded against LOW market), the season-crossing (NH-month table read with SH-flip season), the lcb>point inversion (un-normalized clamp vs normalized recorded point), the lead-pooled bias (60–144h fit applied to 24h forecast). Each runs fine and passes tests.
**Status:** ◐ **MOSTLY FIXED** — metric (✅), season C1/C2 (✅), unit-guard (✅). OPEN: lcb≤q_live assertion (#176), lead-bias (#177).

### R6 — Detect-but-don't-enforce
The system computes wrong-side / disagreement signals correctly but treats them as reference-only annotations: 396 `DIRECTION_AGREES_MAINSTREAM_SHORTING_LIKELY` buy_no receipts + 13 buy_no on our own forecast modal bin reached `trade_score>0`.
**Status:** ◐ **direction-law modal-veto FIXED** (buy_no on our argmax bin now unconstructable). Mainstream submit-enforce must be coupled to arm.

**One-line truth:** R3 + R2 (scopes/throttles) are why almost nothing trades. R1 was the visible symptom (fixed). **Calibration — the thing chased for 3 days — was never the blocker.**

---

# PART II — FULL FINDINGS CATALOG (per agent, with evidence)

## A1. Universe debugger (root cause of 3-city collapse)
- **Funnel (per-stage city counts):** Polymarket open markets ~49–56 → `market_events` (forecasts.db) 49 → **FORECAST_SNAPSHOT_READY emitted = 25 (M–W only)** → pending refreshed ≤8/cycle → receipts 13.
- **Root:** `src/events/triggers/forecast_snapshot_ready.py:472` `ORDER BY … snapshot_id DESC LIMIT ?` under uniform `computed_at='2026-06-04T08:26:40'` → snapshot_id-DESC emits M–W tail only. Fairness round-robin `:142` (`ordered_keys[cycle_index*limit:…]`), `cycle_index` from `source="cycle-N"` `:430`, gated by `_coverage_fairness_emit_enabled` `:40` (default False). Plain emit `src/main.py:3941` passed **no source** → cycle_index frozen 0.
- **Correction of my error:** `market_events`/`executable_market_snapshots` are **0 in zeus-world.db but populated in zeus_trades.db (341,082 ems rows) / zeus-forecasts.db (21,018 market_events)** — the K1 DB split. Market side healthy; I had queried the wrong DB.
- **Correction of "3 cities":** last hour actually had 13 cities forming receipts; the gap is **13 of 51 trading**, missing 38 = alphabetic A–L.
- **51 vs 54:** 54 = forecast roster (`test_cities_config_authoritative.py:29 ==54`, includes forecast-only no-market cities Auckland/Jakarta/Jinan/Lagos/Zhengzhou); 49 have a June-4 market; 51 = operator's tradeable universe.
- **Fix (applied):** thread `cycle-N` source into plain emit when fairness ON; `coverage_fairness_emit_enabled=true`; `emit_limit 20→50`; `_FAMILY_REFRESH_CAP 8→16`. **Verified 49 cities emit.**

## A2. Session critic (re-review of this session's EMOS work) — VERDICT: ACCEPT-WITH-RESERVATIONS
- **LOW truth VALIDATED (the scariest hypothesis, refuted with data):** `observation_instants.running_max` is the misnamed-but-instantaneous hourly temp (31% of within-day steps DECREASE — impossible for a cumulative max). Daily-MIN over the day = the low truth; validated vs **n=1100 settled lows: mean +0.06°C, median 0.00, 98% within 1°C**. Control (obs_max vs settled_low = +8.21°C) confirms min is the right extremum. **The load-bearing LOW-fit assumption HOLDS.**
- **C2 season-crossing (the twin bug it caught):** fit table is NH-month-keyed (`fit_emos_calibration.py:32`); seam correct; but shadow ledger (`event_reactor_adapter.py:4701`), EMOS-CI override (`:5008`), boot guard (`main.py:907`) used hemisphere-aware `season_from_date(lat)` → SH cities (Sao Paulo/Wellington DJF) served the OPPOSITE-season cell (μ off ±1.4°C, served flips emos↔raw). **Shadow ledger = promotion evidence → corrupted for SH.** **FIXED** (commit `dd1a493`: canonical `emos_season` + 3-key everywhere).
- **C1 table-migration miss:** `main.py:908`, `event_reactor_adapter.py:4746`, `calibration_bakeoff.py:113`, `validate_analytic_ci_coverage.py:271` used OLD 2-key `city|season` against the now-3-key table → every lookup None → `served=missing` → EMOS-CI license silently drops ALL cities when armed. **FIXED.**
- **M1 (LOW served without forward monitor):** sole-calibrator seam serves LOW live but `score_emos_forward.py:389` filters `metric=='high'` → LOW has zero forward-settlement observability. **OPEN — shadow-acceptable; must extend the forward monitor before any LOW promotion.**
- **M2 (hard-2026-regressors served emos):** gate was fit2024→gate2025 only; Tel Aviv/Jeddah JJA low shipped emos at ~3× worse CRPS; gate tested `p24` but served `pf`. **FIXED** (commit `bd97ddd`: 2026-regime arm → 0/150 OOS regressions; HIGH left byte-identical via hybrid table).

## A3. Data-semantics auditor — VERDICT: ACCEPT-WITH-RESERVATIONS (live path clean)
Could not find a third live metric/unit/season/day catastrophe; the live q-seam is fortified.
- **Refuted:** unit mismatch at settlement (`_assert_settlement_unit_identity` adapter:3632 fail-closed; F cities store °F, C store °C; sigma scales ×1.8 scale-only); metric-blindness (`model_bias_ens` keys on metric; Platt embeds metric in `model_key`); settlement-day vs target-date (local-calendar-day aggregation aligns; DST handled by named-tz); `running_max` monotone-misread (Day0 reader uses MAX aggregation, documents non-monotone).
- **Minor #1 (latent unit-safety, offline):** `settlement_resolution.py:132,149` `from_settlement_row` grades via `grid.bin_for_value(settlement_value)` with NO assert that `row['settlement_unit']==grid_unit`. **FIXED** (fail-closed guard added).
- **Minor #2 (footgun):** `model_bias_ens` SH-flip+month vs EMOS NH-month conventions one import apart; add a freeze-test. **OPEN (doc/test).**
- **Minor #3:** dead `season_from_date` import at `event_reactor_adapter.py:5001`. Cosmetic.
- **Recommended next lane:** the EXIT belief recompute (`monitor_refresh.py`/`portfolio.py`) — entry corrected, exit reads a 0-row `full_transport_v1` family (flag-gated OFF), so entry/exit assign different meaning to the same position's belief. **OPEN — untraced.**

## A4. Empty-store / fail-open auditor — VERDICT: REVISE (root + several silent-residual defects)
- **C1 (the universe throttle):** `main.py:2704 _FAMILY_REFRESH_CAP=8` × `executable_market_snapshot.py:27` 30s TTL × reader `event_reactor_adapter.py:5605`. Full discovery logged `executable_candidate_city_count=48, coverage_status=PARTIAL`, but per-cycle warm `families_checked=8`. Live DB: only **26 snapshots with freshness_deadline≥now**. **This IS the "3-city universe."** Fix = decouple identity-sizing from price-freshness (#178).
- **C2 (dead writer + corpse):** `config/settings.json` `market_channel_ingestor_enabled:false`; gate `event_reactor_adapter.py:5427`. `BEST_BID_ASK_CHANGED`/`BOOK_SNAPSHOT` stopped **2026-06-01T09:33**; **234,845 + 9,632 events stuck `pending`** under `edli_reactor_v1` (NULL city). **RE-ENABLED this session** (`market_channel_ingestor_enabled:true`); the 244k corpse still needs dead-lettering.
- **M1:** redecision queue saturated — `enqueued=50 cap=50 skipped_pending≈899` every cycle. Standing ~900 backlog never drains.
- **M2:** slug-discovery fallback probes only ~18–28 of 378 jobs/scan (`market_scanner.py:1585 ZEUS_MARKET_DISCOVERY_SLUG_MAX_REQUESTS=28`); cursor rotates (slow-tail). Secondary (tags are primary).
- **M3:** `opportunity_fact`=0 rows; cycles yield 1–3 proof_accepted, rest `settlement_day` / `TRADE_SCORE_NON_POSITIVE`. Settlement-day families dominate the surviving residual (ties to R3).
- **M4:** user-channel (fills) websocket crash-loop (attempts 3/4 stopped 02:23; SSL UNEXPECTED_EOF + VENUE_AUTH_FALLBACK). Inert pre-arm; needs backoff + DATA_DEGRADED surfacing.
- **Missing observability:** no "executable universe size ≥ N" health gate; no dead-letter TTL for stuck pending; `model_bias` table 0 rows in forecasts.db (readers fail-soft to identity — follow-up).

## A5. Boundary-mismatch auditor — VERDICT: ACCEPT (reactor q/calibration/verdict pipeline intact)
- Traced the 5-hop chain `build_event_bound_no_submit_receipt → _generate_candidate_proofs → _live_yes_probabilities → _canonical_probability_and_fdr_proof → _evaluate_and_store_mainstream_agreement`; instance-identity holds at every hop. The `_payload(event)` fresh-parse pattern is self-consistent (each gate's write→read is instance-local).
- **Minor (real):** `_edli_q_source` dead write `event_reactor_adapter.py:3752` — zero readers repo-wide → cannot audit which calibrator served a receipt (the **#120 observability gap**). If meant for receipt provenance, the consumer is missing.
- **Stale docstring:** `emos_q_builder.py:48` still says "single-metric HIGH-only" though table is now `_meta.metric="multi"` (no runtime gate reads it; 3-key lookup is correct — cosmetic but Fitz-#4 currency risk).
- **NOT traced (next-pass):** `evaluator.py:6937` (`json.loads(json.dumps())` tuple→string risk), `:7028`; `replay.py:820-844,926-930` (re-derives p_raw/p_cal/members from separate JSON columns — possible mismatched-vintage); `monitor_refresh.py` exit-q domain; `collateral_ledger.py` int-dict; `executor.py:242` deep-copy round-trip.
- **Structural recommendation:** parse `_payload(event)` ONCE per event and thread the single instance (make the fresh-parse divergence unconstructable — the original `_payload` bug class).

## A6. Direction/sign/extremum auditor — VERDICT: REVISE
- **Major #1 (lcb>point inversion at scale):** **26,017 / 60,411 (43%) buy_no live receipts had `q_lcb_5pct > q_live`** (worst gap 0.79: q_live=0.146, q_lcb=0.941). Clamp `market_analysis.py:1128` caps `q_no_lcb ≤ 1 - p_posterior[bin_idx]` (UN-normalized); recorded `q_live = proof.q_posterior = 1 - yes_q` where `yes_q` is the **normalized** `evaluate_live_bins().normalized()` (`event_reactor_adapter.py:3463`, `inference_engine.py:36`). When the family's posteriors sum to S<1, `1 - p/S < 1 - p` → clamp ceiling exceeds the point. **Contained by `min(q_5pct, q_posterior)` in `trade_score.py:48` (no wrong-size capital); ~0 inversions at HEAD on 06-04** (commit `a5c6812368` widened the raw 5th-pctile). Latent domain mismatch remains. **OPEN (#176).**
- **Major #2 (wrong-side buy_no reference-only):** `mainstream_agreement.py:337-350` checks 3&4 correctly; **396 live buy_no carry `DIRECTION_AGREES_MAINSTREAM_SHORTING_LIKELY`**; enforcement `event_reactor_adapter.py:366` is fail-closed (`is not True`) but double-gated OFF (`real_order_submit_enabled` AND `mainstream_agreement_enforce_on_submit`). **Couple to arm.**
- **Proven clean:** buy_no q_lcb is independently tail-grounded (`_bootstrap_bin_no`, NOT 1−q_lcb_yes); bias-correction sign warms cold cities (`corrected=members−(forecast−actual)`); MIN/MAX metric-gated (`RemainingMemberExtrema.for_metric` makes wrong-extremum unconstructable); WMO half-up + `Bin.contains` inclusive correct; double-mean-correction antibody fail-closed.
- **Orchestrator addition (FIXED this session):** found **13/1466 live buy_no would-trade on our OWN forecast modal bin** (Paris buy_no on 20°C while our point=20.8; NYC buy_no on 64-65°F low, point 64.8). Added **direction-law veto: buy_no on `argmax(p_posterior)` bin is unconstructable** (`market_analysis.py`, pre-edge), regardless of price. buy_no on non-modal bins unaffected.

## A7. Money-path falsification — VERDICT: REVISE (core beliefs hold; one structural defect)
Probed the 3 actively-trading cities (Qingdao high, Seoul low, NYC low — all target 06-05, all buy_no) vs Polymarket + reconstructed q from raw ECMWF.
- **Beliefs that HELD (tried to break, could not):** target_date = local settlement day (no off-by-one); station resolves correctly (Qingdao ZSQD matches despite stale `airport_name` label); q_live = `proof.q_posterior` (model belief, not a market quote, `event_reactor_adapter.py:801,827`); the system is simply disarmed.
- **Major (real, CRITICAL-on-arm): lead-mismatch bias.** `read_bias_model` (called `event_reactor_adapter.py:4215`) keyed `(city, season, metric, data_version, month)` with **NO lead param**. Selected Qingdao-high row `lead_bucket=LEGACY_POOLED`, `lead_band_hours=[60,144]`, fit on 7 pairs at 2.5–6d lead. **Live snapshot lead=24.0h.** Empirical Qingdao-high bias at 24h = **+0.21°C (n=16)**, but the system applies **+0.609°C** from the 60–144h pool. Net ~+0.4°C warp on every live q; can flip near-mode bins. **Fix:** lead_bucket in key + live lookup, OR fail-closed when snapshot lead ∉ band. **OPEN (#177).** (Initial −4.9°C "catastrophe" RETRACTED after re-pairing at correct lead — magnitude honest, the defect is lead-pooling.)
- **Minor:** receipt flat `mainstream_bin_label` ("17°C" mainstream) ≠ `receipt_json.bin_label` ("19°C" traded) — querying the column gives the WRONG traded bin. Qingdao `airport_name="Liuting"` stale (resolving ZSQD matches). Three DBs each hold `edli_no_submit_receipts`/`settlements`/`position_current` — confirm readers resolve K1-canonical.
- **Open Q:** all 3 active cities buy_no, never buy_yes — confirm the system CAN fire buy_yes (else one-sided by construction).

## A8. Fresh-eyes adversarial — the systemic roots
- **Finding 1 (SYSTEMIC ROOT = R3):** the only live scope is structurally forbidden from settlement-day; the day0 owner is OFF. `market_phase.py:287` admit set; `day0_extreme_trigger_enabled=False`; day0 emits 0 events all-time. **This best explains "nothing trades in days."** (Verified this session: it's `RuntimeError`-gated, PR332.)
- **Finding 2 (= R4):** `platt_models` UNIQUE-collision (`store.py:540`) swallowed → 858× as no-trade reason on 06-05 (PRE_SETTLEMENT) candidates. Sibling: `mainstream` column drift (37×).
- **Finding 3 (calibration smell = the flood):** 30,059 scored buy_no, median q_live 0.999, 84% >0.95 — manufactured OTM-tail edges; only FDR (2,165) + Kelly (5,173) gates stop a flood. Possible YES-price/NO-probability mispairing in the buy_no proof worth auditing (`money_path_adapters.py:87`). **EMOS dispersion is the structural answer (deployed).**
- **Finding 4:** order-book event stream died 2026-06-01 (ingestor off — = A4-C2). Live pricing comes from direct CLOB GET, so not necessarily stale, but the event lane is an unmonitored failure.
- **Lesser:** `live_health_composite.json` business_plane = `CANDIDATE_COUNTER_MISSING/DEGRADED` (health surface already knows candidates aren't flowing, but heartbeat says "alive"); `decision_events`/`position_current`/`settlements`=0 rows, `no_trade_regret_events`=129,933 (the system's entire output is regret rows); `EVENT_BOUND_SELECTED_CANDIDATE_MISSING` (2,106), `FSR_SOURCE_RUN_NOT_COMPLETE` (1,012).

---

# PART III — FINDING → ROOT → STATUS MATRIX

| Finding | Agent | Root | Severity | Status |
|---|---|---|---|---|
| FSR snapshot_id-DESC emission bias | A1,A8 | R1 | CRIT | ✅ FIXED (49 cities) |
| Refresh-cap × 30s TTL throttle | A4 | R2 | CRIT | ◐ cap 8→16; decouple #178 |
| Settlement-day dead zone (day0 PR332) | A8,A4-M3 | R3 | CRIT | ❌ build, not flag |
| platt_models UNIQUE swallowed | A8 | R4 | CRIT | ❌ #174 |
| `mainstream` column drift | A8,A7 | R4 | MAJOR | ❌ #175 |
| metric-crossing (HIGH→LOW) | (pre-fleet) | R5 | CRIT | ✅ FIXED |
| season-crossing C1/C2 | A2 | R5 | CRIT | ✅ FIXED |
| lcb>q_live inversion | A6 | R5 | MAJOR | ◐ #176 (contained) |
| lead-mismatch bias | A7 | R5 | MAJOR | ❌ #177 |
| LOW served, no forward monitor (M1) | A2 | R5 | MAJOR | ❌ pre-LOW-promote |
| 2026-regressors served emos (M2) | A2 | R5 | MAJOR | ✅ FIXED |
| wrong-side buy_no reference-only | A6 | R6 | MAJOR | ◐ modal-veto ✅; arm-couple |
| buy_no-on-our-modal | A6(orch) | R6 | MAJOR | ✅ FIXED |
| buy_no q degeneracy (flood) | A8 | R5/R6 | MAJOR | ◐ EMOS dispersion deployed |
| market_channel_ingestor off + 244k corpse | A4,A8 | R2/obs | MAJOR | ◐ re-enabled; dead-letter TODO |
| redecision backlog 900 saturated | A4-M1 | R2 | MAJOR | ❌ |
| `_edli_q_source` dead write (#120) | A5 | obs | MINOR | ❌ |
| bin_label column ≠ traded bin | A7 | R5 | MINOR | ❌ |
| settlement_resolution unit guard | A3 | R5 | MINOR | ✅ FIXED |
| exit-belief asymmetry (untraced) | A3 | R5 | MAJOR? | ❌ next lane |
| user-channel websocket crash-loop | A4-M4 | obs | MINOR | ❌ pre-arm inert |
| slug-discovery 28-req cap | A4-M2 | R1-tail | MINOR | ❌ |
| evaluator/replay boundaries untraced | A5 | R5 | ? | ❌ next-pass |
| bias-vs-EMOS season convention footgun | A3 | R5 | MINOR | ❌ freeze-test |

---

# PART IV — IMPLEMENTATION PLAN (concrete, sequenced, arm-aware)

**Principle:** Phase A–B require NO capital (safe, reversible, shadow). Phase C is a build. Phase D crosses the arm line only after A–B prove clean (iron rule #6).

### Phase A — unblock the surviving (D-1) window
**#174 platt_models UNIQUE-collision → idempotent upsert.**
- Files: `src/calibration/store.py:~540` (save), the generic catch that stores the DB error as a rejection reason.
- Change: `INSERT … ON CONFLICT(<unique cols>) DO UPDATE SET …` (or `INSERT OR REPLACE`) so a re-fit of the same (metric,cluster,season,data_version,input_space,is_active,cycle,source_id,horizon_profile) updates rather than throws. Stop the upstream catch from writing raw `IntegrityError` strings into `rejection_reason`; classify DB faults as `CALIBRATION_STORE_DEGRADED` (loud) not a per-candidate no-trade.
- RED test: write the same platt key twice → second must update, not raise; a reactor candidate must NOT get a DB-error rejection reason.
- Verify: live `no_trade_regret_events` stops accruing `UNIQUE constraint failed` reasons.

**#175 `edli_no_submit_receipts.mainstream` column drift.**
- Files: the receipt-writer (`src/events/no_submit_receipts.py` insert) vs the live `edli_no_submit_receipts` schema (`src/state/schema/edli_no_submit_receipts_schema.py`).
- Change: determine whether the writer means an existing column (rename) or a genuinely-missing one; add the column via `_ensure_column` migration if real. Operator-run migration on the live world DB (K1).
- RED test: insert a receipt with the mainstream field set → row persists, no `no such column`.
- Verify: the 37×/period write-failure log stops.

**#176 lcb ≤ q_live boundary antibody.**
- Files: `src/engine/event_reactor_adapter.py:3136-3141` (restore) + `src/strategy/market_analysis.py:1128` (clamp).
- Change: make the clamp reference the SAME domain as the recorded point — clamp `ci_lo` against the **normalized** q_no the adapter records (or record q_live un-normalized). Add a fail-closed `assert q_lcb_5pct <= q_live + ε` at the restore site so the inversion is unconstructable regardless of which leg's domain drifts (the boundary-crossing relationship test the original miss lacked).
- RED test: an S<1 family must NOT produce q_lcb>q_live.

### Phase B — correctness before any capital
**#177 lead-mismatch bias.**
- Files: `src/calibration/ens_bias_repo.py` (`read_bias_model`), caller `event_reactor_adapter.py:4215`, `src/contracts/bias_treatment.py`.
- Change: thread `lead_hours`/`lead_bucket` into the bias key + live lookup; OR fail-closed (skip correction, served=raw) when the live snapshot lead ∉ the row's `lead_band_hours`. Never pool LEGACY across leads for live trading.
- RED test: a 24h snapshot must NOT receive a 60–144h-band bias; `assert bias.lead_band contains snapshot.lead_hours`.

**#178 decouple universe sizing from the 30s TTL (R2).**
- Files: `src/main.py:2704` (`_FAMILY_REFRESH_CAP`), `src/engine/event_reactor_adapter.py:5587-5636` (reader; the design comment already endorses "freshness 针对价格不针对市场"), `executable_market_snapshot.py:27`.
- Change: size the evaluable universe off **market-identity presence** (`require_fresh=False`), enforce the 30s freshness ONLY at the submit gate (`assert_snapshot_executable`). Make the price-warm batch/async so all ~49 families stay <30s without serial `/book` overrun. Add a loud "executable universe size ≥ N" health gate so a collapse to 3 alarms instead of looking healthy.
- Verify: simultaneously-tradeable cities ≈ universe, reactor cycle stays <60s.

**Arm-coupling (R6).** Boot-guard: refuse to set `real_order_submit_enabled=true` unless `mainstream_agreement_enforce_on_submit=true`. So arming can never leave the wrong-side detector advisory.

### Phase C — settlement-day scope (the big build, R3)
**PR332 day0.** Finish the observation-aware `DAY0_EXTREME_UPDATED` path so day0 owns `SETTLEMENT_DAY` (currently `RuntimeError`). Until built, the system trades only `PRE_SETTLEMENT_DAY` (D-1) — acceptable for the first fills. This is multi-file (the day0 trigger, the hard-fact online caller, the scope assertion `_assert_edli_live_scope`, the absorbing-mask q path) — scope and estimate separately; do NOT flag-flip.

### Phase D — the first fill (#179)
With A–B clean + the safe scopes online, find ONE candidate clearing EVERY real gate (phase, trade_score>0 after cost, FDR, Kelly, cert dual-chain, mainstream-agreement internal+external, direction-law) on a D-1 market, positive after-cost edge, direction-law-correct, matching our forecast AND external mainstream. **Then arm → real fill (programmatic, no manual completion) → durable P&L row → 120-min 守護 with evidence-gated exits.** Repeat to 3 fills (GOAL DONE).

---

# PART V — ARM-READINESS GATE (the exact line to a fill)
Arm (`real_order_submit_enabled=true`) only when ALL hold (iron rules #2/#6):
1. Phase A done — no swallowed-DB rejections, receipts write.
2. Phase B done — bias lead-matched (or fail-closed), q_lcb≤q_live enforced, enforce-on-submit coupled.
3. A specific candidate: D-1 market, `trade_score>0` after cost, FDR-pass, Kelly-positive at the live on-chain bankroll, cert dual-chain present, `direction_agrees_our_modal=True`, `mainstream_agreement_pass=True` (matches external forecast), direction-law-correct.
4. q_lcb honest (not artifact); Fractional-Kelly size within forward-settlement-licensed risk.
Arming before 1–2 = ruin (a manufactured/lead-warped/inverted-lcb edge sized by Kelly).

---

# PART VI — DELIBERATE STATES (config), not bugs
- `real_order_submit_enabled=false` — disarmed (no fills by design until armed).
- `day0_*=false` — settlement-day scope; **`RuntimeError`-gated (PR332), a build not a flag.**
- `market_channel_ingestor_enabled` — **re-enabled this session** (was the dead book-event lane).
- `mainstream_agreement_enforce_on_submit=false` — reference-only by 2026-06-03 directive; couple to arm.

---

# PART VII — SESSION MISTAKES & CORRECTIONS (so the next session does not repeat)
1. **Wrong DB:** declared "market tables empty" by querying `zeus-world.db` for tables in `zeus_trades.db`/`zeus-forecasts.db` (K1 split). Market side was healthy (341k ems rows).
2. **refresh_cap=50** overran the 60s cycle → reverted to 16.
3. **Flag-flipped day0 → crashed boot** (`DAY0_OUT_OF_SCOPE_FOR_PR332`); reverted; daemon recovered. day0 is PR332, a build.
4. Briefly mis-called the fairness fix "wrong path" right before the monitor confirmed 49 cities.
5. Over-counted direction-law violations (used `|traded−our_point|≤1`); the real veto is `bin==argmax` only.
6. M2 over-reach: re-fit perturbed live HIGH params (broke golden tests) → rebuilt as a hybrid (HIGH untouched + 2026-gated LOW).

---

# APPENDIX — key file:line index
- Universe: `src/events/triggers/forecast_snapshot_ready.py:40,142,430,472`; `src/main.py:2704,3918,3941,3960`.
- Phase gate / day0: `src/strategy/market_phase.py:160,287`; `src/engine/event_reactor_adapter.py:596-599,704-718` (forecast-only phase), day0 RuntimeError guard (PR332).
- Calibration: `src/calibration/store.py:540`; `src/calibration/emos.py` (`emos_season`,`emos_cell_key`); `src/calibration/ens_bias_repo.py` (`read_bias_model`); `src/calibration/emos_q_builder.py:48`.
- Direction/lcb: `src/strategy/market_analysis.py:1028-1135` (`_bootstrap_bin_no`,clamp:1128), buy_no loop (modal veto); `src/strategy/mainstream_agreement.py:337-350`; `src/strategy/live_inference/{inference_engine.py:36,trade_score.py:48}`; `src/engine/event_reactor_adapter.py:366,3136-3141,3463,3632,3752,4215,5406`.
- Freshness/universe: `src/contracts/executable_market_snapshot.py:27`; `src/engine/event_reactor_adapter.py:5427,5587-5636`; `src/data/market_scanner.py:1585`.
- Settlement: `src/contracts/settlement_resolution.py:132,149`; `architecture/db_table_ownership.yaml:620,676` (K1).

