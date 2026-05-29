# CRITIC — Statistical Soundness of the Tribunal-Draft-2 Reshape

- Date: 2026-05-29
- Authority basis: ADVERSARIAL read-only statistical review of estimators + selection +
  inference against live code at worktree `stat-whole-refactor` HEAD 08e6600d2f.
  Angle: statistical ABSOLUTE correctness (operator priority; replay explicitly de-weighted).
- Reviewer: critic agent (Opus, max effort). Tools read-only; this file is the durable record.
- Scope reviewed: TRIBUNAL_DRAFT2_RESPONSE / TRIBUNAL_REFRAME + B/C/D evidence docs; code:
  ens_bias_model.py, ens_error_model.py, ens_bias_repo.py, platt.py, scoring.py, blocked_oos.py,
  decision_group.py (effective_sample_size.py), decision_group_id.py, audit_refit_proper_scores.py,
  score_error_model_candidates.py, ensemble_signal.py.

> Verdict: the reshape's STRUCTURAL spine (contract, lineage ledger, lead/cycle/product keying,
> raw-default accept gate) is sound and worth building. But the STATISTICAL machinery the plan
> leans on — "LCB>0 accept gate", "EXTEND audit_refit for IMPROVEMENT mode (~20 LOC)", "EB
> shrinkage makes fine keying feasible" — is, at HEAD, either non-existent, anticonservative, or
> mis-specified for the n=12-18 autocorrelated-daily regime it will run in. Four SEV-1s below.
> The plan's own §5 honesty ("improvement mode underpowered for months") is correct and is the
> single most important sentence in the document — but the spec body then quietly under-budgets
> the inference rigor required to make that honesty safe.

---

## SEV-1 findings (ship-broken / adopts noise as signal)

### S1. No multiple-comparisons control on the candidate accept-gate — and Zeus already owns the fix it ignores
**Math/evidence.** The IMPROVEMENT gate (`score_error_model_candidates.py:64-121`) runs, per the
plan's own §3a keying, across ~28 buckets (2 product × 2 cycle × 7 lead) × 6 candidates × 3 proper
scores. `choose_candidate` accepts a candidate on a **per-bucket** `improvement_lcb > 0` (line 100)
with NO family-wise or FDR adjustment across buckets/candidates. At a nominal one-sided 5% LCB,
~28×6 ≈ 168 independent "does this beat raw" tests yield ~8 false adopts by chance even if every
true effect is zero. The accept-gate is the *serving authority* (REFRAME §2, DRAFT2 §3a), so each
spurious win arms a known-harmful correction on a thin bucket — i.e. the plan re-creates the sd3
failure mode it exists to kill, just bucket-by-bucket instead of globally.

The damning part: **Zeus already has the antibody.** `src/contracts/decision_evidence.py:3` —
*"Entry uses bootstrap p-value + BH-FDR (α=0.10, n=200+, CI_lower > 0)"*; `execution_intent.py:1346-1399`
carries `fdr_family_id`/`fdr_hypothesis_id`; `semantic_types.py:77` has `FDR_FILTERED`. The
*edge-entry* path does multiplicity control correctly; the *calibration candidate-selection* path
does not, despite being a larger simultaneous-inference family on thinner data.

**Fix.** Make candidate selection a declared FDR family. Compute a one-sided bootstrap p-value per
(bucket,candidate) for H0: score_raw − score_cand ≤ 0; apply Benjamini-Hochberg at α across the
*entire* family of bucket×candidate tests (reuse the `bh_fdr` machinery referenced in
decision_evidence.py); adopt only BH-survivors. The per-bucket `lcb>0` then becomes a within-family
effect-size floor, not the family-wise gate. Without this, raise α dramatically or shrink the
candidate set — but FDR is the right, already-precedented answer.

### S2. The "LCB>0 accept gate" does not exist at HEAD and is mis-scoped as "~20 LOC"
**Evidence.** `choose_candidate` *consumes* `improvement_lcb` as a caller-supplied dict
(`score_error_model_candidates.py:67,79,96`); it computes nothing. The module docstring (lines
18-20) states the scoring path "is wired in a follow-up commit" — confirmed by C-doc O3. The only
runnable OOS gate, `blocked_oos.py`, has **zero bootstrap**: `recommend_calibration_promotion`
(lines 229-262) gates on a point `brier_improvement > 0` with no CI, and `_fit_bucket` calls Platt
with `n_bootstrap=0` (line 116). DRAFT2 §2d/§3a and D-doc Claim 2 call the LCB a "~20 LOC addition
to `_aggregate_metrics`". That estimate is wrong by the part that matters: a *correct* LCB here is
not "bootstrap the mean of per-distribution scores" — it must (a) resample at the right dependence
unit (S3), (b) be a paired difference raw−cand on the *same* resampled units, (c) carry the FDR
family (S1), and (d) cohere with the 2/3-score rule (S4). The 20-LOC version will compile and
produce an anticonservative number that passes review and ships noise.

