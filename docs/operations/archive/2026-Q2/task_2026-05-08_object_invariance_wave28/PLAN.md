# Object Invariance Wave 28 - Monitor Posterior to Exit EV Gate Authority

Status: PLANNING-LOCK EVIDENCE FOR LOCAL SOURCE/TEST SLICE, NOT LIVE UNLOCK, NOT VENUE OR DB MUTATION AUTHORITY

Created: 2026-05-08
Last reused or audited: 2026-05-08
Authority basis: root AGENTS.md object-meaning invariance goal; docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md remaining-mainline ledger; src/execution/AGENTS.md; src/state/AGENTS.md

## Scope

Repair one bounded boundary class:

`monitor refreshed native posterior -> exit trigger hold-value EV gate`

This wave does not mutate live/canonical databases, run migrations, backfill or relabel legacy rows, submit/cancel/redeem venue orders, publish reports, or authorize live unlock. It is source/test enforcement only.

## Phase 0 - Repo-Reconstructed Map

Money path for this wave:

`position state -> monitor quote/probability refresh -> EdgeContext -> exit trigger -> exit/hold decision`

Authority surfaces:

- Probability refresh authority: `src/engine/monitor_refresh.py::refresh_position` builds `EdgeContext` from held-token quote and fresh model posterior.
- Exit decision authority: `src/execution/exit_triggers.py::evaluate_exit_triggers` and direction-specific helpers. The modern `src/state/portfolio.py::ExitContext` path already has a freshness field and missing-authority gate; this wave hardens the producer and legacy helper seam so both paths see the same authority loss.
- Economic side rule: `src/execution/AGENTS.md` states exit probabilities are in native position space: buy_yes uses P(YES), buy_no uses P(NO).
- Exit context guard surfaces in `src/state/portfolio.py` already require executable `best_bid` for modern exit decisions; this wave is only the legacy trigger helper seam.

## Phase 1 - Boundary Selection

Candidates after Wave27:

| Boundary | Live-money relevance | Material values | Bypass/legacy risk | Patch safety |
| --- | --- | --- | --- | --- |
| Monitor posterior -> exit EV gate | Can cause wrong hold/exit decision | `EdgeContext.p_posterior`, `Position.p_posterior`, `best_bid`, `direction`, `effective_shares` | Buy-yes helper consumes stale position posterior while buy-no consumes fresh context posterior; producer can also materialize stale fallback as `EdgeContext.p_posterior` | Safe as a producer/consumer source repair plus relationship tests |
| Full report/replay/learning sweep | Affects attribution/learning authority | trade lots, settlement env, confirmed economics | Requires broad read-model route | Defer after live-decision wave |
| Historical DB contamination audit | Could reveal polluted rows | physical DB rows | Requires dry-run/rollback plan | Operator decision required |

Selected: monitor posterior to exit EV gate, because it directly affects live exit/hold behavior and has a narrow, testable source repair.

## Phase 2 - Material Value Lineage

