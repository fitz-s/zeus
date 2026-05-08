# Object Invariance Wave 29 - Monitor Result Probability Reporting Authority

Status: PLANNING-LOCK EVIDENCE FOR LOCAL SOURCE/TEST SLICE, NOT LIVE UNLOCK, NOT VENUE OR DB MUTATION AUTHORITY

Created: 2026-05-08
Last reused or audited: 2026-05-08
Authority basis: root AGENTS.md object-meaning invariance goal; Wave28 critic residual; docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md

## Scope

Repair one bounded boundary class:

`monitor execution state -> per-cycle MonitorResult reporting artifact`

This wave does not mutate live/canonical databases, run migrations, backfill or relabel legacy rows, submit/cancel/redeem venue orders, publish reports, or authorize live unlock. It is source/test enforcement only.

## Phase 0 - Repo-Reconstructed Map

Money/report path for this wave:

`position state -> monitor loop skip/error/current path -> MonitorResult -> CycleArtifact decision_log JSON`

Authority surfaces:

- Producer: `src/engine/cycle_runtime.py::execute_monitoring_phase`.
- Artifact record: `src/state/decision_chain.py::MonitorResult`.
- Fresh probability authority: current-cycle `refresh_position` result plus `Position.last_monitor_prob_is_fresh`.
- Skipped/error paths: quarantine resolution, unknown direction, and monitor-chain-missing exception reporting.

## Phase 1 - Boundary Selection

Candidates after Wave28:

| Boundary | Live-money relevance | Material values | Bypass/legacy risk | Patch safety |
| --- | --- | --- | --- | --- |
| Monitor skip/error path -> MonitorResult | Can corrupt operator report/replay interpretation | `fresh_prob`, `fresh_edge`, `last_monitor_prob`, `Position.p_posterior` | Skipped paths use `pos.last_monitor_prob or pos.p_posterior` without current-cycle authority | Safe in `cycle_runtime` only |
| Read-model loaders -> riskguard | Can collapse unknown to `0.0` | `last_monitor_prob`, `last_monitor_edge` | `float(row or 0.0)` ambiguity | Defer; touches state/riskguard loaders |
| Historical DB artifacts | Can contain old stale values | decision_log blobs | Requires audit/backfill/relabel decision | Operator decision required |

Selected: monitor skip/error path -> `MonitorResult`, because it is the direct residual bypass from Wave28 and can be repaired without schema/data mutation.

## Phase 2 - Material Value Lineage

| Value | Real object denoted | Origin | Source authority | Evidence class | Unit/side/time | Transform | Persistence | Downstream consumers | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `MonitorResult.fresh_prob` | Current-cycle monitor probability, when available | `cycle_runtime.execute_monitoring_phase` | monitor refresh | report evidence | native held-side probability at current monitor time | copied from `EdgeContext.p_posterior` only on current refresh | decision_log artifact JSON | reports/replay/operator diagnostics | Broken on skipped/error paths if filled from stale position fields |
| `MonitorResult.fresh_edge` | Current-cycle monitor edge, when available | `cycle_runtime.execute_monitoring_phase` | monitor refresh | derived report evidence | native edge at current monitor time | copied from current `last_monitor_edge` only when probability was fresh | decision_log artifact JSON | reports/replay/operator diagnostics | Broken if old edge survives skipped/error paths |
| `Position.p_posterior` | Entry or stored position posterior | portfolio/read model | entry/read-model state | historical decision evidence | native-ish by position, not current monitor time | fallback in skipped paths | Position state | MonitorResult fallback | Broken as `fresh_prob` fallback |
| `Position.last_monitor_prob` | Last recorded monitor probability | prior monitor cycle | previous monitor evidence | stale unless refreshed this cycle | native held-side probability | fallback in skipped paths | Position state/projection | MonitorResult fallback | Ambiguous as current-cycle report value |

## Phase 3 - Failure Classification

### W29-F1 - Skipped/error monitor results report stale probability as `fresh_prob`

Severity: S1. It does not submit orders directly in the repaired paths, but it can corrupt operator reports, replay diagnostics, and learning/performance interpretation.

Object meaning that changes:

`Position.p_posterior` or prior `last_monitor_prob` denotes entry/prior-cycle belief. `MonitorResult.fresh_prob` denotes the probability for this monitor result. Skipped/error paths materialize old belief under the current-cycle report field.

Boundary:

`Position` read model -> `cycle_runtime.execute_monitoring_phase` skipped/error `MonitorResult`.

Code path:

- quarantine resolution monitor result;
- unknown direction monitor result;
- monitor-chain-missing exception monitor result.

Economic impact:

Reports and replay can attribute a hold/no-exit result to a current probability that was never refreshed. That can mislead operator diagnosis and any downstream learning/reporting that reads artifacts as current monitor evidence.

Reachability:

Active reporting path. Not an exit-actuation path in this wave.

## Phase 4 - Repair Design

Invariant restored:

`MonitorResult.fresh_prob` and `fresh_edge` may only contain current-cycle monitor evidence. If the monitor did not run or did not have current probability authority, the fields must be absent/non-authoritative, not filled from position fallbacks.

Durable mechanism:

- Add a cycle-runtime helper that emits current monitor probability/edge only when the current refresh supplied a finite posterior and `last_monitor_prob_is_fresh` is true.
- For skipped/error monitor results that never reached current refresh, set `fresh_prob=None` and `fresh_edge=None`.
- For normal monitor results, route through the helper so stale Wave28 `NaN` or false freshness does not appear as a numeric current report value.
- Add relationship tests for quarantine and monitor-chain-missing paths.

