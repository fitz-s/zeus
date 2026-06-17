NO-GO for flipping qkernel_spine_enabled live; APPROACH verdict is CORRECT-BUT-SUBOPTIMAL, confidence: medium. The single Arrow-Debreu spine is the right replacement for the broken scalar/q_lcb/NO/neg-risk path, but this realization is not live-safe yet because the integration bridge can select route economics the unchanged _CandidateProof submit path cannot execute, the live seam does not reconstruct the same forecast case metadata used by the replay, day0 is a no-op at the bridge, and the replay proves calibration sanity rather than positive after-cost settlement EV.

1. APPROACH / PLAN VERDICT

Verdict: CORRECT-BUT-SUBOPTIMAL.

The rebuild’s core idea is directionally right: a daily temperature family is a mutually exclusive, collectively exhaustive settlement distribution, so the belief object should be one normalized Arrow-Debreu distribution over the full outcome space, and every YES/NO/synthetic route should be priced as a payoff vector against that same q. The spec explicitly replaces the old forecast→q→decision path with a settlement-station PredictiveDistribution authority and a family-level Arrow-Debreu decision kernel, and its spine is exactly the right “one belief, many instruments” decomposition: EventResolution → OutcomeSpace → FreshModelSet → DebiasAuthority → Day0Conditioner → PredictiveDistribution → JointQ → JointQBand → FamilyBook → InstrumentRouteSet → PayoffVectorDecision → MarketCoherence → Sizing → Exit in docs/rebuild/consult_build_spec.md:0-1,31-68. 
GitHub

That architecture directly attacks the known failure modes the PR itself names: stale warm-bias authority, incoherent q_lcb/modal collapse, NO semantics bug, ignored negative-risk economics, scalar Kelly/score selection, and a cap bolted onto broken transforms. The PR description says the old path could put a warm +2.8C book on live rails, manufacture LCB/modal collapse, treat NO as a scalar complement instead of a basket payoff, miss neg-risk route economics, and select through scalar Kelly/caps rather than vector utility. 
GitHub
 The new spine is therefore not cosmetic; it is the minimum coherent representation for a MECE payoff family.

The architecture is still suboptimal because a coherent payoff spine is not, by itself, an alpha model. The superior realization is:

Settlement-scored posterior edge model + Arrow-Debreu execution spine. Keep the Arrow-Debreu family/payoff-vector executor, but replace the current heuristic Normal/floor belief with a hierarchical posterior predictive model trained and monitored directly on settlement outcomes and market-relative EV cells. The belief side should be a CRPS/log-score/PIT-calibrated ensemble or Bayesian model averaging layer over (city, settlement station/source, metric, lead, season, regime, day0 state, model member, recent residual structure), with posterior draws of the entire q vector. The edge side should be a separate, settlement-graded posterior over q_model - q_market_after_cost by side/route/city/metric/lead/liquidity class, shrunk to zero when the class has thin data. The execution side should optimize and submit the actual chosen route, including multi-leg synthetic/arb routes when executable, instead of mapping everything back to a single native _CandidateProof.

That dominates the current realization because calibration and alpha are distinct. A q can be calibrated and still have zero or negative after-cost EV if the market is equally calibrated or better. The ARM report itself says the replay cannot settlement-grade per-bin after-cost EV because the snapshot table lacks the per-row bin label, and it records only market-implied EV rather than true realized EV in the current offline pass. docs/rebuild/arm_replay_report.md:10-20 and scripts/qkernel_arm_replay.py:20-29. 
GitHub
+1

Strongest case for flipping anyway: the PR claims the center is fixed, q is coherent, sigma is honest, tests are green, and the feature flag defaults OFF with rollback. The PR description says the replay covered 693 settled families, removed the warm center, produced coherent q, got roughly honest sigma, and leaves after-cost EV coverage limited. 
GitHub
 That case does not survive the integration defects below: the live bridge does not preserve route identity, does not reconstruct replay-equivalent forecast metadata, and uses a no-day0 reader. Those are not statistical confidence issues; they are path correctness issues.

2. THE BELIEF + CALIBRATION MATH

The forecast center is sound as a fail-closed guard, not as the best estimator. src/forecast/center.py enforces the invariant that mu* must stay inside the debiased member hull unless all members collapse, and the builder applies DebiasAuthority, Huber consensus, optional EMOS shrink, then envelope fallback. src/forecast/center.py:3-7,34-45. 
GitHub
 That is a good safety transform because it prevents a stale or external calibrator from moving the center outside the live member envelope. It is not yet a superior center: the file says there is no per-member OOS residual vector yet, so weights collapse to equal-member weighting, and EMOS_OOS_STRENGTH_DEFAULT=0.0. src/forecast/center.py:11,15-18. 
