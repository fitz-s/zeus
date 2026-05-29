# CRITIC — Data-Asymmetry & Shrinkage-Target Validity (ADVERSARIAL)

- Date: 2026-05-29
- Authority basis: read-only adversarial review of TRIBUNAL_DRAFT2_RESPONSE_2026-05-29.md
  + TRIBUNAL_REFRAME_2026-05-29.md, verified against live code at worktree
  `stat-whole-refactor` HEAD `08e6600d2f` (src/calibration/ens_bias_model.py,
  ens_error_model.py, ens_bias_repo.py, scripts/score_error_model_candidates.py),
  evidence files A/B/C/D, and sd3_validation_evidence/product_stratified_high.csv.
  Quantitative leak-zone reproduced with the verbatim estimator math.
- Verdict: **REVISE** — the plan's central self-described antibody ("leaf fallback is
  RAW, not the TIGGE prior", Draft-2 §3a) is FALSE as the estimator is actually built and
  as D_impl_feasibility.md L66-73 recommends wiring it. Three SEV-1 leaks survive. The
  shrinkage architecture is salvageable ONLY with explicit segregation (see §Verdict).

---

## SEV-1 findings (block the redesign as written)

### SEV-1-A — "leaf = raw" is a GATE property the ESTIMATOR contradicts; the moderate-n leak zone is huge, not n=0
**Claim under attack:** Draft-2 §3a/§3a-CRITICAL: "the leaf fallback is RAW, not the TIGGE
prior … When a bucket is thin and nothing clears the gate → serve raw, not the prior."

**Evidence — the estimator emits the TIGGE-shrunk value at ALL n, and the recommended
wiring makes the child prior = the TIGGE-built parent:**
- `ens_bias_model.py:155-156`: `w = v0/(v0+v_o); bias = w*e_bar + (1-w)*prior_mean`.
  The posterior is a CONTINUOUS blend toward `prior_mean`. It equals raw (e_bar) only as
  `w→1` (n→∞), and equals the full prior at `w=0` (n=0). For all moderate n it is a
  TIGGE-contaminated value.
- `ens_bias_model.py:137-143`: when `live is None or n<=0` the posterior is `prior_mean`
  EXACTLY. And `fit_bucket` (`:226`) sets `live=None` whenever `len(opendata_residuals) <
  min_live_n` (default **20**). So below 20 live pairs the emitted candidate is the FULL
  TIGGE prior.
