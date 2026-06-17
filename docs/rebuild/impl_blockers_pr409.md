# Created: 2026-06-15
# Authority basis: docs/rebuild/consult_review_pr409.md §5/§7 + the round-2
#   corrections docs/rebuild/consult_review_pr409_round2.md §1/§3/§5.

# PR #409 q-kernel integration bridge — four live-path blockers FIXED (+ round-2 corrections)

Worktree: `/Users/leofitz/zeus/.claude/worktrees/qkernel-rebuild` (isolated; live daemon
runs a different tree). No deploy, no flag flip, no commit. Flag-OFF path is byte-neutral
(every change is confined to the `qkernel_spine_enabled()`-ON branch or to the Stage-0
observability-only producer stash, which already never feeds a decision).

## New shared file

- `src/forecast/forecast_case_factory.py` — the SINGLE `forecast_case_metadata(...)`
  derivation of `(season, lead_hours, regime_key)` used by BOTH the live bridge and the
  ARM replay, so they cannot drift. `season = emos_season(target)` (the SAME helper the
  σ-floor lookup keys on), `regime_key = "default"`, `lead_hours` = real elapsed lead from
  the forecast source cycle to the target settlement finalization. Constants
  `REPLAY_LEAD_HOURS = 24.0`, `REPLAYED_LEAD_BUCKET = "24h"`, `DEFAULT_REGIME_KEY`.

---

## BLOCKER 1 — live-vs-replay forecast-case mismatch (round-2: source-cycle + emos_season + 24h bucket)

What changed:
- `src/engine/qkernel_spine_bridge.py:276` `build_forecast_case(family, *, source_cycle_time_utc)`
  now derives `season`/`lead_hours`/`regime_key` via the shared factory and sets
  `issue_time_utc` / `source_cycle_time_utc` to the FORECAST SOURCE CYCLE (lines 326-327,
  316-320) — NOT decision_time. Was `lead_hours=0.0, season="", regime_key="",
  source_cycle_time_utc=decision_time`.
- `src/engine/qkernel_spine_bridge.py:431` `_served_predictive_inputs` now reads
  `_edli_spine_source_cycle_time_utc` and FAILS CLOSED (`SPINE_INPUTS_UNAVAILABLE`) when
  absent (line 432); `_parse_source_cycle_time` at line 443.
- `src/engine/qkernel_spine_bridge.py:738-745` `decide_family_via_spine` restricts to the
  replay-validated `REPLAYED_LEAD_BUCKET` ("24h"); a case outside it returns the typed
  `QKERNEL_LEAD_BUCKET_NOT_REPLAYED` no-trade.
- `src/engine/event_reactor_adapter.py:11581` Stage-0 producer now stashes
  `_edli_spine_source_cycle_time_utc` from the snapshot's `source_cycle_time` / `issue_time`
  / payload `cycle` (the same canonical accessor the calibration path uses); read seam list
  updated at line ~7600.
- `scripts/qkernel_arm_replay.py` imports `emos_season` (as `season_for`) and the factory
  constants so the replay and live use the identical season helper + lead/regime constants.

Resolution note (canonical helpers DID exist): `emos_season` (= `season_for`, byte-identical)
in `src/calibration/emos.py`; `lead_bucket_for` in `src/forecast/sigma_authority.py`. No
canonical regime helper exists for this spine — `"default"` is the replay convention (used).
The `_edli_spine_source_cycle_time_utc` payload key did NOT exist; added at the producer site.

Test: `tests/integration/test_qkernel_spine_blockers_pr409.py`
- `test_live_bridge_forecast_case_matches_arm_replay` — live case season/regime/metric ==
  replay; lead bucket == "24h"; served σ-floor is non-None and equal to the replay's cell;
  issue/source_cycle == the forecast source cycle.
- `test_spine_inputs_unavailable_when_source_cycle_absent` — absent source-cycle ⇒
  `SPINE_INPUTS_UNAVAILABLE`.
