# Over-engineering simplification plan (2026-06-12)

Authority: operator law 2026-06-12 ("不允许设置任何的cap，实际上消除我系统中的过度设计…
你们要做的事是完善该具体设计并且朝积极方面出发" + "现在就解除这些限制和乱七八糟的gate")
+ ChatGPT Pro consult REQ-20260612-094905-f452ca (/tmp/cgc_answer_REQ-20260612-094905-f452ca.txt,
485-line audit, GitHub-evidence-cited) + local verification (/tmp/flag_gate_inventory.md, haiku;
code-gate existence re-probed by orchestrator — haiku's NOT_FOUND verdicts on code gates were
WRONG, all four exist by exact name).

K structural decisions (consult Section A, locally endorsed):
- K1 single belief/q authority (no second LCB/cap/veto outside the q certificate)
- K2 identity-bound book certificate (wall #5 landed 65061974b6 is the start)
- K3 transient-vs-terminal taxonomy with NO attempt caps
- K4 single final-intent/mode authority (receipt layer must not be a second brain)
- K5 boot quarantines entries, never kills the daemon (only schema/identity corruption is fatal)
- K6 queue fairness is algorithmic (fair cursor + backlog), never a numeric cap

## Wave 1 — covered by the operator's blanket word (artificial throttles; execute)

| Item | Location (verified) | Action |
|---|---|---|
| MAX_EXECUTABLE_SNAPSHOT_RETRIES = 8 | src/events/reactor.py:286 | DELETE attempt-cap terminalization; terminal only on event horizon (timeliness floor / market delisted / source identity invalid). Attempt count is not a market fact. |
| forecast_sharpness_gate_enabled (false) | settings + src/strategy/market_analysis.py:386 | DELETE flag + gate code (proven 50/54-city zero-trade bomb; documented in PR #406) |
| live_canary_enabled / edli_live_min_canary_count / edli_live_promotion_artifact_required | src/main.py:600,689,792 | DELETE canary/artifact machinery; operator arm = the only gate |
| edli_arm_gate_artifact_required, k1_persist_presubmit_snapshot_enabled | settings only (DEAD keys) | DELETE keys; presubmit witness persistence becomes unconditional fail-soft |
| no_submit_proof_limit (250) | src/main.py:689 area | DELETE production cap (visibility caps hide blockers); display pagination only |
| redecision_max_per_cycle (200) | src/events/continuous_redecision.py:449 | REPLACE with fair round-robin cursor + persistent backlog (50→200 was a bigger landmine, not a fix) |
| coverage_fairness_emit_enabled (true) | src/events/triggers/forecast_snapshot_ready.py:69 | DELETE flag, fairness unconditional; delete OFF branch |
| market_substrate_refresh_enabled (true) | src/main.py:5950 | DELETE flag, always-on (freshness is correctness, not policy) |
| mainstream_agreement_reference_enabled (true) | src/engine/event_reactor_adapter.py:9202 | DELETE flag, always annotate (reference-only metadata needs no flag) |
| redecision_continuous_enabled + redecision_screen_enabled | src/events/continuous_redecision.py:442,445 | MERGE to one always-on-when-armed organ (the fill-rate organ is not a feature) |
| _check_s1_without_s2_sla as runtime tripwire | src/main.py:5187 | MOVE to deploy/CI check; runtime = critical alert + entry quarantine, never daemon death |
| Boot wallet / source-health / warm-budget fatalities | src/main.py (startup checks) | CONVERT to entry-quarantine + degraded health; only schema/DB-identity mismatch stays fatal (K5) |
| _is_transient_money_path_reason string-contains | src/events/reactor.py:1546 | REPLACE with typed reason enum carrying transient/terminal at emission site |

## Wave 2 — strategy-math/behavior changes (operator decision per item)

| Item | Current | Question for operator |
|---|---|---|
| Baseline LCB cap (min(baseline, replacement)) in live path | active | Remove legacy baseline as a live veto (diagnostics only)? Changes live q. |
| replacement_q_market_anchor_enabled (true) | flag | Internalize the anchor into the q materializer (no flag)? It was an interim antibody; σ fitted artifact now live — re-evaluate need. |
| replacement_0_1_fused_q_shape_enabled (true) | flag | Always-on (strategy of record) + delete soft-anchor fallback live-submit path? |
| replacement_qlcb_settlement_sigma_floor_enabled (false) + edli_settlement_sigma_floor_required (false) | two flags, one concept | Merge to ONE q_lcb construction rule inside the certificate? |
| bias_treatment_v2_enabled (false) / replacement_0_1_eb_bias_correction_enabled (false, settlement-refuted) | flags off | Delete refuted branches outright? |
| CANONICAL_EXIT_PATH (false) / HOLD_VALUE_EXIT_COSTS (false) / exit_bias_family_unify_enabled (false) | legacy exit live | Make canonical cost-aware exit always-on and delete legacy branch? Needs verification pass; kills exit twin-authority. |
| Receipt second-brain merge (_receipt_money_path_blocker re-checks trade score/Kelly/FDR/LCB/capital-efficiency) | reactor.py:1484 | Big refactor: final intent cannot exist unless invariants hold; reactor checks identity + side-effect legality only. Multi-day. |
| taker_fok_fak_live_enabled (true) | flag | Fold taker law into final-intent policy, delete flag? |

## Wave 1 additions (second consult extraction, operator-pasted; verified locally)

| Item | Location (verified) | Action |
|---|---|---|
| day0 family notional cap $25 | adapter:12658-12671 (_DAY0_FAMILY_NOTIONAL_CAP_DEFAULT_USD=25.0, consumed ~2761) | DELETE — direct no-caps-law violation; sizing = q_lcb + Kelly + portfolio only. Added to Wave1B scope. |
| canary_force_taker | adapter:1292 param + ~1590 call chain | DELETE — mode-authority bypass; proof rest_then_cross is the single mode authority. Added to Wave1B scope. |

## Follow-ups (next wave, not in flight)

- _transient_requeue_reasons is in-memory (reactor): restart loses transient reason/class. Persist
  disposition+reason on the event row (consult K3 "EventProcessingDisposition"). Schema change —
  sequence after Wave1A lands.
- edli_live_scope full deletion (forecast_only/day0_shadow branches die; admission = event type +
  source truth). The 13:25Z regret rows show DAY0_SCOPE_SHADOW_ONLY rejections right up to the flip —
  the scope string is a standing purgatory.
- Micro-position hold <$1 hardcoded (portfolio exit) → venue min-order truth.
- pre_submit_balance_allowance_check_enabled / durable_submit_outbox_enabled / edli_source_run_dual_chain_enabled:
  delete flag, behavior always-on (venue reality is not optional).
- no_submit_visible_depth_fill_lcb second fill-LCB bar → merge into one executable cost/fill model (Wave 2 K1/K6).
- Boot quarantine conversions detail per consult: frozen_as_of fatal → cell-level calibration quarantine;
  venue heartbeat raise → unhealthy mark + continue; stage-file staleness → WAITING readiness.

## Honest gates that STAY (consult concurs)
FSR structural completeness; day0 hard-fact source/station/DST/rounding eligibility;
source_truth_gate narrowed to structural identity; COMMAND_CREATED-in-no-submit invariant;
position native-side invariant; NC-09 complement carve-out as PlacementBound type;
schema/DB-registry fail-fast; day0 oracle anomaly pause; stale-quote cancel (deadline from
book certificate, not config ms).

Execution note: Wave 1 dispatch AFTER the fill-bridge quarantine agent lands (main.py collision).