**Fix.** Treat the LCB engine as a first-class deliverable with its own relationship tests
(coverage simulation at n=12-18, see S3), not a tail-end addendum. Until it exists and is
coverage-validated, the gate cannot certify anything; the IMPROVEMENT-mode phase (Phase 6) must be
marked BLOCKED, not "compose from existing harness".

### S3. Variance-of-the-mean and bootstrap both assume IID; the data are autocorrelated daily forecasts re-keyed so the SAME settlement-day Y recurs — n is inflated, every CI is anticonservative
**Math/evidence.** Two independent places assume independence that the re-keying destroys:

1. **EB likelihood variance.** `ens_bias_model.py:145` `v_o = live.sigma2 / live.n` with
   `sigma2 = statistics.variance(opendata_residuals)` and `n = len(opendata_residuals)`
   (`fit_city_predictive_error:322-323`; `fit_bucket:226-229`). `Var(mean)=σ²/n` holds only for
   IID. Daily 2 m-temp forecast errors are strongly serially correlated (synoptic persistence,
   multi-day heat regimes), so effective n ≪ n. V_O is understated → `w = V0/(V0+V_O)`
   (`:155`) is *over*-stated → the posterior over-trusts thin, autocorrelated live evidence. This
   is the exact "shrinkage toward live noise" risk, and it bites hardest in precisely the thin
   fine-keyed buckets the plan adds.

2. **Re-keyed duplication of Y.** `decision_group_id_v1_hash` (`decision_group_id.py:111-119`)
   includes `target_date`, `forecast_available_at`, **and** `lead_days_bucket`. So one settlement
   day at lead 1 vs lead 2 vs 00z vs 12z = *distinct* decision-groups carrying the *same* outcome
   Y and near-identical weather realization. Any estimator counting rows/groups as independent
   (the EB `n`, the bootstrap resample unit) double-counts the same physical event.

`effective_sample_size.py`/`decision_group.py` exists but only collapses bin-rows→decision-groups
for *Platt maturity* counting (`summarize_maturity_shadow`); it is NOT consulted by the bias
estimator's V_O, and it does not deduplicate same-day cross-lead/cross-cycle events.

**Fix.** (a) Define the independent unit as the **settlement day** (city×target_date), not the
snapshot row or the decision-group. (b) For the EB likelihood, use an effective sample size
n_eff = n·(1−ρ)/(1+ρ) with ρ the lag-1 autocorrelation of daily residuals (or cluster-robust
variance by target_date), and feed n_eff into V_O. (c) For the LCB, use a **block bootstrap**
resampling whole settlement-days (moving/circular block to preserve short-range autocorrelation),
never IID resampling of rows or groups. (d) Add a coverage relationship-test: simulate AR(1)
daily residuals at n=12-18, confirm the LCB achieves ≥ nominal one-sided coverage before any gate
trusts it.

### S4. The OOS fold engine the plan will EXTEND is not date-blocked → train/test leak the same Y across leads & cycles
**Evidence.** The plan designates `audit_refit_proper_scores.py` as the engine to extend for
IMPROVEMENT mode (DRAFT2 §2d, D-doc Claim 2). Its fold assignment is
`fold_of = {g: (i % n_folds) for i, g in enumerate(sorted(groups))}` (`audit_refit_proper_scores.py:376`)
where `groups` are decision_group_ids. Because a single `target_date` maps to *many* groups (S3:
different lead_bucket / forecast_available_at), target_date 2026-06-01@lead1 can land in a TRAIN
fold while 2026-06-01@lead2 lands in the TEST fold. Same settlement outcome, same atmosphere →
**leakage**, which inflates exactly the OOS scores feeding the accept gate. (The standalone
`blocked_oos.py` *is* clean — it forward-splits on `target_date >= test_start`, lines 77,171-172 —
but that is not the harness the plan extends, and it has no RPS/PIT and no bootstrap.)

**Fix.** Block folds by `target_date` (all groups sharing a settlement day go to the same fold),
or better, by contiguous date blocks to also respect autocorrelation. This is mandatory *before*
any LCB is computed on this harness; an LCB on leaked folds is worse than no LCB because it looks
rigorous.

---

## SEV-2 findings (likely-wrong / materially weakens the inference)

