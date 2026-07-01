# EMOS affine center calibration — before/after (2026-07-01)

## ★★★ AUTHORITATIVE (2026-07-01): PASSES the DECISION-level gate — this is the real bar
The center-MSE nested pass is necessary but NOT sufficient (consult Q6). The live-capital gate is the
DECISION-level test: held-out, does correcting the center improve the **TRADED bin-q's log-loss on the
REAL settled bin**? (μ → `bin_probability_settlement(μ,σ)` on the integer bin that actually settled;
identity μ vs corrected μ'.) Money-path verified first: the live traded q reads the materialized
posterior center `anchor_value_c` (the `replacement_0_1` reactor lane) — the exact value this layer
corrects — so the correction reaches the decision (not the raw-member fallback lane).

**Layer enabled ONLY when the decision-nested (global-date reselection) traded-q log-loss lower-CI>0:**
- **HIGH: PASSES → 13 cities served.** decision-nested traded-q log-loss lcb95 **+0.024** (all-cell
  portfolio; +0.082 on the selected cells), Brier lcb95 +0.011; center-MSE nested lcb95 +0.118. On the
  served-13 held-out cells the traded-q log-loss goes **1.897 → 1.749**. The correction genuinely
  sharpens the probability on the outcome that actually settled — leak-free, decision-level, CI-clear.
- **LOW: FAILS → 0 served (layer disabled).** decision log-loss lcb95 **−0.007** (only Paris clears
  center-MSE, and it does NOT survive the decision bar). Low is world-class; nothing serves.

Lead-gated (served_lead=day-ahead; other leads identity) + range-guarded (in-support only) +
atomic-written; kill switch `enabled:false`. Tests: 21 calibration + 6 materializer pass.
**This is the genuine pass. Everything below is prior/superseded — do not cite its numbers.**

## (superseded) production-hardened after consult REQ-20260701-063727
Supersedes everything below. The frontier consult reviewed the WHOLE surface (PR #421) and found the
EB direction right but the layer not production-sound as shipped. All flagged defects fixed + verified:

| consult finding | fix (shipped) | verified |
|---|---|---|
| **BLOCKER** selection optimism (select cities + report selected-set ΔMSE from same evidence) | LAYER enabled ONLY if a NESTED global-date selection has portfolio lower-CI>0 (reselect cities inside each held-date fold; score the policy that would've been selected without the block) | HIGH portfolio ΔMSE **+0.192, lcb95 +0.118**; LOW +0.048, lcb95 **+0.001 (marginal)** |
| **HIGH** lead mismatch (L1 fit applied at all leads) | materializer computes lead & serves ONLY at `served_lead` (day-ahead); other leads → identity | lead=1 serves; lead=0/2 → identity |
| **HIGH** strong-slope extrapolation | `apply_affine_in_support` clamps μ to observed [x_lo,x_hi] before the affine (tilt held flat outside) — data-derived, no clamp constant | above-range μ frozen at x_hi endpoint, not extrapolated |
| **HIGH** cross-city EB prior leaks the held date | per-city gate uses leave-one-GLOBAL-date-out (whole date removed from ALL cities) | — |
| **MED** non-atomic write / defaults-on | temp+fsync+os.replace; artifact carries `source_commit`, `training_date_range`, `served_lead`, `selection_protocol` | — |

**Served now (enabled, live seam lead-gated + range-guarded):**
- **HIGH: 13 cities** (nested portfolio lcb95 +0.118, robust). Per-city global-date gLODO all positive.
- **LOW: 1 city (Paris, b=1.0 pure level)** — nested lcb95 +0.001, MARGINAL (bootstrap-noise boundary);
  lowest-risk correction (no slope, range+lead guarded). Treat as canary-grade.
- Honest headline is the NESTED portfolio number (+0.118 lcb), NOT the selection-conditional served-set
  "+26%" / "+1.15" (those are upward-biased by selection — kept only as the per-city diagnostic).
- Tests: 21 calibration + 6 materializer-center pass. Kill switch: `enabled:false` / delete → identity.

## (earlier, superseded) EB rewrite: no hard-coded shrink, day-ahead one-per-date basis
Supersedes every number below it. Three corrections to what shipped:

1. **NO hard-coded numbers.** The prior fit hard-coded a shrink strength κ=40 AND a slope clamp
   [0.85,1.15] — 7 of 14 "served" cities had their b pinned to the clamp value 0.850, i.e. their
   coefficient was the hard-code, not the runtime data. Replaced with **empirical-Bayes shrinkage**:
   each city is pulled toward identity by w = τ²/(τ²+se²), where se² is the city's OWN sampling
   variance and τ² is the cross-city spread of true effects, estimated by method of moments. Every
   quantity is a function of the runtime data — no κ, no clamp.
2. **Independent unit = date, at the day-ahead DECISION lead.** ONE served center per (city,date) at
   lead-1 (the freshest day-ahead cycle — the point that feeds the primary traded decision), NOT all
   cycles. The prior "all rows" basis deflated se² (adjacent cycles are correlated → ~150 rows but
   only ~19 independent dates), which is exactly what made a hard clamp look necessary. Honest se on
   ~19 dates lets EB shrink correctly on its own.
3. **Validation = leave-one-DATE-out, EB refit per fold, date-block bootstrap CI≥0.**

**Served (gate: LODO ΔMSE>0 AND date-block 95% lower-CI≥0):**
- **HIGH: 13 cities** — Buenos Aires, Busan, Chicago, Dallas, Guangzhou, Karachi, Kuala Lumpur,
  Los Angeles, Milan, Munich, Seoul, Taipei, Tokyo. Pooled LODO ΔMSE +1.15.
- **LOW: 0 cities.** Low is world-class (|bias| ≤0.8, τ_b²→0 so every b→1); no low city's correction
  survives the date-block CI. The earlier "Paris/NYC low +32.2%" was the hard CLAMP forcing b=0.850 —
  overfit, RETRACTED. Honest low = nothing to serve yet.
- **Leak-free OOS (date-blocked, served-13 only; other 36 high cities stay identity byte-for-byte):**
  RMSE **1.596→1.182 (+26.0%)**, |bias| 0.57→0.11, 238 held-out cells.
- The intercept `a` can look large (e.g. Karachi a=+10.72) but that is only the line's y-intercept at
  0°C; the NET correction across each city's OBSERVED temperature range is bounded and small near the
  mean, larger only at temperature extremes where the models genuinely diverge (representativeness).
  Marginal cities (Tokyo/Karachi/Munich, date-block lcb barely >0) are the weakest served units.
  σ untouched. Kill switch: artifact `enabled:false`, or delete the file → lookup returns identity.

**Every number below this section (κ-shrink, clamp, "20 high", "14 high", low "+32.2%") is
superseded — do NOT cite it.**

## FINAL BASIS (operator sentence #1): the RUNTIME served center, not any raw endpoint
"使用真实参与概率计算的运行态组合数据". The 运行态组合数据 IS the served fused center
`forecast_posteriors.anchor_value_c` — the value that literally feeds the live probability q.
Earlier passes calibrated on raw model endpoints (previous_runs = ECMWF ifs025 0.25° coarse; then
single_runs raw) — both WRONG: previous_runs is a different product (ifs025 vs live ifs9 gap sd 1.52,
e.g. Jeddah −1.44 on ifs025 → +0.08 on ifs9 = pure coarse-grid artifact), single_runs is a raw source
not the combination. Refit on the served center itself. No product gap ⇒ no transfer bridge.
Runtime history is short (~19-20 served days/city today) ⇒ **leave-one-out** validation (walk-forward
needs ~25 prior); the served (a,b) sharpens as the live history accrues.

**Before/after on the runtime served center (LOO-validated, shrunk-to-identity, slope-clamped):**
- HIGH: **20 cities served** (LOO OOS ΔMSE 95% lower-CI ≥ 0), served-set pooled LOO ΔMSE +0.51.
  Before/after (N=375): RMSE **1.513→1.300 (+14.1%)**, CRPS **0.841→0.721 (+14.2%)**, |bias| 0.42→0.28.
- LOW: **2 cities** (Paris, NYC) — the rest of the 8 venue-low cities are already world-class
  (pooled low bias −0.01), correctly not corrected.
- Corrections are TINY (heavy shrinkage on thin data): e.g. Guangzhou bias +1.68 → served affine only
  ~+0.5 at operating temp. σ untouched. Everything below is superseded (raw-endpoint bases).

---


Walk-forward (leak-free) on the REAL runtime combined center. BEFORE = served runtime center; AFTER =
affine-corrected μ'=a+b·μ (shrunk-to-identity, slope clamped [0.85,1.15]). 19 served cities, N=2069
settled cells, σ=1.48 (pooled realized). Every served city improves on RMSE, CRPS, and bin log-loss.

## CORRECTION 2026-07-01 — ground-truth completeness fix (supersedes the numbers below)
The original fit used `settlement_outcomes` as ground truth. That was a DATA BUG (operator-flagged):
the venue only records TRADED markets — low settlements exist for just 9 cities, AND even for high
only ~44% of forecast days were settled (4,712 of 9,613 forecast cells). Fixed to use the COMPLETE
OBSERVED extreme (`observations.high_temp/low_temp`, all 54 cities, both metrics, matches venue
settlement 100% within 0.6C where a market exists; venue settlement preferred where present).
On the complete ground truth:
- **HIGH: 8 production / 11 canary** — Buenos Aires, Guangzhou, Hong Kong, Kuala Lumpur, Moscow,
  Singapore, Taipei, Toronto. Served-set RMSE 1.705→1.478 (**+13.3%**), pooled OOS ΔMSE +0.723 CI
  [+0.56,+0.90]. (The earlier "12" leaned on the incomplete traded-day subset; 8 is the robust set.)
- **LOW: data now present + the low forecast IS biased (HK struct +2.6, Tokyo +1.1), but 0 served** —
  Zeus materializes a live low center for only 8 venue-low cities, and the previous_runs(ifs025)↔
  single_runs(ifs9) product gap fails the live-low transfer for all of them (honest: the correction
  cannot be shown to help the served low center). Recorded, not served.
The tables below are the pre-correction (settlement-only) numbers, kept for provenance.

## Pooled
| metric | BEFORE | AFTER | improvement |
|---|---|---|---|
| RMSE | 1.633 | 1.441 | **+11.8%** |
| MAE | 1.276 | 1.101 | +13.7% |
| \|bias\| | 0.690 | 0.311 | halved |
| CRPS (proper score) | 0.910 | 0.794 | **+12.8%** |
| settlement-bin log-loss (q on realized bin) | 1.922 | 1.792 | +6.8% |
| 90% coverage | 87.4% | 91.3% | → 90 target (calibration up) |

- Served-set (19) ΔMSE = 1.633²−1.441² = **+0.590** (block CI [+0.49,+0.69]); the +0.327 figure is a
  DIFFERENT mask (all 50 cities incl. the identity ones). Both are correct — quoted separately here to
  fix the metric-mask ambiguity the consult flagged [BLOCKER].
- Live single_runs transfer (the actual served product): pooled ΔMSE +0.228 / 909 cells, 33/49 cities
  improve — the affine transfers to the ifs9 product (a constant offset did not: it harmed 4).

## Consult verification (REQ-20260701-034919, Pro Extended) — verdict REVISE→conditions cleared locally
The consult confirmed the affine is the RIGHT family (E[s−μ|μ]=a+(b−1)μ, so a+bμ is aligned; a constant
offset assumes b=1) and asked for a harder audit. All of it cleared locally:
- **Out-of-fold σ** (walk-forward σ from prior residuals only, same σ before/after): CRPS 0.912→0.789
  (+13.4%), bin-LL 1.936→1.777 (+8.2%), 90% cov 84.4→89.6 — the distributional gains SURVIVE OOF σ.
- **Nested blocked policy replay** (select served units on the EARLY 60% only; score the UNTOUCHED late
  40%): outer-block ΔMSE **+0.796, date-block CI [+0.595,+1.008], excludes 0**; CRPS +16.5%. → the
  selection policy GENERALIZES (not post-selection optimism).
- **Threshold-wise Brier** at operational cutpoints round(μ)+{−1,0,+1,+2}: ALL positive, worst +0.005 →
  NO decision threshold harmed (the consult's sharpest "CRPS-up but decisions-worse" test).
- **Transfer-gate tightening** [HIGH]: production now requires the transfer 95% lower-CI, not a point.
  → **PRODUCTION tier = 12 units** (Amsterdam, Buenos Aires, Chicago, Dallas, Guangzhou, Kuala Lumpur,
  Los Angeles, Milan, Munich, Taipei, Toronto, Wellington), served-pooled ΔMSE +0.436 CI [+0.33,+0.55].
  **CANARY tier = 7** (Ankara, Atlanta, Hong Kong, Mexico City, Sao Paulo, Seoul, Wuhan) — serve=False
  (inert live), accruing live obs until their transfer CI tightens.
Open follow-ups (consult, non-blocking): hierarchical partial-pool vs per-unit affine comparison; the
full nested bootstrap that redoes selection inside each replicate; edge-case unit tests + kill switch.

## Per-city (RMSE b→a | CRPS b→a | bin-LL b→a)
```
Seoul         2.10→1.49 | 1.220→0.838 | 2.304→1.816
Ankara        1.99→1.54 | 1.192→0.862 | 2.202→1.852
Guangzhou     1.90→1.59 | 1.119→0.916 | 2.124→1.884
Taipei        2.10→1.82 | 1.220→1.014 | 2.296→2.056
Buenos Aires  1.64→1.44 | 0.907→0.776 | 1.919→1.783
Munich        1.21→1.02 | 0.700→0.597 | 1.653→1.557
Los Angeles   1.60→1.41 | 0.895→0.792 | 1.927→1.798
Toronto       1.98→1.83 | 1.110→0.994 | 2.191→2.065
Mexico City   1.49→1.38 | 0.828→0.764 | 1.815→1.751
Wellington    1.10→1.01 | 0.636→0.587 | 1.598→1.552
Kuala Lumpur  1.40→1.32 | 0.796→0.749 | 1.758→1.711
Milan         1.08→1.01 | 0.634→0.597 | 1.587→1.552
Chicago       1.97→1.90 | 1.123→1.066 | 2.225→2.162
Atlanta       1.55→1.49 | 0.855→0.812 | 1.878→1.835
Wuhan         1.41→1.36 | 0.806→0.771 | 1.768→1.734
Hong Kong     1.21→1.15 | 0.693→0.663 | 1.650→1.621
Dallas        1.47→1.42 | 0.826→0.791 | 1.805→1.774
Amsterdam     0.86→0.82 | 0.532→0.517 | 1.493→1.478
Sao Paulo     1.21→1.18 | 0.692→0.677 | 1.650→1.634
```
Corrections are tiny in mild conditions (median served |b−1|=0.046, 32 non-served cities identical) and
precise only in extremes (Taipei −0.3@21°C → +1.9@37°C where all models genuinely lag). σ untouched.