| Value | Real object denoted | Origin | Source authority | Evidence class | Unit/side/time | Transform | Persistence | Downstream consumers | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `EdgeContext.p_posterior` | Current refreshed posterior in held-side native probability space | `monitor_refresh.refresh_position` | fresh monitor/model evidence | live monitor probability evidence | P(YES) for buy_yes, P(NO) for buy_no at monitor time | model-only posterior after side transform | in-memory `EdgeContext` | exit trigger EV gate / modern `ExitContext` builder | Broken if replaced with stale `Position.p_posterior` or stale fallback value |
| `Position.last_monitor_prob_is_fresh` | Producer attestation that monitor probability came from current evidence | `monitor_refresh.refresh_position` | monitor producer | authority flag | monitor time boolean | set only from explicit `prob_refresh_is_fresh is True` | Position read model | modern `ExitContext.missing_authority_fields` | Repaired: unknown/false no longer becomes current probability |
| `Position.last_monitor_edge` | Last monitor-time edge derived from fresh posterior and current market | `monitor_refresh.refresh_position` | monitor producer | derived monitor evidence | monitor time native edge | `p_posterior - p_market` only when probability authority is fresh | Position read model / monitor artifacts | monitor result, reports | Repaired: stale/unknown refresh writes non-finite edge so prior-cycle edge cannot masquerade as current |
| `Position.p_posterior` | Stored position posterior from entry or previous state | portfolio state/loaders | position read model | stale or historical probability evidence | native-ish by position but not necessarily monitor-current | copied into Position | DB/cache/JSON depending loader | legacy helper fallback | Ambiguous for current exit EV |
| `best_bid` | Executable sell price for the held token | monitor quote refresh / exit context | CLOB quote | executable market evidence | held-token bid at monitor/exit time | passed to trigger | in-memory | sell-vs-hold EV gate | Preserved |
| `effective_shares` | Corrected fill-authority held shares | Position property | fill authority/current exposure | economic exposure evidence | shares | property selection from confirmed/filled fields | position object | EV gate notional | Preserved by prior tests |

## Phase 3 - Failure Classification

### W28-F1 - Buy-yes exit EV gate can consume stale position posterior instead of current monitor posterior

Severity: S0/S1 depending on runtime path. It can directly affect live hold/exit decisions when legacy trigger helpers are used.

Object meaning that changes:

`EdgeContext.p_posterior` denotes current monitor-time native posterior. `Position.p_posterior` may denote entry-time or stale read-model probability. The buy-yes EV gate uses the stale object while treating it as current hold EV.

Boundary:

`monitor_refresh.refresh_position` / `EdgeContext` -> `exit_triggers._evaluate_buy_yes_exit`.

Code path:

- `src/execution/exit_triggers.py::_evaluate_buy_yes_exit` computes hold EV with `position.p_posterior`.
- `src/execution/exit_triggers.py::_evaluate_buy_no_exit` already uses `current_edge_context.p_posterior`.

Economic impact:

If a stale `Position.p_posterior` is higher than the current monitor posterior, Zeus can incorrectly hold a buy-yes position even when the current posterior says selling is better. If stale probability is lower, it can exit too aggressively.

Reachability:

The modern portfolio `ExitContext` path has stronger guards, but the legacy trigger helper remains imported and tested as an exit surface. Because this is live-money exit logic, the seam must be corrected rather than documented.

### W28-F2 - Stale monitor fallback can masquerade as current `EdgeContext.p_posterior`

Severity: S1/S0 depending on consumer path. It can corrupt live exit decision quality if a consumer trusts `EdgeContext.p_posterior` as current monitor evidence without separately checking `last_monitor_prob_is_fresh`.

Object meaning that changes:

`Position.p_posterior` / stale stored monitor probability denotes entry-time or previous-cycle belief. `EdgeContext.p_posterior` denotes current monitor-time posterior. `refresh_position` initialized and returned stale fallback values under the current field name when refresh failed or explicitly returned not-fresh.

Boundary:

`monitor_probability_refresh` -> `refresh_position` -> `EdgeContext` -> exit/report consumers.

Code path:

- `src/engine/monitor_refresh.py::refresh_position` initialized `current_p_posterior = pos.p_posterior`.
- It only converted to `NaN` for `support_topology_stale`, not for other false/unknown freshness states.
- Legacy `src/execution/exit_triggers.py` helpers did not reject non-authoritative probability contexts.

Economic impact:

A stale posterior could drive edge reversal counters, EV hold/sell comparison, or reporting as if it were current monitor evidence.

Reachability:

The modern `Position.evaluate_exit()` path usually fails closed through `ExitContext.fresh_prob_is_fresh`, but legacy helpers and reports can still consume `EdgeContext` directly. The producer must make stale probability non-authoritative before any consumer gets it.

## Phase 4 - Repair Design

Invariant restored:

All exit-trigger EV gates must consume the same current native posterior object that crossed the monitor boundary in `EdgeContext`. They must not silently substitute stale `Position.p_posterior`.

