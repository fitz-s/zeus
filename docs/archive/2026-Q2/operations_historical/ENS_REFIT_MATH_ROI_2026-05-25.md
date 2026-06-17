# ENS Refit — Math / ROI Consolidated Report

Created: 2026-05-25
Last reused or audited: 2026-05-25
Authority basis: ENS_REFIT_REFINEMENT_ROADMAP_2026-05-25.md §4 (audit battery); operator
math-ROI directive 2026-05-25. Consumes ENS_REFIT_FULLDB_HIGH/LOW_2026-05-25.md (§4.1),
ENS_ROUTE6_TRANSPORT_BETA_2026-05-25.md, scripts/audit_refit_proper_scores.py,
scripts/rebuild_calibration_pairs.py (commit d973d0d00b), src/calibration/ens_error_model.py.
DB: /private/tmp/ens_refit/full.db (read-only, 33 GB; trade tables empty).

> Bottom line up front. (1) There is NO ungated p_raw variant in the DB and NO production
> route-to-raw gate today — the operator's "measure both ways" framing presumes a switch that
> does not exist. (2) Every §4.1 raw-vs-ft comparison is computed on ZERO-overlap date sets
> (global (city,target_date) overlap = 0.0% for HIGH and LOW), so raw-vs-ft Brier/LogLoss/RPS
> deltas are confounded. (3) HK HIGH full_transport is nonetheless an ABSOLUTE calibration
> failure on its own OOS data (PIT 96.9% in [0,0.1], +6.3°F over-warm at every lead) — the
> carve-out is justified by PIT pathology, NOT by the confounded Brier delta. (4) Miami HIGH is
> mostly confound, not pathology. (5) PR #64 would wire ft to live with NO cohort exclusion
> unless one is added — that is the entire reason the carve-out matters.

---

## 1. THE CRITICAL QUESTION — gated vs ungated; is the HK/Miami HIGH regression real?

### 1a. Structural finding: there is no gated/ungated dichotomy in the data

The SNR gate is NOT a runtime route-to-raw switch. It is a continuous confidence weight
λ ∈ [0,1] baked into bias subtraction at p_raw GENERATION time:

- `src/calibration/ens_error_model.py:39-56` — `correction_strength(bias, bias_sd, heterogeneity_var)`
  returns λ from z = |bias| / sqrt(bias_sd² + het_var); z<1 → λ=0, z≥2 → λ=1, linear between
  (SNR_LO=1.0, SNR_HI=2.0).
- `:85-99` — `predictive_error_from_posterior` sets `effective_bias_c = λ · bias_c`.
- `:135-139` — `p_raw_vector_with_error_model` subtracts `effective_bias_c · scale` pre-MC and
  widens the MC draw by `total_residual_sd · scale` via `p_raw_vector_from_maxes(extra_member_sigma=…)`.

The seeder that produced `error_model_family='full_transport_v1'` (commit d973d0d00b,
`scripts/rebuild_calibration_pairs.py` + `_parallel.py`) fits one `PredictiveErrorModel` per
(city, season, metric) via `fit_city_predictive_error` → `predictive_error_from_posterior`. So
**the §4.1 `full_transport_v1` p_raw is the SNR-GATED path by construction.** There is no λ=1
("ungated") family in `calibration_pairs_v2` — only `none` (raw) and `full_transport_v1` (gated).

Verdict on the operator's "(a) ungated / (b) gated" request: **NOT SEPARABLE from this DB.**
Producing an ungated number would require a fresh multi-hour re-seed on the 33 GB DB with the
gate forced off. We do not fabricate one. The audit harness
(`scripts/audit_refit_proper_scores.py`) reads pre-seeded p_raw and applies NO further gating
(confirmed: `_load_rows` selects stored `p_raw` by `error_model_family`; no λ recomputation).

### 1b. Structural finding: there is no production route-to-raw gate today

