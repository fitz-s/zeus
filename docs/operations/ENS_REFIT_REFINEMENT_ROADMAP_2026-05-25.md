# ENS Refit — Post-Refit Refinement Roadmap (Math-First, Gated)

Created: 2026-05-25
Last reused or audited: 2026-05-25
Authority basis: operator research directive 2026-05-25 ("降格报告为启发向量，从数学目标函数出发"); supersedes the A–F framing in `ENS_REFINEMENT_ROUTES_RESEARCH_2026-05-25.md` (kept as context, not ground truth).

> **Reports are heuristic vectors, NOT ground truth.** The only acceptance
> evidence is blocked-OOS proper scores. Every rule below is expressed in
> probability / uncertainty / transport / proper-score / risk terms — never in
> city names. Zeus-specific (targets bin probabilities, Platt, edge, Kelly,
> live no-bet) but never SF-specific.

## 0. Established baseline (already on main: #334 / #336)

Universal predictive-error layer `src/calibration/ens_error_model.py`:
`T_draw = E_m + λ·b + η + ε_instrument` — b=posterior bias, λ=SNR confidence
gate, η=forecast/station residual, ε=instrument noise. `PredictiveErrorModel`
records `bias_c, residual_sd_c, heterogeneity_var_c2, correction_strength,
effective_bias_c, total_residual_sd_c`. `p_raw_vector_from_maxes` accepts
`extra_member_sigma` (quadrature with instrument sigma); `extra_member_sigma=0.0`
⇒ byte-identical legacy MC path (train/serve share one generator).

Blocked-OOS model-layer result (48 cities / 216 held-out): `full_transport`
Brier 0.828 / LogLoss 2.155 ≫ raw 1.065 / 4.834, also beats F50-only
1.053 / 6.027. ⇒ bias-only is insufficient; location+scale+gate+transport shape
is correct.

## 1. Zeus objective (3 layers)

1. **Forecast probability**: calibrated bin vector p; proper scores
   LogLoss = −log p_Y, Brier = Σ(p_k−1{Y=k})². LogLoss punishes "truth-bin
   mass = 0" (the raw/bias-only failure).
2. **Calibration**: P(Y=k | p_k≈r) ≈ r — reliability / ECE / PIT uniformity.
3. **Trading**: max E[log(1+fR)]. Binary YES ≈ Kelly f*=(p−q)/(1−q), but p has
   posterior uncertainty ⇒ robust Kelly on a lower credible bound:
   p_LCB = E[p] − zσ_p ; f = c·max(0, (p_LCB − q − cost)/(1−q)).
   **Probability and risk models must share uncertainty — not a point estimate.**

## 2. Domain-identity lock during refit (HARD)

```
full_transport_v1 p_raw → full_transport_v1 calibration_pairs
→ full_transport_v1 Platt → live serving uses identical p_raw generator
```
NO model change mid-refit (would poison freshly-generated calibration_pairs'
input domain). All Routes below are POST-refit.

## 3. Refinement routes (ROI-ranked, each gated)

| Rank | Route | ROI | Complexity | Trigger |
|---|---|---|---|---|
| 1 | Finish 10k full_transport refit + Platt | highest | in progress | mandatory |
| 2 | Ordered-bin CDF / RPS calibration | high | low-med | Platt ECE/RPS poor |
| 3 | Robust Kelly / edge-uncertainty gate | high | med | after p_cal passes, pre-live |
| 4 | Conformal / no-bet overlay | high | low-med | high-uncertainty cohorts |
| 5 | Spread-dependent residual scale (EMOS) | med-high | low-med | residual vs ENS-spread correlated |
| 6 | Day-specific Δ(F25−F50) transport β | med-high | med | coastal/high-gradient residual still poor |
| 7 | Prequential drift monitors | med-high | med | mandatory after live |
| 8 | Market-prior fusion (noisy obs) | med | high | after forecast p_cal stable |
| 9 | Dirichlet/multinomial calibration | med | high | corrected Platt still fails |
| 10 | Spatial-gradient / k-nearest features | conditional-high | med-high | Δ transport insufficient + OOS proof |

### Route 2 — Ordered-bin CDF calibration (top non-mandatory candidate)
Bins are ordinal; independent per-bin Platt discards order. Calibrate on the CDF
threshold logits with monotonicity:
`logit F'(t_j) = a_j·logit F(t_j) + b_j` s.t. F'(t_1)≤…≤F'(t_K);
`p'_j = F'(t_j) − F'(t_{j−1})`. Add **RPS = Σ_j (F(j) − 1{Y≤j})²** as the
ordinal proper score. Applies HIGH/LOW, °F/°C; shoulder bins via CDF endpoints.

