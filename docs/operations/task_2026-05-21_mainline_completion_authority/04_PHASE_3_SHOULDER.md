# Phase 3 — Shoulder Strategy Refinement (Substantive Authority)

This file is the bridge between (a) v4 §M ENUM ("Shoulder"), (b) dossier §7 substantive intent, (c) current `origin/main` code state, (d) Phase 3 planner v2 output at `docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md`.

## Why shoulder is structurally different from finite bins

Finite-range bin (e.g., "Chicago high temperature ∈ [60, 65)"):
- bounded interval
- sibling bin probabilities sum to ~1
- tail miss capped by adjacent bin payoff
- standard Bernoulli treatment OK

Open shoulder (e.g., "Chicago high temperature ≥ 95" or "≤ 40"):
- unbounded state region
- payoff geometry: NO position is a short on the tail (sells the lottery); YES position is a long on the tail
- rare-event sample scarcity → calibration noise dominates the tail
- correlated weather-regime crash risk: heat dome / cold front shifts MULTIPLE cities' shoulders simultaneously
- source-anomaly exposure: station sensor spike can falsely push the shoulder bin (Paris 2026 case per dossier §0.7)
- retail lottery demand can systematically overprice shoulder YES → naive `posterior - market` shows fake edge on sell-NO side
- market-maker inventory skew can leave shoulder NO book one-sided

Implication: shoulder cannot share Kelly multiplier, FDR family, or exposure cap with finite-range bins. Mixing them produces a hidden short-vol position the portfolio is unaware of.

## Required object model (dossier §7.3, verbatim)

`ShoulderStrategyVNext` carries 21 fields (verifier recount 2026-05-21: 21 enumerated rows vs original "20" header — row count is authoritative):

```
is_open_shoulder: bool                                    # gate
shoulder_side: Literal["upper", "lower"]                   # which side of the distribution
metric: Literal["high", "low"]                             # temperature extremum
tail_direction: Literal["above_threshold", "below_threshold"]
finite_adjacent_bin: Optional[BinId]                       # the bin next to the shoulder

tail_probability_raw: float                                # ensemble Monte Carlo
tail_probability_calibrated: float                         # post-Platt
tail_probability_stressed: float                           # +2σ stress test

tail_regime_tag: WeatherRegimeTag                          # heat_dome / cold_snap / normal / shoulder_season / source_anomaly / unknown
retail_lottery_bias_score: float                           # diagnostic; if shoulder ask >> historical median, flag
extreme_weather_underpricing_score: float                  # diagnostic; if regime is extreme but shoulder posterior < market, flag
source_anomaly_score: float                                # cross-station deviation z-score

native_yes_quote: Optional[ExecutionPrice]                 # not 1 - NO_bid; native YES book
native_no_quote: Optional[ExecutionPrice]                  # not 1 - YES_ask; native NO book
liquidity_gate: bool                                       # depth_at_best_ask ≥ intended_size

shoulder_family_id: HypothesisFamilyId                     # see §below
tail_correlation_cluster: ClusterId                        # which weather system this shoulder belongs to
max_loss_scenario: float                                   # max-loss USD if tail realizes against position

kelly_haircut: float                                       # ∈ [0.05, 0.20] per dossier §7.5
max_exposure_cap: float                                    # absolute USD cap per shoulder side
no_trade_reason: Optional[NoTradeReason]                   # if not None, do not size
```

## Five variants per dossier §7.4

| # | Variant | Verdict |
|---|---|---|
| 1 | Sell extreme shoulder (short tail) | `SHADOW_FIRST`; live only with shoulder-specific cap + stress evidence + Day0-bound elimination |
| 2 | Buy mispriced shoulder (long tail during extreme regime) | `UNKNOWN_BUT_INTERESTING`; only during tagged extreme regime + native YES depth |
| 3 | Center-vs-shoulder pair (family relative value) | `SHADOW_FIRST`; requires two-leg fill simulation |
| 4 | Tail-hedged shoulder basket (cross-city tail-vs-tail) | `RESEARCH_ONLY` |
| 5 | Shoulder no-trade gate | `IMPLEMENTATION_READY_FOR_CLAUDE` — record no-trade reason when shoulder edge depends on mid/last price or lacks native NO depth |

## Kelly + FDR + risk rules (dossier §7.5)

**Family ID grammar** (extension of current `make_hypothesis_family_id`):
```
shoulder_family_id := f"shoulder:{city}:{metric}:{target_date}:{source_id}:{regime}"
```

Versus a finite-bin family which uses `city × target_date × metric × bin_index`. Shoulder gets its own discriminator so the BH gate at `selection_family.py` doesn't lump shoulder hypotheses with center hypotheses.

