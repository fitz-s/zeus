# Independent cross-validation: Fable 5, same clean-room prompt (REQ-20260612-174119)

Status: ARCHIVED_REFERENCE — cross-validation evidence behind the calibration authority; not standalone live law.

Operator ran the identical zero-context prompt through Fable 5 independently.
RECONCILIATION VERDICT (2026-06-12): every load-bearing theorem CONVERGES with the
GPT answer — bias-absorption formula k²=k_true²+(δ/σ)² (identical KL derivation,
both imply δ/σ≈1.21 for our k=1.5833); joint interval-censored (b,k) likelihood
with TWO independent identifiability proofs; uniform-mixture condemnation; Morris
EB shrinkage; class-conditional blend with near-mode α→market; one-sided cap
inadmissible (two independent dominance proofs); fractional Kelly κ*=SNR²/(SNR²+1)
algebraically identical to GPT's λ=1/(1+τ²/σ²); N_eff identical.

Implementation-level divergences (adjudicated by the answers' own walk-forward
gates, not by fiat): tail family (Dirichlet-by-distance vs Student-t — implement
both in Step 2, log-loss decides); pool form (linear+stacking vs log-opinion —
both 1-D fits, same gate); e_min loss convention (adopt GPT's θ_eff derivation,
Fable's ε/(1−ε) as upper bound).

Fable-unique adoptions: σ_min = Δ_bin/(2√3) rounding-implied floor; hard Step-1
gates (k_new < 1.34, modal-q 0.22→0.30-0.34 expected, PIT-uniformity check);
Monte-Carlo verification of the k* formula.

Full Fable response follows verbatim.

---
[Full Fable 5 response archived from operator paste 2026-06-12 — see conversation transcript REQ-20260612-174119. Key formulas restated for grep-ability:]

k* = sqrt(1 + (delta/sigma)^2)   — scale-only MLE bias absorption (KL minimizer)
ell(b,k) = sum_i log[Phi((u_j - mu_i - b_loc)/(sigma_i k)) - Phi((l_j - mu_i - b_loc)/(sigma_i k))]
Identifiability: shift = odd-parity, spread = even-parity perturbations on simplex; Fisher PD for K>=3
EB shrinkage: b_EB = tau^2/(tau^2 + s_l^2) * b_MLE; tau^2 = max(0, mean(b^2) - mean(s^2)) (Morris)
Student-t alternative: cell prob via T_nu CDF; nu fitted jointly (one param replaces w)
Log-opinion pool: p_hat ∝ q_model^alpha * q_market^(1-alpha) (normalized); alpha by 1-D line search
Class-conditional alpha_d by distance-from-mode; n_d >= ~30 winning hits per class
One-sided cap inadmissible: truncated-one-direction estimator dominated by optimal linear blend
FLB: logit(p_true) = a + b*logit(pi_market), fit by logistic regression on (obs, bin) binaries
N_eff = N / (1 + (nbar-1) rho_w + nbar (M-1) rho_b / M)
Derived z*: z* = sqrt(2 c p(1-p) / [q(1-q)/N_eff])  (Kelly-breakeven special case)
Fractional Kelly: kappa* = SNR^2/(SNR^2+1), SNR = edge/sigma_p
e_min = c p(1-p) + eps/(1-eps)
sigma_min = Delta_bin / (2 sqrt(3))  (uniform-on-bin SD; rounding-implied floor)
Near-mode radius: r = k sigma_pred / Delta_bin (curvature-derived, not 1.5 hardcode)
n_min = z*^2 sigma_param^2 / delta_target^2
Decay weighting: w_i = exp(-dt/tau), tau by walk-forward argmax
Step-1 gates: walk-forward logloss -0.05 nats/obs; k_new < 1.34; PIT approx uniform
Step-2 gate: -0.02 nats/obs; nu in [3,50]; modal-q within 0.03 of market modal
Expected modal-q trajectory: 0.22 -> 0.30-0.34 (step1) -> 0.33-0.37 (step2)
MC check: simulate N(mu+delta, sigma^2), fit scale-only MLE, confirm k* formula
