# May 2 Strategy Update Execution Plan

Created: 2026-05-02
Status: planning packet, no implementation performed
Route: `topology_doctor --navigation --route-card-only` admitted this file as a T1 `plan_packet`

## Purpose

Turn the May 2 strategy review into an execution sequence that survives context compaction and prevents the review artifact from becoming false authority.

Primary review artifact: [`docs/artifacts/Zeus_May2_review_ strategy_update.md`](../../artifacts/Zeus_May2_review_%20strategy_update.md)

The review's controlling verdict is that the draft strategy plan is `REVISED`, not accepted; Zeus should launch with a smaller, stricter, phase-gated, executable-snapshot-authorized live portfolio while shadowing inverse/tail expansion and deferring price-drift/family-relative strategies until evidence is clean. Source lines: [`L1-L40`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1-L40).

## Non-Negotiable Constraints

1. Treat `STRATEGIES_AND_GAPS.md` as hypothesis, not authority. Evidence lock comes before strategy changes because stale or draft code can otherwise become false authority. Source lines: [`L471-L483`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L471-L483).
2. Do not expand live strategy surface before live safety preconditions are verified: riskguard P0 and the 15-minute `opening_hunt` restart now require receipt verification rather than fresh implementation, while `$5` caps remain mandatory until bankroll truth is fixed. Source lines: [`L485-L488`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L485-L488); current drift evidence: [`REMAINING_TASKS.md#L16-L21`](../task_2026-05-02_full_launch_audit/REMAINING_TASKS.md#L16-L21).
3. Lock strategy taxonomy before any dormant wiring. Unknown fallback live must be blocked; four economic quadrants belong in shadow/reporting first. Source lines: [`L490-L493`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L490-L493).
4. Executable quote authority is mandatory before live decisions. Posterior edge alone is insufficient; the review names executable economics divergence as the biggest live blocker. Source lines: [`L33-L39`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L33-L39), [`L563-L591`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L563-L591).
5. Exit/hold policy must precede Day0 widening, because wider Day0 creates positions whose best action may be hold/redeem, not sell. Source lines: [`L475-L498`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L475-L498), [`L702-L734`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L702-L734).
6. Reporting cohorts must be clean before promotion. Shadow, diagnostic, live canary, backtest, and settlement evidence have different promotion value. Source lines: [`L832-L903`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L832-L903).

## Critic Review Amendments

Critic review was run before implementation and found that the plan is directionally right but under-specifies several repo-drift and authority-split risks. These amendments are mandatory before closing Stage 0 or starting Stage 1.

1. Add a current repo reconciliation gate to Stage 0. It must capture branch/ref, dirty/untracked files, PR/merge status, current-fact invalidations, and which review artifacts are evidence only.
2. Split strategy authority into separate surfaces instead of saying "the catalog matches code": `KNOWN_STRATEGIES`, `CANONICAL_STRATEGY_KEYS`, `LIVE_SAFE_STRATEGIES`, `_LIVE_ALLOWED_STRATEGIES`, `STRATEGY_KELLY_MULTIPLIERS`, DB CHECK constraints, edge-observation/reporting keys, and attribution-drift classifier keys.
3. Treat the current live authority split as a Stage 0/1 blocker. `LIVE_SAFE_STRATEGIES` still includes `shoulder_sell`, while `_LIVE_ALLOWED_STRATEGIES` excludes it and Kelly gives it zero live size. Evidence: [`control_plane.py#L37-L53`](../../../src/control/control_plane.py#L37-L53), [`control_plane.py#L316-L323`](../../../src/control/control_plane.py#L316-L323), [`kelly.py#L72-L82`](../../../src/strategy/kelly.py#L72-L82).
4. Audit every classifier surface, not only final live submission. Fallback labels still exist in `cycle_runner._classify_edge_source`, `evaluator._edge_source_for`, and `evaluator._strategy_key_for_hypothesis`; those can contaminate shadow/reporting evidence even when live intent fails closed. Evidence: [`cycle_runner.py#L317-L338`](../../../src/engine/cycle_runner.py#L317-L338), [`evaluator.py#L727-L758`](../../../src/engine/evaluator.py#L727-L758).
5. Pull minimal reporting/normalization requirements forward. Stage 4 can own full reporting, but Stage 0/1 must prove that `discovery_mode`, direction, bin role, phase, execution mode, and shadow/live status can be represented before taxonomy acceptance. Existing reporting still mirrors the four legacy keys and has recall limits. Evidence: [`edge_observation.py#L40-L42`](../../../src/state/edge_observation.py#L40-L42), [`attribution_drift.py#L25-L38`](../../../src/state/attribution_drift.py#L25-L38).
6. Reconcile current-fact contradictions before strategy code. `current_state.md` may lag the actual branch, and `current_data_state.md` can conflict with live harvester enablement now recorded in `REMAINING_TASKS.md`. Any invalidated current-fact surface must be marked stale or refreshed before it supports strategy planning.
7. Add source-contract quarantine to Stage 1/2 live gates. Paris remains a current source-contract caution path; phase-correct strategy logic must still block city/date/metric candidates under source quarantine.
8. Change Stage 3 wording from "add per-strategy multipliers" to "audit existing per-strategy multipliers and add only missing phase/liquidity/time-to-resolution dimensions." Per-strategy multipliers already exist in [`kelly.py#L72-L82`](../../../src/strategy/kelly.py#L72-L82).

## Execution Order

The review's final implementation order is the spine for this plan: Stage 0 evidence/catalog truth, Stage 1 critical live blockers, Stage 2 minimal portfolio, Stage 3 sizing/exit/killswitch integration, Stage 4 promotion/reporting evidence, Stage 5 real-market improvement, Stage 6 dormant/out-of-scope routing. Source lines: [`L515-L523`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L515-L523).

## Stage 0 — Evidence Lock / Catalog Truth

Objective: verify actual strategy keys, modes, feature flags, docs, and branch truth; prevent the draft from becoming false authority. Source lines: [`L926-L939`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L926-L939).

Do:

0. Run the current repo reconciliation gate: record branch/ref, dirty/untracked files, relevant PR/merge state, current-fact invalidations, and artifact/evidence status.
1. Create or update the launch-audit evidence section in the existing full-launch audit packet, not in authority docs.
2. Assert the current strategy authority surfaces separately: buildable keys, runtime canonical keys, boot live-safe keys, runtime live-allowed keys, sizing live multiplier keys, DB CHECK constraints, edge-observation keys, attribution-drift classifier keys, and reportable keys.
3. Explicitly separate live, shadow, dormant, diagnostic, risk-management, and exit-policy roles.
4. Record feature-flag truth, especially native multi-bin buy-NO shadow/live flags.
5. Verify resolved receipts for riskguard P0 and `opening_hunt` 15-minute cadence; keep bankroll truth as unresolved structural debt and preserve `$5` caps.
6. Record current branch/ref and note that review artifacts are evidence, not law.
7. Add or update static/relationship tests proving docs' live catalog, feature flags, and runtime authority surfaces cannot drift silently.

Review references:

- Review says current four keys are code-confirmed but not unconditional live-active, and `shoulder_sell` should be shadow-only until native NO evidence is promoted: [`L7-L9`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L7-L9).
- Final strategy taxonomy: `center_buy`, `opening_inertia`, `settlement_capture` live under phase gates; `shoulder_sell` shadow; `shoulder_buy` and `center_sell` shadow after redesign; `middle_state_recheck` diagnostic; `price_drift_reaction` shadow post-launch; `risk_off_exit` live risk-management; `settlement_hold` launch-required exit policy: [`L529-L541`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L529-L541).
- Stage 0 file/test/rollback envelope: [`L926-L939`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L926-L939).

Likely files:

- `docs/operations/task_2026-05-02_full_launch_audit/STRATEGIES_AND_GAPS.md`
- `docs/operations/task_2026-05-02_full_launch_audit/REMAINING_TASKS.md`
- `tests/test_architecture_contracts.py` or a dedicated strategy-catalog static test

Verification:

```bash
pytest tests/test_architecture_contracts.py tests/test_edge_observation.py tests/test_attribution_drift.py
```

Exit criteria:

- Strategy catalog accepted: code, docs, reports, and feature flags agree on live/shadow/dormant keys; unknown keys quarantine. Source lines: [`L1459-L1463`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1459-L1463).

Rollback:

- Revert docs/test additions only. Source lines: [`L937-L939`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L937-L939).

## Stage 1 — Critical Strategy Live Blockers

Objective: prevent wrong live trade, wrong phase, wrong sizing, wrong exit, and unsafe killswitch behavior. Source lines: [`L941-L954`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L941-L954).

Do:

1. Enforce no live unknown/fallback quadrant.
2. Audit and reconcile every classifier surface: `_classify_edge_source`, `_edge_source_for`, `_strategy_key_for`, `_strategy_key_for_hypothesis`, `cycle_runtime` strategy resolution, DB writes, edge observation, attribution drift, and reports.
3. Block native buy-NO live unless the live flag and required evidence gates are explicitly true.
4. Keep `shoulder_sell` shadow-only by default unless a later promotion packet reconciles all live authority surfaces.
5. Require executable snapshot/reprice before live order intent.
6. Add source-contract quarantine checks by city/date/metric; Paris must remain blocked for new entries until conversion release evidence is complete.
7. Add phase tags where needed so a candidate cannot be live merely because its strategy key exists.
8. Preserve rollback through the existing runtime-live allowlist. A new negative taxonomy flag is rejected for Stage 1 because it would create a second authority surface; `_LIVE_ALLOWED_STRATEGIES` plus `is_strategy_enabled()` is the rollback boundary that keeps the existing three live-allowed keys while blocking `shoulder_sell` and dormant inverse taxonomy.

Review references:

- Missing dependency: strategy taxonomy before dormant wiring, executable snapshot authority before live decisions: [`L471-L476`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L471-L476).
- Strategy taxonomy and routing step: block unknown fallback live, distinguish four economic quadrants in shadow, report cohorts by key/phase/direction/bin role: [`L490-L493`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L490-L493).
- Eligibility gates require live-allowed strategy key, phase compatibility, forecast/observation/quote/native-NO/order-policy/sizing/exit/killswitch/reporting proofs: [`L576-L591`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L576-L591).
- Stage 1 packet envelope: [`L941-L954`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L941-L954).

Likely files:

- `src/engine/evaluator.py`
- `src/engine/cycle_runner.py`
- `src/engine/cycle_runtime.py`
- `src/strategy/market_analysis.py`
- `src/execution/exit_triggers.py`
- `config/settings.json`
- strategy/evaluator/executable-price tests

Verification:

```bash
pytest tests/test_strategy*.py tests/test_execution*.py tests/test_ws_poll_reaction.py
```

Exit criteria:

- Active launch strategy accepted: `settlement_capture`, `center_buy`, and `opening_inertia` only live under phase/executable gates; `shoulder_sell` shadow unless native NO live is promoted. Source lines: [`L1462-L1464`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1462-L1464).

Rollback:

- Re-block new taxonomy by keeping `_LIVE_ALLOWED_STRATEGIES == {settlement_capture, center_buy, opening_inertia}` and using `set_strategy_gate` only to disable those already-live keys. Do not add a negative `DISABLE_NEW_TAXONOMY` flag; critic review found that a second flag surface is more live-open than the existing control-plane boundary. Source lines: [`L952-L954`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L952-L954).

## Stage 2 — Minimal Launch Strategy Portfolio

Objective: define and enforce the final live/shadow/dormant set. Source lines: [`L956-L969`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L956-L969).

Do:

1. Encode the launch matrix: live `settlement_capture`, `center_buy`, `opening_inertia`; shadow `shoulder_sell`; dormant redesign for `shoulder_buy` and `center_sell`; diagnostic `middle_state_recheck`; not-now `price_drift_reaction` and family-relative mispricing.
2. Gate `settlement_capture` by authorized Day0 observation evidence; do not turn Day0 forecast-convergence into live alpha by default.
3. Gate `center_buy` by fresh forecast-update phase.
4. Gate `opening_inertia` by opening phase only.
5. Prove shadow strategies produce no execution intent.

Review references:

- Best final launch portfolio table: [`L17-L31`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L17-L31).
- Market phase taxonomy and allowed live alpha: [`L543-L554`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L543-L554).
- Minimal launch-safe strategy portfolio with gates/tests/rollback: [`L595-L609`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L595-L609).
- Stage 2 packet envelope: [`L956-L969`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L956-L969).

Likely files:

- Strategy policy config/code
- evaluator classifier and phase gates
- discovery mode docs/tests
- reporting docs

Verification:

```bash
pytest tests/test_evaluator*.py tests/test_riskguard.py tests/test_db.py
```

Exit criteria:

- Shadow strategy accepted: live size zero, executable snapshot evidence, separate report cohort, no live command rows. Source lines: [`L1464-L1465`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1464-L1465).
- Dormant strategy safe: no runtime live path, no fallback live attribution, explicit revisit gate. Source lines: [`L1465-L1466`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1465-L1466).

Rollback:

- Pause all alpha except `settlement_capture`. Source lines: [`L967-L969`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L967-L969).

## Stage 3 — Sizing / Exit / Killswitch Integration

Objective: make sizing, exit, and killswitch strategy/phase-aware without overbuilding. Source lines: [`L971-L984`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L971-L984).

Do:

1. Keep fractional Kelly as the core sizing engine.
2. Audit existing per-strategy multipliers first, then add only missing phase/liquidity/time-to-resolution dimensions; do not create a parallel second sizing layer.
3. Require executable price/depth/fee/spread/tick/min-order constraints for sizing.
4. Implement or formalize settlement-hold policy using held-token bid/depth versus expected redemption/hold value.
5. Add local killswitch layers: strategy, market-family, city/date/metric, adapter/execution, position lot.
6. Emit operator-visible reasons for local blocks.
7. Ensure RED and DATA_DEGRADED behavior remains consistent with risk law.

Review references:

- Sizing verdict: Kelly core remains, but strategy/phase/liquidity/time-to-resolution multipliers are required; `$5` caps remain load-bearing until bankroll truth and riskguard stability are fixed: [`L11-L15`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L11-L15).
- Best sizing design and required inputs: [`L627-L645`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L627-L645).
- Per-strategy sizing rules: [`L646-L659`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L646-L659).
- Liquidity/depth/spread/fee constraints and sizing tests: [`L661-L686`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L661-L686).
- Exit design: position-lot-specific first, risk-state-specific second, strategy-informed third; held-token bid/depth, fresh snapshot, and command truth required: [`L702-L734`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L702-L734).
- Exit tests: [`L736-L746`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L736-L746).
- Killswitch architecture, required triggers, and tests: [`L752-L800`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L752-L800).
- Stage 3 packet envelope: [`L971-L984`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L971-L984).

Likely files:

- `src/strategy/kelly.py`
- `src/strategy/risk_limits.py`
- `src/execution/exit_triggers.py`
- `src/execution/exit_lifecycle.py`
- `src/engine/monitor_refresh.py`
- risk policy config/tests

Verification:

```bash
pytest tests/test_riskguard.py tests/test_pnl_flow_and_audit.py tests/test_exit*.py
```

Exit criteria:

- Sizing accepted: typed executable price, fee/depth/slippage/tick, strategy/phase multipliers, `$5` cap retained. Source lines: [`L1466-L1467`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1466-L1467).
- Exit accepted: held-token bid/depth, lot-specific exit, hold-to-resolution policy, RED override, command truth. Source lines: [`L1467-L1468`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1467-L1468).
- Killswitch accepted: global and local layers, strategy/family/city/metric/adapter/position gates, audited operator override. Source lines: [`L1468-L1469`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1468-L1469).

Rollback:

- Revert to generic cap with all buy-NO live blocked. Source lines: [`L982-L984`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L982-L984).

## Stage 4 — Promotion / Reporting Evidence

Objective: ensure strategy evidence is valid and cohort-clean. Source lines: [`L986-L999`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L986-L999).

Do:

1. Add cohort axes for phase, discovery mode, direction, bin role, metric, posterior mode, execution mode, order policy/type, fill status, and exit status.
2. Split live, shadow, diagnostic, backtest, and settlement-outcome evidence.
3. Forbid dormant promotion unless evidence is cohort-clean and live-size-zero assumptions were maintained until explicit promotion.
4. Extend attribution drift to catch quadrant-key mismatch.
5. Make unknown strategy keys quarantine rather than backfill into a live bucket.

Review references:

- Evidence matrix: diagnostic-only, shadow executable, live canary, backtest, settlement outcome: [`L832-L840`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L832-L840).
- Shadow evidence rules: executable quote required, no midpoint-only promotion, native NO quote required, live-size zero for dormant keys: [`L842-L850`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L842-L850).
- Live evidence rules: decision envelope, command/venue fill truth, partial-fill separation, exit included, canonical settlement: [`L852-L860`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L852-L860).
- Report cohort axes: [`L862-L877`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L862-L877).
- Invalid promotion blockers: [`L879-L893`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L879-L893).
- Stage 4 packet envelope: [`L986-L999`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L986-L999).

Likely files:

- `src/state/edge_observation.py`
- `src/state/attribution_drift.py`
- `src/state/db.py`
- report scripts/tests

Verification:

```bash
pytest tests/test_edge_observation.py tests/test_attribution_drift.py tests/test_pnl_flow_and_audit.py
```

Exit criteria:

- Reporting/promotion accepted: shadow/live/diagnostic separated, fill/exit/settlement required, no midpoint-only promotion. Source lines: [`L1469-L1470`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1469-L1470).

Rollback:

- Reports can read old four-key cohorts but must mark inverse strategies blocked. Source lines: [`L997-L999`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L997-L999).

## Stage 5 — Real-Market Improvement Layer

Objective: add non-critical but high-upside strategy improvements after the safe spine exists. Source lines: [`L1001-L1014`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1001-L1014).

Do:

1. Add shadow `price_drift_reaction` only after Stage 4 evidence isolation exists.
2. Add shadow family-relative mispricing only with complete-family identity and quote hashes.
3. Persist market-event cause, token id, old/new bid/ask, quote hash, cooldown reason, and no-live-submit proof.
4. Enforce dedupe, per-token cooldown, snapshot freshness, tick-size-change rejection, and flood control.

Review references:

- Price-drift and family-relative are highest-upside but post-launch improvement, not launch-safe now: [`L37-L39`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L37-L39).
- Price-drift reaction is Stage 7 in corrected dependency graph and should be shadow market-WS event layer post-launch: [`L511-L512`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L511-L512).
- Minimal portfolio marks `price_drift_reaction` and `family_relative_mispricing` as `NEW_NOT_NOW`: [`L606-L607`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L606-L607).
- Stage 5 packet envelope: [`L1001-L1014`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1001-L1014).

Likely files:

- Market WebSocket ingestion
- scanner / shadow evaluator trigger code
- snapshot/report tests
- strategy backlog docs

Verification:

```bash
pytest tests/test_user_channel_ingest.py tests/test_ws_poll_reaction.py tests/test_market*.py
```

Exit criteria:

- Shadow-only improvement has no live submit path and no live command rows.

Rollback:

- Disable market-channel shadow listener. Source lines: [`L1012-L1014`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1012-L1014).

## Stage 6 — Dormant / Research / Out-of-Scope Routing

Objective: route weak, premature, high-complexity, and non-strategy items so launch stays focused. Source lines: [`L1016-L1029`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1016-L1029).

Do:

1. Move not-now strategy ideas into a strategy backlog with dependency-before-revisit.
2. Keep riskguard P0 and bankroll truth visible as launch-relevant but non-strategy work.
3. Keep data-ingest resilience, TIGGE historical backfill, PhysicalBounds fallback, oracle path centralization, price-drift live trading, family-relative live trading, opening-window widening, and native NO live promotion out of the launch-critical strategy implementation path unless their stated dependency is met.
4. Add static checks or docs checks proving dormant strategy names do not imply live eligibility.

Review references:

- Out-of-scope routing table: [`L907-L920`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L907-L920).
- Stage 6 packet envelope: [`L1016-L1029`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1016-L1029).

Likely files:

- `docs/operations/task_2026-05-02_full_launch_audit/REMAINING_TASKS.md`
- strategy backlog docs
- launch audit docs
- static architecture tests if needed

Verification:

```bash
pytest tests/test_no_deprecated_make_family_id_calls.py tests/test_architecture_contracts.py
```

Exit criteria:

- Out-of-scope routing accepted: non-strategy blockers listed with launch relevance; improvements not blocking safe launch. Source lines: [`L1470-L1471`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1470-L1471).

Rollback:

- Restore backlog entries. Source lines: [`L1027-L1029`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1027-L1029).

## Cross-Stage Acceptance Gates

Use these gates before calling the whole strategy update complete:

1. Strategy catalog accepted: code, docs, reports, and feature flags agree; unknown keys quarantine. Source lines: [`L1459-L1463`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1459-L1463).
2. Active launch strategy accepted: three live alpha keys only under phase/executable gates; `shoulder_sell` shadow unless native NO live is promoted. Source lines: [`L1462-L1464`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1462-L1464).
3. Shadow strategy accepted: live size zero, executable quote evidence, separate cohort, no live command rows. Source lines: [`L1464-L1465`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1464-L1465).
4. Dormant strategy safe: no runtime live path, no fallback live attribution, explicit revisit gate. Source lines: [`L1465-L1466`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1465-L1466).
5. Sizing accepted: typed executable price, fee/depth/slippage/tick, strategy/phase multipliers, `$5` cap retained. Source lines: [`L1466-L1467`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1466-L1467).
6. Exit accepted: held-token bid/depth, lot-specific exit, hold-to-resolution policy, RED override, command truth. Source lines: [`L1467-L1468`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1467-L1468).
7. Killswitch accepted: global and local layers plus audited operator override. Source lines: [`L1468-L1469`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1468-L1469).
8. Reporting/promotion accepted: shadow/live/diagnostic separated; fill/exit/settlement required; no midpoint-only promotion. Source lines: [`L1469-L1470`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1469-L1470).
9. Out-of-scope routing accepted: non-strategy blockers listed with launch relevance; improvements not blocking safe launch. Source lines: [`L1470-L1471`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1470-L1471).

## Final Verification Loop

Before closeout, answer the review's verification questions directly:

1. Was `STRATEGIES_AND_GAPS.md` treated as a draft hypothesis rather than authority?
2. Was the actual review artifact read and cited?
3. Was the strategy catalog verified against code?
4. Was market opportunity reconstructed independently?
5. Were current strategies compared against better possible strategies?
6. Were all six design gaps preserved but reframed?
7. Was the draft dependency order validated or revised?
8. Was the best launch portfolio defined?
9. Were live blockers, promotion blockers, improvements, and out-of-scope items separated?
10. Were sizing, exit, and killswitch designed at the right level?
11. Were strategy-level live-money errors prevented?
12. Were implementation prompts or packet instructions produced?
13. Was the plan smaller, stricter, and more execution-aware than the draft?

Source lines: [`L1473-L1528`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L1473-L1528).

## Immediate Next Action

Start with Stage 0, not Stage 1. Stage 0 is the only safe first implementation packet because it freezes branch/docs/code/catalog truth and prevents the review or draft from becoming false authority. Source lines: [`L471-L483`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L471-L483), [`L926-L939`](../../artifacts/Zeus_May2_review_%20strategy_update.md#L926-L939).