- `test_lead_bucket_outside_24h_is_typed_no_trade` — a 5-day-out cycle ⇒
  `QKERNEL_LEAD_BUCKET_NOT_REPLAYED`.

RED-on-revert: PROVEN. Reverting to `season=""`/`regime_key=""`/`lead=0` blanks season/regime
(equality fails) and drops the lead to the "day0" bucket (bucket equality fails); reverting
issue to decision_time mis-buckets the lead; removing the source-cycle requirement skips the
SPINE_INPUTS_UNAVAILABLE no-trade.

## BLOCKER 2 — route identity → PROOF-NATIVE single-leg routing (maker AND taker)

What changed (round-2 correction: NOT negrisk ask-ladder; preserve the maker buy_no edge):
- `src/decision/family_decision_engine.py:281` new `RouteSetBuilder` protocol; `:472`
  constructor accepts an injectable `route_set_builder` (defaults to
  `build_negrisk_route_set`); `:599` `decide()` uses the injected builder.
- `src/engine/qkernel_spine_bridge.py:989` `_proof_native_direct_route_set_builder` — builds
  a `NegRiskRouteSet` whose `direct_yes`/`direct_no` are priced at each `_CandidateProof`'s
  OWN `execution_price` (the exact maker/taker all-in cost the submit path carries), ONE leg
  each (token/condition = the proof's), with EVERY neg-risk surface empty. Injected at
  `:795`. The engine is also driven `enable_negrisk_routes=False` (`:786`).
- `src/engine/qkernel_spine_bridge.py:850` defensive guard: if a non-`DIRECT_*` route is ever
  selected, return the typed `NO_TRADE_ROUTE_NOT_DIRECTLY_EXECUTABLE`
  (`_selected_route_is_direct` at `:652`).

Why this matters: the v1 live edge class is a maker buy_no into an EMPTY NO ask (a resting
bid behind the complementary YES book). `negrisk_routes` direct-NO walks the NO ASK (taker),
which marks an empty-NO-ask bin non-executable and DISCARDS that edge. Pricing from the
proof's own `execution_price` preserves it. Synthetic/arb/conversion stay disabled until a
real multi-leg route-intent submit exists.

Test: `tests/integration/test_qkernel_spine_blockers_pr409.py`
- `test_maker_buy_no_edge_priced_from_proof_not_ask_ladder` — empty NO ask ladder; the spine
  can still select a one-leg maker buy_no (token/condition matches the proof; len(legs)==1).
- `test_direct_route_edge_uses_proof_execution_price_not_ask` — each direct route's avg_cost
  == the proof's execution_price; all neg-risk surfaces empty.
- `test_non_direct_selection_is_refused_as_typed_no_trade`.

RED-on-revert: PROVEN by runtime patch — falling back to `build_negrisk_route_set` makes the
empty-NO-ask maker buy_no non-executable and the spine selects a different leg (the maker edge
is discarded); the avg_cost==execution_price assertion fails (route prices off the 0.75 ask).

## BLOCKER 3 — day0 hard-fact gap → hard-block on the day0 lane (round-2: typed QKERNEL_DAY0_NOT_WIRED)

What changed:
- `src/engine/event_reactor_adapter.py:288` promoted `_DAY0_LANE_EVENT_TYPES`
  (`{"DAY0_EXTREME_UPDATED"}`) to module level (the in-adapter day0 boundary now references
  the same constant).
- `src/engine/event_reactor_adapter.py:2509-2512` the reactor seam hard-blocks the spine on
  the day0 lane BEFORE the spine call: flag ON + `event.event_type in _DAY0_LANE_EVENT_TYPES`
  ⇒ typed `QKERNEL_DAY0_NOT_WIRED` no-trade (does NOT route through the no-observation
  conditioner, does NOT fall back to a forecast-blind legacy decision on a same-day market).
  Flag ON + forecast type ⇒ spine (`:2513`); flag ON + other / flag OFF ⇒ legacy.
- Constant `NO_TRADE_QKERNEL_DAY0_NOT_WIRED` at `qkernel_spine_bridge.py:145`.

Test: `tests/integration/test_qkernel_spine_blockers_pr409.py`
- `test_day0_event_type_is_in_day0_lane_and_excluded_from_forecast_lane`.
- `test_reactor_seam_hard_blocks_day0_before_spine` — asserts the seam source has the day0
  gate, emits `QKERNEL_DAY0_NOT_WIRED`, and the day0 branch precedes the forecast spine call.

RED-on-revert: PROVEN. Removing the `_spine_flag_on and _is_day0_event` branch routes day0
through the spine and the structural assertions fail.

## BLOCKER 4 — current exposure not in SELECTION

What changed:
- `src/engine/event_reactor_adapter.py:9365` new `_family_existing_exposure_for_selection_by_bin_id`
  — builds a PER-BIN family exposure map for selection by matching each open committed
  position's `condition_id` to the family bin (keyed by `_candidate_bin_id`), independent of a
  selected proof (unlike `_family_existing_exposure_by_bin_id`, which collapses onto the
  already-selected bin). Same-cycle city-keyed reservations are NOT fabricated onto a bin
  (no bin identity at selection; the post-selection recapture nets them by city).
- `src/engine/event_reactor_adapter.py:2514-2538` the seam builds this map and passes it as
  `extra_exposure_by_bin_id` into `decide_family_via_spine` (was `None` / flat baseline).

Test: `tests/integration/test_qkernel_spine_blockers_pr409.py`
- `test_existing_exposure_changes_selected_delta_u_winner` — heavy exposure on the flat-winner
  bin changes the selected ΔU winner (or shrinks it to a no-trade).
- `test_reactor_seam_passes_real_exposure_into_selection` — structural.

RED-on-revert: PROVEN. Passing the flat baseline (the pre-fix `None`) makes the two selections
identical (the concave ΔU no longer shrinks the held bin) and the test fails.

---

## Verification

- Money-path + live_inference: `tests/money_path tests/strategy/live_inference` — 331 passed
  (baseline 331 passed; byte-neutral flag-OFF).
- Integration: `tests/integration/test_qkernel_spine_routing.py` (6) +
  `tests/integration/test_qkernel_spine_blockers_pr409.py` (10) — 16 passed.
- Decision engine + route set: `tests/decision/test_family_decision_engine.py` +
  `tests/execution/test_negrisk_route_set.py` — 14 passed (route-set injection defaults
  correctly to `build_negrisk_route_set`).

Combined run (money_path + live_inference + both integration files): 347 passed.

## Pre-existing failures NOT caused by these changes (DISCLOSED)

A broad `-k "day0 or reactor or event_reactor or qkernel"` run shows 22 failures in
`tests/test_day0_remaining_day_pricing.py`, `tests/test_phase6_day0_split.py`,
`tests/test_settlement_day_observation_authority.py`. VERIFIED pre-existing: with my changes
stashed, the representative failures (`test_flag_default_off`,
`test_day0_truth_classification_persisted`, `test_riskguard_trailing_loss_stale_does_not_halt`)
fail IDENTICALLY on the baseline. They are live-config flag state (e.g.
`_day0_remaining_day_q_enabled()` is ON in this worktree's config) and DB/environment
conditions — none touch the qkernel spine, the seam, or any file I changed.

## NOT done (explicitly out of scope for these four blocker fixes — larger arcs)

- The settlement-graded EV replay harness (`scripts/qkernel_settlement_ev_replay.py`,
  round-2 §2/§5 [BLOCKER]) — the hard deploy gate; a separate build.
- Spine receipt/gate semantics (`spine_edge_lcb`/`spine_delta_u` mandatory receipt fields,
  round-2 §5 [HIGH]) — a separate change to the receipt + downstream submit gates.
- Multi-leg route-intent submit (synthetic/arb/conversion) — deferred by design.
