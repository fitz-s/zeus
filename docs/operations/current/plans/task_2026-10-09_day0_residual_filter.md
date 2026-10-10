# Day0 daily-max probability: residual-state filter (consult REQ-20261009-125231-45b597)

Answer: /tmp/cgc/answer_REQ-20261009-125231-45b597.txt (verified, Pro). Operator mandate 2026-10-09: "determine a superior algorithm that re-assembles the forecast as observations arrive, for more accurate probability; use consult for the math criterion"; earlier: first-principles, no constant centre offset, forecast accuracy irrelevant, probability vs market and speed matter.

## Estimand
q_k(t) = P(Y_c ∈ B_k | I_t), Y_c = κ_c(max over eligible reports in the contract day of the publisher's native value). The target is the PUBLISHED settlement maximum (report eligibility, native encoding, κ rounding), not the continuous physical max. 5-min whole-°C rows inform the state; they are not settlement-contributing records or hard floors.

## Model (week-one choice)
- Per deterministic provider m, residual state x_m = (b, v): b is today's station-minus-model level error, v the tendency error. dx = G_p x du + L_p dW, G = [[-λ_b, 1], [0, -λ_v]], λ_b ≥ 0 (λ_b = 0 means full persistence is nested), λ_v > 0. Three phases (warming / near forecast peak / cooling) from the run's own forecast and solar phase; phases shape transfer, never declare the peak.
- Mean transfer h hours ahead: e^{-λ_b h}·b̂ + (e^{-λ_v h} − e^{-λ_b h})/(λ_b − λ_v)·v̂. This replaces the single exp(−h/4.2).
- Initial state mean 0 each day; only the initial COVARIANCE is fitted (no centre offset).
- Observations enter as INTERVALS: whole °C [y−0.5, y+0.5), tenths [y−0.05, y+0.05). Truncated-Gaussian moment update (consult §2 equations); assumed-density approximation, validated on settlement tails. Delayed reports are assimilated at measurement time with bounded replay. A tenths refinement replaces its coarse twin, never counts twice.
- New forecast run: rebase b_new = b_old + f_old(t) − f_new(t), v likewise, P preserved. No skill credit for merely arriving.
- Fit θ = {λ_b, λ_v, L, P0} per (model, phase) by penalized chronological observation likelihood; rolling ≤42 prior days, daily refit, strong shrinkage to pooled; no city-specific dynamics yet.
- Spread: a_t² = exp(γ·[log(1+h), log(1+S_ENS²), log(1+D_provider²), |b̂|, |v̂|, obs age]) multiplies future process noise only (never re-inflates already-conditioned paths). γ fitted by RPS through the full report/max/rounding operator, held-out log score must also improve. Variance intercept and process-noise floor are allowed; a centre intercept is forbidden.
- Max: coherent correlated residual trajectories on a 5-min grid including real report instants and the fractional tail to contract end; apply native encoding + κ; Y = κ(max(A, R)). Common random numbers across bins/updates; Rao-Blackwellized Gaussian integration for tails. Rounding κ_F(c) = floor(1.8c + 32 + 0.5) via SettlementSemantics, never banker's rounding.
- Peak: π_peak = P(R_t ≤ A_t | I_t), π_samebin = P(Y ∈ B_current | I_t), from the same distribution. No clock atom. Remove clock maturity as a prerequisite for statistical SELL.
- Later, behind state + spread: tempered, forgetting BMA weights on stored pre-observation predictive likelihoods (cap one hourly unit of weight per hour); NAM/HRRR admitted as deterministic paths with their own residual law; ENS = within-ECMWF uncertainty covariate, never 51 votes.
- Market: pool q* = (1−β)p + βq_weather, β fitted out-of-fold on log loss; keep q_weather separately. Not a Bayesian multiplication (double counting).

## Release gates
Primary: full-family categorical log loss; co-primary: normalized RPS; also multiclass Brier, CRPS on discrete law. Causal rolling-origin replay; per family-day averaging; paired vs incumbent and vs same-time market; contiguous date-block bootstrap. Ship a component only if U95(ΔLL) < 0 and U95(ΔRPS) < 0 vs its immediate parent, not carried by one date/city, slices disclosed. Latency gate: p95 receipt → published q ≤ 2 s.

## Concrete findings to fix (consult)
- [HIGH] event_reactor_adapter.py:47390 renormalizes after dropping virtual complement bins → probabilities become conditional on the listed subset (e.g. 0.20/0.10 with 0.70 elsewhere → 0.667/0.333). Require the complete payoff partition; refuse an incomplete family.
- [HIGH] materializer.py:7800 current-evidence shape bypasses spread calibration; the new builder must own calibrated uncertainty.
- [HIGH] day0_hourly_vectors.py:4742 terminal fallback collapses remaining paths to the current obs while part of the day remains.
- [HIGH] event_reactor_adapter.py:48273 SHIFT_BIN old-leg sale blocked by absent clock maturity.
- [MEDIUM] day0_window.py:157 returns knot count as "hours remaining".
- Our own finding (root cause of the Dallas pin): two shapes alternate — floor-only fused normal (A) vs remaining-day carrier (B); fix in flight fix/day0-q-follows-current-observation.

## Data facts
- day0_hourly_vectors retention 3 days (DAY0_VECTOR_RETENTION_DAYS); live table holds 10-06 18Z..now.
- raw_forecast_artifacts + raw_manifests JSON: hourly temperature_2m responses for single_runs providers archived since 2026-10-01 (icon 3,971, ecmwf 3,470, ukmo 3,447, nbm 2,037+3,386, nam 787 ...); ifs9 anchor since 06-07. ≈9 days of causal paths → enough to start rolling fit, thin; extend retention of hourly vectors (disk 36 GiB free; table ~110 MB for 3 days).
- Observations: observation_prints (hourly + 5-min asos5 being added).

## Laws for this work
- No additive centre constant of any kind (memory no-constant-offset-on-nwp-forecast).
- No shadow-only rollout (operator law 2026-06-13): ship components LIVE once they pass the gates; the consult's "shadow behind a flag" is replaced by the causal replay gate.
- One canonical distribution per family generation, consumed by entry, valuation and exit; compare-and-swap publication.

## Plan
1. Correctness first (in flight): A/B shape alternation; topology renormalization (consult HIGH #1); terminal fallback; knot-count horizon.
2. Causal replay harness on raw_manifests (10-01+) + observation_prints + settlements + books.
3. Residual (b, v) filter with interval updates; ablations: unconditioned, τ=4.2, τ=12, full persistence, filter.
4. Coherent max operator + κ; spread a_t fit.
5. Gate → ship live. Then BMA weights / NAM-HRRR; then market pool.
6. Retention: keep hourly vectors ≥ 45 days for the rolling fit.

## 2026-10-09 operator follow-up: "most physically faithful algorithm + validated on history"
- Builder agent feat/day0-residual-filter produced nothing in ~4 h (no commits, no files); stopped.
- Consult follow-up fired on the same thread: REQ-20261009-175734-bbb462 (scratchpad/consult_day0/followup1.md): challenge own design vs NGR/EMOS-style conditional regression, analogs, QRF, GP residual, EnKF on ENS, hierarchical pooling; pick first + challenger; validation protocol for our data (causal Oct archive + Aug–Sep backfill via Open-Meteo previous-runs), min family-days, rejection criteria; covariates; corrections.
- Empirical validation started now (read-only, scratchpad/persist/): causal October anchors 08/10/12/14 local; innovation e0 vs remaining-max residual; α(h0, lead) with no intercept; implied e-fold vs live 4.2 h / 12 h / full persistence; family log loss + RPS for α=0, τ=4.2, τ=12, α=1, fitted α (leave-one-date-out); level vs trend (Δe).
- Data facts: causal hourly paths only since 10-01 (city-runs icon 73.9k, ecmwf 62.7k, ukmo 60.9k, nbm 14.9k, hrrr 1.8k, nam 0.8k); older raw manifests pruned (daily maxes only, back to June); METAR obs since 07-15; WU/ogimet hourly obs back to 2024 for ~54 cities; settled HIGH families Jun 1,434 / Jul 1,419 / Aug 1,569 / Sep 1,529 / Oct 410.
- asos5 live acceptance (peer session, 23:00Z, 248cc37c2 loaded 21:58Z): 10/10 °F stations have asos5 channels (KBKF/KDEN none); 0 unrecorded errors; page rows unaffected. Effective density 6–8 values/h/station (source cells carry METAR text with air_temp null — skipped correctly), not 12. First-appearance lag n=62: p10 8.3, p50 12.6, p90 18.3, max 20.1 min. Plan the observation operator for ~7/h and ~13 min possession lag.
- Backfill route facts: scripts/backfill_openmeteo_previous_runs.py:139-190 calls httpx.get directly (bypasses OpenMeteoQuotaTracker) and reduces to daily max/min (:237). A research backfill of hourly previous_dayN paths must go through src/data/openmeteo_client.py:471 fetch() (quota lease, weight = locations·max(1,vars/10)·max(1,days/14)) and keep the hourly series in scratch, not a DB. Offsets chosen after the consult answer (causality per local hour).

## 2026-10-09 evening: consult follow-up answer + first validation
Consult REQ-20261009-175734-bbb462 (/tmp/cgc/answer_REQ-20261009-175734-bbb462.txt) revised its own design:
- Build FIRST a pooled direct conditional-max regression (5 shared params, no location intercept, no mean-centred features): μ_m = r_m + β_b·e_m + β_d·d_m (r_m = provider remaining max, e_m = current innovation, d_m = same-vintage 2 h residual change); G = Σ π_m Φ((x−μ_m)/s); log s² = γ0 + γ_h log(1+h) + γ_S log(1+S²); floor A applied by bin cases. Fit by penalized full-family NLL averaged within family-day.
- CHALLENGER: restricted residual level/slope filter with ONE pooled persistence law (phase-dependent λ not identifiable from 9 days). Corrections: interval update is assumed-density (non-Gaussian posterior; validate vs quadrature, bin error < 0.005); v rebase formula corrected; report-mask sampling invalid under SPECI timing (direct settlement model avoids it).
- Criterion: E[LL(q)−LL(p)] = KL(p‖q); E[RPS(q)−RPS(p)] = Σ(Q_k−P_k)²/(K−1). Gates: U95(ΔLL)<0 and U95(ΔRPS)<0 on locked confirmation; conditional calibration within 5 pp in groups with ≥~100 independent equivalents; upper-tail coverage; latency p95 ≤ 1 s, p99 ≤ 5 s; market-outperformance claim only from paired same-time comparison.
- Data: Historical Forecast product is NOT causal (stitched first hours); Previous Runs stitched across runs (fallback only); Single Runs = coherent vintage (archived from 2026-04-02).
- Answer to its "smallest missing fact": Aug–Sep raw_model_forecasts single_runs rows DO keep exact run (source_cycle_time), provider availability (source_available_at) and our possession time (captured_at) — but only the DAILY max per run; hourly paths pruned. openmeteo_response_store.provider_runs holds measured availability delays from 09-30 (ecmwf_ifs mean 6.9 h, max 7.9; icon 3.8/4.2; ukmo 7.8/9.2; nbm 1.3/3.1; hrrr 1.8/3.0; nam 2.8/3.2).

October causal development replay (scratchpad/persist/REPORT.md; 408 family-days, 9 dates, LODO; proxy B = equal-weight provider mixture, σ per anchor by training RMSE):
- Conditioning on the current reading itself: τ=4.2 vs no innovation ΔLL −0.132 [−0.146, −0.119], ΔRPS −0.0074.
- τ=12: worse than 4.2 by +0.025 LL [+0.012, +0.038]. Full persistence: +0.126 LL (08h disaster +0.276 vs none). Both rejected.
- Lead-indexed carry-over α (≈0.67 ≤4 h, 0.47 4–6 h, 0.27 6–8 h, 0 beyond; per anchor) vs τ=4.2: ΔLL −0.0098 [−0.0156, −0.0039], ΔRPS −0.0008 [−0.0012, −0.0003]; date- and city-block CIs exclude 0; gain at 08/10h only; 14h none; US midday worse (+0.0105).
- 2 h trend Δe: no robust value (all LODO CIs include 0, sign flips) → β_d = 0.
- Settled max exceeds hourly-model max by +0.49..0.70 °C at every anchor; left in σ (no intercept law). Physical candidate: max over reports/sub-hourly path > max of hourly-sampled smooth path (E[max] ≥ max E) — belongs to the max operator, to be tested, never a constant.
- Status: development replay (October already inspected), not confirmation.
Running: Aug–Sep conditional daily-max head (scratchpad/dailyhead/; folds Jul15–Aug21 est, Aug22–Sep4 dev, Sep5–30 locked) — B0 = floor-only D_m shape (≈ A).

CORRECTION (persist v2, consult protocol): the first-pass "lead-indexed α beats τ=4.2" result above is VOID (left-censored target R = settled − max F_rem, RMSE σ). v2 (B-proxy, frozen dispersion, 6 anchors 08–18, possession-time selection, 410 family-days):
- Table 1 (only g changes): LL g=0 1.0499, τ4.2 0.9816, τ12 1.0011, persist 1.0937; vs τ4.2 U95 ΔLL: g=0 +0.090, τ12 +0.026, persist +0.144 → τ=4.2 best of the four fixed laws.
- Table 2 (per-law spread multiplier, rolling origin, 262 family-days Oct 4–9): g=0 and persist still worse (U95 +0.123/+0.210); τ12 − τ4.2 = +0.020 [−0.020, +0.047], not separated. Fitted a: τ4.2 0.80, τ12 0.85 (proxy spread too wide).
- Through-origin β to the peak hour (same vintage): 08h 0.05, 10h 0.44, 12h 0.67, 14h 0.84, 16h 0.88, 18h 0.95 → no single e-fold (implied τ 2.3–7.8 h); morning residual does not persist, afternoon residual nearly fully does. Phase-dependent carry-over is the hypothesis for the backfill confirmation, not a ship.
- Near-upper-edge group (<0.25 bin width, 57 family-days): τ12 better by −0.027 [−0.036, −0.008] (uncorrected multiplicity).
- Paired SD (family-day LL): g=0 0.290, τ12 0.155, persist 0.447 — use for n_eff.
- Floor violation: Paris 10-07 18h METAR floor 19.0 °C, settlement 18 → hard observed floor can assign 0 to the winning bin; the floor must be an interval observation.
Live decision: keep exp(−h/4.2) (live constant settings['day0']['current_state_innovation_e_fold_hours'], applied in src/signal/day0_window.py:79). No change shipped.

## 2026-10-09 Paris 10-07 HIGH: page-omission defect in the fast-residual likelihood (served q)
- Settlement source: NOAA page LFPB (config/cities.json:1076-1101). Page max 18 (13:00Z report). The page never carried the 14:00Z slot; METAR 14:00Z said 19 (AWC + ogimet). Settled 18.
- Served q(18) (forecast_posteriors, fused_day0_fast_residual_likelihood): 0.648 at 13:38Z, 0.0085 at 14:44Z, then 0.0099 from 15:32Z to 21:35Z. Market yes-18 at 0.154–0.172.
- Mechanism: day0_fast_obs.py:697 build_fast_station_residual_likelihood pairs page and fast by observation instant, so slots the page omits never enter the sample. The "page omits the fast-max slot" scenario gets unknown_weight = 1 − 0.05^(1/n) (day0_fast_obs.py:311, :865-872) = 0.0099 at n = 301. That is a function of n, not a measured rate. Applied in replacement_forecast_materializer.py:6842-6881.
- Omission was observable: the page 14:30 row was possessed at 15:24 while 14:00 never appeared.
- Impact: none realised. Orders buy_yes 17 (q 0.52) and buy_no 18 (q 0.99) were rejected pre-venue by deployment_freshness_mismatch; both would have lost.
- Base rate: Sep 1–Oct 8, 7 of 925 NOAA °C city-days had MAX(fast) > MAX(page).
- Fix design: unknown_weight → P(page does not carry the fast-extreme slot | that slot's page state), estimated per state:
  - carried → residual law;
  - pending → 7-day slot-omission rate;
  - passed-without → 1 − late-backfill rate.
  - The bound scenario already transports the remaining-day path.
- Measurement running: scratchpad/omission/.
- UNVERIFIED: the monitor/exit lane read_day0_observation_context_from_instants (day0_observation_reader.py:1713) uses MAX over AWC + ogimet as a hard floor.
- Ownership asked of the "best forecast" peer.
- The best-forecast peer reports no owner, so the fix is ours. Collision surface is fix/day0-fast-route-kinds r3:
  - fast_extreme_supersedes_settlement plus its city= call;
  - _day0_noaa_preliminary_carrier and the carrier-inputs-absent path.
  - Expect only nearby-hunk rebases.
- Peer lifecycle data (09-13..10-05, 36,442 AWC rows):
  - outcomes: kept 98.41%, absent 1.41%, corrected 0.18%;
  - SPECI is about 5× more likely to be absent than routine;
  - absences cluster in shared outages (09-20 03–07Z: 257 of 514);
  - °C page visibility lag since G5: p50 13, p90 33, max 85 min.
- Design constraints:
  - per-kind rates;
  - a separate outage component;
  - passed-without only once a later slot is possessed AND the age exceeds the visibility tail.
- Lucknow 09-06 is the canonical second case: METAR 37 dropped by the page, settled 31.

## 2026-10-09 Aug–Sep conditional daily head (scratchpad/dailyhead/REPORT.md)
- Model C: μ_m = A + βF(D_m−A) + βT(Tnow−A) + βΔ·ΔT, no intercept. Folds fixed in advance: est Jul15–Aug21, dev Aug22–Sep4, locked confirmation Sep5–30 with daily expanding refit.
- Confirmation result, C − B0 where B0 = floor-only D_m shape ≈ A, the KNOWN-BROKEN shape (1,300 family-days, 26 dates):
  - ΔLL −0.0397 (U95 −0.0345), ΔRPS −0.00253 (U95 −0.00220); all 4 weeks negative; robust to hard floor, 42-day window, strict settled_at, 12Z origin.
  - Gain is almost all from ΔT (2 h raw temperature trend). C' (βΔ = 0) ≈ B0 on RPS.
  - Worse at 08h (+0.018) and when the trend is flat (+0.012).
  - Coefficients (median): βF 0.971, βT 0.006, βΔ 0.199.
- Calibration poor: 16h realized below predicted, PIT skewed low → pooled βF≈1 over-weights the forecast after the peak.
- Post-hoc (NOT a result): per-anchor coefficients give ΔLL −0.147 vs B0, βF falling 1.0 (08h) → 0.07 (18h). Consistent with October through-origin β to peak rising 0.05 → 0.95.
- Physical reading: the information is in the raw trajectory (is the temperature still rising, i.e. has the peak passed?) and in solar phase. It is not in the anomaly trend: October Δe, the innovation change, had no value.
- Floor: 15 family-days of the omission class (METAR ≥ 1 °C above settlement). A−0.5 cannot fix this for whole-°C cities; the page-omission model owns it.
- Caveat: B0 is not the incumbent B. C vs B is untested on Aug–Sep (hourly paths pruned).

## PRE-REGISTRATION (written before any October or prospective scoring of these variants)
Hypothesis H_phase: the physical carry-over of today's observed state into the remaining-day max depends on diurnal phase. A phase-dependent direct conditional-max model beats the incumbent B (persist v2 B-proxy with g = exp(−h/4.2)) on full-family LL and RPS.

Variants (no location intercept, no mean-centred features, no city terms; variance intercept allowed):
- C_h (daily head, phase): μ_m = A + βF(k)(D_m−A) + βT(k)(Tnow−A) + βΔ(k)ΔT.
  - log s² = γ0(k) + γh·log(1+h) + γS·log(1+S²).
  - k = local anchor hour; piecewise-linear knots at 08/10/12/14/16/18.
  - Penalty: λ1·Σ second differences across knots (smoothness) + λ0·(βF−1)² + λ0·βT² + λ0·βΔ².
  - λ0 = 0.1 (dev-fold choice already made); λ1 chosen on the Aug22–Sep4 dev fold from {0.1, 1, 10} BEFORE October is touched.
  - Fit once on Jul15–Sep30 and frozen.
- D1 (hourly direct, pooled, consult spec): μ_m = r_m + βb·e_m; π_m equal; log s² = γ0 + γh·log(1+h) + γS·log(1+S²); βd = 0 (Δe had no value).
- D2 (hourly direct, phase): D1 with βb(k), γ0(k) on the same knots and smoothness penalty.
- D3 = D2 + βΔ(k)·ΔT (raw 2 h trend).
- Floor for all variants: interval A−q, q = report half-width; the omission class is reported separately.

Scoring:
- (a) Development: October 1–9 on the persist v2 panel (6 anchors, 410 family-days, possession-time selection), paired with B-proxy τ4.2 (persist Table 1 and Table 2 a-scaled). Hourly variants use LODO; C_h is frozen from Jul–Sep. Labelled development, because October already informed H_phase.
- (b) CONFIRMATION = prospective: all HIGH families settling 2026-10-10 onward. Score once n ≥ 250 family-days (≈ 6 days), refitting daily on all earlier settled data.

Gate to ship (replacing B's innovation response live), on (b):
- U95(ΔLL) < 0 and U95(ΔRPS) < 0 vs B, 3-day date-block bootstrap;
- not carried by one date or city;
- no upper-tail undercoverage at 12h/16h;
- reliability gap ≤ 5 pp in groups with ≥ 100 equivalents.

Reject H_phase if:
- D2/C_h fail vs B on (b);
- or the knot coefficients flip sign under leave-one-week-out;
- or the gain disappears when B gets the same per-anchor spread calibration (a(k)).
- SECOND LANE, reviewer repro, confirmed on live 9ebbd044d (pre-existing since 3538c7bb2, 07-27):
  - adapter ~38866-38880 sets `_edli_day0_probability_boundary_native` = the AWC/ogimet value whenever it is more absorbing than the page.
  - `_day0_probability_boundary_native` ~47853-47868 returns it.
  - The scenarios fallback ~47940 gives ((boundary, 1.0),).
  - ENTRY is gated (~41592); HELD_MONITOR and REDUCE_ONLY are not.
  - Paris q = [0, 0, 1] with the proxy vs [0, 0.99994, 0.00006] page-only.
  - The margin the code comment relies on is 0 when matched residuals are all 0.
  - One omission model replaces both lanes: proxy value = a scenario weighted by the measured P(page carries slot | state), never survival 1.
- Reviewer, fix/day0-q-follows-current-observation (interim):
  - The proxy-certainty P1 is pre-existing; the branch adds only rejection/partition gates and no WRH analytic route. No production widening established.
  - Residual gap: HELD_MONITOR with a CURRENT (not pinned) WRH floor-only bundle is accepted. The point recomputes [0, .85, .15], but the source-clock intersection caps c2 NO at .625 (from the stale floor-only shape) instead of .85. The ENTRY-only guards at 41538/42711 do not cover the prepared cap. So legacy floor-only authority survives as a q_lcb CAP on held exits. It does not serve the point. To be closed in the same branch: a floor-only bundle must not contribute to the cap intersection on any lane.