**Kelly haircut**:
```
shoulder_kelly_multiplier := min(0.20, max(0.05, base_haircut_from_evidence_tier))
```

Standard Kelly says `f* = (b*p - q) / b` where `b` is odds, `p` is posterior. Shoulder forces `f_effective = f* × shoulder_kelly_multiplier × m_existing_multipliers`. Cap range 0.05-0.20 is a structural choice — even at maximum, shoulder uses ≤ 20% of normal Kelly until forward evidence demonstrates calibration on the tail.

**Hard caps**:
- Per-side notional cap (USD) — operator-set, separate per upper/lower shoulder
- Cluster cap: sum of shoulder exposure across cities in same weather-system cluster ≤ `cluster_cap_usd`
- Same-direction shoulder sell across multiple cities under one heat-dome/cold-front: REFUSE; reduce to one city

**Stress scenarios** (per dossier §7.5, all required before each shoulder decision):
1. +2σ forecast error (perturb posterior in adverse direction)
2. station anomaly (apply Paris-style sensor-spike to the source temperature)
3. late-day advection (apply afternoon temperature shock)
4. source revision (assume official observation revises against position)
5. model tail under-dispersion (assume ensemble underestimates tail mass by factor)
6. correlated city crash (assume all cities in cluster realize same-direction tail)

A shoulder candidate that fails ANY stress with `posterior_stressed × payoff - fee_adjusted_cost > 0` invalid is rejected. Output goes to `tail_stress_scenarios` table for replay.

## Day0-bound interaction (dossier §7.6)

Shoulder is safer ONLY after Day0 bound has eliminated tail:
- Upper shoulder sell BEFORE event → dangerous (tail still in flight)
- Upper shoulder sell AFTER `HIGH_IMPOSSIBLE_DETERMINISTIC` AND source-matched observation → near-deterministic settlement capture
- Lower shoulder sell in low market AFTER `LOW_THRESHOLD_CROSSED` → wrong direction; metric semantics inverted

Phase 3 cannot ship the deterministic-bound interaction fully because dossier §6.2's 6-class `Day0BoundState` is not yet on `origin/main` (current `BoundClassification` is 3-class scaffold from Phase 0 PR 5). Phase 3 records an `xfail` relationship test `test_shoulder_day0_bound_eliminates_tail()` that becomes `PASS` after Phase 5 / 6 lands the full `Day0BoundState`.

## Schema impact (per planner v2 output)

| Bump | Table | Purpose |
|---|---|---|
| world 15→16 | `tail_stress_scenarios` (new) | Per-shoulder-decision stress probe results |
| world 16→17 | `shoulder_exposure_ledger` (new) | Aggregate shoulder exposure across cities + clusters for cluster cap enforcement |

Both additive. INV-37 (`get_world_connection_flocked()`) enforced for cross-DB writes.

## NoTradeReason additions (per planner v2 output)

6 new SHOULDER_* members:
- `SHOULDER_STRESS_FAIL` — fails one of the 6 stress scenarios
- `SHOULDER_REGIME_MISMATCH` — required regime tag not present (e.g., buy-shoulder requires `WeatherRegimeTag.HEAT_DOME`)
- `SHOULDER_NATIVE_NO_DEPTH_INSUFFICIENT` — native NO ask exists but depth_at_best_ask < intended_size
- `SHOULDER_DAY0_BOUND_NOT_ELIMINATED` — Day0 bound has not yet eliminated tail; defer to settlement window
- `SHOULDER_NO_TRADE_GATE` — shoulder edge depends on mid/last price or complement-NO
- `SHOULDER_CLUSTER_CAP_EXCEEDED` — adding this trade would breach `cluster_cap_usd`

## Dispatch order (per planner v2)

Strictly sequential T1 → T2 → T3:

| Track | Sub-objects | LOC | Branch |
|---|---|---|---|
| T1 | `WeatherRegimeTag` 6-member enum (HEAT_DOME/COLD_SNAP/NORMAL/SHOULDER_SEASON/SOURCE_ANOMALY/UNKNOWN) + `correlation_cluster_for(city, regime)` + `make_hypothesis_family_id(source, regime)` + `make_edge_family_id(source, regime)` extension (parallel per G5) | ~300 | `feat/phase3-t1-weather-regime-tag-20260521` |
| T2 | `ShoulderStrategyVNext` 21-field + `TailStressScenario` table + 6 stress scenarios + `_classify_via_registry` in `strategy_profile.py` replacing hardcoded triplicate at evaluator L1462/1478/1494 + Kelly haircut at `phase_aware_kelly_multiplier` L198 + `no_trade_events` table-rebuild migration (ATTACH+SAVEPOINT per INV-37) + SCHEMA_VERSION 15→16 | ~500 | `feat/phase3-t2-shoulder-vnext-stress-20260521` |
| T3 | `ShoulderExposureLedger` table + `cluster_cap_usd` enforcement + shadow-readiness report + Day0-bound xfail antibody | ~350 | `feat/phase3-t3-shoulder-ledger-readiness-20260521` |