GitHub
 The better estimator is a settlement-scored model-specific center/posterior, not equal-weight Huber plus inactive/default EMOS shrink.

The sigma floor deviation is reasonable in principle but not yet safe at the live seam. The spec originally said sigma=max(sigma_before_floor, floor...) in docs/rebuild/consult_build_spec.md:44-47. 
GitHub
 The implementation intentionally serves the realized walk-forward floor instead of the RSS width when the floor exists, arguing that RSS double-counts spread and was over-dispersed in replay. src/forecast/sigma_authority.py:2-9,52-57. 
GitHub
+1
 That is mathematically defensible if the floor is an out-of-sample settlement-calibrated predictive width for the same cell. A “floor-only” sigma is not automatically under-dispersed; it is superior to max(RSS,floor) if RSS is known to be inflated by mixing model disagreement and already-realized residual error.

The implementation risk is cell identity. build_sigma looks up settlement_sigma_floor(case.city, season, metric, required=False) and the artifact carries no sample count in the returned receipt (n=0), while the live bridge builds a ForecastCase with lead_hours=0.0, season="", and regime_key="". src/forecast/sigma_authority.py:26-32,49-57; src/engine/qkernel_spine_bridge.py:29-31. 
GitHub
+1
 The ARM replay, by contrast, constructs replay cases with lead_hours=24 and season_for(...). scripts/qkernel_arm_replay.py:11-16. 
GitHub
 That is a live-vs-replay equivalence break. The right width estimator is not “always RSS” or “always floor”; it is a posterior predictive residual model by settlement cell with sample-count/uncertainty-aware shrinkage, and the live case metadata must match the replay metadata exactly.

The ARM report also has wording inconsistencies that must be cleaned before using it as deployment evidence. Its table reports predictive_rss with std(z)=0.93 and sigma/RMSE=0.99, while floor_only has std(z)=0.86 and sigma/RMSE=1.09; the prose simultaneously describes RSS as materially over-dispersed and floor-only as narrower even though the table’s mean sigma is larger for floor-only. docs/rebuild/arm_replay_report.md:5-8. 
GitHub
 That does not falsify the sigma choice, but it does mean the deployment note should not be treated as a clean statistical proof until the replay labels/columns are reconciled.

The q_lcb band coherence is correct. src/probability/joint_q_band.py asserts every draw row sums to one, perturbs center/sigma, and re-normalizes each draw through the same joint q construction. src/probability/joint_q_band.py:20-32. 
GitHub
+1
 This fixes the old invalid operation of taking marginal lower bounds independently and then pretending they remain a family distribution. The superior version is to sample from the full posterior over model weights, residual regimes, and calibration parameters, not just mu/sigma perturbations.

NO-as-basket is correct. src/probability/instruments.py defines NO as 1 - e_i, computes fair_no from the joint complement, and computes no_lcb from the complement of the per-draw joint samples. src/probability/instruments.py:3-8,20-28. 
GitHub
 The subtle point: on the same row-normalized sample matrix, quantile(1 - q_i, alpha) is mathematically the same as 1 - quantile(q_i, 1-alpha). The old bug was not the algebraic identity; it was using a broken/incoherent source matrix. The test captures that by proving basket semantics on band.samples and showing the failure on an unnormalized fake matrix. tests/probability/test_no_basket_semantics.py:16-36. 
GitHub

The neg-risk route math is directionally correct but not integrated safely. src/execution/negrisk_routes.py models direct/synthetic/arb routes and leaves conversion routes shadowed because no on-chain convert/merge/split primitive exists. src/execution/negrisk_routes.py:0-4,47-76. 
GitHub
+1
 That is the right route surface. It becomes unsafe when FamilyDecision.selected is mapped back to a single _CandidateProof without carrying route identity or leg list; see the live blocker in the findings.

The payoff-vector ΔU sizing is the right mathematical decision rule. src/decision/payoff_vector.py computes edge_lcb as the lower quantile of q_draw @ payoff_vector - cost, maps each q draw into the utility matrix including outside residual mass, and only passes live candidates with positive lower-bound edge, positive marginal utility, executable route, direction proof, and coherence. src/decision/payoff_vector.py:31-42,53-61. 
GitHub
+1
 That is strictly better than scalar q - price or fixed-% success gates. It remains correct only if the cost/payoff vector is the actual executable route and the current portfolio exposure is passed into selection.

3. VALIDATION SOUNDNESS

The ARM replay is a useful calibration sanity check, not a proof of positive after-cost EV.