### S5. "≥2 of 3 proper scores" is an ad-hoc, anticonservative rule on highly-correlated scores
**Evidence.** `MIN_PROPER_SCORE_WINS = 2` over {logloss, rps, brier}
(`score_error_model_candidates.py:30-33,57-61`). On a multinomial bin distribution these three are
strongly positively correlated (all minimized by the true distribution; RPS and Brier especially
co-move). "Win 2 of 3" is therefore close to "win 1 of ~1.5 effective independent scores" — it is
*easier* to pass than a single-metric test at the same α, not a multi-metric safeguard. It also
silently mixes a strictly-local-improper-for-ordinal score (Brier ignores bin order) with an
ordinal one (RPS) and a sharpness-sensitive one (logloss), so a candidate that helps tails but
hurts the mode can pass on the two that happen to co-move.

**Fix.** Pick ONE primary proper score as the gate (logloss or RPS — both strictly proper and
decision-relevant), put its difference through the FDR+block-bootstrap LCB (S1/S3), and demote the
other two to reported diagnostics / a catastrophe veto. If a composite is wanted, define it as a
single pre-registered statistic (e.g. RPS) with the others as one-sided guardrails, not a vote.

### S6. EB shrinks every thin bucket toward a parent the plan itself proved is harmful
**Evidence/math.** `posterior_bias` shrinks toward `prior_mean = mu_t + delta_g`
(`ens_bias_model.py:129,156`); the prior is the TIGGE structural prior, and `fit_city_predictive_error`
transports it (`transport_bias_prior`, `:295-325`). REFRAME §3 + DRAFT2 §3a state TIGGE→OpenData
transfer **hurts 7/11 buckets** (Jeddah 2.05→9.06). With ~28× more buckets on ~7,858 LOW pairs,
almost every fine bucket has n_live < `min_live_n=20` → live=None → posterior == prior
(`:137-143`), i.e. the leaf collapses to the known-harmful transported mean. DRAFT2 §3a *recognizes*
this ("auto-collapse to the prior = sd3 under a new name") and resolves it by making the gate
default to raw — but that resolution depends entirely on S1/S2/S4 being correct. If the gate is
anticonservative (which it is at HEAD), the harmful-prior leaf is exactly what leaks through. The
EB estimator is thus only as safe as the gate; the plan's claim that adding hierarchy levels
"makes the correct keying data-feasible" (§3a) is a statement about *point estimates*, not about
*identifiability* — the variance components (V0 transfer floor vs per-leaf V_O) are not jointly
identifiable at n_live<20, so the shrinkage weight w is essentially a prior-driven constant, not
learned.

**Fix.** Three concrete guards: (a) make the leaf fallback **raw identity**, not the prior, when
n_eff below threshold — i.e. the candidate generator should emit `raw` (not the transported prior)
as the thin-bucket value, so a gate failure degrades to raw not to the harmful correction;
(b) gate the transport step on measured same-window equivalence per bucket (already half-built:
`MIN_PAIRED_N=5`, `ens_error_model.py:315`) and widen V_TRANSFER when paired-Δ is large rather than
trusting `delta_g`; (c) report w per bucket in the manifest so a reviewer can see which "fits" are
actually 100% prior.

### S7. PIT-based dispersion diagnosis is confounded by discretization — under-dispersion claim is not cleanly supported
**Evidence.** `_pit_u` (`audit_refit_proper_scores.py:204-214`) uses the non-randomized PIT
`F(Y)=cumsum(p)[outcome_idx]`; the docstring itself warns a U-shape "arises from discretization
alone when bin probability mass >> 1/K" and that randomized PIT is needed for a strict uniformity
test. The Tribunal's under-dispersion finding (REFRAME W2: "LogLoss 11-25 under-dispersion") and the
plan's σ-rekey rationale lean on this PIT/score signal. With ~1–3°F bins and concentrated mass, a
U-shaped PIT is the *expected* artifact of discretization even for a perfectly-dispersed forecast —
so "PIT is U-shaped → ensemble under-dispersed → add/re-key σ" is not a clean inference.

**Fix.** Use randomized PIT `u ~ Uniform(F(Y−1), F(Y))` (the code already names this) for any
dispersion verdict, and corroborate with a proper-scoring decomposition (reliability vs resolution)
rather than PIT shape alone. Re-validate the under-dispersion premise before spending the σ-rekey
budget; it may be partly an artifact.

---

## SEV-3 findings (weaknesses / get-right-before-claiming-equivalence)

