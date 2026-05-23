# Phase 4 — FDR Family-ID `spread_bucket` Extension + Candidate Stubs Production

## v4 §M scope

- Line 1100: "candidate stubs" (deferred from Phase 0)
- Line 1101: "FDR family-ID `spread_bucket` extension — Phase 4 `selection_family.py` work (Critic 2 P8)"

## Why these two surfaces ship together

Candidate stubs (stale_quote_detector, liquidity_provision_with_heartbeat, neg_risk_basket, resolution_window_maker, cross_market_correlation_hedge, weather_event_arbitrage) all consume the spread/depth/freshness fields landed in Phase 0 PR 2+7 + Phase 2 T4 MarketAnalysisVNext. Each candidate strategy generates its own hypothesis family. Without `spread_bucket` discrimination, wide-spread candidates share BH discoveries with tight-spread ones — a tight-spread candidate's edge is "borrowed" against a wide-spread family's discovery threshold, which structurally over-claims significance.

The dossier §2.5 + Critic 2 P8 framing: family scope today is candidate/snapshot; needs to widen to `hypothesis_family_id × source_truth × weather_system × strategy_family × time_window × spread_bucket`.

## FDR + spread_bucket (math basis)

Benjamini-Hochberg (BH) step-up procedure on a family of `m` hypothesis p-values: sort `p_(1) ≤ p_(2) ≤ ... ≤ p_(m)`; the largest `k` such that `p_(k) ≤ k·α/m` is the rejection threshold. Discovery rate FDR ≤ α under independence; weaker control (FDR ≤ α·log(m)) under positive dependence.

The structural problem: when a family pools tight-spread bins (where `fee_adjusted_edge` is small but real) with wide-spread bins (where `fee_adjusted_edge` is dominated by spread noise), the wide-spread bins inflate `m` without contributing genuine signal. Sorting then promotes false discoveries.

`spread_bucket` partition: `Literal["tight", "medium", "wide"]` at thresholds `≤$0.05`, `≤$0.10`, `>$0.10` (matches Phase 0 PR 2+7 `EffectiveKellyContext` spread buckets). Each bucket gets its own BH family; tight-spread candidates compete only against tight-spread peers.

Math spec §14.6 covers FDR; verify §14.6 anchor exists and read prescription verbatim at plan time. Critic 2 P8 is the operator-recognized framing.

## Candidate strategies (production from stubs)

Six files in `src/strategy/candidates/` exist as stubs (verify each: `git show origin/main:src/strategy/candidates/<name>.py | head -30` to see if it's `pass` or actual implementation). Per dossier §13.2 (shadow priority list) + §12 Phase 6 (handoff):

| Candidate | Source-of-edge | Required inputs (all already on main) | Verdict |
|---|---|---|---|
| `stale_quote_detector` | book hash unchanged after info event | `book_hash`, `info_event_time`, `MarketAnalysisVNext.microstructure_metrics` | `SHADOW_FIRST` |
| `liquidity_provision_with_heartbeat` | market-maker batch quoting cadence | book hash transitions, fill_probability (TBD field) | `SHADOW_FIRST` |
| `neg_risk_basket` | family completeness (sum of YES asks vs theoretical) | `negRisk` metadata, full token book per family | `UNKNOWN_BUT_INTERESTING` |
| `resolution_window_maker` | source-known-but-venue-unresolved discount | `ResolutionEra` (Phase 0 PR 1) + UMA listener + `umaResolutionStatus` | `SHADOW_FIRST` |
| `cross_market_correlation_hedge` | cross-city same-weather-system | `WeatherRegimeTag` (Phase 3 T1) + `correlation_cluster_for` | `RESEARCH_ONLY` initially; shadow after Phase 5 shrinkage |
| `weather_event_arbitrage` | weather alert / extreme event lag | external alert feed (TBD whether wired) | `UNKNOWN_BUT_INTERESTING` |

Each candidate ships with:
1. Production implementation (~150-400 LOC).
2. `strategy_profile_registry.yaml` entry with `live_status: shadow` (NEVER live in Phase 4).
3. Relationship test asserting candidate emits `decision_events` rows (Phase 1 T1) or `no_trade_events` rows (Phase 2 T2) consistently — never silently drops.
4. Shadow cohort tag for `ShadowDecisionLogVNext` (Phase 6 dependency; Phase 4 lays groundwork via cohort field).

## Required pre-checks before Phase 4 dispatch

- Phase 3 T1 `WeatherRegimeTag` MUST be landed (cross_market_correlation_hedge needs it).
- Phase 0 PR 1 `ResolutionEra` confirmed on main (resolution_window_maker needs it). ✓ landed.
- `MarketAnalysisVNext` field set covers spread/depth/freshness (Phase 2 T4). ✓ landed.

## Dispatch order

Suggested: ONE planner-driven phase with 4 internal tracks:
- T1 (~250 LOC): `spread_bucket` field on `make_hypothesis_family_id` + `selection_family.py` BH partition; antibody asserting tight-bucket BH threshold tighter than mixed-bucket.
- T2 (~250 LOC each, parallel): production for `stale_quote_detector` + `resolution_window_maker` (lowest-risk, narrowest scope, well-defined edge source).
- T3 (~250 LOC each, parallel after T2): `liquidity_provision_with_heartbeat` + `weather_event_arbitrage` (require richer microstructure consumption).
- T4 (~300 LOC each, requires Phase 3 + Phase 5): `cross_market_correlation_hedge` + `neg_risk_basket` (need correlation matrix shrinkage + negRisk metadata audit).

Per-track opus SCAFFOLD critic on T1 (math correctness), sonnet SCAFFOLD critic on T2/T3/T4 candidate production. One opus wave-critic before merge.

## Schema impact

- No schema bumps for T1 (`spread_bucket` is computed, not stored on a new column unless persistence required for replay).
- T2-T4: maybe extend `decision_events` or `no_trade_events` with `strategy_id` for cohort attribution. Verify these columns exist (Phase 1 T1 + Phase 2 T2 should have them).
- Optional: `shadow_experiment_cohort` lookup table (small, additive).

## NoTradeReason additions

Per candidate strategy, 1-3 new NoTradeReason members (e.g., `STALE_QUOTE_FILL_INFEASIBLE`, `RESOLUTION_DISPUTED`, `NEGRISK_FAMILY_INCOMPLETE`, `WEATHER_ALERT_SOURCE_UNTRUSTED`).

## Verifier probes

1. `git show origin/main:src/strategy/selection_family.py | grep -E "spread_bucket"` returns matches; signature documents the bucket partition.
2. Relationship test: synthetic 100-hypothesis family mixing tight + wide spreads → BH partition produces strictly more conservative threshold in wide-spread sub-family than tight-spread sub-family (proves partition reduces, not raises, FDR).
3. Each of 6 candidate files in `src/strategy/candidates/` is non-stub (>50 LOC, has `evaluate()` or equivalent function).
4. `architecture/strategy_profile_registry.yaml` has rows for each candidate with `live_status: shadow`; `is_runtime_live` returns False for each.
5. Synthetic candidate decision → `decision_events` row written with `strategy_id` matching candidate name.
6. Tag `phase4_track*_landed` + umbrella `phase4_landed`.

## What Phase 4 does NOT do

- Promote any candidate to `live_status: live`. That awaits Phase 6 `EvidenceLadder` tier ≥ 6.
- Shrink correlation matrix (Phase 5).
- Settlement type-gate (Phase 7).