## Phase 5 - Verification Plan

Required proof:

- Focused relationship tests for skipped/error monitor result artifacts.
- Existing monitor lifecycle runtime tests around ORANGE/incomplete/chain missing.
- Compile touched source/tests.
- Planning-lock and map-maintenance closeout.
- Critic review if this wave expands beyond the three admitted files.

## Implemented Repair

- `src/engine/cycle_runtime.py`
  - Added `_current_monitor_result_probability_and_edge()` to return current-cycle report probability/edge only when an `edge_ctx` exists, `last_monitor_prob_is_fresh` is true, and values are finite.
  - Quarantine-resolution, unknown-direction, and monitor-chain-missing skipped/error paths now emit `fresh_prob=None` and `fresh_edge=None` instead of falling back to stale position fields.
  - Normal monitor result reporting now also routes through the helper, so Wave28 non-authoritative probability contexts do not become numeric report evidence.
- `tests/test_live_safety_invariants.py`
  - Added/updated skipped monitor relationship assertions for quarantine, fill-authority quarantine, unknown direction, and expired quarantine.
- `tests/test_runtime_guards.py`
  - Added relationship assertions for normal ORANGE reporting, incomplete context reporting, refresh failure, and time-context failure.

## Verification Results

- `python3 scripts/topology_doctor.py --navigation --task "pricing semantics authority cutover: monitor result reporting must preserve probability freshness authority and reject stale Position.p_posterior fallback" --intent modify_existing --write-intent edit --files src/engine/cycle_runtime.py tests/test_live_safety_invariants.py tests/test_runtime_guards.py` -> admitted.
- `python3 scripts/topology_doctor.py --navigation --task "create operation planning packet for object meaning invariance wave29 monitor result reporting probability authority" --intent create_new --write-intent add --files docs/operations/task_2026-05-08_object_invariance_wave29/PLAN.md` -> admitted.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/engine/cycle_runtime.py tests/test_live_safety_invariants.py tests/test_runtime_guards.py docs/operations/AGENTS.md docs/operations/task_2026-05-08_object_invariance_wave29/PLAN.md docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md --plan-evidence docs/operations/task_2026-05-08_object_invariance_wave29/PLAN.md` -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files src/engine/cycle_runtime.py tests/test_live_safety_invariants.py tests/test_runtime_guards.py docs/operations/AGENTS.md docs/operations/task_2026-05-08_object_invariance_wave29/PLAN.md docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md` -> pass.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m py_compile src/engine/cycle_runtime.py tests/test_live_safety_invariants.py tests/test_runtime_guards.py` -> pass.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_live_safety_invariants.py::test_monitoring_marks_quarantine_for_admin_resolution_once tests/test_live_safety_invariants.py::test_monitoring_skips_fill_authority_quarantine_without_chain_quarantine tests/test_live_safety_invariants.py::test_monitoring_unknown_direction_report_has_no_fresh_probability tests/test_live_safety_invariants.py::test_quarantine_expired_marks_distinct_admin_resolution_reason tests/test_runtime_guards.py::test_orange_risk_exits_favorable_position_through_monitor_lifecycle tests/test_runtime_guards.py::test_orange_risk_does_not_override_incomplete_exit_context tests/test_runtime_guards.py::test_monitor_refresh_failure_near_settlement_is_operator_visible tests/test_runtime_guards.py::test_incomplete_exit_context_near_settlement_escalates_monitor_chain tests/test_runtime_guards.py::test_time_context_failure_near_active_position_escalates_monitor_chain -q --tb=short` -> `9 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_live_safety_invariants.py -q --tb=short` -> `116 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_runtime_guards.py::test_orange_risk_exits_favorable_position_through_monitor_lifecycle tests/test_runtime_guards.py::test_orange_risk_holds_when_bid_is_unfavorable tests/test_runtime_guards.py::test_orange_risk_does_not_override_incomplete_exit_context tests/test_runtime_guards.py::test_yellow_risk_does_not_take_favorable_exit tests/test_runtime_guards.py::test_monitor_refresh_failure_near_settlement_is_operator_visible tests/test_runtime_guards.py::test_monitor_refresh_failure_far_from_settlement_is_not_chain_missing tests/test_runtime_guards.py::test_incomplete_exit_context_near_settlement_escalates_monitor_chain tests/test_runtime_guards.py::test_monitor_execution_failure_does_not_become_chain_missing tests/test_runtime_guards.py::test_time_context_failure_near_active_position_escalates_monitor_chain tests/test_runtime_guards.py::test_unknown_direction_positions_are_not_monitored -q --tb=short` -> `10 passed`.

## Downstream Sweep

- Skipped quarantine, fill-authority quarantine, unknown-direction, and monitor-chain-missing paths no longer materialize stale probability/edge as fresh monitor result evidence.
- Normal monitor result path now requires current refresh authority and finite values.
- Deferred: read-model loaders and riskguard still collapse missing `last_monitor_prob` / `last_monitor_edge` to `0.0`; that is a separate state/riskguard boundary.

## Critic Loop

- Pending. If Wave29 is bundled with another reporting/read-model repair, run critic after several findings and patches per original goal.

## Stop Conditions

Stop and request operator decision if repair requires:

- changing decision_log schema or migrating artifact JSON;
- rewriting historical decision_log rows;
- changing riskguard/read-model loader semantics outside this wave;
- publishing reports or promoting replay/diagnostic artifacts into authority.
