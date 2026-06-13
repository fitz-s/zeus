# Plan: C2 (FDR-gate replacement) + C3 (James-Stein + N_eff width) — flag-gated builds

Authority: docs/authority/statistical_calibration_addendum_2026-06-13.md (A2, A10,
C2, C3, D1-D5) + consult-2 raw + Fable double-review (convergent, BLOCKER-rated).
Operator scope: build now, flags default to CURRENT behavior; flips operator-gated.

## C2 — kill the vacuous {0,1}-p-value BH gate (task #60)

Mechanism (consult-2 BLOCKER, double-confirmed): the FDR/BH stage consumes
degenerate p ∈ {0,1} so multiplicity correction is a no-op; exclusive sum-to-one
bins violate PRDS so BH is unproven even with continuous p; FDR is not the
log-wealth objective. Replacement: per-candidate posterior edge samples →
lfsr_j = P(e_j ≤ 0 | D); correlation-aware EB shrinkage over the day's candidate
universe (normal-normal; Tweedie at N≥200); license = shrunk posterior edge
clears e_min with P ≥ π_min AND posterior expected log growth > 0. Receipts gain
lfsr + shrunk-edge columns; fdr_* receipt columns remain for provenance.
Flag: `replacement_selection_eb_shrinkage_enabled` (default false = current BH
path); when false, NEW values still computed and logged shadow-style on receipts.

## C3 — James-Stein toward market + N_eff width correction (task #61)

Mechanism (addendum A10): member-vote confidence widths assume N=51 independent
members; measured ρ_w=0.255 → N_eff=3.71 (state/member_correlation_fit.json) —
~14× evidence inflation in every member-proportion bound. Step-0 lever:
q̂^JS = (1−λ)q̂ + λ·q_mkt, λ = (K−2)/(N_eff·χ²(q̂, q_mkt)) — admissible, fit-free,
defers to market when model≈market. Flag: `replacement_q_james_stein_enabled`
(default false); N_eff width correction flag:
`replacement_neff_width_correction_enabled` (default false). Shadow logging when
off, same pattern as C2.

## Surfaces
- C2: src/engine (reactor/evaluator selection stage), src/state receipts schema
  (additive columns), new src/strategy/selection_shrinkage.py, tests.
- C3: src/strategy/probability_uncertainty.py (N_eff), new
  src/strategy/james_stein_blend.py, materializer call site, tests.

Both: settings.json flag additions (default false), architecture registries,
relationship tests BEFORE implementation per repo law.
