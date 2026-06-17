Verdict: YES to the shortened path, but with one correction: “direct-native-only” must be proof-native maker/taker single-leg routing, not the current ask-ladder-only direct route; settlement-graded EV replay remains the hard deploy gate. Confidence: high on the engineering checklist, medium on profitability until the EV replay prints.

Revising the round-1 scope: submit plumbing is no longer a suspected blocker. I accept your local verification that the live path reaches real SDK sign+POST and has recent filled venue commands. The remaining blockers are exactly the decision/economics path: route identity, live==replay case metadata, day0 exclusion, exposure before ΔU argmax, spine receipt/gate semantics, and settlement-graded EV proof. I did not independently reopen main.py, executor.py, or polymarket_v2_adapter.py in this round; your local result is now the working premise.

1. Minimum-safe-to-live checklist — confirmed, with one important correction

Direct-native-route-only is correct for v1, but implement it as proof-native single-leg routing. Do not merely pass enable_negrisk_routes=False and leave the current direct_no route as “NO ask only.” negrisk_routes.py says enable_negrisk_routes=False suppresses synthetic, pair arb, full basket arb, and conversion while keeping direct YES/NO routes, which is the right route-class boundary for v1. But current direct NO is explicitly “the NO_i ask,” not a native maker bid route, while the reactor’s target live edge class is maker buy_no into an empty NO ask, priced as a resting bid behind the complementary book. So v1 must either enumerate direct-native routes from the reactor _CandidateProof/NativeSideCandidate itself, including maker and taker, or add DIRECT_NO_MAKER / DIRECT_YES_MAKER one-leg route types. Multi-leg synthetic/arb/conversion can wait; native maker cannot if that is the live edge class. src/execution/negrisk_routes.py:44-46,70-73; src/engine/event_reactor_adapter.py:1000-1009. 
GitHub
+2
GitHub
+2

Live ForecastCase must be built by the same factory as replay. Current bridge still sets lead_hours=0.0, season="", regime_key="", and source_cycle_time_utc=decision_time; replay uses lead_hours=24.0, season_for(target_date), and regime_key="default". That mismatch can silently select the wrong sigma floor bucket because lead_bucket_for(case) maps lead_hours<1 day to day0 and <2 days to 24h. Fix with a shared ForecastCaseFactory used by both the reactor bridge and the replay. src/engine/qkernel_spine_bridge.py:29-31; scripts/qkernel_arm_replay.py:10-11; src/forecast/sigma_authority.py:17,30-32. 
GitHub
+3
GitHub
+3
GitHub
+3

Hard-block qkernel on DAY0 event types until day0 is wired. Current bridge uses _NoDay0Reader, and the reactor explicitly has a day0 event class DAY0_EXTREME_UPDATED. Even if forecast_only currently rejects same-day markets, the qkernel branch should fail closed on event.event_type in _DAY0_LANE_EVENT_TYPES so future forecast_plus_day0 changes cannot route a day0 family through a no-observation conditioner. src/engine/qkernel_spine_bridge.py:44-46; src/engine/event_reactor_adapter.py:107-124,185-187. 
GitHub
+3
GitHub
+3
GitHub
+3

Pass real exposure into decide() before argmax. The current qkernel seam calls decide_family_via_spine(... extra_exposure_by_bin_id=None) even though the marginal-utility exposure path already exists and explicitly changes ΔU when the book is already leaning into a family outcome. Refactor exposure calculation so it does not require a selected proof, then pass the per-family exposure map into the spine before candidate scoring. src/engine/event_reactor_adapter.py:192-193,546-550,645-650; src/engine/qkernel_spine_bridge.py:58-63. 
GitHub
+3
GitHub
+3
GitHub
+3

Receipt and downstream gates must consume spine economics, not legacy q_lcb_5pct. The bridge currently overlays q_posterior and trade_score but keeps proof q_lcb_5pct because spine edge_lcb is an edge, not a probability. That is fine as telemetry, but unsafe if any downstream submit gate still reads legacy q_lcb_5pct > price or TRADE_SCORE_NON_POSITIVE as the live admission authority. Add non-null spine_route_id, spine_edge_lcb, spine_delta_u, spine_q_dot_payoff, spine_cost, spine_payoff_vector_hash, and spine_decision_hash; in qkernel mode, downstream gates must re-proof spine_edge_lcb>0 and spine_delta_u>0, not legacy scalar fields. src/engine/qkernel_spine_bridge.py:72-76; src/decision/payoff_vector.py:24-35; src/decision/family_decision_engine.py:68-69. 
GitHub
+3
GitHub
+3
GitHub
+3

