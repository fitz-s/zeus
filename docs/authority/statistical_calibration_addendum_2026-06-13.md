# Statistical calibration addendum — 2026-06-13

Status: AUTHORITY EXTENSION of `statistical_calibration_authority_2026-06-12.txt`.
Sources (three independent derivations, cross-validated by convergence):
1. **Consult-2** (GPT Pro, no-Zeus-context): era-mixture fitting, two-stage
   contamination, correlated-bin multiplicity, maker/taker. Raw answer archived at
   `consult2_era_contamination_fdr_maker_2026-06-13_raw.txt`.
2. **GPT addendum** ("superior frameworks" follow-up, operator-forwarded 2026-06-13).
3. **Fable addendum** (independent same-prompt run, operator-forwarded 2026-06-13).

Convergence is the correctness check (operator method): where two independent
derivations agree, the result is law; where they diverge, a walk-forward bake-off
decides (log-loss AND RPS reported side by side).

---

## A. CONVERGENT RESULTS (law)

### A1. Sizing: Zeus's q_lcb + Kelly IS distributionally-robust Kelly (validated)
Both addenda independently derive DR-Kelly:
- Fable: `f* = max(0, f_Kelly(q_L))` — worst case of the credible interval is the
  boundary nearest no-edge; conservatism comes from interval WIDTH, no tuning knob.
- GPT: KL-ambiguity Donsker–Varadhan dual `sup_λ [ρ + log E_Π exp(−λ g)]/λ`.
The interval form is exactly Zeus's existing `q_lcb + fractional Kelly` shape.
VERDICT: current sizing architecture is theoretically grounded; the remaining work
is the QUALITY of the interval (N_eff, era pooling, selection shrinkage below),
not the sizing rule. The SNR-fitted fractional λ remains as a multiplier only if
walk-forward shows interval-Kelly alone under-shrinks; do not stack both blindly.

### A2. Multiplicity: BH/FDR on the trading path is CONDEMNED (consult-2 BLOCKER)
- Current BH consumes p-values in {0,1} → vacuous (every 0 passes, every 1 fails).
- Mutually exclusive bins violate PRDS (proof: T1=Z, T2=−Z; increasing set
  D={T2>c} has P(D|T1=t) decreasing in t) → ordinary BH invalid even with real
  p-values; BY (×H_m ≈ 3.1 stricter at K=12) is valid but solves the WRONG
  objective: FDR controls E[V/R], not bankroll log growth.