It supports three claims: the center warm-bias bug is mostly gone, the q/modal probabilities are not grossly overclaiming, and the served sigma is in the right broad scale. The report says 762 settled families were available and 697 replayed, with high/low coverage of 604/93, center mean error around -0.510C, point-q modal predicted 0.304 versus realized 0.313, and q_lcb mean 0.194 versus realized 0.313. docs/rebuild/arm_replay_report.md:1-4. 
GitHub
 Those are good signs.

It does not prove edge. The report explicitly says after-cost EV-by-class is coverage-limited because the offline snapshot lacks the exact per-row bin label and therefore cannot settlement-grade each row’s candidate payoff. It records market-implied EV only. docs/rebuild/arm_replay_report.md:10-12,18-20; scripts/qkernel_arm_replay.py:20-29. 
GitHub
+1
 That gap is load-bearing. The success criterion is continuous positive after-cost settlement EV, not calibrated q in isolation.

The modal-bin “no over-claiming” argument is valid only as a reliability sanity check. The report’s modal calibration gap of roughly +0.009 means the model is not wildly claiming modal bins more often than they settle in that replay sample. docs/rebuild/arm_replay_report.md:13-15. 
GitHub
 It does not rule out base-rate favorite-buying. A model can be well calibrated on modal bins and still lose money if the market prices those modal bins efficiently or with a better margin than your q. Positive EV requires settlement-labeled, after-cost, quote-time comparison against the traded side and route.

4. WHERE IS THE GENUINE ALPHA?

With no latency or data-speed edge and airport/station settlement as ground truth, durable alpha can only come from four places.

First, settlement-semantics edge: exact station mapping, rounding/preimage handling, HK oracle_truncate, metric-specific max/min definitions, and day0 hard constraints. The new EventResolution and OutcomeSpace pieces are the right foundation here, including explicit settlement semantics and topology validation. src/probability/event_resolution.py:0-30; src/probability/outcome_space.py:0-9. 
GitHub
+1
 But the bridge currently uses _NoDay0Reader, so one of the most genuine intraday alpha sources is not actually live-wired. src/engine/qkernel_spine_bridge.py:44-46. 
GitHub

Second, calibration edge: better posterior predictive distribution than the market’s implicit distribution, especially in city/season/regime/lead cells where public forecasts are biased or under/overdispersed. The center and sigma changes are a credible first pass, but not yet a proven market-relative edge.

Third, microstructure/route edge: synthetic NO, complete-family basket economics, negative-risk direct/arb, and liquidation route optionality. The route set models these, but the integration bridge loses route identity, so the live engine may not actually capture the modeled edge. src/execution/negrisk_routes.py:47-76; src/engine/qkernel_spine_bridge.py:64-66. 
GitHub
+1

Fourth, market-incoherence avoidance: refusing deep q/market disagreements can prevent catastrophic model incidents, as market_coherence.py is designed to do. src/decision/market_coherence.py:0-5,35-59. 
GitHub
+1
 This is not alpha by itself; it is a kill switch for likely bad state.

Phantom alpha classes here are under-dispersed sigma, base-rate modal/favorite buying, scalar q-price profits without family payoff accounting, NO as 1 - q_lcb_yes from an incoherent draw matrix, unexecutable conversion routes, and midpoint/projected-market q when the book is incomplete.

5. PER-FILE / PER-STAGE FINDINGS

[BLOCKER] integration-route identity — src/engine/qkernel_spine_bridge.py:64-66 — impact: the engine can select a synthetic/arb route priced by negrisk_routes, but the bridge maps only (bin_id, side) back onto one _CandidateProof, so the unchanged submit path cannot execute the route whose payoff/cost generated the decision. — concrete fix: either restrict build_negrisk_route_set to direct native routes at the bridge until multi-leg submit exists, or introduce a route-intent object carrying route_id, legs, atomicity/fallback, expected cost, and receipt fields through RiskGuard and venue submission. — verify locally: pytest tests/integration/test_qkernel_spine_routing.py -q plus add a failing fixture where synthetic NO is cheaper than direct NO and assert the bridge does not return a single native proof for the synthetic route. 
GitHub
+2
GitHub
+2

[BLOCKER] live-vs-replay forecast-case mismatch — src/engine/qkernel_spine_bridge.py:29-31 — impact: the live bridge builds ForecastCase with lead_hours=0.0, season="", and regime_key="", while sigma floor lookup and ARM replay rely on city/season/metric/lead semantics; this can silently serve the wrong sigma floor or no floor. — concrete fix: compute the exact issue/target lead, season, regime, settlement source, and metric used by scripts/qkernel_arm_replay.py, and assert equality between live and replay case construction for the same snapshot. — verify locally: rg "lead_hours=0.0|season=\"\"|regime_key=\"\"" src/engine src/forecast scripts tests && pytest tests/integration/test_qkernel_spine_routing.py -q. 
GitHub
+2
GitHub
+2