Settlement-graded EV replay is the hard deploy gate. Calibration replay is not enough. The current ARM script explicitly says executable_market_snapshots cannot map a condition to the exact settled bin and therefore records market-implied EV only; that gap must be closed before live qkernel submit. scripts/qkernel_arm_replay.py:20-29,48-49. 
GitHub
+1

2. Settlement-graded EV replay design — hard deploy gate

Build a new scripts/qkernel_settlement_ev_replay.py rather than overloading the calibration harness. The replay should exercise the exact v1 live policy:

forecast_only, replay-equivalent ForecastCase, day0 blocked, direct-native single-leg only, no synthetic/arb/conversion, real exposure model if historical exposure is reconstructable, qkernel selection by vector edge_lcb>0 and optimal_delta_u>0, and settlement-graded realized P&L.

2.1 Data sources and the condition_id → settled-bin registry

Use settlement_outcomes only for verified truth, as the ARM replay already does. Then use the market topology registry, not snapshots alone, to map condition_id to bin geometry. The canonical registry is the market_events family table consumed by _event_family_market_topology_rows(): it returns the complete family for (city, target_date, temperature_metric), fails loud if any sibling lacks a condition_id, and carries range_label, range_low, range_high, and outcome. That is the missing join the ARM script calls out. src/engine/event_reactor_adapter.py:1081-1088; scripts/qkernel_arm_replay.py:20-29. 
GitHub
+1

The replay should reconstruct Ω from market_events via the same topology path as live, not build_grid_omega(). The current ARM script’s build_grid_omega() creates synthetic grid condition IDs and is useful for calibration, but it cannot grade a real selected condition/token. scripts/qkernel_arm_replay.py:10; src/probability/outcome_space.py:0-3. 
GitHub
+1

Settlement bin resolution should be:

settlement_value_native = VERIFIED settlement_outcomes.settlement_value

Then, only if the stored value is raw/unrounded, apply SettlementSemantics.for_city(city).round_single / the same rounding authority carried by EventResolution. Hong Kong must remain oracle_truncate; other current cities use WMO half-up per the event-resolution contract. Then find the unique topology bin with lower_native <= settlement_value_native <= upper_native, with shoulders treated as open-ended. src/probability/event_resolution.py:0-3; src/probability/outcome_space.py:0-3. 
GitHub
+1

2.2 Decision-time executable cost

For every replay decision timestamp, select the latest executable full-family snapshot at or before decision time, exactly like the reactor’s _latest_snapshot_rows_for_event_family(... fresh_at=decision_time, require_fresh=True). Use those rows to generate the same _CandidateProof objects as live, then call the same qkernel bridge/engine in replay mode. The key invariant is: the cost used in EV replay must be the cost the live submit intent would carry, not a midpoint, not a complement, and not the route engine’s ask-only proxy if the proof is a maker bid. The payoff-vector layer already expects all-in probability-unit cost and records it as CandidateEconomics.cost. src/engine/event_reactor_adapter.py:979; src/decision/payoff_vector.py:34-35. 
GitHub
+1

For taker direct-native candidates, executable cost is the all-in average fill cost from the visible ask ladder at the chosen shares, including venue fee/tick and min-order constraints.

For maker direct-native candidates, executable cost is the actual resting bid limit if filled. The code says a maker buy_no into an empty NO ask has all-in cost equal to the quote and zero taker fee, but the replay must not assume a resting quote filled unless the historical data proves it. Snapshots alone can price the quote; they do not prove fill. Maker fill proof needs actual own fills/venue commands, exchange trade prints, or a strict historical crossing rule that is demonstrably conservative. src/engine/event_reactor_adapter.py:1000-1009. 
GitHub

This creates a clean fork:

For the shortest provable pre-live gate, make v1 taker-direct only unless you have fill-grade maker history. That is easiest to settlement-grade from snapshots.

For the highest-throughput v1, include native maker only if the replay can prove maker fills with actual fill/trade evidence. Otherwise maker may be positive expected EV but not settlement-graded fill EV yet.