`grep` for the error-model layer in live serving (`src/forecast/`, `src/oracle/`,
`src/strategy/`, `src/signal/`) returns EMPTY for `error_model_family`,
`p_raw_vector_with_error_model`, `fit_city_predictive_error`, `ens_error_model`. The layer is
offline-only (rebuild + serve-guard). **PR #64's effect is to WIRE `full_transport_v1` p_raw to
live serving (refit → live platt_models_v2 / model_bias), not to introduce a gate.** Any claim
of the form "no live regression because production already routes HK/Miami HIGH to raw" is FALSE —
there is no such route. Absent a cohort carve-out in #64, ft ships to live for all 48 cities.

### 1c. Confound discovery: §4.1 raw vs ft is measured on DISJOINT date sets

| Scope | `none` (city,date) | `full_transport_v1` (city,date) | overlap |
|---|---|---|---|
| GLOBAL HIGH | 26,453 | 17,821 | **0 (0.0%)** |
| GLOBAL LOW | 18,509 | 923 | **0 (0.0%)** |
| HK HIGH | 606 dates (2024-01..2026-02) | 254 dates (2024-03..2026-05) | **0** |
| Miami HIGH | 365 dates (2024-06..2025-11) | 504 dates (2024-01..2026-05) | **0** |

The raw and ft families share NO common (city, target_date). Every raw-vs-ft delta in §4.1 —
including the headline global Brier −15% AND the HK/Miami HIGH "regression" — compares two models
on two non-overlapping test populations. The deltas are directionally suggestive but NOT
apples-to-apples. (Miami raw's strong 0.766 Brier sits on a summer-heavy 2024-06..2025-11 window;
Miami ft spans the full annual cycle incl. winter — part of its worse score is a harder test set.)

### 1d. Hand-check (the absolute, confound-free measurement)

Re-derived from the DB on ft's OWN OOS data (reconciles with §4.1 Brier to 4 dp → harness valid):

| Cohort | groups | PIT_mean | PIT<0.1 | Brier | pred mass-center |
|---|---|---|---|---|---|
| HK HIGH raw | 5016 | 0.618 | 0.153 | 0.9775 | 26.05 °F |
| **HK HIGH ft** | 2541 | **0.013** | **0.969** | **1.1551** | **32.37 °F (+6.3°F warmer)** |
| Miami HIGH raw | 2920 | 0.762 | 0.036 | 0.7658 | 86.60 °F |
| **Miami HIGH ft** | 4689 | **0.413** | **0.262** | **0.8909** | **82.05 °F (−4.6°F)** |

PIT = F(Y_obs). HK HIGH ft PIT collapses to ~0 (96.9% in [0,0.1]) = outcomes fall in the predicted
LEFT tail = **ft systematically predicts HK HIGH far too warm (+6.3°F)**. This is an absolute
calibration failure on ft's own data — no cross-comparison to raw needed, so the disjoint-date
confound does NOT excuse it. HK HIGH ECE 0.0155 vs 0.0010 global = 15× worse.

HK HIGH ft PIT pathology by lead bucket (present at EVERY lead, not horizon-driven):

| lead | n | PIT_mean | PIT<0.1 |
|---|---|---|---|
| 0 | 312 | 0.043 | 0.894 |
| 1 | 324 | 0.005 | 0.988 |
| 2-3 | 644 | 0.007 | 0.980 |
| 4-5 | 636 | 0.008 | 0.981 |
| 6-7 | 625 | 0.012 | 0.971 |

The over-warming is constant across leads → a per-bucket POSTERIOR-BIAS sign/magnitude error for
the HK HIGH bucket, not a transport-architecture failure. Confirmation: HK LOW under ft is a huge
WIN (§4.1 LOW: Brier 1.4376→0.8815, LogLoss 24.41→2.14, RPS 3.31→0.92). Same city, same code, same
gate, opposite metric. Per `scripts/onboard_cities.py:1017-1028`, `fit_city_predictive_error` is
called SEPARATELY per `metric in ("high","low")` → HK HIGH and HK LOW have independent posteriors.
So the defect is localized: **HK HIGH posterior bias is wrong; HK LOW posterior is correct.**