- D_impl_feasibility.md L66-73 (the plan's own recommended extension) wires the child as
  `BiasPrior(mu_t=parent.bias, v0=parent.sd**2)` where `parent = fit_bucket(tigge_by_coarse,
  opd_by_coarse)` — i.e. the child shrinks toward the TIGGE-built city×season parent. This
  is the prior, renamed "parent". The two-layer separation the plan promises does not exist
  at the estimator layer.

**Quantified leak zone (Jeddah MAM, the worst measured transfer case: bias_tigge=-7.35,
bias_opd=+1.72, raw MAE 2.05 → TIGGE-corrected MAE 9.06, product_stratified_high.csv:7):**
Using the verbatim math, parent v0≈0.61 (σ_tigge≈3, n_tigge=25, +V_TRANSFER 0.25),
σ_opd≈2.5:

| n_opd | w_live | emitted bias | leak vs OpenData truth | verdict |
|------:|-------:|-------------:|-----------------------:|---------|
| 5  | 0.328 | -4.38 | -6.10 | POISONED |
| 15 | 0.594 | -1.96 | -3.68 | POISONED |
| 17 | 0.624 | -1.69 | -3.41 | POISONED |
| 20 | 0.661 | -1.35 | -3.07 | POISONED |
| 50 | 0.830 | +0.18 | -1.54 | POISONED |
| 100| 0.907 | +0.88 | -0.84 | POISONED |
| 200| 0.951 | +1.28 | -0.44 | marginal |
| 500| 0.980 | +1.54 | -0.18 | clean |

**The leak zone extends to n_opd≈200 before the candidate is within 0.5°C of OpenData
truth.** EVERY OpenData bucket that exists today has n_opd=12-18 (CSV columns) — all below
`min_live_n=20`, so the emitted candidate is the FULL -7.35 prior (≈9°C / catastrophic).
Re-keying by product×cycle×lead (Draft-2 §3a: "~28×" more buckets) drives essentially
every bucket far below 20. **The redesign mass-produces exactly the buckets where the
estimator emits the proven-harmful TIGGE correction.** This is sd3 under a new name — the
precise outcome the plan claims to prevent.

**Why the plan's reasoning fails:** it assumes the accept-gate (`choose_candidate`) is a
sufficient firewall. But the gate only chooses among *candidates the scoring layer
constructs*, scored on OOS the plan itself admits is underpowered (§5/§3: "1 of 11 clears
LCB"). A firewall that rejects ~10/11 corrections still PASSES the ~1/11 that clear LCB on
n=12-18 noise — and §3's own data shows the single "winner" (SF MAM) is a "known station-gap
artifact." The gate's safety depends entirely on LCB power that does not exist at live depth.

**Concrete design fix:** Do NOT shrink the child toward a TIGGE-built parent. Make the
shrinkage target itself segregation-safe:
1. The hierarchy parent for any OpenData leaf MUST be an OpenData-only ancestor
   (city→metric→global OpenData pooled mean), NEVER the TIGGE prior. TIGGE may only enter
   as a SEPARATE candidate (`tigge-prior-bias` / `tigge→opd-transfer`), never as the
   shrinkage target of an OpenData leaf.
2. When the OpenData ancestor chain is itself thin (the common case today), the leaf prior
   is the **raw-identity prior** `BiasPrior(mu_t=0, v0=large)` — shrink toward ZERO
   correction, not toward TIGGE. Then "thin ⇒ raw" is an ESTIMATOR property (mathematically
   enforced), not a downstream gate hope.
3. Add a relationship test: for any leaf with n_live<200 and |bias_tigge−bias_opd|>2°C, the
   emitted candidate's distance from raw MUST be ≤ its distance from the TIGGE prior. This
   makes SEV-1-A unconstructable.

### SEV-1-B — Product non-commensurability: 6h-window (mx2t6) and 3h-window (mx2t3) residuals are different random variables and must NOT share a hierarchy parent
**Claim under attack:** the whole pooling premise — that TIGGE (mx2t6, 6h aggregation) can
serve as a structural prior for OpenData (mx2t3, 3h aggregation) via `transport_bias_prior`
+ paired Δ.

**Evidence they are non-commensurable:**
- A_source_extraction.md §D1: mx2t6 aggregates over a 6h window `[step-6h, step]`; mx2t3
  over 3h. The daily extremum of a 6h-max field and a 3h-max field are DIFFERENT statistics
  of the same temperature process: a 6h max ≥ 3h max by construction, with a window-width
  dependent positive offset that varies by diurnal shape, city, and season. They are not the
  same RV with a constant shift.
- C_ledger_scorer_praw.md O1: the evidence ledger already mixes both under one
  `source_kind='prior'` literal (`build_ens_residual_evidence.py:227`) with NO product
  stratification — "the bias model fit from this mixed ledger will be biased toward whichever
  product has more coverage in each season bucket." TIGGE has ~38M pairs; OpenData 592k HIGH.
  The mixed-pool mean is ~99% TIGGE-window statistic.
- The plan's claimed defense (`paired_delta_abs` variance inflation, `ens_bias_model.py:132-135`)
  REQUIRES ≥5 same-window paired (TIGGE,OpenData) deltas per fine bucket
  (`ens_error_model.py:315`, `MIN_PAIRED_N=5`; below it `delta_gated=[]` → NO mean shift,
  NO inflation → prior passes through UNCHANGED). After 28× re-keying, same-(city,season,
  cycle,lead,date) overlaps where BOTH products exist are vanishingly rare. **And the paired
  Δ is itself the 6h−3h window difference — a cross-product quantity, i.e. the plan proposes
  to correct product non-commensurability using a sample of the non-commensurability.**

**Reproduced:** with a well-measured paired Δ (n≥5 capturing the full 9°C offset) the
posterior is clean (w≈0.99, bias≈+1.72). With a thin paired Δ (n<5, the realistic
post-rekey case) the prior passes through and the leak is identical to SEV-1-A
(n=17 → bias −1.69, leak −3.41). The defense is real but evaporates at exactly the keying
granularity the plan demands.

**Concrete design fix:** Full product SEGREGATION at the model level. Two separate
estimators keyed by product; they never share a parent. TIGGE may only *propose* a candidate
into the OpenData accept-gate, and that candidate must independently win OpenData same-window
settlement OOS (LCB>0) before it can be served. Drop `transport_bias_prior` as a shrinkage
mechanism — keep it only as an explicit, separately-gated candidate generator. Enforce with
a typed `Residual(forecast, settlement)` whose constructor (Draft-2 §3b) ALSO asserts
`forecast.product == bucket.product` so a mx2t6-derived residual is unconstructable inside an
mx2t3 bucket's pool.

### SEV-1-C — Lead-as-a-level does NOT fix the sign flip; the thin 48h serving leaf shrinks toward a 0-12h-dominated parent of the WRONG SIGN
**Claim under attack:** Draft-2 §3a/§3c: adding lead_bucket as a hierarchy level recovers
the lead-varying signal (Busan −2.97@0-12h → +0.16@24-48h).

**Evidence:**
- Operator core fact (task brief) + REFRAME §2.1: evidence is short-lead-dominated (~38M
  TIGGE pairs, archive, short-lead-heavy); the 48h serving leaf is thin.
- `ens_bias_repo.py:254,281`: `load_bucket_residuals` pools ALL `lead_hours <= 48` into ONE
  mean. The city×season parent (the shrinkage target under the recommended pattern) is
  therefore numerically dominated by the thick 0-12h leads. Bias FLIPS SIGN by lead, so the
  parent mean is the WRONG SIGN for the 48h leaf.

**Quantified (parent lead-pooled mean ≈ −2.5 [0-12h-dominated], 24-48h leaf truth +0.16,
σ_opd 2.5, parent v0≈0.29):**

| n_leaf_48h | w_live | emitted | leak |
|-----------:|-------:|--------:|-----:|
| 3  | 0.124 | -2.17 | -2.33 |
| 5  | 0.191 | -1.99 | -2.15 |
| 10 | 0.321 | -1.65 | -1.81 |
| 20 | 0.486 | -1.21 | -1.37 |
| 50 | 0.702 | -0.63 | -0.79 |

The serving lead (48h, the actual ~48h trade lead per the operator) is exactly the thin
leaf; the poisoning parent is exactly the thick wrong-lead pool. Lead-as-a-level
re-creates SEV-1-A with lead substituting for product. **The dimension the plan adds to fix
the sign flip is the dimension along which the parent poisons hardest.**

**Concrete design fix:** lead leaves must NOT shrink toward a lead-pooled parent. Either
(a) shrink each lead leaf toward the SAME-lead OpenData ancestor only (and to raw when thin),
or (b) treat lead as a within-bucket covariate (as Platt already does for `lead_days`,
B_calibration_keying.md C3) rather than a shrinkage level. Do NOT pool across leads to form a
parent mean. Add a relationship test: sign(emitted_bias[lead]) must not be forced to
sign(parent) when same-lead live n<200.

---

## SEV-2 findings (significant rework; not independently fatal)

### SEV-2-D — The 752 contaminated LOW rows are NOT mechanically blocked from the re-keyed ledger; "must precede" is a sequencing wish, not an antibody
**Claim under attack:** Draft-2 §1 + REFRAME §4: D1-LOW source fix "MUST precede … any LOW
evidence rebuild in the new ledger (else the 752 contaminated rows poison LOW training)."

**Evidence:** D1 LOW DISAGGREGATION (A_source_extraction.md): 752 rows carry
`training_allowed=1, contributes_to_target_extrema=1` with a 1-4°C warm min. The contract/
ledger as designed (Draft-2 §3b: `Residual` constructor raises on unresolved
product/cycle/lead/window) keys on PROVENANCE FIELDS — product, cycle, lead, window — none
of which the 752 rows violate (they ARE valid mx2t3 12z/00z rows with a correct-looking
window; the defect is a VALUE error inside an accepted window, A_source §"step
mis-classification"). The typed contract asserts identity, not value correctness. So a LOW
evidence rebuild that runs before the STEP_HOURS fix lands will admit all 752 rows — the
"must precede" is enforced by nothing but operator memory.

**Concrete design fix:** Gate the LOW ledger rebuild on a DATA CONDITION, not a phase note.
Add a precondition assertion to the LOW ledger builder: refuse to build if any LOW row's
`aggregation_window_hours`/derived window width ≠ 3 for an mx2t3/mn2t3 product
(`ingest_grib_to_snapshots._forecast_window_from_payload`), OR stamp every row with an
`extractor_step_hours` provenance field and have the `Residual` constructor raise when
`product∈{mx2t3,mn2t3} AND extractor_step_hours≠3`. This makes the contaminated row
unconstructable rather than merely sequenced-around. (Live-money impact today is ZERO — LOW
is shadow-only, settings.json:65 `apply_to_metrics:["high"]`, manager.py:958 LOW→
RAW_UNCALIBRATED — hence SEV-2 not SEV-1. But the redesign explicitly plans the LOW rebuild,
so the antibody must exist before that phase, not as a footnote.)

### SEV-2-E — Cycle-as-a-level collides with the loader's per-date cycle DEDUP; "cycle hierarchy" and the existing HIGH=0Z/LOW=12Z selection are mutually inconsistent
**Claim under attack:** Draft-2 §3a / REFRAME §5: re-key bias model on
`...×product×cycle×lead_bucket`.

**Evidence:** `ens_bias_repo.py:316-350`: `load_bucket_residuals` already does a per-
target_date cycle PREFERENCE — HIGH prefers 0Z, LOW prefers 12Z, "if a preferred-cycle
snapshot exists for a date, it wins over any other cycle." This is a cycle DEDUP (one cycle
per day enters the residual list), not a cycle stratification. If the redesign adds `cycle`
as a hierarchy/bucket key while this loader still collapses to one preferred cycle per date,
the non-preferred cycle's bucket will be systematically empty (HIGH 12z bucket ≈ 0 rows; LOW
0z bucket ≈ 0 rows) → those leaves auto-fall to the parent → SEV-1-A again, now for a cycle
that the loader deliberately discarded. The plan never reconciles the new cycle key with the
existing cycle-preference logic.

**Concrete design fix:** Decide ONE cycle semantics. Either (a) cycle is a serving-selection
detail (keep the 0Z/12Z preference, do NOT make it a bias bucket key — the preferred cycle is
the only one that ever serves that metric), or (b) cycle is a genuine bucket key, in which
case `load_bucket_residuals` must STOP deduping by cycle and return per-cycle residual lists.
Both-at-once is the silent-empty-bucket trap. Recommend (a): cycle is not a bias dimension;
HIGH always serves 0Z, LOW always 12Z, so a per-cycle bias is unidentifiable for the
non-served cycle.

### SEV-2-F — EQUIVALENCE-mode "before fixture" cannot certify the analytic p_raw because the production MC is itself stochastic and rounding-quantized; |Δ|≈0 is unprovable, only |Δ|≤MC-noise is
**Claim under attack:** Draft-2 §2a/§3d: analytic p_raw vs 10k MC must be "≈0 / within MC
sampling noise."

**Evidence:** C_ledger_scorer_praw.md C3-c: `settlement_semantics.round_values()`
(`ensemble_signal.py:254-258`) quantizes each member draw to integer °F BEFORE binning. The
analytic mixture-of-normals CDF is exact only PRE-rounding; post-rounding the true
distribution is a staircase the smooth CDF cannot reproduce exactly. So the achievable bar is
NOT |Δ|≈0 but |Δ| ≤ (MC sampling noise + rounding-discretization bias). The plan cites the
"two scorers reconcile to |Δ|≤2e-4" precedent (Draft-2 §2a/§2d) — but that is MC-vs-MC on the
same sampler, NOT analytic-vs-MC across the rounding nonlinearity. The precedent bar is the
wrong bar.

**Concrete design fix:** Specify the equivalence tolerance as a function of bin width and
member σ, derived analytically (the rounding bias per bin is bounded by the probability mass
within ±0.5°F of each bin edge). Require the analytic path to evaluate Φ at the rounding
PREIMAGE edges `[a−0.5, b+0.5)` (Draft-2 §3d already says this — good) AND prove the residual
discretization error bound is below the chosen tolerance, rather than asserting |Δ|≈0.
Retire MC only when analytic matches MC to within the *derived rounding-error bound at the
production bin grid*, under every `settlement_rounding_policy`.

---

## SEV-3 findings (correctness/hygiene; low blast radius)

### SEV-3-G — `min_live_n` inconsistency between the two fit entry points
`ens_bias_model.fit_bucket` defaults `min_live_n=20` (`:206`); `ens_error_model.py:42`
comment claims "Matches _ERROR_MODEL_MIN_LIVE_N in ens_bias_model.py (both are 5)" but
`fit_predictive_error_bucket` (`:244`) and `fit_city_predictive_error` (`:281`) pass
`min_live_n=DEFAULT_MIN_LIVE_N=20`. The "both are 5" comment is stale/contradicted by the
code it sits next to. Under the redesign's 28× thinner buckets, whether the live cutoff is 5
or 20 changes which buckets emit prior-only vs blended — a load-bearing constant documented
wrong. **Fix:** reconcile the comment; pin the live-sufficiency N in the gate-set hash so a
change auto-quarantines stale rows (the hash mechanism at `ens_error_model.py:78-109` already
pins `DEFAULT_MIN_LIVE_N` — good — but not `fit_bucket`'s separate default 20).

### SEV-3-H — `coverage_months` is calendar-indexed while `season` is hemisphere-flipped (SH); a re-keyed diagnostic could mis-certify SH coverage
Per B_calibration_keying.md O3: post-sd3 SH rows carry SH-flipped `season` but
calendar-month `coverage_months`. A re-keyed evidence audit that checks "is target month in
coverage_months" will mis-judge SH cities. Low blast radius today (most live cities NH) but
the redesign adds a coverage-driven thin-bucket fallback decision — **fix:** make
`coverage_months` hemisphere-aware (store the season-label months) or have the fallback logic
key on `season` not `coverage_months`.

---

## Verdict on the deepest question: SEGREGATION vs POOLING

**The operator's framing is correct and the plan's is not. "Eliminate data asymmetry" is
NOT achievable by re-keying a shrinkage estimator whose root prior is TIGGE.** Re-keying
multiplies buckets, drives n_live far below the n≈200 needed for `w→1`, and at every
moderate-n leaf the estimator emits a TIGGE-contaminated candidate (SEV-1-A) — across product
(SEV-1-B) and lead (SEV-1-C) the poisoning parent is exactly the thick, wrong-window /
wrong-sign pool. The accept-gate cannot rescue this because its safety depends on LCB power
the live data does not have (§5: 1/11 clears; the 1 is a known artifact).

**Honest treatment = SEGREGATED per-product (and lead-respecting) models + transfer-only-on-
proof, accepting that most OpenData buckets have NO usable correction yet.** Concretely:
1. Two product-segregated estimators; OpenData leaves shrink only toward OpenData ancestors,
   and toward RAW-IDENTITY (mu=0) when the OpenData ancestor is thin — never toward TIGGE.
2. TIGGE is demoted from "shrinkage prior" to "one explicit candidate" that must
   independently win OpenData same-product/same-lead settlement OOS (LCB>0) to be served.
   Drop `transport_bias_prior` as a shrinkage target.
3. Lead handled as a within-bucket covariate (Platt-style) or same-lead-only shrinkage,
   never lead-pooled-parent shrinkage.
4. The default — and for ~10/11 buckets the PERMANENT near-term answer — is raw identity.
   This is not a failure of the redesign; it is the correct admission that 592k HIGH /
   7,858 LOW pairs at ~48h lead cannot yet certify any correction. Build the segregated
   machinery so corrections turn on automatically as live depth crosses LCB>0 per bucket —
   but do not let the architecture pretend a correction exists where the data says none does.

The plan's structural instincts (typed Forecast/Settlement contract, accept-gate with raw
default, analytic p_raw, dual-run migration) are sound and worth building. But its load-
bearing statistical claim — that extending the EB hierarchy with raw-leaf-fallback eliminates
the asymmetry — is refuted by its own estimator code and its own CSV. Adopt the contract and
the gate; REPLACE the shrinkage target. Until §1-§3 above are fixed, the redesign re-arms the
exact bias it exists to kill.