2.3 Realized settlement payoff

For each qkernel-selected and fillable candidate:

payoff_j = 1{settled_bin_id == selected_bin_id} for buy_yes.

payoff_j = 1{settled_bin_id != selected_bin_id} for buy_no.

pnl_per_share_j = payoff_j - all_in_cost_per_share_j.

pnl_usd_j = filled_shares_j * pnl_per_share_j.

For taker counterfactuals, filled_shares_j is capped by visible executable depth at decision time and by the same sizing/min-order rule the submit path would use. For actual fills, use actual filled size, average fill price, and fees from the venue/order records. For resting maker orders without fill evidence, filled_shares_j=0 for settlement-graded fill P&L; do not count hypothetical maker wins.

2.4 PASS criterion — no fixed win-rate bar

The pass/fail bar is not win-rate and not a fixed success percentage. It is the sign of after-cost EV under a conservative posterior for the exact live-enabled policy.

Define the v1 policy class first, for example:

QKERNEL_DIRECT_NATIVE_V1 = forecast_only + lead_bucket=24h + regime=default + day0_blocked + direct-native one-leg + no synthetic/arb/conversion + exact qkernel selection + exact submit-mode cost.

For each replayed, settlement-graded, fillable decision in that class, compute stake-weighted after-cost P&L per dollar at risk:

r_j = pnl_usd_j / capital_at_risk_j.

Aggregate by the leaves that will actually be live-enabled: route_type, execution_mode maker/taker, side YES/NO, metric high/low, lead bucket, city or station group, liquidity bucket, and forecast cycle. Use hierarchical shrinkage with a zero-mean prior for leaf EV; leaves with no or thin data shrink toward zero and therefore cannot pass merely by absence of evidence.

PASS: the conservative lower posterior bound of the stake-weighted policy EV is strictly above zero after fees/slippage/fillability, and every enabled leaf either has non-negative conservative EV or is excluded from the live scope. The only economic threshold is zero. There is no win-rate threshold, no “60% hit rate,” no fixed profit target, and no cap/haircut substituting for a broken transform.

FAIL: any of these conditions holds: condition_id cannot be mapped to a settled bin; cost is not the actual executable cost of the route; maker fill is assumed rather than proven; the direct-native live class has lower-bound EV ≤ 0; or positive EV exists only in synthetic/basket/arb routes while v1 cannot execute those routes.

Suggested command target:

python scripts/qkernel_settlement_ev_replay.py --policy qkernel-direct-native-v1 --strict-condition-bin-join --strict-fillability --no-day0 --no-synthetic --no-arb --no-conversion --require-live-replay-case-equality

3. Canonical live ForecastCase metadata source

Use one shared factory, for example src/engine/qkernel_forecast_case_factory.py, and import it from both src/engine/qkernel_spine_bridge.py and scripts/qkernel_settlement_ev_replay.py.

Field mapping:

city, city_id, station_id, settlement_source_type, unit, resolution: from event_resolution_for_city(runtime_cities_by_name()[family.city], target_local_date, metric). Current bridge already resolves this authority; keep it. src/engine/qkernel_spine_bridge.py:29-31; src/probability/event_resolution.py:0-3. 
GitHub
+1

target_local_date, metric, family_id: from the candidate family built from market_events / event topology, not from a selected snapshot subset. The topology function says the family universe comes from market_events, not executable snapshots, because q/FDR must run over the full MECE family. src/engine/event_reactor_adapter.py:1081-1084. 
GitHub

source_cycle_time_utc and issue_time_utc: from the forecast/member source cycle that produced _edli_spine_*, not from decision_time. The current payload stash includes _edli_spine_mu_native, _edli_spine_sigma_native, raw/debiased members, and q vector, but I found no _edli_spine_source_cycle_time_utc key in the bridge or adapter. Add it at the same producer stash site and fail closed if absent. src/engine/event_reactor_adapter.py:477-479,843; src/engine/qkernel_spine_bridge.py:34-41. 
GitHub
+1

lead_hours: compute from the forecast source cycle and the target forecast date in the same way replay does. For the shortest safe v1, restrict live qkernel to the replay-proven 24h bucket; if lead_bucket_for(case) != "24h", return a typed no-trade until that bucket has its own settlement-EV replay. src/forecast/sigma_authority.py:17. 
GitHub

