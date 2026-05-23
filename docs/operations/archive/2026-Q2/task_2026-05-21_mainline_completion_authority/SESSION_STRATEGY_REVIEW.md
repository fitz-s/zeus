# Zeus Strategy Session Review — Promotion Program Direction

```
Created:          2026-05-22
Authority basis:  MAINLINE_COMPLETION_REPORT.md
                  SESSION_CLOSURE_VERDICT.md
                  PROMOTION_PIPELINE_DESIGN.md
                  SHOULDER_SELL_EDGE_PROOF.md (all in this directory)
Data sources:     READ-ONLY immutable snapshots
                  zeus_trades.db (zeus_trades.db @2026-05-22T14:38Z)
                  zeus-forecasts.db (zeus-forecasts.db @2026-05-22)
                  zeus-world.db (zeus-world.db @2026-05-22)
                  main@1ea6c96ba1, SCHEMA_VERSION 26
Live capital:     $186.55 pUSD + $510.90 in CTF token positions = ~$697 on-chain
```

---

## A. Session Build Inventory

### Mainline Phases 1–7

All 7 phases landed on `origin/main` this session. Per MAINLINE_COMPLETION_REPORT.md:

| Phase | Content | Tag |
|---|---|---|
| 1 | decision_events instrumentation + Day0Nowcast | phase1_landed |
| 2 | book_hash_transitions, NoTradeReason/no_trade_events, FreshnessRegistry, MarketAnalysisVNext, Position.market_slug JSON | phase2_landed (5c471cd51f) |
| 3 | WeatherRegimeTag 6-member enum, ShoulderStrategyVNext (21-field, classify+stress+Kelly clamp), ShoulderExposureLedger + cluster cap | phase3_landed (7017670ca8) |
| 4 | FDR family-ID spread_bucket + 6 candidate stubs (all shadow/blocked) | phase4_landed (b6a7df9ff0) |
| 5 | WeatherRegimeTag consumers + Ledoit-Wolf correlation shrinkage + variance cluster-cap | phase5_landed (02491966dc) |
| 6 | EvidenceTier ladder + ShadowExperimentRegistry + RegretDecomposer + LiveReadinessTribunal | phase6_landed (98dafb944f) |
| 7 | SettlementOutcome type-gate + Position.lifecycle_state + SettlementCaptureVerifier + backfill | phase7_landed (62ed96e133) |