[BLOCKER] day0 hard-fact gap at live seam — src/engine/qkernel_spine_bridge.py:44-46 — impact: _NoDay0Reader returns no observed extreme, so a same-day/day0 event can price physically impossible bins unless the qkernel branch is strictly forecast-only. — concrete fix: wire Day0ObservationState from the event payload/db into Day0Conditioner, or hard-block qkernel_spine_enabled for every non-pure-forecast event type until day0 is live. — verify locally: rg "DAY0|_FORECAST_DECISION_EVENT_TYPES|qkernel_spine_enabled|_NoDay0Reader" src/engine tests && add pytest that DAY0_EXTREME_UPDATED cannot select a bin below observed high / above observed low. 
GitHub
+2
GitHub
+2

[HIGH] validation/Ev proof gap — docs/rebuild/arm_replay_report.md:10-20 — impact: the replay proves center/q/sigma calibration sanity, not positive after-cost settlement EV by traded side/route/class, which is the only success criterion. — concrete fix: add condition_id→bin resolution to the snapshot replay, compute realized payoff by candidate side and route at decision-time executable cost, and report posterior EV by city/metric/lead/side/liquidity/route class with shrinkage to zero on thin cells. — verify locally: python scripts/qkernel_arm_replay.py after adding bin-label join; require a table of settlement-graded after-cost EV, not market-implied EV only. 
GitHub
+1

[HIGH] current exposure not in route selection — src/engine/qkernel_spine_bridge.py:58-63 and src/engine/event_reactor_adapter.py:192-193 — impact: the bridge constructs the decision engine with flat/empty extra exposure, so argmax ΔU can choose the wrong leg once the account already holds family risk; resizing after selection cannot repair a wrong instrument choice. — concrete fix: pass the real current PortfolioExposureVector/per-bin holdings into FamilyDecisionEngine.decide() before candidate scoring and selection. — verify locally: rg "extra_exposure_by_bin_id=None|flat baseline" src/engine src/decision tests and add a test where existing YES exposure makes the scalar-best/new-buy candidate negative marginal utility. 
GitHub
+1

[HIGH] receipt/downstream q semantics — src/engine/qkernel_spine_bridge.py:71-76 — impact: the bridge overlays q_posterior and trade_score but preserves the legacy _CandidateProof shape, including q_lcb_5pct; downstream receipt/revalidation can still read stale legacy q_lcb instead of spine edge_lcb/delta_u. — concrete fix: extend _CandidateProof or the submission intent with explicit spine_q_lcb, edge_lcb, delta_u, route_id, and make downstream gates ignore legacy q_lcb when decided_by_spine=True. — verify locally: rg "q_lcb_5pct|edge_lcb|delta_u|decided_by_spine" src/engine src/decision src/state tests. 
GitHub
+2
GitHub
+2

[HIGH] deploy runbook overstates non-blocking gaps — docs/rebuild/deploy_runbook.md:0-6 — impact: the runbook treats day0 no-op, no-floor cells, and after-cost EV replay coverage as watch items, but day0 and EV proof gaps are live-capital blockers for this mandate. — concrete fix: change runbook to “merge flag OFF; no live flip until bridge route identity, replay-equivalent case metadata, day0 gating, and settlement-graded EV replay are green.” — verify locally: sed -n '1,80p' docs/rebuild/deploy_runbook.md. 
GitHub

[MEDIUM] market-implied q projection on incomplete books — src/decision/market_coherence.py:35-59 — impact: unquoted bins are assigned zero before projection while depth/spread trust is computed only on quoted bins, which can create false coherence blocks or false comfort on incomplete markets. — concrete fix: emit NO_MARKET_Q/INSUFFICIENT_MARKET_DEPTH unless the candidate’s required bins, or preferably the full Ω, have sufficient two-sided quote coverage. — verify locally: pytest tests/decision/test_market_coherence.py -q plus add partial-book Ω coverage cases. 
GitHub

[MEDIUM] center estimator is safe but not alpha-optimal — src/forecast/center.py:11-18,34-45 — impact: equal-weight Huber/envelope-lock avoids stale calibrator disasters but leaves model-specific settlement skill unused. — concrete fix: train CRPS/log-score/MAE weighted center or Bayesian model averaging by city/metric/lead/season/regime, and keep the envelope lock only as an incident fallback. — verify locally: pytest tests/forecast/test_center_envelope.py -q and add walk-forward center comparison against equal-weight Huber. 
GitHub