season: emos_season(target_local_date), same helper used by sigma floor lookup. Do not leave it blank. src/forecast/sigma_authority.py:30-32. 
GitHub

regime_key: "default" for v1 because replay uses default. Do not leave it blank. Later regimes require separate replay buckets.

4. Is direct-native-only sufficient for the first profitable fill?

Yes, conditionally: direct-native-only is sufficient for v1 if the settlement-EV replay proves positive EV for the direct-native class. Multi-leg route-intent does not have to ship first.

But there is a sharp distinction:

Direct-native single-leg maker/taker is sufficient and should ship first. It uses the existing live submit path and one selected _CandidateProof. This is the shortest path to a real settlement-graded fill.

Direct-taker-ask-only may not be sufficient because it can discard the apparent dominant live edge class: native maker buy_no into an empty NO ask. The code comments explicitly name that as the target edge class, while negrisk_routes.py direct NO currently walks the NO ask. src/engine/event_reactor_adapter.py:1000-1009; src/execution/negrisk_routes.py:44-46. 
GitHub
+1

Synthetic-NO/basket/arb route-intent is not required for the first fill unless the settlement-EV replay shows all positive EV is in synthetic/basket/arb and direct-native EV is not positive. In that case, route-intent becomes a v1 blocker because the current bridge maps only (bin, side) back to a single proof and cannot execute the route whose economics it selected. src/engine/qkernel_spine_bridge.py:52-66; src/decision/family_decision_engine.py:57-58; src/execution/negrisk_routes.py:30-33. 
GitHub
+3
GitHub
+3
GitHub
+3

5. Ordered findings / implementation checklist

[BLOCKER] settlement-EV replay hard gate — scripts/qkernel_arm_replay.py:20-29,48-49 — impact: current replay cannot prove positive settlement-graded after-cost EV because snapshots alone cannot map a selected condition to the settled bin and current EV is market-implied only — concrete fix: add scripts/qkernel_settlement_ev_replay.py joining settlement_outcomes to market_events topology and decision-time executable snapshots, then grade selected qkernel candidates by realized payoff minus all-in cost — verify locally: python scripts/qkernel_settlement_ev_replay.py --policy qkernel-direct-native-v1 --strict-condition-bin-join --strict-fillability --no-day0 --no-synthetic --no-arb --no-conversion --require-live-replay-case-equality.

[BLOCKER] direct-native route realization — src/execution/negrisk_routes.py:44-46,70-73; src/engine/event_reactor_adapter.py:1000-1009; src/engine/qkernel_spine_bridge.py:58-66 — impact: simply disabling neg-risk routes leaves current direct NO as ask-only and can exclude the maker buy_no edge class the live reactor already knows how to submit — concrete fix: in qkernel v1, enumerate only proof-native one-leg direct routes from _CandidateProof/NativeSideCandidate, preserving maker/taker mode and proof execution price; separately assert selected route has exactly one leg and token/condition matches the selected proof — verify locally: add tests/integration/test_qkernel_direct_native_maker_route.py where NO ask is empty, complementary YES bid exists, qkernel can select a one-leg maker buy_no, and synthetic routes remain disabled.

[BLOCKER] synthetic/arb/conversion disabled until route-intent submit — src/decision/family_decision_engine.py:46-58; src/execution/negrisk_routes.py:30-33,70-73; src/engine/qkernel_spine_bridge.py:52-66 — impact: if synthetic wins best_no_route, current bridge still maps only (bin, side) to one native proof and would submit different economics than selected — concrete fix: pass enable_negrisk_routes=False or bypass build_negrisk_route_set in v1 direct-native mode; assert route_type in {"DIRECT_YES","DIRECT_NO","DIRECT_YES_MAKER","DIRECT_NO_MAKER"} and len(legs)==1 before overlay — verify locally: pytest tests/execution/test_negrisk_route_set.py tests/integration/test_qkernel_spine_direct_native_only.py -q.

