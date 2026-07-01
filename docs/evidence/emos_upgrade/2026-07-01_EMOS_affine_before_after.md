# EMOS affine center calibration — before/after (2026-07-01)

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