Durable mechanism:

- Change buy-yes EV gate to use `current_edge_context.p_posterior`, matching buy-no.
- Change `refresh_position` so only an explicit `prob_refresh_is_fresh is True` can populate `EdgeContext.p_posterior`; false/unknown/exception paths produce non-finite probability, edge, and CI fields, keep prior `last_monitor_prob` untouched, and clear `last_monitor_edge` to non-finite because it has no independent freshness flag.
- Add a legacy exit-trigger probability-authority guard so stale/non-finite probability contexts cannot increment reversal counters or trigger probability-driven exits.
- Add relationship tests that prove:
  - fresh monitor posterior crosses `refresh_position -> evaluate_exit_triggers -> hold EV`;
  - stale fallback cannot cross that seam as executable probability.

## Phase 5 - Verification Plan

Required proof:

- Focused relationship test in `tests/test_lifecycle.py`.
- Compile `src/execution/exit_triggers.py` and touched test file.
- Focused lifecycle/entry-exit symmetry tests.
- Existing broader live-safety trigger tests if the focused repair touches legacy helper behavior.
- Critic review after patch because this wave affects exit/hold semantics.

## Implemented Repair

- `src/engine/monitor_refresh.py`
  - `refresh_position` now treats probability freshness as an explicit producer attestation. False/unknown/exception refreshes materialize as non-finite `EdgeContext` probability/edge/CI fields and add stale-authority breadcrumbs instead of writing stale `Position.p_posterior` into current monitor context.
  - `last_monitor_edge` is also set non-finite when probability authority is absent, preventing prior-cycle edge from appearing as a current monitor result.
- `src/execution/exit_triggers.py`
  - Buy-yes EV gate now computes hold value from `current_edge_context.p_posterior`.
  - Buy-no EV gate already used `current_edge_context.p_posterior`, so both direction paths now preserve the monitor-current native posterior object.
  - Legacy helper entry points reject non-finite/non-authoritative probability context before updating reversal state.
- `tests/test_lifecycle.py`
  - Added a relationship test proving buy-yes hold EV receives the `EdgeContext` posterior and not stale `Position.p_posterior`.
  - Added monitor->exit relationship tests for fresh posterior preservation and stale fallback rejection.
- `tests/test_live_safety_invariants.py`
  - Updated the legacy helper relationship fixture to carry a posterior-bearing edge context and assert the same object preservation.
  - Updated Day0 fallback semantics so missing observation authority leaves monitor probability non-authoritative.
- `tests/test_entry_exit_symmetry.py`
  - Aligned an old `MagicMock` exit fixture with current `Position` semantics by setting `effective_cost_basis_usd`.

## Verification Results

