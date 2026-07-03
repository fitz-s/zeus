# Consult-2 double-review: Fable 5 independent same-prompt answer (2026-06-13)

Status: ARCHIVED_REFERENCE — cross-validation evidence behind the calibration authority; not standalone live law.

Cross-validation status: CONVERGENT with the GPT consult-2 answer
(`consult2_era_contamination_fdr_maker_2026-06-13_raw.txt`) on every load-bearing
result. Refinements folded into `statistical_calibration_addendum_2026-06-13.md`
section D.

## Convergence table (the correctness check)

| Topic | GPT consult-2 | Fable 5 | Verdict |
|---|---|---|---|
| BH on {0,1} p-values | vacuous BLOCKER | vacuous BLOCKER ("literal no-op") | CONVERGENT |
| PRDS for exclusive bins | counterexample T1=Z, T2=−Z | negative association of multinomial (Joag-Dev & Proschan 1983) | CONVERGENT (two proofs) |
| BY penalty at 2K=24 | H24≈3.776, q→0.026 | identical | CONVERGENT |
| FDR vs log-wealth | wrong objective | dominance proof: any gate zeroing f*>0 strictly reduces E[log W] | CONVERGENT |
| Winner's curse | E[max] ≈ s√(2 ln N_eff) | identical, N_eff = N/(1+(N−1)ρ̄) | CONVERGENT |
| Edge fix | EB shrinkage, correlation-aware | EB normal-normal + Tweedie upgrade (N≥200) | CONVERGENT + refinement |
| Era fitting | EB partial pooling + LRT decision rule | EB partial pooling; LRT = DIAGNOSTIC ONLY (pretest estimator has unbounded relative risk near null) | CONVERGENT + refinement (always partial-pool) |
| Boundary variance test | Self–Liang / bootstrap | Self–Liang ½χ²₀+½χ²₁ mixture | CONVERGENT |
| Decay step bias | −Δλ^{n1} | exact share s=λ^{n1}(1−λ^{n0})/(1−λ^{n1+n0}); λ=0.999, n1=400, n0=4000 → 66% contamination | CONVERGENT (Fable exact form) |
| QUARANTINED | exclude OR confusion-matrix measurement error; weights biased | exclude OR **CAR interval-widening** (Heitjan–Rubin); weights biased for ANY w>0 (estimating-equation proof) | CONVERGENT + refinement (CAR composes with censored likelihood) |
| Stage-1→2 inflation | ṽ_m = v_m + C_ε,mm; δ=−wᵀε; RMS √(wᵀC_εw) | identical; adds: common settlement-noise component ⇒ offset does NOT average away in M | CONVERGENT |
| Joint estimator | two-way provider×location×era GLS | identical; normalization Σ_m b_m = 0 (provider effects = pure contrasts) | CONVERGENT |
| Identifiability | provider–location graph connected | identical (union-find check) | CONVERGENT |
| Ledoit–Wolf | identity target hides common drift → factor/constant-corr target | equal-weight pull CONSERVATIVE here; autocorrelation ⇒ LW UNDER-shrinks ⇒ floor ρ≥0.5, cap w∈[0,2/M], drop worst provider explicitly if MSE ratio ≥4 | PARTIAL DIVERGENCE → resolved as floor+diagnostic (addendum D4) |
| Maker/taker | V_M = e_r0·λf/(λf+λe)·(1−e^{−(λf+λe)D}); max-min TAKE | V_rest = e₀G+δF; REST iff δ > e₀(1−G)/F; max-min TAKE (same algebra) | CONVERGENT exact |
| Fill estimator | Gamma bucket prior + own-order update | Gamma–Poisson, tape trade-through prior with c∈(0.3,0.7) queue discount; Lomax P(fill≤H)=1−(β/(β+H))^α | CONVERGENT + concrete prior |
| Adverse selection | markout gates optimistic variant | markdown A(δ_d); pessimistic prior A=0.5δ until ≥20 fills/bucket | CONVERGENT + concrete default |
| Priority order | Q1 > Q3 > Q2 > Q4 | Q1 > Q3 > Q2 > Q4 | IDENTICAL |

## Fable-distinct results (ADOPTED into the addendum, section D)

1. **Always-partial-pool law**: never use the era LRT as a pool/no-pool switch
   (pretest-estimator risk is unbounded near the null boundary); fit EB partial
   pooling unconditionally — Σ̂_era→0 recovers full pooling automatically, large
   era effects recover near-separate fits; the newest era converges to its own
   MLE as n grows. LRT/score test is REPORTED as a diagnostic only.
2. **CAR quarantine treatment**: when the per-row settlement ambiguity set A_i
   (union of bins consistent with competing sources) is recoverable and
   contiguous, replace the censoring interval with [min A_i lower, max A_i upper]
   — coarsened-at-random likelihood (Heitjan & Rubin 1991), unbiased and strictly
   more efficient than exclusion. Exclusion remains the fallback when A_i is
   unrecoverable or the ambiguity is directional (diagnostic: compare clean vs
   quarantined covariate distributions + source-disagreement direction).
3. **Tweedie's formula** for the candidate-edge cross-section when daily N≥200:
   E[e|ê] = ê + s²·(d/dê) log f̂(ê) — nonparametric selection-bias correction from
   the marginal density of all candidate edges (Efron 2011).
4. **e-BH on betting e-values** (Wang & Ramdas, JRSS-B 2022) = the ONLY
   admissible FDR-style gate if risk policy ever demands one (valid under
   arbitrary dependence, aligned with log-wealth). Never BH/BY on p-values.
5. **Winner's-curse slope diagnostic**: regress realized PnL/contract on shrunk
   edge; slope ≪ 1 ⇒ still under-shrinking; intercept ≠ 0 ⇒ residual center bias.
6. **EWMA second-order failure**: a local-level filter absorbs an era step by
   inflating q̂, over-discounting the smooth stretches too — decay-only loses
   efficiency everywhere to accommodate a jump that one dummy models exactly.
7. **License constants** (defensible defaults, tunable, NOT derived optima):
   EB posterior sd(b̃_E) < 0.25·median(σᵢ) and sd(k̃_E) < 0.15·k̃_E to trade on
   era-specific parameters; fusion weights move away from 1/M only with ≥10·M
   complete days AND bootstrap CI of each w_m excluding 1/M; maker switch only
   when P[δ_eff > e₀(1−G)/F] > p*=0.6 under MC over the (λ_e, λ_f) posterior;
   λ_e licensed when episode-bootstrap 80% CI ratio < 3; A(δ_d)=0.5·δ_d
   pessimistic adverse-selection prior until ≥20 own fills per depth bucket.
