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
| 1b | Sizing-governance lock | DONE b0c342208 — config/risk_policy.yaml (content-addressed, policy_version+sha256 logged at boot) + assert_risk_policy_artifact as Guard 5 in _run_boot_guards; 4 levers pinned (kelly_multiplier 0.125, max_correlated_pct 0.25, max_portfolio_heat_pct 0.5, max_single_position_pct 0.1), 3 deliberately-not-pinned documented; direction law (lower-only overrides) enforced structurally; 36/36 tests. |
| 2 | Bug A repair (multi-tranche cert aggregation) | DONE 558229342 — 74 tests pass; live shadow validation: 111/174 Aug UNATTRIBUTABLE now resolve (all size-weighted, 0 fallback), remaining 63 = all-tranche failures (30 single-tranche Bug-B cohort + 33 shared-upstream), fail-closed intact. Fill-dedup handled in panels (P3/P4). |
| 3 | Decision certificate: add decision-time executable side price p₀ + candidate-set provenance | TODO — scouting done: certs already carry q_live/q_source/global_limit_price/global_jit_book_snapshot_id; gap = explicit top-of-book side price + candidate set beyond sha256 |
| 4 | Four non-circular panels (forecast/selection/execution/lifecycle) | DONE f77ec1c91 — scripts/scoreboard_panels.py, 19/19 tests, registered (script_manifest.yaml + AGENTS.md + writer-lock allowlist); live smoke reproduces P1 verdict (market beats q all buckets) |
| 5 | Pipeline repairs | 5a bootstrap stall alert DONE d1aeeeb52. 5b riskguard stuck-alert DONE a7da84b26 (+ test rot repair 583cdb52d). 5b Type-C preemption churn DONE (source inside 9df4569f0 via worktree sweep — md5-verified; tests 20080e298, 15/15): bounded revalidation N=3/300s env-overridable, day0/fill wakes hard-veto (never coalesced), generation = adapter-factory lifetime (DRAIN automatic), Type-A five gates untouched with structural non-regression test. Design doc 24756dda1. Open: INV-47 registry entry for the new gate site (governance decision); Type-A first slice (CAS-fence ×205 orthogonal to Tier-0) deferred — Tier-0 taker-only already disables gates 2+5-depth. |
| 6 | Tier-0 live research mode | DONE 0653ed8bb + 9df4569f0 — admission gate (price<0.25, taker-only typed rejection, one-per-(city,target_date) cluster incl. same-cycle race closure), flat stake = venue min_order_size shares at the single sizing chokepoint, aggregate 2% open-cost ceiling live, drawdown-kill live-wired (seed-once start-equity in control_overrides_history `tier0:start_equity:epoch:<N>`, epoch bump = new episode via reviewed commit; realized P&L via canonical R0-a reducer, never forbidden columns; breach → sanctioned auto-pause). Flag `feature_flags.TIER0_RESEARCH_MODE` default OFF. 80 tests across the two slices. **Activation gate: Item 3 (true p0 certs) + release checklist below.** |
| 7pre | Selection-lift preregistration | DONE — tier0_selection_lift_preregistration_2026-08-24.md FROZEN before first Tier-0 settlement: within-city-date price-matched contrast, permutation inference, city-date + date-block clustering (larger governs), single evaluation at 100 clusters, decision rule incl. selector-retirement branch. |
| 7 | Ordinal-selection discriminator (prospective; historical candidate sets not persisted — hash only) | TODO (depends 3) |
| 8 | Convex hold-to-settle | PARTIAL 274054532 — auction SELL candidacy blocked for positions with avg entry price <0.25 (blocks GLOBAL_CAPITAL_OPTIMAL_SELL AND auction-routed q-scored sells; hard-fact zero-support direct sells, RED force, emergencies untouched; avg_price via EffectiveExposure fill/chain authority, lawful). Remaining: scale-in deletion for Tier 0 (moot while entries paused; enforce at Tier-0 release), historical exit audit (done in investigation, exit-review agent). |
| 9 | Market-anchored calibrator | DONE 5f969c590 — module + walk-forward harness + report script, 21/21 tests, all registries. **LIVE RESULT (537 walk-forward predictions, p0-proxy caveat): β converges ~0.10-0.12 (λ=10 heavy shrinkage). Paired log-loss ALL: p0 0.5481, raw q 0.8988, calibrated r̂ 0.5580 — even optimally-shrunk q does NOT beat market price out of sample (marginally worse overall; better only in June and the <0.15 agreement bucket). Gate A verdict as of today: q_cal ≈ parity-at-best → per the two-gate law this licenses NOTHING for capital; live posture stays market-anchored, q has no cardinal authority. Raw q's 0.90 vs 0.55 confirms the original indictment.** |
| 10 | Two-gate promotion evaluator | DONE c6d520be4 — promotion_gates module + CLI + anti-peeking ledger + tier1 formula (pure, unwired), 30/30 tests, registered. **LIVE VERDICT TODAY: Gate A = FAIL_CATASTROPHIC_DEGRADATION (3 of 4 |q−p| buckets breach the 0.05 upper-bound margin; pooled upper 0.044 > δ_A 0.01; date-clustering governs, se 0.021) — q_cal licenses NOTHING; Gate B = no tier0 sample yet. The machinery produces honest refusals, as designed.** |

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

## Tier-0 activation checklist (execute in order; any failure = stay paused)

1. Branch landed on `live` via the sanctioned path (PR or cherry-pick — operator decides), daemon restarted via deploy_live.py (mesh-coherent), boot log shows: risk_policy artifact policy_version + sha256, Guard 5 pass, TIER0_RESEARCH_MODE state.
2. Item 3 verified live: a dry-cycle certificate carries decision_p0 + decision_p0_source; tier0_candidate_set_provenance receives rows on a completed auction (check after first post-restart auction).
3. `feature_flags.TIER0_RESEARCH_MODE: true` set in live config/settings.json (operator edit — untracked file, verify against risk_policy ceilings at boot).
4. Resume entries: sanctioned control-plane `resume_entries` command (clears `control_plane:global:entries_paused`). The Tier-0 admission gate is now the only entry path.
5. First-hour verification: every submitted ENTRY has price<0.25, TAKER_LIMIT, size == venue min_order_size, one per (city,target_date); scoreboard_panels P3 shows the fills; any violation → re-pause immediately.
6. Standing cadence: run scoreboard_panels + calibrator_walkforward_report + promotion_gates_report --dry-run weekly; selection_lift_report stays locked until 100 clusters (~8-10 weeks); NO formal Gate-B evaluation before the preregistered stopping count.

## Rollback

Global entry pause is the universal rollback. No path from Tier 0 back to q-Kelly. Tier-0 drawdown >10% → auto-pause (live-wired). Any cap/certificate/reconciliation breach → full pause.