[MEDIUM] sigma floor lacks exposed cell confidence — src/forecast/sigma_authority.py:26-32 — impact: the served floor artifact has no effective sample count/confidence and lookup appears city/season/metric-scoped, so thin or missing cells can be overtrusted. — concrete fix: return floor cell metadata (station, metric, lead bucket, season, regime, n, last_updated, shrinkage_target) and use posterior floor uncertainty in JointQBand sigma draws. — verify locally: rg "settlement_sigma_floor|n=0|SigmaArtifact" src/forecast src/calibration tests. 
GitHub

[MEDIUM] ARM report internal inconsistency — docs/rebuild/arm_replay_report.md:5-8 — impact: the sigma table/prose conflict makes the “RSS over-dispersed 1.94x / floor honest” claim hard to audit. — concrete fix: regenerate the report with clearly named columns for served_floor, predictive_rss, floor_only, realized z, RMSE, mean sigma, and exact replay config. — verify locally: python scripts/qkernel_arm_replay.py && git diff docs/rebuild/arm_replay_report.md. 
GitHub
+1

[MEDIUM] synthetic route tests do not cover bridge execution identity — tests/integration/test_qkernel_spine_routing.py:1-4 — impact: the smoke test asserts the spine returns a well-formed _CandidateProof, but that is exactly the problem for multi-leg route economics because well-formed native proof is insufficient. — concrete fix: add tests where the selected FamilyDecision route is synthetic/arb and assert the bridge either returns a multi-leg intent or refuses live selection. — verify locally: pytest tests/integration/test_qkernel_spine_routing.py tests/execution/test_negrisk_route_set.py -q. 
GitHub
+1

[LOW] predictive builder double-application coupling — src/forecast/predictive_distribution_builder.py:26-33 — impact: the builder calls build_center then reapplies DebiasAuthority to raw members for receipt/debiased vector, which is safe only if both paths stay bit-identical. — concrete fix: make build_center return the debiased members/applied debias artifact used for the center, and have the builder consume that single receipt object. — verify locally: pytest tests/forecast/test_single_predictive_distribution_authority.py tests/forecast/test_debias_authority.py -q. 
GitHub
+1

[LOW] unreachable legacy-mu fallback should be removed — src/engine/qkernel_spine_bridge.py:36-41 — impact: _served_predictive_inputs returns None when members are absent, but build_fresh_model_set still contains a fallback to mu; dead fallbacks are future legacy seams. — concrete fix: delete the fallback and assert members are non-empty at the function boundary. — verify locally: rg "fallback|mu" src/engine/qkernel_spine_bridge.py && pytest tests/integration/test_qkernel_spine_routing.py -q. 
GitHub

[LOW] unit-conversion audit in debias authority — src/forecast/debias_authority.py:8-17,40-41 — impact: the authority correctly refuses stale artifacts and applies residual_mean, but any use of absolute Celsius-to-Fahrenheit conversion for a shift/sigma would be wrong if _c_to_native is reused outside absolute forecasts. — concrete fix: separate absolute temperature conversion from delta/sigma conversion helpers and type the artifact fields as absolute-vs-delta. — verify locally: rg "_c_to_native|residual_mean|residual_sigma" src/forecast tests. 
GitHub

[LOW] no-trade schema version check may reject new provenance version — src/state/schema/no_trade_events_schema.py:6,12,23-25 — impact: SCHEMA_VERSION=55 while CREATE_TABLE_SQL accepts only versions 14..42; migration maps unknown versions to 36, so writers using 55 would fail or be downgraded. — concrete fix: confirm the actual writer never writes this module’s SCHEMA_VERSION, or extend the CHECK range and migration mapping to include 55. — verify locally: rg "no_trade_events_schema.SCHEMA_VERSION|schema_version" src tests | head -80 && pytest tests/decision/test_live_receipt_contract.py -q. 
GitHub
+1

[NIT] architecture fingerprint — architecture/_schema_fingerprint.txt:1 — impact: fingerprint changed as expected, but it is only provenance. — concrete fix: regenerate after final schema/runbook edits. — verify locally: git diff -- architecture/_schema_fingerprint.txt && make schema-fingerprint || true. 
GitHub

[LOW] config flag default — config/settings.json:82,266-436 — impact: setting qkernel_spine_enabled=false and edli_bias_correction_enabled=false is safe for merge, but the note’s “same _CandidateProof shape” is exactly why multi-leg route economics are unsafe when flag ON. — concrete fix: update the flag note to say direct-route-only until route-intent submit exists. — verify locally: jq '.feature_flags.qkernel_spine_enabled, .edli_bias_correction_enabled' config/settings.json. 
GitHub