Miami HIGH ft: PIT_mean 0.413, PIT<0.1 0.262 — mildly left-skewed (slight over-warm) but PIT center
is near-uniform and not catastrophic. Most of Miami's "regression" is the disjoint-date confound,
not a calibration pathology.

### 1e. REGRESSION VERDICT

- **HK HIGH: REAL pathology (carve out).** Absolute PIT failure (96.9% bin-0, +6.3°F over-warm,
  all leads), ECE 15× global. Independent of the disjoint-date confound. Root cause: wrong HK HIGH
  posterior bias, not transport architecture (HK LOW is fine).
- **Miami HIGH: NOT a real pathology; mostly confound.** PIT_mean 0.413 ≈ center, modest left-skew.
  The §4.1 Brier +16% is dominated by the disjoint, harder ft test window. No carve-out required on
  PIT/ECE grounds; flag for shadow-window re-eval on matched dates.
- There is NO "gated path that already routes HK/Miami HIGH to raw." The gate is intrinsic and
  continuous; live serving does not consume the layer yet. **A live regression WOULD occur for
  HK HIGH if #64 ships ft to live without an HK-HIGH carve-out.**

---

## 2. Routes 1–10 ROI ranking (blocked-OOS verdicts)

| Rank | Route | ROI claim | Beats ft baseline (blocked OOS)? | Verdict | Evidence |
|---|---|---|---|---|---|
| 1 | Finish 10k ft refit + Platt | highest | global Brier 0.88 vs raw 1.04, LogLoss 2.55 vs 7.26 (§4.1, confound-caveated) | **PASS (global), FAIL (HK HIGH)** | §4.1 HIGH/LOW; §1d hand-check |
| 2 | Ordered-bin CDF / RPS calibration | high | not run | **UNTESTED** | no result doc; trigger = "Platt ECE/RPS poor" |
| 3 | Robust Kelly / edge-uncertainty gate | high | not run (needs decision tables) | **UNTESTED** | §4.3 blocked (trade tables empty) |
| 4 | Conformal / no-bet overlay | high | not run | **UNTESTED** | risk layer; would catch HK-HIGH-type over-confidence |
| **5** | **Spread-dependent residual scale (EMOS)** | med-high | **not run — script present, no result** | **UNTESTED** | `scripts/experiment_route5_spread_scale.py` exists; ROI index line 24 "fix pending"; precondition (residual↔ENS-spread correlation) unmeasured on full.db |
| **6** | **Day-specific Δ(F25−F50) transport β** | med-high | **NO** | **FAIL (testable subset); UNTESTABLE (HK/Miami)** | ENS_ROUTE6 doc: 33 paired groups Brier +0.136 / LogLoss +4.47 / RPS +2.44 all WORSE; mean β≈−1.5 (overfit on 5-day sample); HK 0 paired days, Miami 1 |
| 7 | Prequential drift monitors | med-high | n/a (runtime health) | **UNTESTED** | mandatory post-live; not a calibration improvement |
| 8 | Market-prior fusion | med | not run | **UNTESTED** | explicitly deferred until p_cal stable (roadmap §3) |
| 9 | Dirichlet/multinomial calibration | med | not run | **UNTESTED** | only after corrected Platt proven insufficient |
| 10 | Spatial-gradient / k-nearest | conditional-high | not run | **UNTESTED** | gated behind Δ-transport (Route 6) which already FAILED |

**Route 5 explicit blocked-OOS verdict: UNTESTED.** Script exists but no result was produced (ROI
index flags `_load_groups` bottleneck). Its roadmap trigger — residual-vs-ENS-spread correlation —
has not been measured on full.db, so the precondition is unverified. No PASS/FAIL is defensible.