### S8. Analytic-p_raw equivalence has unit/rounding subtleties beyond DRAFT2 §3d
**Evidence.** The MC path rounds in display space: `measured = settlement_semantics.round_values(noised)`
then bins (`ensemble_signal.py:256-258`), with `effective_sigma = hypot(instrument_sigma, residual_sd)`
in **native member unit** (`:252`). DRAFT2 §3d says convert μ_m and σ to °F and evaluate Φ at the
preimage [a−0.5, b+0.5). Correct for °F-settled integer cities — but (a) the **instrument sigma**
(`sigma_instrument_for_city`) must be converted too, not just the residual σ; (b) °C-settled cities
have a different rounding lattice (the preimage half-width is 0.5°C, and °C→native may not be
identity); (c) `round_values` may not be plain round-half-up for every market
(`settlement_rounding_policy` varies) — the analytic Φ-preimage must be derived per policy. The
plan's EQUIVALENCE gate (analytic vs MC |Δ|≈0 under *every* rounding policy, §2a/§5) is the right
guard; this note just flags that "≈0" will fail first on °C cities and on the instrument-σ
conversion, and those are correctness bugs to fix, not tolerances to loosen.

**Fix.** Derive the preimage in the exact space where `round_values` operates, per
settlement_rounding_policy; convert *both* sigma components; add °C-settled and non-round-half-up
cities as mandatory EQUIVALENCE fixtures, not just °F examples.

### S9. `available_at` string-comparison freshness can desync ledger from inference (re-confirm)
**Evidence.** C-doc O2: ledger keeps freshest by `str(av) > str(prev["available_at"])`
(build_ens_residual_evidence.py:151-155) — lexical compare of timestamp strings. Naive vs
+00:00-suffixed strings sort incorrectly, so the ledger row can differ from the inference-elected
snapshot (`executable_forecast_reader.py:1231` ranks by epoch). Statistically this means the
evidence the bias is fit on is not guaranteed to be the evidence served — a silent train/serve skew
that no amount of gate rigor catches.

**Fix.** Parse to tz-aware datetime before comparison in both paths; assert ISO-8601 UTC on ingest.

### S10. `robust_mean` trim interacts with tiny n to silently change the estimand
**Evidence.** `robust_mean(trim=0.1)` (`ens_bias_model.py:172-185`) drops `k=int(n*trim)` per tail;
for n<10 k=0 (plain mean), for n in [10,19] k=1. So within the thin live buckets, the estimator
silently switches between "mean" and "10%-trimmed mean" as n crosses 10 — two different estimands —
right in the n=12-18 regime the operator flagged. Defensible, but undocumented as an estimand shift
and not reflected in the posterior SD (which still uses `statistics.variance`, not a trimmed-var).

**Fix.** Either fix the estimand (always-trim or never-trim within the gated regime) or compute the
SD consistent with the trimmed location (trimmed variance / bootstrap SE), so the Kelly haircut SD
matches the point estimate's actual sampling distribution.

---

## What is statistically SOUND (acknowledge, do not re-litigate)
- The raw-identity DEFAULT with "correction is never an entitlement" (REFRAME §3, gate §2b) is the
  right Bayesian-decision posture for a thin-data regime.
- EQUIVALENCE mode (refactor must not move probabilities) is well-founded and the |Δ|≤2e-4
  cross-scorer precedent (DRAFT2 §2a) is a real, usable bar.
- Strictly-proper scoring rules are correctly implemented (scoring.py; audit_refit _brier/_logloss/
  _rps dist functions are textbook-correct).
- The Platt block-bootstrap *by decision_group* (platt.py:191-216) is the right idea for parameter
  CIs — it just (i) runs with n_bootstrap=0 in the audit harness and (ii) still needs date-blocking
  + day-level resampling to be valid here.
- DRAFT2 §5's honesty that improvement-mode "cannot certify makes-money pre-launch, only not-worse
  on available data" is correct and should be elevated to a hard gate label, not buried in prose.

## Bottom line for the operator
Build the spine. But the four SEV-1s mean the *statistical gate* that decides whether any
correction serves is, at HEAD, absent (S2), uncontrolled for multiplicity (S1), anticonservative
under autocorrelation (S3), and computed on a leaky fold engine (S4). Until those four are fixed
AND coverage-validated by simulation at n=12-18, the only statistically defensible served model is
raw identity — which is fortunately the plan's own default. The danger is shipping the gate in its
"~20 LOC" form, where it will emit confident LCB>0 adopts that are noise. Sequence: fix S4 (date-
blocking) → build S2 (real block-bootstrap LCB, S3) → wrap S1 (FDR family) → simplify S5 (single
primary score) → only then enable IMPROVEMENT-mode adoption.