[LOW] design docs consistency — docs/rebuild/consult_build_spec.md:44-47 — impact: the spec still describes max(sigma_before_floor,floor) while implementation serves floor-only when available. — concrete fix: amend the spec to distinguish original plan from replay-justified floor-only realization, with conditions under which RSS can re-enter. — verify locally: rg "max\\(sigma_before_floor|floor-only|RSS" docs/rebuild src/forecast. 
GitHub
+1

[LOW] ARM replay script scope — scripts/qkernel_arm_replay.py:0-4,20-29 — impact: script is explicitly read-only and calibration-only; using it as a deploy proof would exceed its stated scope. — concrete fix: split into calibration_replay and settlement_ev_replay, and make the deploy gate consume the latter. — verify locally: python scripts/qkernel_arm_replay.py --help || python scripts/qkernel_arm_replay.py. 
GitHub
+1

[LOW] src/decision/__init__.py:1 — impact: package marker only; no substantive defect found from the diff surface. — concrete fix: no change unless exports are intentionally public API. — verify locally: python - <<'PY'\nimport src.decision\nprint(src.decision)\nPY. 
GitHub

[LOW] src/decision/decision_receipt.py:0-14 — impact: receipt fields are reconstructable, but route/payoff/edge fields remain optional/null until later wiring, which weakens live auditability at the exact cutover. — concrete fix: require non-null route_id, edge_lcb, delta_u, q_band_basis, and payoff_vector_hash whenever decided_by_spine=True. — verify locally: pytest tests/decision/test_live_receipt_contract.py -q. 
GitHub
+1

[LOW] src/decision/family_decision_engine.py:47-56 — impact: orchestrator order is correct, but direction law forbids buying underpriced non-modal YES bins even when the vector EV is positive; that is an operator-law trade-off, not a math optimum. — concrete fix: keep direction law because the operator requires it, but report rejected positive-EV non-modal opportunities separately for evidence. — verify locally: pytest tests/decision/test_family_decision_engine.py -q. 
GitHub
+1

[LOW] src/decision/payoff_vector.py:31-61 — impact: vector edge/sizing math is correct, but only if route cost/payoff and exposure are live-accurate. — concrete fix: bind CandidateRoute.route_id and cost hash into the decision receipt and submit intent. — verify locally: pytest tests/decision/test_payoff_vector_edge.py tests/decision/test_vector_sizing_authority.py -q. 
GitHub
+1

[LOW] src/execution/family_book.py:2-36 — impact: complete-book and native executable cost surface are structurally right. — concrete fix: no math change; add integration assertion that complete-book identity survives from market topology to _CandidateProof mapping. — verify locally: pytest tests/execution/test_family_book.py -q. 
GitHub

[LOW] src/execution/liquidation_value.py:5-10 — impact: exit value as max of direct sell, conversion, and hold is correct, but Stage 10 is not yet a live reactor exit authority. — concrete fix: wire liquidation value into exit monitor only after route-intent execution is present. — verify locally: pytest tests/execution/test_liquidation_value_engine.py -q && rg "liquidation_value" src/engine src/execution. 
GitHub

[LOW] src/execution/negrisk_routes.py:65-69 — impact: conversion route shadowing is acceptable only while the on-chain primitive is absent; it must not become permanent shadow alpha. — concrete fix: either implement conversion/merge/split execution or remove conversion from live route consideration entirely. — verify locally: pytest tests/execution/test_negrisk_route_set.py -q. 
GitHub

[LOW] src/forecast/day0_conditioner.py:31-33 — impact: conditioner math is correct, but it is not used at the bridge because the bridge reader returns no observation. — concrete fix: wire observed running high/low into the bridge. — verify locally: pytest tests/forecast/test_day0_extreme_conditioner.py -q. 
GitHub
+1

[LOW] src/forecast/types.py:1 — impact: type module is part of the right authority split; no independent defect found from the reviewed call sites. — concrete fix: ensure ForecastCase requires non-empty season/regime/lead when live_eligible=True. — verify locally: python -m pytest tests/forecast -q && rg "ForecastCase\\(" src tests. 
GitHub

[LOW] src/probability/__init__.py:1 — impact: package marker only; no substantive defect found. — concrete fix: no change. — verify locally: python - <<'PY'\nimport src.probability\nprint(src.probability)\nPY. 
GitHub

[LOW] src/probability/event_resolution.py:0-30 — impact: settlement semantics, rounding, and HK truncation are the correct root authority. — concrete fix: add regression fixtures for every live city with known station/source and rounding mode. — verify locally: pytest tests/probability/test_settlement_preimage_threading.py tests/probability/test_outcome_space_contract.py -q. 
GitHub