**Route 6 explicit blocked-OOS verdict: FAIL.** On the 33 testable paired distributions, every
proper score regresses vs the ft baseline (Brier +0.1355, LogLoss +4.4677, RPS +2.4440;
ENS_ROUTE6_TRANSPORT_BETA_2026-05-25.md lines 87-98). Mean β ≈ −1.5 signals overfitting on a
5-calendar-day paired sample. CRITICALLY, Route 6 is UNTESTABLE on the §4.1 catastrophic cohort
(HK 0 paired F25+F50 days, Miami 1) — so even if it worked it could not fix HK HIGH. Route 6 does
not earn its rank-6 ROI today; revisit only when ≥30 days of paired F25+F50 data accrue.

---

## 3. §4.2 — Is Platt redundant or harmful on top of full_transport?

**Status of measurement.** §4.2 was SKIPPED in both §4.1 validation docs ("--no-platt or Platt fit
failed"). The blocked 5-fold Platt on ft HIGH fits logistic regression on ~13.5M rows/fold × 5
folds, each exceeding the 600s harness timeout (ROI index lines 56-62). The "ECE 0.0010 → 0.0378"
figure in the brief is the operator's stated CONCERN, not a computed result on full.db. We did not
fabricate a number; the DB holds 160 in-sample-fit `platt_models_v2` rows (all
`error_model_family='full_transport_v1'`), which give an optimistic LOWER bound, not an OOS verdict.

**Mathematical argument (decisive without re-fit).** ft p_raw global ECE = 0.0010 (HIGH) / 0.0032
(LOW) — at or near the floor of 10-bin ECE resolution for n≈218k/152k. A monotone post-hoc
calibration map (Platt) on an input that is already calibrated to ≈0.001 CANNOT improve reliability;
any departure from the identity map only injects estimation noise. Where per-cohort ft ECE is
already < ~0.005, Platt is at best redundant and at worst mildly harmful (the operator's 0.0378
intuition is the expected direction). Where ft ECE is genuinely poor (HK HIGH 0.0155, Houston
0.0082, Shanghai 0.0086), Platt MIGHT help — but those cohorts have an upstream p_raw pathology that
Platt should not be asked to paper over.

**Recommendation: GATE Platt on per-cohort raw(ft) ECE.** Ship ft p_raw DIRECTLY (skip Platt) where
ft ECE ≤ 0.005 (the vast majority of cohorts). Apply Platt only where ft ECE > 0.005 AND the PIT is
not pathological — and prefer fixing the upstream posterior (HK HIGH) over masking with Platt. Do
NOT globally drop Platt (legacy continuity) and do NOT globally keep it (it degrades already-calibrated
cohorts). This is consistent with roadmap Route 2 (ordered-CDF) being the escalation if gated Platt
still fails RPS/reliability.

---

## 4. Gate-aware per-cohort ship/no-ship verdict for PR #64

PR #64 = production data replace (refit → live `platt_models_v2` / `model_bias`).

**§4.3 precondition (BLOCKED, must be stated).** The decision audit (edge dist, Kelly size,
false-positive edge rate, paper-replay PnL/regret) CANNOT run on full.db — `decision_events`,
`execution_fact`, `opportunity_fact`, `probability_trace_fact` are all empty (0 rows). It requires:
production `zeus-world.db` (decision_events) + `zeus-forecasts.db` (probability_trace_fact) +
FRESH traces generated AFTER #64 lands + flag ON + daemon restarted. **No §4.3 evidence exists yet;
#64 must not be declared trade-safe on §4.1/§4.2 alone.**

**Ship criteria (PIT/ECE absolute, not raw-vs-ft delta — delta is confounded):**

| Cohort | ft PIT | ft ECE | vs raw* | Verdict for #64 |
|---|---|---|---|---|
| GLOBAL HIGH | broad, slight underdispersion | 0.0010 | −15% Brier (confounded) | **SHIP** (gate Platt per §3) |
| GLOBAL LOW | reasonable | 0.0032 | −15% Brier (confounded) | **SHIP** (gate Platt per §3) |
| **HK HIGH** | **96.9% bin-0, +6.3°F warm, all leads** | **0.0155** | +18% Brier (confound + real) | **NO-SHIP / CARVE-OUT** — route to legacy `model_bias`; fix HK HIGH posterior before ft |
| HK LOW | near-uniform | 0.0055 | −39% LogLoss | **SHIP** |
| **Miami HIGH** | PIT_mean 0.413, mild left-skew | 0.0039 | +16% Brier (mostly confound) | **CONDITIONAL** — no PIT/ECE pathology; re-eval on matched dates in shadow; not a hard carve-out |
| Miami LOW | reasonable | 0.0069 | strong win | **SHIP** |
| Other 46 HIGH / LOW cohorts | no PIT spike >30% in any extreme bin | ≤ ~0.008 | improve | **SHIP** (gate Platt per §3) |

\* raw-vs-ft delta is confounded by 0% date overlap; shown for context only, NOT a ship driver.

**Carve-out rule (generic, no city names in code):** for any (city, metric) bucket where ft OOS PIT
concentrates > 30% of mass in a single extreme decile [0,0.1] or [0.9,1.0] OR ft ECE > 5× the global
ft ECE, exclude that bucket from the ft live-replace and retain legacy `model_bias` until its
posterior is re-fit and re-validated. Under this rule HK HIGH carves out; Miami HIGH does not.

---

## 5. Limitations

- **Disjoint test sets (primary).** `none` vs `full_transport_v1` share 0% of (city,target_date)
  globally. All §4.1 raw-vs-ft deltas are non-comparable; only ABSOLUTE ft metrics (PIT, ECE on ft's
  own data) are confound-free. The headline global −15% Brier is suggestive, not proven head-to-head.
- **No ungated variant.** "Gated vs ungated" is not separable from this DB; the gate is intrinsic to
  generation. An ungated measurement needs a fresh forced-λ=1 re-seed (multi-hour on 33 GB).
- **§4.2 Platt is a math argument + in-sample bound, not a finished blocked-OOS run** (harness
  timeout). The recommended per-cohort ECE gate is the action; the exact 0.0378 was not recomputed.
- **Route 5 UNTESTED; Route 6 FAIL on a 5-day paired sample.** Neither can address HK HIGH (Route 6
  has zero HK paired data).
- **§4.3 BLOCKED** — no live trade tables; #64 trade-safety is unverified pending post-migration traces.
- Correlation/PIT pathology ≠ causal proof of the posterior defect; the lead-invariant +6.3°F shift
  is strong circumstantial evidence pending direct inspection of the HK HIGH `model_bias_ens_v2`
  posterior (not present in this staging DB).

---

## REGRESSION VERDICT (gated vs ungated) + COHORT SHIP TABLE

**Regression verdict.** The DB has no ungated variant and no production route-to-raw gate, so
"gated vs ungated" is not measurable and the operator's framing does not match the code. The §4.1
raw-vs-ft deltas are confounded by 0% date overlap. On confound-free absolute metrics: **HK HIGH
full_transport is a REAL calibration pathology (carve-out justified by PIT pathology — 96.9% in
[0,0.1], +6.3°F over-warm at every lead, ECE 15× global — NOT by the confounded Brier delta);
Miami HIGH is mostly confound, not pathology.** A live regression would occur for HK HIGH if #64
ships ft without a carve-out.

| Cohort | Ship #64? | Driver |
|---|---|---|
| GLOBAL HIGH / LOW | SHIP (Platt gated per §3) | ft well-calibrated globally |
| HK HIGH | **NO-SHIP / CARVE-OUT** | PIT 96.9% bin-0, +6.3°F warm, ECE 0.0155; fix posterior first |
| HK LOW | SHIP | strong win, near-uniform PIT |
| Miami HIGH | CONDITIONAL (shadow re-eval, matched dates) | no PIT/ECE pathology; delta is confound |
| Miami LOW | SHIP | strong win |
| Other 44 cohorts | SHIP (Platt gated per §3) | no PIT spike, ECE ≤ ~0.008 |

Carve-out is justified by PIT pathology, not by the confounded Brier delta.