### Route 3 — Robust Kelly / posterior edge uncertainty
Use lower credible probability p_α = Q_α(p), not E[p]:
`f = c·max(0, (p_α − q − cost)/(1−q))`. Same-city-date bins are correlated ⇒
exposure budget Σ_i|f_i| ≤ B_{city,date}, ranked by risk-adjusted edge.
High-uncertainty buckets (coastal / model-disagreement) must shrink size.

### Route 4 — Conformal / no-bet overlay
No-bet is a valid output. Nonconformity s_i = −log p_{Y_i}; if prediction set too
wide or traded bin outside conformal-valid set ⇒ no-bet / reduce Kelly. Pure risk
layer, does NOT touch p_raw/p_cal. Acceptance: traded-subset LogLoss/Brier/realized
edge improves; no-bet cohort coverage explains avoided losses; volume drop acceptable.

### Route 5 — Spread-dependent residual scale (EMOS)
Per-day ENS spread s_i = std(E_{i,m}). σ_i² = a_g + b_g·s_i² + h_g
(full_transport = b_g≡0). Only one extra coeff on an already-computed quantity.
Test after refit IF residual vs spread correlation exists.

### Route 6 — Day-specific Δ transport β
Current transport: b_25 = b_50 + E[Δ], Δ=F25−F50 (stable mean transport).
Extend: b_{25,i} = b_50 + μ_Δ + β(Δ_i − μ_Δ), strong shrinkage β~N(0,τ²).
β=0 keeps day-specific 0.25 signal; β=1 ≈ F50 correction. Δ is a legitimate
lineage feature — does NOT change target identity (no cell remap). Test only
after full_transport baseline established.

### Routes 7–10
7 prequential: L_t=−log p_{Y_t}; CUSUM S_t=max(0,S_{t-1}+L_t−E[L]); breach ⇒
downgrade cohort / widen residual / cut Kelly / retrain. Runtime health, not
complexity. 8 market fusion: logit p_post = α·logit p_model + (1−α)·logit
p_market + b — only with complete family + devigged YES + liquidity + freshness +
OOS proof; deferred so calibration ≠ microstructure confound. 9/10 only after
corrected Platt is proven insufficient (chain is stat-intact; defect was
product/input-domain, not Platt math).

## 4. Post-refit audit battery (run IMMEDIATELY on refit completion)

### 4.1 p_raw audit — models {raw, F50_only, full_transport(10k)}
Metrics: Brier, LogLoss, **RPS**, P(actual), **PIT**, ECE.
Cohort splits: global · city-cluster · coastal/inland · US-°F vs °C · HIGH vs LOW
· lead bucket · cycle.

### 4.2 p_cal audit
old Platt-on-raw · old Platt-on-full_transport (diagnostic only) · new Platt-on-full_transport.
Acceptance: new full_transport Platt **improves or preserves** Brier/LogLoss/ECE;
no city-family catastrophic regression; tail reliability acceptable OR no-bet catches it.

### 4.3 decision audit
edge distribution · Kelly-size distribution · #candidates · false-positive edge
rate · paper-replay PnL/regret.

### PIT reading rule
U-shaped ⇒ underdispersed (increase residual scale, Route 5); skewed ⇒ residual
bias (fix bias/transport, Route 6); both ⇒ location+scale.

## 5. Execution sequence (bottom-line)

1. Let 10k MC full_transport refit finish. 2. Eval p_raw & p_cal on
Brier/LogLoss/RPS/PIT/ECE. 3. p_cal passes ⇒ paper replay + robust-Kelly
uncertainty gates (Route 3). 4. p_cal reliability fails ⇒ ordered-CDF calibration
(Route 2) BEFORE complex families. 5. residual scale fails ⇒ spread-dependent
scale (Route 5). 6. coastal residual remains ⇒ day-specific Δ transport (Route 6)
BEFORE k-nearest. 7. high-confidence wrong trades remain ⇒ conformal/no-bet (Route 4).

## 6. Do-NOT list (during/just-after refit)

- No manual SF/coastal cell remap.
- No k-nearest gradient features before Δ-transport baseline evaluated.
- No Platt→complex-calibration switch before corrected Platt tested.
- No market-prior fusion before forecast p_cal stabilizes.
- No mean-residual as success criterion.
- No single city/date judgement.
- No report-as-truth; blocked-OOS proper scores only.