- `python3 scripts/topology_doctor.py --navigation --task "exit trigger evaluation native posterior EV gate object invariance: buy_yes hold value must consume current EdgeContext.p_posterior not stale Position.p_posterior" --intent modify_existing --write-intent edit --files src/execution/exit_triggers.py tests/test_lifecycle.py` -> admitted.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/execution/exit_triggers.py tests/test_lifecycle.py docs/operations/task_2026-05-08_object_invariance_wave28/PLAN.md --plan-evidence docs/operations/task_2026-05-08_object_invariance_wave28/PLAN.md` -> pass.
- `python3 scripts/topology_doctor.py --navigation --task "pricing semantics authority cutover: state-owned exit decisions must consume current native posterior EdgeContext and held-token best_bid; update live safety invariant fixture" --intent modify_existing --write-intent edit --files tests/test_live_safety_invariants.py` -> admitted.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m py_compile src/execution/exit_triggers.py tests/test_lifecycle.py tests/test_live_safety_invariants.py tests/test_entry_exit_symmetry.py` -> pass.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m py_compile src/engine/monitor_refresh.py src/execution/exit_triggers.py tests/test_lifecycle.py tests/test_live_safety_invariants.py tests/test_entry_exit_symmetry.py` -> pass.
- `python3 scripts/topology_doctor.py --navigation --task "monitor_refresh to exit trigger native posterior authority: stale probability fallback must not materialize as current EdgeContext.p_posterior" --intent modify_existing --write-intent edit --files src/engine/monitor_refresh.py src/execution/exit_triggers.py tests/test_lifecycle.py` -> admitted.
- `python3 scripts/topology_doctor.py --navigation --task "pricing semantics authority cutover: monitor probability freshness authority fixtures must explicitly mark fresh recompute evidence in runtime and live safety tests" --intent modify_existing --write-intent edit --files tests/test_runtime_guards.py tests/test_live_safety_invariants.py` -> admitted.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/engine/monitor_refresh.py src/execution/exit_triggers.py tests/test_lifecycle.py tests/test_live_safety_invariants.py tests/test_entry_exit_symmetry.py docs/operations/AGENTS.md docs/operations/task_2026-05-08_object_invariance_wave28/PLAN.md docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md --plan-evidence docs/operations/task_2026-05-08_object_invariance_wave28/PLAN.md` -> pass.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/engine/monitor_refresh.py src/execution/exit_triggers.py tests/test_lifecycle.py tests/test_live_safety_invariants.py tests/test_entry_exit_symmetry.py tests/test_runtime_guards.py docs/operations/AGENTS.md docs/operations/task_2026-05-08_object_invariance_wave28/PLAN.md docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md --plan-evidence docs/operations/task_2026-05-08_object_invariance_wave28/PLAN.md` -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files src/engine/monitor_refresh.py src/execution/exit_triggers.py tests/test_lifecycle.py tests/test_live_safety_invariants.py tests/test_entry_exit_symmetry.py tests/test_runtime_guards.py docs/operations/AGENTS.md docs/operations/task_2026-05-08_object_invariance_wave28/PLAN.md docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md` -> pass.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_lifecycle.py::TestExitTriggers::test_monitor_current_posterior_flows_to_buy_yes_ev_gate tests/test_lifecycle.py::TestExitTriggers::test_stale_monitor_probability_cannot_drive_exit tests/test_lifecycle.py::TestExitTriggers::test_buy_yes_ev_gate_uses_current_edge_context_posterior tests/test_live_safety_invariants.py::test_day0_refresh_fallback_keeps_probability_non_authoritative tests/test_runtime_guards.py::test_refresh_position_support_topology_stale_blocks_exit_probability -q --tb=short` -> `5 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_runtime_guards.py::test_monitor_quote_refresh_changes_exit_price_not_posterior_dispatch tests/test_runtime_guards.py::test_monitor_quote_refresh_survives_microstructure_log_failure tests/test_live_safety_invariants.py::test_same_cycle_day0_crossing_refreshes_through_day0_semantics tests/test_live_safety_invariants.py::test_day0_window_refresh_uses_day0_observation_semantics tests/test_live_safety_invariants.py::test_day0_window_live_refresh_uses_best_bid_not_vwmp tests/test_live_safety_invariants.py::test_day0_refresh_fallback_keeps_probability_non_authoritative -q --tb=short` -> `6 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_live_safety_invariants.py -q --tb=short` -> `115 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_lifecycle.py tests/test_entry_exit_symmetry.py tests/test_churn_defense.py tests/test_live_safety_invariants.py tests/test_cross_module_relationships.py tests/test_runtime_guards.py::test_monitor_quote_refresh_changes_exit_price_not_posterior_dispatch tests/test_runtime_guards.py::test_monitor_quote_refresh_survives_microstructure_log_failure tests/test_runtime_guards.py::test_refresh_position_support_topology_stale_blocks_exit_probability tests/test_runtime_guards.py::test_buy_no_exit_ev_gate_uses_held_token_best_bid_not_p_market_vector tests/test_runtime_guards.py::test_buy_no_exit_ev_gate_allows_sell_when_best_bid_beats_hold_value -q --tb=short` -> `171 passed, 4 skipped`.
- Full `tests/test_runtime_guards.py` was sampled for contamination/noise. Wave28-related freshness fixture failures were fixed. Remaining failures are outside this wave (`transfer_sigma` fixture expectation, `load_portfolio` fixture stubs, and `position_events.env` fixture requirement) and are not used as Wave28 proof.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_lifecycle.py::TestExitTriggers::test_buy_yes_ev_gate_uses_current_edge_context_posterior tests/test_lifecycle.py::TestExitTriggers::test_edge_reversal_needs_two_confirmations tests/test_lifecycle.py::TestExitTriggers::test_no_exit_when_edge_healthy -q --tb=short` -> `3 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_live_safety_invariants.py::test_legacy_exit_triggers_use_fill_authority_shares tests/test_live_safety_invariants.py::test_legacy_buy_no_exit_triggers_use_fill_authority_shares -q --tb=short` -> `2 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_lifecycle.py tests/test_entry_exit_symmetry.py tests/test_churn_defense.py tests/test_runtime_guards.py::test_buy_no_exit_ev_gate_uses_held_token_best_bid_not_p_market_vector tests/test_runtime_guards.py::test_buy_no_exit_ev_gate_allows_sell_when_best_bid_beats_hold_value tests/test_live_safety_invariants.py::test_legacy_exit_triggers_use_fill_authority_shares tests/test_live_safety_invariants.py::test_legacy_buy_no_exit_triggers_use_fill_authority_shares -q --tb=short` -> `51 passed`.