[LOW] src/probability/outcome_space.py:0-9 — impact: MECE/topology validation is the right invariant and should remain hard fail-closed. — concrete fix: no soft repair of invalid topology; keep set-equality checks. — verify locally: pytest tests/probability/test_outcome_space_contract.py -q. 
GitHub

[LOW] src/probability/joint_q.py:22-30 — impact: one normalized q over complete Ω is correct. — concrete fix: expose q-sum and topology hash in live receipts for every submitted order. — verify locally: pytest tests/probability/test_joint_q.py tests/probability/test_settlement_preimage_threading.py -q. 
GitHub

[LOW] src/probability/joint_q_band.py:20-32 — impact: per-draw row-simplex renormalization is correct; structural posterior uncertainty is still thin. — concrete fix: sample model weights/residual regimes, not just center/sigma perturbations. — verify locally: pytest tests/probability/test_joint_q_band.py -q. 
GitHub
+1

[LOW] src/probability/instruments.py:20-28 — impact: YES/NO instrument semantics are correct. — concrete fix: keep tests phrased around row-normalized samples to avoid resurrecting the old NO bug. — verify locally: pytest tests/probability/test_no_basket_semantics.py -q. 
GitHub
+1

[LOW] src/state/db_writer_lock.py:1 — impact: no q-kernel math defect found from the diff surface; ensure it remains an operational writer lock only. — concrete fix: no change. — verify locally: pytest tests -q -k db_writer_lock. 
GitHub

[LOW] tests are mostly adversarial at unit level — tests/forecast/test_sigma_authority.py:19-22, tests/probability/test_joint_q_band.py:13-28, tests/decision/test_payoff_vector_edge.py:11-24 — impact: the RED-on-revert tests for sigma floor, q-band simplex, and vector payoff are meaningful, not trivial. — concrete fix: keep them, but add integration tests for route identity, day0, exposure, and replay metadata. — verify locally: pytest tests/forecast/test_sigma_authority.py tests/probability/test_joint_q_band.py tests/decision/test_payoff_vector_edge.py -q. 
GitHub
+2
GitHub
+2

[LOW] implementation-note docs — docs/rebuild/impl_stage0_producer.md through docs/rebuild/impl_w5b_integration.md — impact: these docs should be treated as design/implementation notes, not independent proof; any note claiming live readiness must be reconciled with the bridge blockers above. — concrete fix: add a post-review erratum section linking the four live blockers and required local tests. — verify locally: rg "live|non-blocking|same _CandidateProof|route_id|day0|floor" docs/rebuild. 
GitHub
+1

[LOW] evidence/ledger docs — docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md and docs/rebuild/q_engine_violation_ledger.md — impact: useful for drift accounting, but should not be used as EV proof. — concrete fix: distinguish “invariant fixed” from “settlement EV proven” in the ledgers. — verify locally: rg "EV|after-cost|settlement|proof|validated" docs/evidence/qkernel_rebuild docs/rebuild/q_engine_violation_ledger.md. 
GitHub

[LOW] remaining test files — tests/decision/test_market_coherence.py, tests/decision/test_vector_sizing_authority.py, tests/execution/test_family_book.py, tests/execution/test_liquidation_value_engine.py, tests/execution/test_negrisk_route_set.py, tests/forecast/test_center_envelope.py, tests/forecast/test_day0_extreme_conditioner.py, tests/forecast/test_debias_authority.py, tests/forecast/test_single_predictive_distribution_authority.py, tests/probability/test_joint_q.py, tests/probability/test_outcome_space_contract.py, tests/probability/test_settlement_preimage_threading.py — impact: the test surface is broad, but the missing adversarial seams are live bridge route execution, day0 event eligibility, exposure-aware selection, and replay/live metadata equivalence. — concrete fix: add those four integration tests before live flip. — verify locally: pytest tests/decision tests/execution tests/forecast tests/probability tests/integration -q. 
GitHub
+1

6. IDEAL-vs-ACTUAL DIFFERENCE + SUPERIOR REALIZATIONS, RANKED BY EV LEVERAGE

Ideal: settlement-graded edge proof. Actual: calibration replay only. The ARM replay supports center/q/sigma sanity, but it does not prove fillable, after-cost, settlement EV by class. This is the highest leverage gap because a calibrated q with no market-relative edge still loses. Upgrade: condition_id→bin join, quote-time cost, realized settlement payoff, and Bayesian shrinkage by route/side/city/metric/lead class. 
GitHub
+1