[BLOCKER] live==replay ForecastCase — src/engine/qkernel_spine_bridge.py:29-31; scripts/qkernel_arm_replay.py:10-11; src/forecast/sigma_authority.py:17,30-32 — impact: current bridge metadata makes live look like day0/blank-regime while replay is 24h/default, so the validated sigma floor may not be the served live floor — concrete fix: create a shared ForecastCaseFactory, thread _edli_spine_source_cycle_time_utc, set season=emos_season(target_date), regime_key="default", and hard-no-trade if live case is outside the replay-proven bucket — verify locally: pytest tests/integration/test_qkernel_live_replay_forecast_case_identity.py -q.

[BLOCKER] qkernel DAY0 hard block — src/engine/event_reactor_adapter.py:107-124,185-187; src/engine/qkernel_spine_bridge.py:44-46 — impact: qkernel has no day0 observation reader, so same-day event types can price impossible bins if ever admitted — concrete fix: pass event_type into decide_family_via_spine or gate before the call; return QKERNEL_DAY0_NOT_WIRED for DAY0_EXTREME_UPDATED until a real Day0Reader is wired — verify locally: pytest tests/integration/test_qkernel_day0_hard_block.py -q.

[HIGH] exposure before qkernel argmax — src/engine/event_reactor_adapter.py:192-193,546-550,645-650; src/engine/qkernel_spine_bridge.py:58-63 — impact: flat selection can choose the wrong candidate before existing family risk is considered — concrete fix: refactor _family_existing_exposure_by_bin_id into a family-level exposure builder independent of selected proof and pass it into the bridge before engine.decide() — verify locally: pytest tests/integration/test_qkernel_exposure_changes_selected_candidate.py -q.

[HIGH] spine receipt and gate semantics — src/engine/qkernel_spine_bridge.py:72-76; src/decision/payoff_vector.py:24-35; src/decision/family_decision_engine.py:68-69 — impact: downstream code can still reject on legacy scalar trade_score/q_lcb_5pct or record an unreconstructable qkernel decision — concrete fix: add mandatory qkernel receipt fields and make qkernel-mode submit gates consume spine_edge_lcb and spine_delta_u; legacy fields become telemetry only — verify locally: rg "TRADE_SCORE_NON_POSITIVE|q_lcb_5pct|spine_edge_lcb|spine_delta_u|qkernel_spine" src tests.

[HIGH] maker fillability in EV replay — src/engine/event_reactor_adapter.py:1000-1009 — impact: maker quote economics can look positive without producing settlement-graded fills if historical fillability is assumed rather than proven — concrete fix: either v1 replay is taker-direct only, or maker replay requires actual fills/trade-tape evidence that the resting order would have filled at the quoted limit — verify locally: python scripts/qkernel_settlement_ev_replay.py --policy qkernel-direct-native-v1 --strict-maker-fill-proof.

[MEDIUM] topology registry reuse — src/engine/event_reactor_adapter.py:1081-1088,995; src/probability/outcome_space.py:0-3 — impact: duplicating topology/bin reconstruction in replay risks a live/replay mismatch — concrete fix: expose a small reusable topology loader that returns the same OutcomeSpace from market_events for both reactor and replay — verify locally: pytest tests/integration/test_qkernel_replay_uses_live_topology_registry.py -q.

6. Highest-value local checks

Run these first:

rg "lead_hours=0.0|season=\"\"|regime_key=\"\"|source_cycle_time_utc=issue|_edli_spine_source_cycle|_NoDay0Reader|extra_exposure_by_bin_id=None|q_lcb_5pct|TRADE_SCORE_NON_POSITIVE|enable_negrisk_routes" src tests

pytest tests/integration/test_qkernel_direct_native_maker_route.py tests/integration/test_qkernel_spine_direct_native_only.py tests/integration/test_qkernel_live_replay_forecast_case_identity.py tests/integration/test_qkernel_day0_hard_block.py tests/integration/test_qkernel_exposure_changes_selected_candidate.py -q

Then the deploy gate:

python scripts/qkernel_settlement_ev_replay.py --policy qkernel-direct-native-v1 --strict-condition-bin-join --strict-fillability --no-day0 --no-synthetic --no-arb --no-conversion --require-live-replay-case-equality

The smallest fact that would change the v1 route answer: if the settlement-EV replay shows direct-native lower-bound EV is not positive and all positive EV comes from synthetic-NO/basket/arb, then multi-leg route-intent must ship before live qkernel. Otherwise, direct-native proof-native routing is the shortest correct path to the first settlement-graded profitable fill.