- REPLACEMENT (law): posterior expected-log-utility thresholding
  + correlation-aware EB selection shrinkage (winner's curse correction):
  `ê|e ~ N(e,S)`, `e ~ N(μ1, τ²R)`; trade on shrunk posterior mean m_j, license by
  `P(e_j > e_min | D) ≥ π_min` (0.90 live) and posterior expected log growth > 0.
  Selected-edge sanity: at N=24 candidates the expected max of pure noise is
  ≈1.79σ; at N=288 ≈2.73σ — raw top edges are selection-inflated by construction.
- lfsr (local false-sign rate) = P(e_j ≤ 0 | D) is the posterior replacement for
  the p-value column in receipts.

### A3. Monitoring: anytime-valid calibration alarm (both addenda, same machinery)
Test martingale over the SELECTED class (e.g. NO-against-modal-bin trades):
`M_T(λ) = Π [1 + λ(I_t/p_t − 1)]`, mixture over λ ∈ [0,1]; Ville's inequality
gives anytime-valid evidence. The 7/7 sell-the-mode sequence would have exploded
this martingale — it is the automatic circuit breaker for class-conditional
miscalibration (suspend the class, force recalibration). Wilson intervals are not
valid under optional stopping; anytime-valid CS replaces them where decisions are
sequential.

### A4. Ordered-bin structure must be exploited on the CDF scale (all three sources)
Bins are ordered intervals of one rounded latent variable, not exchangeable
classes. All proposed calibration upgrades operate on the predictive CDF:
- GPT: monotone CDF transport `q̃_k = G(F(b_k)) − G(F(a_k))`, Bernstein basis,
  identity at uniform weights; subsumes uniform mixture, Student-t tails, and the
  distance-Dirichlet from the 06-12 authority (which is hereby DEPRECATED as the
  Step-2 candidate in favor of CDF-scale maps).
- Fable: Beta-transformed linear pool (BLP) `Q̂_j = B_{α,β}(w·Q_model + (1−w)·Q_mkt)`
  — a 3-parameter special case of monotone CDF transport applied to the blend.
- Fable: cumulative link (free cutpoints) as a robustness check only.
UNIFIED LAW: one monotone CDF-scale recalibration layer. Candidates for the
bake-off: {Bernstein-G (R≈6, KL-to-uniform + smoothness penalty, moment
identifiability constraints E[Φ⁻¹(U)]=0, E[Φ⁻¹(U)²]=1), BLP (α,β,w)}.
BLP is the low-parameter production default; Bernstein-G is promoted only if it
wins walk-forward. Two-stage cross-fitting: fit (b,k) with G=id on train folds,
freeze, fit G on held-out folds.

### A5. Era semantics: explicit eras for known breaks, decay for smooth drift
(consult-2 Q1 + GPT state-space addendum, consistent)
- Pipeline/settlement semantic changes = STEP changes. Exponential time-decay
  leaves bias −Δλ^{n_new} (λ=0.99, n=300 → 5% of the step persists; λ=0.995 → 22%).
  Era dummies have zero step bias. → data_version/era is a MODEL TERM, not a filter.
- Default = EB partial pooling on transformed scale φ_e=(b_e, log k_e, …):
  era MLEs + Laplace variances, hierarchical N(φ0, Σ_era), marginal-likelihood Σ;
  posterior `φ̃_e = φ̂0 + Σ(Σ+V_e)⁻¹(φ̂_e − φ̂0)`. Newest era converges to its own
  MLE as n grows (shrinkage is O(1/n)) — partial pooling dominates newest-only at
  n≈300–500 and full pooling unless era effects are absent both statistically
  (LRT/score p≥0.10; boundary variance test via parametric bootstrap, Self–Liang)
  and practically (UCB95 of max traded-bin prob shift < ε_pool ≤ half min edge).
- THIS IS THE DEPLOY UNLOCK for the 6117 historical settlements: old eras enter
  as partially-pooled evidence, never as naively pooled rows.

### A6. QUARANTINED rows: exclude or model, never down-weight
Exactly two unbiased treatments (score-unbiasedness proofs in raw doc):
(A) exclude, valid under A⊥C|X; (B) explicit measurement-error likelihood
`π̃_o = Σ_c M_oc π_c` (confusion matrix) or variance convolution BEFORE binning.
Arbitrary weights 0<w≤1 on quarantined labels reduce but never remove bias.
PRIMARY = exclude; sensitivity model with estimated M only if overlapping
settlement sources exist.

### A7. Provider fusion: joint two-way model, not sequential de-bias-then-weight
Stage-1 bias error inflates measured residual variance (ṽ_m = v_m + C_ε,mm) and
distorts inverse-variance weights; realized center bias has RMS √(wᵀC_ε w) and
positively-correlated provider bias errors do NOT diversify away.
LAW: fit `d_im = μ + β_m + γ_ℓ + η_e (+ (βη)_me) + u_im` JOINTLY (providers fixed
M=5, locations random if sparse, eras per A5), derive GLS weights from the joint
residual covariance `w ∝ Ω⁻¹1`. Identifiability: provider–location bipartite graph
must be connected (b_m vs c_ℓ only identified up to one global offset); pairwise
co-coverage required for off-diagonal Ω, else diagonal+factor structure.
Ledoit–Wolf μI target biases weights toward EQUAL weighting and hides common-mode
era drift — use factor / constant-correlation target AFTER era residualization.

### A8. Maker/taker: max-min default is TAKE for robust positive edge
`V_M = e_r0 · λ_f/(λ_f+λ_e) · (1−e^{−(λ_f+λ_e)D})` vs `V_T = e_x`.
inf over λ_f→0 or λ_e→∞ drives V_M→0 ⇒ for a robust positive-edge candidate,
crossing locks the edge; REST only when conservative bounds (λ_f^L, λ_e^U, e^L)
still give `V_M^L − V_T^L > ε_exec`. D→∞ threshold: rest iff λ_f > λ_e·e_x/s.
Estimators: λ_e from OU/exponential gap decay (per family/bin-distance/era;
report half-life); λ_f from Gamma-prior book-dynamics buckets updated by own-order
exposures (`posterior fill prob = 1 − (b/(b+D))^a`); queue depth r>1 → Erlang.
With 10–50 own orders the maker license will usually FAIL until the book prior is
validated — consistent with REST_DEFAULT being currently inappropriate for
robust-edge candidates >6h out ONLY where LAW 1 (operator) doesn't forbid crossing.
Markout diagnostic gates the optimistic persistent-improvement variant.

### A9. Fitting objective: report BOTH interval log-loss and RPS
Fable's CRPS/RPS case (distance-aware, outlier-robust, EMOS-standard) vs the
06-12 authority's MLE. Divergence → bake-off, not fiat: fit (b,k[,G]) under both
objectives, compare walk-forward on BOTH metrics + modal reliability. RPS is the
natural objective for ordered bins; log-loss stays as the licensing metric for
tails. Expect CRPS-fitted ν (tails) larger / k smaller — under log-loss, outliers
inflate spread (the exact k-absorption pathology of the 06-12 fit).

### A10. James–Stein shrink toward market = sanctioned Step-0 interim
`q̂^JS = (1−λ_JS)q̂ + λ_JS·q_mkt`, λ_JS = (K−2)/(N_eff·χ²(q̂, q_mkt)), with
N_eff = 3.71 (measured 2026-06-13: ρ_w=0.255 over 178 AIFS events, ρ_b=0.140 over
4163 deterministic events; artifact state/member_correlation_fit.json). Admissible,
fit-free, auto-deferring to market when model≈market. This is the fast lever for
the sell-the-mode class while A4/A5 land — and note N_eff=3.71 makes λ_JS large:
the member-vote shape carries ~14× less evidence than N=51 pretended.

---

## B. DIVERGENCES → BAKE-OFFS (walk-forward decides; log both metrics)

| Topic | Candidates | Default until decided |
|---|---|---|
| CDF recalibration | Bernstein-G vs BLP(α,β,w) | BLP (3 params) |
| Model–market blend | arithmetic α-blend (06-12) vs log-opinion pool vs BLP-blend vs JS-toward-market | JS Step-0, then arithmetic |
| Fitting objective | interval log-loss vs RPS | fit both, report both |
| Time dynamics | static era-EB (A5) vs state-space (b_t,k_t) Laplace filter | era-EB (state-space later; easier to destabilize) |
| Tails | Student-t ν vs tail-heavy G | absorbed into the G bake-off |

## C. IMPLEMENTATION ORDER (supersedes 06-12 migration steps 0/2/3/5/8)

1. **C1 (was Q1)** era-aware EB partial pooling in fit_bias_scale.py over the FULL
   settled history (6117 outcomes) — pooled/free-era/EB triple fit + LRT + boundary
   bootstrap + decision rule. THE deploy unlock.
2. **C2 (was Q3)** kill the {0,1}-p-value BH gate on the trading path; posterior
   lfsr + EB selection shrinkage + expected-log-utility license; receipts carry
   lfsr + shrunk edge. e-process monitor (A3) armed on the NO-vs-modal class.
3. **C3** Step-0 James–Stein toward market with measured N_eff (A10) + recompute
   q_lcb width with N_eff=3.71 instead of N=51.
4. **C4 (was Q2)** joint provider×location×era de-bias + GLS weights; connectivity
   audit first (BLOCKER check: graph connected).
5. **C5** CDF-transport bake-off (A4/B) with cross-fitting; RPS+log-loss dual report.
6. **C6 (was Q4)** maker/taker λ_e/λ_f estimators + max-min rule (after C1–C3).

Every fitted artifact keeps the standing licensing law: CI-width on the DECISION
scale (`1.96·max se(π_ij) < ε_edge/2`), never n≥30.

---

## D. DOUBLE-REVIEW REFINEMENTS (Fable 5 independent same-prompt run, 2026-06-13)

Cross-validation: CONVERGENT on every load-bearing result (full convergence
table: `consult2_crossvalidation_fable5_2026-06-13.md`). Both sources rank the
priority IDENTICALLY: Q1 era > Q3 multiplicity > Q2 fusion > Q4 execution.
Adopted refinements (supersede the corresponding lines above where they differ):

- **D1 (refines A5)**: ALWAYS fit EB partial pooling; never use the era test as
  a pool/no-pool switch (pretest-estimator risk unbounded near the null). The
  LRT/score/boundary tests are reported diagnostics. Σ̂_era→0 recovers full
  pooling automatically; newest era converges to its own MLE as n grows.
- **D2 (refines A6)**: preferred QUARANTINED treatment = CAR interval-widening
  (observe ambiguity set A_i → censoring interval [min A_i, max A_i]); unbiased
  and strictly more efficient than exclusion; composes directly with the
  interval-censored likelihood. Exclusion = fallback when A_i unrecoverable or
  directional. Fractional weighting biased for ANY w>0 (proof in both sources).
- **D3 (refines A2)**: Tweedie's formula as the nonparametric shrinkage upgrade
  at daily N≥200 candidates; e-BH on betting e-values is the only admissible
  FDR-style gate if risk policy demands one; winner's-curse slope diagnostic
  (realized PnL/contract vs shrunk edge, slope≈1 target) goes into the
  settlement-graded monitoring set.
- **D4 (resolves the A7 Ledoit–Wolf divergence)**: keep LW but floor ρ≥0.5
  (autocorrelated walk-forward residuals make LW UNDER-shrink), cap weights to
  [0, 2/M], move off 1/M only with ≥10·M complete days + bootstrap CI excluding
  1/M, and drop a provider explicitly when OOS MSE ratio ≥4 rather than trusting
  the covariance. GPT's factor/constant-correlation target remains the bake-off
  challenger after era residualization.
- **D5 (refines A8)**: adverse-selection markdown A(δ_d) with pessimistic prior
  A=0.5·δ_d until ≥20 own fills per depth bucket; maker switch licensed by
  P[δ_eff > e₀(1−G)/F] > 0.6 under MC over the (λ_e, λ_f) posteriors; λ_f prior
  from tape trade-throughs with queue discount c∈(0.3,0.7); Lomax fill
  probability 1−(β/(β+H))^α.
- **D6 (sharpens A5's decay condemnation)**: exact EWMA contamination share
  s = λ^{n1}(1−λ^{n0})/(1−λ^{n1+n0}) — at λ=0.999, n1=400 fresh rows, n0=4000
  old rows the estimate is still 66% dead-regime; AND the local-level filter
  absorbs the jump by inflating q̂, degrading the smooth stretches too.