## Downstream Sweep

- Monitor producer: `src/engine/monitor_refresh.py::refresh_position` returns finite `EdgeContext.p_posterior` only for explicit fresh probability authority; stale/unknown paths return non-finite probability/edge/CI fields.
- Exit trigger consumer: both buy-yes and buy-no EV gates now consume the `EdgeContext` posterior, and the legacy helper path rejects non-authoritative probability context.
- Modern `src/state/portfolio.py` exit context remains separate and already has `fresh_prob_is_fresh` guards. The producer hardening makes that path receive the same non-authoritative probability object instead of a stale value with a false flag.

Residual risk:

- This wave does not re-audit every report/replay/learning consumer of monitor fields. Any skipped monitor-result path that still reports `pos.last_monitor_prob or pos.p_posterior` is a separate reporting-contamination candidate, not an exit-actuation repair.
- This wave does not change divergence panic thresholds, whale toxicity, lifecycle submission, or exit order execution.

## Critic Loop

- First critic verdict: REVISE.
  - Finding 1: stale monitor fallback could still masquerade as current `EdgeContext.p_posterior`.
  - Finding 2: initial test was a helper snapshot, not a true monitor->exit relationship proof.
- Repair response:
  - Producer now emits non-finite probability/edge/CI when freshness is false/unknown/exception.
  - Legacy exit helpers reject non-authoritative probability contexts.
  - Added monitor->exit fresh and stale relationship tests.
- Second critic verdict: APPROVE.
  - Confirmed producer now makes stale/unknown/exception probability context non-authoritative.
  - Confirmed legacy exit helper rejects non-finite probability authority before reversal counters and EV exits.
  - Confirmed monitor->exit relationship tests cover fresh crossing and stale fallback rejection.
  - Residual risks moved to future waves: report-only skipped monitor paths still use `pos.last_monitor_prob or pos.p_posterior`; replay/learning/read-model consumers still need a separate sweep; physical DB rows and historical monitor artifacts were not audited/backfilled/relabelled.

## Stop Conditions

Stop and request operator decision if repair requires:

- changing lifecycle phases or command grammar;
- schema migration or historical row rewrite;
- venue cancel/redeem/submit side effects;
- changing modern `ExitContext` API or exit execution submission semantics outside the admitted route.

## Topology Notes

- Initial audit route correctly classified this as T0/read-only and required a packet before implementation.
- `semantic-bootstrap` remains blocked by missing `topology_doctor_context_pack` import path.
