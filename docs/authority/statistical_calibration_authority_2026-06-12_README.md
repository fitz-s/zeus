# Statistical calibration authority (2026-06-12)

Source: clean-room math consult REQ-20260612-174119 (zero Zeus context by operator
directive — derivations from first principles + literature only). Full text:
statistical_calibration_authority_2026-06-12.txt. Advisory until each step's
walk-forward gate passes locally; the gates themselves are specified in the text.

## Proven results adopted as law

1. k_wrong² = k_true² + (δ/σ)² — variance-only spread fit ABSORBS unmodeled center
   bias. Our k=1.5833 ⇒ RMS unmodeled bias ≈ 1.22σ if true scale ≈1. The current
   state/sigma_scale_fit.json artifact is certified-contaminated; superseded by Step 1.
2. (b_loc, k_cluster) jointly identifiable from interval-censored categorical
   likelihood given ≥2 finite bin boundaries (proof in text). Estimator: MLE/MAP with
   empirical-Bayes shrinkage S_ℓ = τ²/(τ²+s_ℓ²), (b0, τ) by marginal likelihood.
3. Uniform mixture w CONDEMNED: (1−w)q + w/K pulls every above-uniform bin DOWN —
   provably worsens sell-the-mode. Replacement: distance-tied Dirichlet calibration
   q̃ ∝ q^γ · exp(β_d), penalized log-loss, second-difference smoothness on β.
4. α* = clip[(S_m − S_qm)/(S_q + S_m − 2S_qm)] (observable Brier form) or 1-D convex
   log-loss stacking; CLASS-CONDITIONAL by bin-distance-from-mode. One-sided
   cap = inadmissible as estimator; class-conditional blend dominates.
5. Wilson-on-member-votes = WRONG ESTIMAND (members are correlated model draws, not
   Bernoulli trials of the event). Replacement: posterior over calibrated probability
   (Laplace/Hessian or day-block bootstrap); N_eff = N / (1+(N−1)ρ) family form for
   ensemble-input uncertainty only.
6. Credibility level DERIVED, never chosen: θ_B = C + λ(θ̄ − C); γ* = P(θ ≤ θ_B | D).
   Fractional Kelly λ = argmax_λ Σ log(1 + λ f_K X) on candidate-trade history.
7. Edge threshold after fees: L − C > εC/(1−ε) (θ_min=0 case).
8. Staleness: hard cutoffs → Kalman forgetting w(a)=λ^a, λ = 2/(2+q+√(q²+4q)), q=Q/R
   fitted. Licensing: n_eff from CI-half-width target h* = e_min/L_θη, never n≥30.

## Migration order (each step gated, in text §Migration order)

0. NO-trade necessary condition under coverage error (replay the 7 modal-NO losses
   as deterministic audit).
1. Joint (b,k) interval likelihood; DELETE variance-only k fit. Gate: prequential
   paired log-loss lower-bound positive + modal-class reliability shrinks.
2. Remove uniform w; distance-tied Dirichlet calibration. Gate: ΔLL > complexity.
3. Market fee/spread interval + fitted favorite-longshot power transform γ.
4. Class-conditional α_c blend (near-modal α likely → market).
5. Posterior bounds + growth-fitted λ.
6. Constants audit per the table (σ floor, z, radius, staleness, n-floors).
