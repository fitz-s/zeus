# Reversal Plan — Tier-0 research mode + measurement repair (2026-08-24)

Goal: durable positive capital growth via evidence-gated amplification. Derived from the 2026-08-24 full-book investigation (evidence: session scratchpad `investigation_evidence.md`) collided with a GPT-5.6 Pro consult (REQ-20260824-034739-ed68c1, verdict verified). Operator directive: implement completely.

## Standing verdicts (evidence, not opinion)

- Cardinal q is disqualified from sizing: paired log-loss loses to decision-time price in every month and every |q−p| bucket; deficit grows with disagreement (+0.033→+0.518). Confidence 0.96.
- Ordinal selection value UNRESOLVED: cheap-band (<0.25) frequency edge +1.9pp, clustered t≈0.73, CI [−3.2,+7.0]pp. P(real) ≈ 0.35.
- Rich resting makers toxic (−10.3pp, n=51) but explain only ~22% of rich deficit; rich book disabled entirely in Tier 0.
- Measurement organ defective: grader Bug A (multi-tranche purity gate) blinds 56% of Aug non-randomly; fills table double-counts (0x placeholder + UUID); skill categories are circular (own-q yardstick).
- Runtime absence 51% of Aug: pause 117h / riskguard-DATA_DEGRADED 98h / auction-collapse 107h (3 mechanisms: preflight staleness storm, bootstrap Event starvation, preemption churn) / quiet 58h.
- Aug 1-3 −$642: kelly_multiplier 0.25 in untracked settings.json + dampeners deleted 7/24 + peak bankroll. q unchanged. Fixed to 1/32 on 8/2 (now 1/8 + boot guard).
- Aug 8+ cheap-contrarian tilt: admission allowlist deleted + rerank-past-unqualified-winner (both 8/07), deployed with 245 commits on 8/8.
- 84% of convex early exits overrode the position's own belief-consuming exit law via GLOBAL_CAPITAL_OPTIMAL_SELL (capital-velocity objective, structurally anti-convexity).

## Status ledger

| # | Item | Status |
|---|---|---|
| 1 | Entry pause (runtime) | DONE 2026-08-24T09:22:49Z — `control_plane:global:entries_paused` reason `reversal_plan_2026-08-24_entry_pause_until_tier0_release`, no effective_until. Zero resting entry orders confirmed. |
| 1b | Sizing-governance lock (tracked policy artifact; runtime may lower risk, never raise) | TODO |
| 2 | Canonical economic-fill read model + Bug A repair + retire skill labels | TODO |
| 3 | Decision certificate: add decision-time executable side price p₀ + candidate-set provenance | TODO |
| 4 | Four non-circular panels (forecast/selection/execution/lifecycle) | TODO |
| 5 | Pipeline repairs: bootstrap timeout+alert, generation coherence, preemption bounds, riskguard age alerts | TODO |
| 6 | Tier-0 live research mode | **BLOCKED on operator: venue min order $1.00 > 20bp×$268=$0.54.** Options: (a) stay paused until bankroll ≥$500, (b) accept ~37bp/claim, (c) fund. |
| 7 | Ordinal-selection discriminator (prospective; historical candidate sets not persisted — hash only) | TODO (depends 3) |
| 8 | Delete scale-in for Tier 0; hold-to-settle cheap; audit historical exits | TODO |
| 9 | Market-anchored walk-forward calibrator logit(r̂)=logit(p₀)+α_lead+β·clip(logit q−logit p₀); #451 sigma as predeclared challenger only | TODO |
| 10 | Two gates (A: probability-use = non-inferiority to price; B: capital-use = positive LCB vs fill after execution) + single bounded Tier 1 | TODO (depends 4,7,9) |

## Key consult corrections adopted

- No 32-cell capital ladder (small-n multiple-testing machine). Two gates + one bounded Tier 1: f = min(25bp, 1/4·max[0,(r_L−p_fill)/(1−p_fill)]), aggregate unsettled ≤3%.
- Parity with market NEVER unlocks Kelly (parity = zero edge = zero Kelly).
- Flat micro is a research expense, not growth-optimal; diversification unit = city-date cluster (one entry, one risk budget per city-date).
- No per-bucket isotonic; no per-city params (city = robustness veto); lead gets 3 regularized intercepts.
- High-|q−p| stays ELIGIBLE at micro-risk (diagnostic value; fixed risk already removed the magnitude channel).
- Absence = learning loss, not proven EV loss; command volume must never become a success metric.
- Two-way city/date clustering (weather systems correlate cross-city same-date); use the larger uncertainty.
- Tier-0 caps: 10bp target/city-date, 20bp hard ceiling, 2% aggregate unsettled, 10% research drawdown kill.

## Local verifications of consult's risk assumptions

- V2 (candidate provenance): auction receipts store winner + candidate_evaluations_sha256 + nearest frontier only → historical selection-lift impossible; Item 3 adds prospective provenance; historical marked unknown.
- V3 (venue min): min historical filled entry notional = $1.00 → Tier-0 20bp cap infeasible at $268 bankroll → Item 6 BLOCKED (operator fork above).

## Rollback

Global entry pause is the universal rollback. No path from Tier 0 back to q-Kelly. Tier-0 drawdown >10% → full pause. Any cap/certificate/reconciliation breach → full pause.