Ideal: executable route-intent optimizer. Actual: vector route selected, single _CandidateProof submitted. The payoff-vector economics are correct only if the selected route is the submitted route. Upgrade: live route-intent object and atomic/multi-leg execution; until then, direct-route-only bridge. 
GitHub
+1

Ideal: replay/live identical forecast case. Actual: live bridge uses blank season/regime and zero lead. This can make the replay’s sigma conclusion irrelevant to live. Upgrade: one shared ForecastCaseFactory used by ARM replay and reactor bridge. 
GitHub
+1

Ideal: day0 hard-fact conditioning live wherever same-day state exists. Actual: _NoDay0Reader. Day0 observed extrema are genuine durable alpha and hard constraints. Upgrade: wire day0 state or block qkernel on day0 events. 
GitHub
+1

Ideal: posterior predictive belief. Actual: envelope-locked equal-weight center plus floor sigma. The current belief is a robust safety transform, not a fully learned settlement posterior. Upgrade: CRPS/log-score-trained hierarchical EMOS/BMA with posterior draws of q. 
GitHub
+1

Ideal: exposure-aware family selection. Actual: flat baseline passed into selection. If the account holds positions, selecting before exposure adjustment can choose the wrong leg. Upgrade: pass live holdings/exposures into the utility matrix before candidate scoring. 
GitHub
+1

Ideal: market coherence as a reliable incident detector. Actual: projected q from partial books can be brittle. Upgrade: require quote coverage/depth on required Ω before blocking on market-implied q. 
GitHub

Ideal: receipts are reconstructable decision proofs. Actual: route/payoff/edge fields can remain null or legacy-shaped. Upgrade: make non-null spine receipt fields mandatory when a spine-selected candidate reaches submit. 
GitHub
+1

Operator-law status: the rebuild mostly respects “fix the transform, don’t cap it,” “settlement is truth,” “direction law,” and HK oracle_truncate. The violations/near-violations are operational: treating EV coverage gaps as non-blocking violates settlement-truth discipline for live capital; leaving conversion routes shadow forever would violate the no-permanent-shadow-lane law; mapping route economics into one native proof violates Arrow-Debreu/neg-risk economic truth. 
GitHub
+2
GitHub
+2

7. GO / NO-GO BEFORE FLIPPING LIVE

NO-GO for live money behind qkernel_spine_enabled as currently realized. Merge behind default false is defensible; flipping live submit is not.

Blockers before live flip:

Route identity/execution: direct-route-only bridge or real multi-leg route-intent submit.

Replay/live forecast-case equivalence: fix lead_hours, season, regime_key, settlement source, and sigma floor metadata at the bridge.

Day0 gating/wiring: no _NoDay0Reader on any path that can trade same-day/day0-constrained markets.

Settlement-graded EV replay: prove after-cost EV by actual candidate side/route/bin class, not only center/q/sigma calibration.

Exposure-aware selection: pass real family exposure before ΔU argmax.

Spine receipt semantics: submitted orders must carry route_id, payoff hash, edge_lcb, delta_u, q_band basis, and market-implied q used by the decision.

Highest-value local checks:

rg "lead_hours=0.0|season=\"\"|regime_key=\"\"|_NoDay0Reader|extra_exposure_by_bin_id=None|q_lcb_5pct|route_id|edge_lcb|delta_u|decided_by_spine" src tests

pytest tests/integration/test_qkernel_spine_routing.py tests/decision/test_family_decision_engine.py tests/decision/test_payoff_vector_edge.py tests/probability/test_joint_q_band.py tests/probability/test_no_basket_semantics.py tests/forecast/test_sigma_authority.py -q

Add four RED tests before any live flip: synthetic route selected by engine cannot return a single native proof; day0 observed extreme blocks impossible bins through the bridge; bridge ForecastCase equals ARM replay case metadata for the same snapshot; existing family exposure changes the selected ΔU winner.

Sources I used: PR description/discussion and commit/file list; docs/rebuild/consult_build_spec.md; docs/rebuild/arm_replay_report.md; docs/rebuild/deploy_runbook.md; scripts/qkernel_arm_replay.py; the changed src/forecast, src/probability, src/execution, src/decision, src/engine, and src/state/schema files opened above; and the unit/integration tests cited inline. GitHub’s PR UI showed inline file comments/conversation loading errors, so I did not rely on unloaded inline review comments. 
GitHub
+2
GitHub
+2

Load-bearing assumptions: I assume the raw branch files reflect the current PR head; I did not execute the repository locally; and I treat the user’s “settlement-graded positive after-cost EV” standard as binding over the runbook’s “watch first fill” posture.