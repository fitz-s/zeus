# ENS Refit ROI Audit — Document Index

Generated: 2026-05-25
Branch: draft/ens-refinement-research-2026-05-25 (PR #340)
Operator brief: full_transport_v1 refit complete 14:12 CDT 2026-05-25.

---

## Status snapshot

| Item | Status | Document |
|---|---|---|
| §4.1 p_raw audit HIGH | DONE | ENS_REFIT_FULLDB_HIGH_2026-05-25.md |
| §4.1 p_raw audit LOW | DONE | ENS_REFIT_FULLDB_LOW_2026-05-25.md |
| Before/after live-onboard report | DONE | ENS_BEFORE_AFTER_LIVE_ONBOARD_2026-05-25.md |
| Route 6 (transport beta, spread-dependent) | DONE — FAIL | ENS_ROUTE6_TRANSPORT_BETA_2026-05-25.md |
| Routes 1-10 roadmap | DONE | ENS_REFIT_REFINEMENT_ROADMAP_2026-05-25.md |
| Refinement routes research | DONE | ENS_REFINEMENT_ROUTES_RESEARCH_2026-05-25.md |
| Audit harness (scripts) | DONE | scripts/audit_refit_proper_scores.py |
| Route 5 script | DONE (fix pending) | scripts/experiment_route5_spread_scale.py |
| Route 6 script | DONE | scripts/experiment_route6_transport_beta.py |
| §4.2 p_cal audit (Platt) | PENDING opus | — timed out at 600s per fold |
| Gated regression re-measure | PENDING opus | — needs live zeus-world.db bias posteriors |
| Route 5 PASS/FAIL result | PENDING opus | — _load_groups bottleneck fix needed |
| §4.3 decision audit | BLOCKED | — trade tables empty in full.db |

---

## Key finding from §4.1 (no further compute needed for this)

**Full_transport_v1 improves globally but regresses on HK HIGH and Miami HIGH.**

HIGH global: Brier −15% (1.0381→0.8838), LogLoss −65% (7.2608→2.5543), ECE −88% (0.0083→0.0010).
LOW global: Brier −15% (1.0218→0.8697), LogLoss −66% (6.5051→2.2147), RPS −28% (1.4237→1.0220).

HK HIGH: Brier +18%, LogLoss +66%, RPS +370% — catastrophic regression.
Miami HIGH: Brier +16%, LogLoss +32%, RPS +97% — regression.
HK LOW and Miami LOW: both IMPROVE strongly (HK LOW LogLoss −22.3).

The regression is HIGH-specific and confined to 2 of 48 cities.

---

## Route 6 verdict (data-constrained)

Route 6 (b₂₅,ᵢ = b₅₀ + μ_Δ + β(Δᵢ − μ_Δ)) is UNTESTABLE on the §4.1 catastrophic cohort:
- HK: 0 paired F25+F50 days (zero overlap in full.db)
- Miami: 1 paired day (marginal)
- Global testable subset (33 groups): Brier +0.136, LogLoss +4.47, RPS +2.44 — all WORSE.
Mean beta ≈ −1.5 indicating overfitting on a 5-calendar-day sample.
Revisit when ≥30 days of paired F25+F50 data accrue.
See: ENS_ROUTE6_TRANSPORT_BETA_2026-05-25.md

---

## §4.2 p_cal audit — timeout explanation

The blocked 5-fold Platt fit on full_transport_v1 HIGH requires fitting logistic regression on
~13.5M training rows per fold × 5 folds. Each fold exceeds the 600s python_repl timeout.
Stored `platt_models_v2` in full.db was populated by the validator run — the opus agent should
query those stored parameters directly to skip re-fitting. The stored coefficients cover the
full grouped-blocked OOS evaluation the validator already ran.

---

## §4.3 decision audit — precondition

Cannot be run on full.db. Required tables (decision_events, execution_fact, opportunity_fact,
probability_trace_fact) are all empty (0 rows). The audit requires:
- Production zeus-world.db: decision_events (columns: edge, target_size_usd, city, range_label)
- Production zeus-forecasts.db: probability_trace_fact
- Precondition: refit calibration migrated to live (PR #64 landed + flag ON + daemon restarted)

---

## Documents in this PR (all in docs/operations/)

1. ENS_REFIT_REFINEMENT_ROADMAP_2026-05-25.md — Routes 1-10 ranked roadmap + §4.2/§4.3 spec
2. ENS_REFIT_FULLDB_HIGH_2026-05-25.md — §4.1 HIGH proper scores (all cohorts + PIT)
3. ENS_REFIT_FULLDB_LOW_2026-05-25.md — §4.1 LOW proper scores (all cohorts + PIT)
4. ENS_REFIT_VALIDATION_2026-05-25_results.md — §4.1 LOW (duplicate, written by validator run)
5. ENS_ROUTE6_TRANSPORT_BETA_2026-05-25.md — Route 6 FAIL verdict
6. ENS_REFINEMENT_ROUTES_RESEARCH_2026-05-25.md — Route 5/6 feasibility research
7. ENS_BEFORE_AFTER_LIVE_ONBOARD_2026-05-25.md — Before/after per-cohort operator report (NEW)
8. ENS_ROI_AUDIT_INDEX_2026-05-25.md — this file (NEW)
9. V1V2_SUFFIX_INVENTORY_2026-05-25.md — schema suffix inventory

## Scripts in this PR

- scripts/audit_refit_proper_scores.py — §4.1/§4.2 proper-score harness
- scripts/experiment_route5_spread_scale.py — Route 5 spread-dependent scale experiment
- scripts/experiment_route6_transport_beta.py — Route 6 transport beta experiment