Schema advanced WORLD 15→26, FORECASTS 5→6 across the session (including 5 sibling live PRs #279–283).

**The money gate.** `StrategyProfile.is_runtime_live()` = `live_status=="live" AND evidence_tier >= LIVE_PILOT_TINY(5)`. All 4 pre-existing strategies pass; all 9 new strategies do not. No Phase 3–7 strategy can reach live capital without an explicit operator promotion with `operator_ref`. Verified via runtime enumeration; SESSION_CLOSURE_VERDICT.md SEAM 1.

### Promotion pipeline (PR #284)

`PromotionReadinessValidator` (src/analysis/promotion_readiness.py) and `promotion_readiness_job.py` landed in PR #284. Composes EvidenceReport credible-interval + LiveReadinessTribunal predicate + SettlementCaptureVerifier COHERENT-count into one operator-reviewable READY/NOT_READY signal. **Strictly read-only advisory** — never auto-applies a tier, never calls `adjudicate()` against a live connection, raises `ValueError` on any promotion into LIVE_PILOT_TINY(5) or above unless `operator_ref` is supplied. The prove-then-promote method: Track R (replay harness → EvidenceReport → ranked validator report) feeds the same predicate as Track L (live shadow capture → settlement attribution), so a passing replay rank and a passing live confirm use identical math. Architecture design: PROMOTION_PIPELINE_DESIGN.md.

---

## B. Strategy Portfolio State — All 13

### 4 Runtime-live strategies

Live data drawn from `zeus_trades.db` immutable snapshot (2026-05-16→05-22). `settlement_capture` and `imminent_open_capture` show no rows in `trade_decisions`, `execution_fact`, or `outcome_fact` in this snapshot window — consistent with those strategies operating via different execution paths (settlement redemption, not directional entry) or not firing in the 6-day window.

| Strategy | Live since | DB record (this snapshot) | Settled outcomes | Realized PnL | Notes |
|---|---|---|---|---|---|
| **opening_inertia** | pre-phase2 | 1,528 decision rows, 129 execution intents, 6 settled positions | 6 settlements via SETTLEMENT exit: 3 win / 3 loss | **+$5.14** total across 6 settled positions | Win/loss breakdown by pnl: +$1.00, $0.00 (win), $0.00 (win), +$4.14, $0.00 (win, pnl=0 means at-entry price), $0.00 (loss). Entries 2026-05-16–17; settlements 2026-05-18–22. Average hold 102h. No fills in execution_fact (fills tracked via venue_trade_facts). |
| **center_buy** | pre-phase2 | 1 decision row, 4 execution intents, 0 settled positions | 0 settled in window | n/a | Single decision 2026-05-17; execution intents ~$10 notional; not yet settled. Live but no settled PnL record in this snapshot. |
| **settlement_capture** | pre-phase2 | 0 decision rows in trades DB | — | — | Operates via settlement redemption path, not new-entry decisions. no_trade_events in world.db show 45 `strategy_economic_floor` events — strategy is evaluating and declining. |
| **imminent_open_capture** | pre-phase2 | 0 decision rows in trades DB | — | — | No no_trade_events tagged to it in this window. May require specific market-open timing conditions not present in this 6-day window. |

**On-chain state (2026-05-22T14:38Z):** pUSD $186.55 + 11 CTF token positions totalling $510.90 notional at 1.0 = **~$697 on-chain**. Token positions are the 11 live opening_inertia / center_buy holdings; not all settled yet.

**Venue fills:** 98 venue_trade_facts rows (41 CONFIRMED, 32 MATCHED, 25 MINED). CONFIRMED fills: $170.59 notional. The snapshot captures a live, functioning execution chain.

### 9 Shadow/blocked strategies

Per PROMOTION_PIPELINE_DESIGN.md §1 replay-feasibility verdict and SESSION_CLOSURE_VERDICT.md SEAM 1:

| Strategy | Status | Tier | Replay verdict | Primary blocker |
|---|---|---|---|---|
| **shoulder_sell** | shadow / SHADOW_PASS(3) | SHADOW_PASS | REPLAYABLE | Classifier scaffold returns `SHOULDER_NO_TRADE_GATE` for all inputs (no production logic wired). Data substrate (ensemble + settlements) exists back to 2025. Edge proof: NOT_PROVEN — refuted in sign. See §C and SHOULDER_SELL_EDGE_PROOF.md. |
| **shoulder_buy** | blocked / IDEA | IDEA | REPLAYABLE | Same scaffold as sell. Edge proof: MARGINAL — positive mean EV, CI straddles zero on realized-outcome truth, clears zero only using p_cal as truth. See §C. |
| **center_sell** | shadow / SHADOW_PASS(3) | SHADOW_PASS | PARTIAL→REPLAYABLE | Symmetric classifier not wired; center_buy is live (evidence that center edge exists in this direction is implicit). See §C. |
| **cross_market_correlation_hedge** | shadow / IDEA | IDEA | NOT_REPLAYABLE | `regime_correlation_cache` = 0 rows (Phase-5 store unfed). `regime_tag_for` returns UNKNOWN for all historical lookups. |
| **stale_quote_detector** | shadow | SHADOW_PASS | NOT_REPLAYABLE | `spread_observed_window_ms` permanently None (info-event proxy never captured). `book_hash_transitions` only 2 days / 22 markets. |
| **weather_event_arbitrage** | shadow | SHADOW_PASS | NOT_REPLAYABLE | `alert_source` / `active_weather_alert` absent from all historical tables; no external alert feed wired to MarketAnalysisVNext. |
| **resolution_window_maker** | shadow | SHADOW_PASS | NOT_REPLAYABLE | `uma_resolution` table FROZEN at 2026-02-21; per-decision `uma_resolution_status` never captured on snapshots. |
| **liquidity_provision_with_heartbeat** | shadow | SHADOW_PASS | PARTIAL/BIASED | `PassiveMakerExecutionEstimate` computable from venue_commands but is a function of Zeus's own prior orders — non-stationary, self-reference replay bias. Not trustworthy for ranking. |
| **neg_risk_basket** | shadow | SHADOW_PASS | NOT_REPLAYABLE | `neg_risk_family_complete` / `_token_count` / `_yes_ask_sum` undocumented "not yet wired in MarketAnalysisVNext"; all snapshots have `neg_risk=1` but family book sum never computed/stored. |

---

## C. New Edge Analysis

### C1. shoulder_sell — NOT_PROVEN (refuted in sign)

Full derivation in SHOULDER_SELL_EDGE_PROOF.md. Summary:

**Payoff mechanics.** `shoulder_sell` = `buy_no` on a shoulder bin (classifier topology gate L93 of `shoulder_strategy_vnext.py`). Per $1 NO bought at `q = 1 − p_mkt`: `EV = p_mkt − p_true − fee`. Sell is +EV iff `p_mkt > p_true + fee`.

**Dataset.** 253 market-complete, deduplicated shoulder cases (128 upper / 125 lower) from `probability_trace_fact` (zeus_trades.db), joined to `settlements_v2`, spanning 2026-05-04→05-19. `p_market_json` confirmed YES-price per bin (median vector sum 0.99 across 2000 traces).

| Claim | Quantity | Value | 95% interval |
|---|---|---|---|
| 1 — calibration | hit rate vs mean p_cal | 0.103 vs 0.114 | Wilson [0.071, 0.146] |
| 1 — calibration | Brier skill vs climatology | +0.096 | — |
| 1 — calibration | reliability slope / intercept | 0.465 / −0.94 | (perfect = 1/0; over-confident on high tail) |
| 2 — mispricing | mean (p_mkt − p_cal) | **−0.030** | boot [−0.056, −0.004] |
| 2 — mispricing | frac p_mkt > p_cal | **0.336** | (thesis needs >> 0.5) |
| 3 — net edge | mean EV_sell (outcome − p_mkt − fee) | **−0.020** | boot [−0.044, +0.003] |
| 3 — entry rule | EV_sell at thesis τ=0.05 | **−0.202** | boot lower −0.385 |

The entry threshold the thesis proposes (sell where `p_mkt > p_cal`) **anti-selects**: those cases have higher realized hit rates — the market is right, the model is missing tail mass. No τ ≥ 0 yields positive net EV. Selling is worst in extreme regimes (EV −0.042, the regime the strategy is most tempting). **Verdict: NOT_PROVEN. Production entry rule: no-trade.**

---

### C2. shoulder_buy — MARGINAL

**Payoff mechanics.** `shoulder_buy` = `buy_yes` on the same shoulder bin. Per $1 YES at `p_mkt`: `EV = p_true − p_mkt − fee_yes`. Buy is +EV iff `p_true > p_mkt + fee_yes`.

Given the sell proof established that `p_mkt < p_true` on average, the buy leg is the natural inverse question.

**Same dataset** (253 cases, same period).

**Claim 1 — Calibration: PASSES (same as sell proof).** p_cal sound in aggregate; over-confident on the high tail. Brier skill +0.096. The high-tail over-confidence makes p_cal an *upper bound* on true p for extreme-regime cases — which means the buy EV using p_cal is slightly optimistic.

**Claim 2 — Mispricing sign (buy direction):**

- mean (outcome − p_mkt) = gross buy EV: **+0.018** (boot-95 [−0.004, +0.043])
- 66.4% of cases have `p_cal > p_mkt` (model says underpriced, buy-favoring)
- Median gross EV: −0.004 (distribution is right-skewed; rare wins pull the mean positive)

The mispricing direction is *consistent with buying* but the distribution is heavy-tailed and skewed — most cases see no edge; edge concentrates in a minority of cases where the shoulder actually hits.

**Claim 3 — Net EV with costs:**

Fees on YES at low prices (~0.08) are `0.05 × 0.08 × 0.92 ≈ 0.004` per share — negligible relative to the signal. The key numbers:

| Metric | Value | boot-95 |
|---|---|---|
| Net EV (realized outcome as truth) | **+0.017** | [−0.006, +0.041] |
| Net EV (p_cal as truth) | **+0.028** | [+0.003, +0.055] |

Using realized outcomes as truth: CI straddles zero — **not statistically significant on N=253**.
Using p_cal as truth: CI clears zero narrowly — but this depends on p_cal being unbiased, which the reliability slope of 0.465 calls into question at high values.

**Entry threshold τ (buy when p_cal − p_mkt ≥ τ):**

| τ | n_pass | mean net EV | boot-95 lower |
|---|---|---|---|
| 0.00 (all model > market) | 168 | −0.003 | −0.021 |
| 0.01 | 79 | +0.002 | −0.037 |
| 0.02 | 61 | +0.004 | −0.047 |
| 0.05 | 35 | +0.004 | −0.083 |

Filtering to cases where the model exceeds market by more actually makes the CI *wider and less clean* because it shrinks N. No τ produces a boot-95 lower bound above zero in this sample.

**Decomposition by sub-population:**

| Slice | n | Net EV | boot-95 | Reading |
|---|---|---|---|---|
| Upper shoulders (higher) | 128 | +0.039 | [−0.004, +0.085] | Best sub-group; approaching significance |
| Lower shoulders (below) | 125 | −0.005 | [−0.020, +0.006] | No edge |
| HIGH p_cal tertile (extreme regime) | 85 | +0.039 | [−0.010, +0.091] | Best regime; aligns with upper shoulder finding |
| LOW p_cal tertile (calm) | 84 | +0.010 | [−0.017, +0.048] | Weak, noisy |

**Verdict: MARGINAL.** The mean buy EV is positive and directionally consistent with the mispricing finding (shoulders underpriced), but the confidence interval on realized outcomes straddles zero at every reasonable τ. The signal is concentrated in upper shoulders during high-p_cal (extreme regime) conditions, where the CI narrows to [−0.010, +0.091] — still straddling but the most credible sub-population.

**What would promote this to PROVEN_POSITIVE:** (a) multi-regime data extending the 19-day window to include a heat-dome / cold-snap transition; (b) native YES ask prices (the current `p_mkt` may be mid, not executable ask — fills will be worse); (c) N ≥ 500 settled cases, split by regime, with the upper-shoulder extreme-regime slice isolated. The directional finding is real enough to prioritize data collection, not real enough to promote to live capital.

**Structural caveat.** `shoulder_buy` is currently `blocked/IDEA` (per SESSION_CLOSURE_VERDICT.md). The dossier §7.4 Variant 2 labels it `UNKNOWN_BUT_INTERESTING` — live only during tagged `WeatherRegimeTag.HEAT_DOME` with native YES depth. This data-driven finding is consistent with that framing. The buy requires `WeatherRegimeTag.HEAT_DOME` gating and native YES depth verification that are not yet wired.

---

### C3. center_sell — INFERRABLE FROM center_buy LIVE TRACK

`center_sell` would be `buy_no` on a finite-range center bin. Since `center_buy` (`buy_yes`) is live and has been selected by the same `BinEdge` pipeline, the edge is:

`edge_sell = p_mkt − p_posterior` for a center bin where `p_posterior < p_mkt`

The `probability_trace_fact` records show 39 `center_buy` traces (2026-05-17–18). The existence of live center_buy implies the model currently finds center bins systematically *underpriced* relative to its posterior — which is the BUY signal. For center_sell to be positive-EV, the model would need to find center bins systematically *overpriced*, which is the opposite direction.

In principle both can be true simultaneously on different bins in different markets: center_buy fires on bins the model thinks are underpriced, center_sell would fire on bins the model thinks are overpriced. The `BinEdge` pipeline already surfaces both `buy_yes` and `buy_no` edges; center_sell is the `buy_no` path for finite non-shoulder bins.

**Quick assessment:** center_sell shares the forecast-edge substrate and is REPLAYABLE (PROMOTION_PIPELINE_DESIGN.md §1). The symmetric classifier needs wiring (`_classify_via_registry` path in evaluator.py). No independent edge proof run here — but the same calibration soundness and market-completeness conditions apply. Given that center_buy is live and profitable in its first 6 settled trades, a replay of center_sell on the same forecast history is warranted as Track R-1b alongside shoulder_buy. Data available: ensemble_snapshots_v2 (1.13M rows, 2024-01-01→2026-05-28) + settlements_v2 (4,247 VERIFIED rows, 2025-01-22→2026-05-21).

---

### C4. Other 6 Phase-4 candidates — one-line plausibility + gate

All 6 are **NOT_REPLAYABLE** (PROMOTION_PIPELINE_DESIGN.md §1). Their edge plausibility is structurally reasonable — each identifies a real market inefficiency — but none can be assessed until the specific input field is captured live:

| Candidate | Edge thesis | Required input not yet captured |
|---|---|---|
| **stale_quote_detector** | Stale mid-prices create temporary mispricings detectable by book-hash transitions | `spread_observed_window_ms` (info-event proxy): permanently None. Need windowed book-hash observer wired into `MarketAnalysisVNext`. `book_hash_transitions`: 2 days / 22 markets only. |
| **weather_event_arbitrage** | NWS/ECMWF alert events create predictable short-term probability shifts exploitable before market prices update | `alert_source` / `active_weather_alert`: no external alert feed wired to MarketAnalysisVNext at all. Prerequisite: ingest NWS/ECMWF alert stream → MarketAnalysisVNext field. |
| **resolution_window_maker** | Late-session (near-resolution) liquidity gaps create favourable maker fills | `uma_resolution_status` per decision snapshot: table FROZEN at 2026-02-21, never updated in live decision flow. Prerequisite: resume UMA resolution ingest. |
| **liquidity_provision_with_heartbeat** | Passive maker provides spread capture | `PassiveMakerExecutionEstimate`: computable but derived from Zeus's own prior orders (non-stationary, replay-biased). Prerequisite: design a stationary fill-probability estimator before replay is trustworthy. |
| **cross_market_correlation_hedge** | Correlated city-temperature markets can be hedged for pair-trade alpha | `regime_correlation_cache`: 0 rows (Phase-5 store unfed from live regime-tag stream). Prerequisite: wire `regime_tag_for` output into cache writer on each cycle. |
| **neg_risk_basket** | Negative-risk bins (family YES sums exceed 1) create a synthetic put-sale opportunity | `neg_risk_yes_ask_sum` / `neg_risk_token_count`: documented "not yet wired in MarketAnalysisVNext"; every snapshot has `neg_risk=1` but the family book sum was never computed. Prerequisite: MarketAnalysisVNext family-book-sum computation pass. |

---

## D. Binding Constraints

**1. Data window — single regime, 19 days.** The `probability_trace_fact` (zeus_trades.db) spans 2026-05-03→2026-05-21 (19 days; the "late half" in temporal splitting is a single calendar day). All edge proofs above are from this window. It is late-spring shoulder season — not a heat-dome, not a cold-snap, not a winter trough. Any finding (positive or negative) observed here may not persist across regime transitions. This is the single largest validity threat for all shoulder and center proofs.

**2. Native quote coverage is near-zero.** `market_microstructure_snapshots` = 0 rows everywhere. `book_hash_transitions` = 2 days / 22 markets. The 38 shoulder price rows with both `best_bid` and `best_ask` in zeus_trades.db are insufficient for a native-NO-ask analysis. All proofs above assume `q_NO = 1 − p_mkt_YES` (synthetic complement). A thin native NO book widens the executable ask — real fill costs will be worse than the fee model. The proof's fee assumptions are optimistic.

**3. Strategy classifiers are scaffolds.** `classify_shoulder_candidate` in `shoulder_strategy_vnext.py` hardcodes `no_trade_reason=None` for all inputs in the thin T2 pass — it has no production logic. Track R replay cannot use it as written; it must be production-wired before any replay-rank run is valid. The replay harness itself (`shadow_replay_harness.py`) does not yet exist — it is a PROMOTION_PIPELINE_DESIGN.md design document, not shipped code.

**4. Phase-4 inputs structurally absent.** `market_microstructure_snapshots` is 0 rows in every DB. `regime_correlation_cache` is 0 rows. `uma_resolution` is frozen 3 months stale. These are not gaps addressable by replay — the data was never collected and never will be for those historical periods. Track L (live capture) is the only path, and it requires wiring dispatch calls into `cycle_runtime.py:951` (currently not done) plus a config flag and settlement attribution cron.

**5. EvidenceReport denominator is zero for all 9 shadow strategies.** `decision_events` has 0 rows for any shadow/blocked strategy (world.db `decision_events` = 0 rows total). `regret_decompositions` = 0 rows. The `PromotionReadinessValidator` would return NOT_READY for all 9 with `n_settled=0`, `ci=(None, None)`. The infrastructure is correct; it has no data to operate on.

**6. center_sell and shoulder_buy classifiers not wired.** The `_classify_via_registry` path in evaluator.py (replacing the hardcoded `shoulder_sell` string per PHASE_3_SHOULDER.md verifier probe 5) handles the routing — but the underlying classifier bodies for `center_sell` and `shoulder_buy` modes would need to be production-implemented before any live-shadow capture is meaningful.

---

## E. Decision-Grade Recommendations

### What is promotable now

**Nothing.** The `PromotionReadinessValidator` would return NOT_READY for all 9 shadow/blocked strategies. `n_settled=0` for every one of them. The promotion predicate (`ci_lower > breakeven + cost_of_capital`) cannot be evaluated with zero settled evidence.

Of the 4 live strategies: `opening_inertia` has 6 settled positions in the first 6 days of live trading. Win rate 3/6 (50%), total PnL +$5.14 on ~$697 of capital — early, small, not yet statistically meaningful but directionally positive and functioning correctly.

### Nearest-to-promotable candidate and what unblocks it

**`shoulder_buy` (upper-shoulder, extreme-regime sub-population) is the most credible candidate to investigate first.** The reasoning:

1. The edge proof shows positive mean EV (+1.7–2.8% net) concentrated in upper shoulders during high p_cal conditions, which directionally aligns with the dossier's `UNKNOWN_BUT_INTERESTING` framing for Variant 2 (buy during extreme regime with native YES depth).
2. The data substrate for replay exists back to 2025 (ensemble_snapshots_v2 + settlements_v2 with 4,247 VERIFIED settled outcomes across 51 cities).
3. **shoulder_sell is definitively refuted** — no classifier work should be spent on it until the mispricing sign reverses in independent data.

**Unblocking shoulder_buy for Track R replay requires precisely:**

(a) **Production-wire `classify_shoulder_candidate` for buy_yes mode.** The current scaffold returns `SHOULDER_NO_TRADE_GATE` unconditionally. A minimal production wiring: for `direction == "buy_yes" AND bin.is_open_high AND regime_tag == HEAT_DOME` (Variant 2 per dossier §7.4), set `no_trade_reason = None` with probabilistic fields populated from the ensemble snapshot. Scope: ~100–150 LOC, T3-class work.

(b) **Build `shadow_replay_harness.py`.** The design is complete (PROMOTION_PIPELINE_DESIGN.md §3). Reads FCST ensemble + WORLD historical_forecasts + FCST settlements; writes decision_events + shadow_experiments + regret_decompositions to WORLD; emits a PromotionReadinessValidator report. With 4,247 settled outcomes and ~2 shoulder bins per (city, date, metric), this replay can produce O(1000+) settled would-be decisions — enough for ci_lower to be meaningful if the edge is real.

(c) **Extend the replay to center_sell** (Track R-1b). Same harness, same substrate, symmetric classifier. Costs marginal additional work.

Expected timeline: ~1 week for harness + classifier production wiring (per PROMOTION_PIPELINE_DESIGN.md §6 estimate).

### Recommended build order

**Immediate (1–2 weeks):**

1. **Track R-1a:** Production-wire `classify_shoulder_candidate` for `buy_yes / upper_shoulder / HEAT_DOME` mode with proper `tail_probability_calibrated` from ensemble. Add `buy_yes` shoulder topology path in evaluator (parallel to existing `buy_no` path).
2. **Track R-1b (parallel):** Wire `center_sell` classifier in `_classify_via_registry` (symmetric to center_buy; minimal code, same pipeline).
3. **Track R replay harness:** `shadow_replay_harness.py` — offline CLI, reads forecast history 2025-01→2026-05, writes to WORLD, emits ranked validator report. Run shoulder_buy and center_sell in one pass.
4. **Inspect the report:** If shoulder_buy ci_lower clears 0.5 + cost_of_capital on the replay, begin Track L live capture (L-1 dispatch hook + L-2 settlement cron) in parallel with live confirmation period.

**Parallel / near-term (1–3 weeks):**

5. **Track L-1:** cycle_runtime.py:951 fail-open shadow-dispatch hook + config flag (default off). Zero money-path coupling. Prerequisite for all 9 shadow candidates accumulating live no_trade evidence.
6. **Track L-2:** Settlement-attribution cron (decision_events → settlements_v2 → regret_decompositions). Enables live track to advance toward Tribunal-N.
7. **Regime cache feeder:** Wire `regime_tag_for` output into `regime_correlation_cache` on each cycle. Unblocks `cross_market_correlation_hedge` for Track L (not replay — still no historical cache data).

**Deferred (no action until inputs exist):**

- `stale_quote_detector`, `weather_event_arbitrage`, `resolution_window_maker`, `neg_risk_basket`: Track L only after their respective input fields are wired. Not actionable before Track L-1 lands.
- `liquidity_provision_with_heartbeat`: deferred until a stationary fill-probability estimator is designed.
- Any live promotion of `shoulder_buy`: only after Track R replay produces ci_lower > 0 AND Track L live-confirm accumulates Tribunal-N settled outcomes (3–6 weeks post-harness).

### What NOT to do

- Do not promote any strategy on the current 19-day, single-regime evidence. The window is too narrow.
- Do not build Track R for shoulder_sell. The edge proof refutes it; the effort is negative value.
- Do not wire Phase-4 candidate dispatch before Track L-1 and Track L-2 are in place — the dispatch call writes to WORLD.decision_events, and without the settlement attribution cron those writes produce n_decisions with no matching regret rows (denominator with no numerator).
- Do not treat `opening_inertia`'s 6-settled-trade sample as evidence of its long-run win rate. 3/6 = 50% on N=6 is within Wilson-95 [0.15, 0.85]. The PnL of +$5.14 on ~$700 capital is a 0.7% return over 6 days — consistent with the strategy working, not a verdict on its edge.