Per-track: opus SCAFFOLD critic. One opus wave-critic across all 3 PRs before merge.

## Verifier probes (must pass before merge)

1. `git show origin/main:src/contracts/shoulder_strategy_vnext.py` exists; class `ShoulderStrategyVNext` has all 21 fields per dossier §7.3 (recount 2026-05-21: 21 rows in `04_PHASE_3_SHOULDER.md` §"Required object model").
2. `git show origin/main:src/contracts/weather_regime_tag.py | grep -E "class WeatherRegimeTag"` returns enum definition with exactly 6 members: `HEAT_DOME / COLD_SNAP / NORMAL / SHOULDER_SEASON / SOURCE_ANOMALY / UNKNOWN` (21-field `ShoulderStrategyVNext.tail_regime_tag: WeatherRegimeTag` must accept all 6 values).
3. `make_hypothesis_family_id` signature accepts `source: str = ""` and `regime: str = ""` kwargs; existing callers continue to work (default empty preserves prior family IDs).
4. `src/strategy/kelly.py` `phase_aware_kelly_multiplier` at L198 applies shoulder Kelly clamp `[0.05, 0.20]` at call site (clamp applied to registry value, Interpretation B per AR2/G4). `strategy_kelly_multiplier` L60-78 is NOT modified. Verify `phase_aware_kelly_multiplier` contains the clamp guard; verify `strategy_kelly_multiplier` does NOT.
5. `src/strategy/strategy_profile.py` has a `_classify_via_registry` helper (canonical home per m2) called from `evaluator.py:1462/1478/1494` + `cycle_runner.py:456` — verify those lines NO LONGER contain hardcoded `shoulder_sell` string; verify cycle-axis short-circuits (settlement_capture / opening_inertia / imminent_open_capture) ARE UNCHANGED per AR1.
6. `tail_stress_scenarios` table exists in `state/zeus-world.db` with PRAGMA `user_version` ≥ 16; `shoulder_exposure_ledger` exists with `user_version` ≥ 17.
7. `NoTradeReason` enum membership grew by 6 SHOULDER_* members (re-count via `git show origin/main:src/contracts/no_trade_reason.py`).
8. Tags `phase3_track1_landed`, `phase3_track2_landed`, `phase3_track3_landed` exist; `phase3_landed` umbrella tag on the last merge sha.
9. CI green on the last 5 main runs after Phase 3 closure.
10. Relationship test `test_shoulder_day0_bound_eliminates_tail` exists as `xfail` with reason citing pending Phase 5/6 Day0BoundState upgrade.
11. Relationship test `test_shoulder_cluster_cap_under_heat_dome` exists and passes — **synthetic heat-dome 3-city** scenario: `WeatherRegimeTag.HEAT_DOME` tagged cluster, 3 cities same-direction shoulder sell attempted in sequence → only first city's shoulder trade passes; subsequent 2 rejected with `SHOULDER_CLUSTER_CAP_EXCEEDED`. (T3 test description, pinned per m4.)
12. Relationship test `test_shoulder_stress_fail_rejects_candidate` exists and passes — **synthetic +2σ-stress reject** scenario: shoulder candidate with baseline posterior surviving threshold but `TailStressScenario.FORECAST_PLUS_2SIGMA` adversely perturbed posterior exceeds loss threshold → candidate rejected with `SHOULDER_STRESS_FAIL`. (T2 test description, pinned per m4.)

## What this phase explicitly does NOT do

- Live promotion of shoulder_sell to `live_status: live`. End-state is `shadow` for shoulder_sell, **`blocked` for shoulder_buy** (UNKNOWN_BUT_INTERESTING = research-only per §7.4; `dormant_redesign → shadow` transition does NOT happen at Phase 3 end). Live promotion is Phase 6+ work after `EvidenceLadder` tier ≥ 6.
- Full 6-class `Day0BoundState` (dossier §6.2). That stays Phase 5/6.
- `MarketAnalysisVNext` field additions (e.g., `executable_exit_value`, `fill_probability`). Phase 4 work.
- Candidate stub production (`stale_quote_detector` etc.). Phase 4.
