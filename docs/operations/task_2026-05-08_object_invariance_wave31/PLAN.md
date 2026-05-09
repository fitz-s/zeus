# Object Invariance Wave 31 - D4 Exit Evidence Hard Gate

Status: PLANNING-LOCK EVIDENCE FOR LOCAL SOURCE/TEST SLICE, NOT LIVE UNLOCK, NOT VENUE OR DB MUTATION AUTHORITY

Created: 2026-05-08
Last reused or audited: 2026-05-08
Authority basis: root AGENTS.md object-meaning invariance goal; docs/to-do-list/known_gaps.md D4; src/engine/AGENTS.md; src/execution/AGENTS.md; docs/reference/zeus_execution_lifecycle_reference.md; Polymarket order lifecycle docs checked 2026-05-08

## Scope

Repair one bounded boundary class:

`entry DecisionEvidence -> statistical exit decision -> monitor result -> exit intent/execution`

This wave does not mutate live/canonical databases, run migrations, backfill or
relabel legacy rows, submit/cancel/redeem venue orders, publish reports, or
authorize live unlock. It is source/test enforcement only.

## Phase 0 - Repo-Reconstructed Map

Money-path slice for this wave:

`ENTRY_ORDER_POSTED decision_evidence_envelope -> load_entry_evidence() -> Position.evaluate_exit() -> cycle_runtime monitor decision -> build_exit_intent() -> execute_exit() -> execute_exit_order() -> Polymarket CLOB order`

Authority surfaces:

- Entry evidence authority: `position_events.payload_json.decision_evidence_envelope`
  on canonical `ENTRY_ORDER_POSTED`, read by `src/state/decision_chain.py`.
- Evidence contract authority: `src/contracts/decision_evidence.py`.
- Monitor/exit orchestration authority: `src/engine/cycle_runtime.py`.
- Exit execution authority: `src/execution/exit_lifecycle.py` and
  `src/execution/executor.py`.
- External venue reality: Polymarket orders are signed and submitted to CLOB;
  sell market/limit orders are real venue side effects, while RED, chain,
  venue, and settlement exits are different evidence classes from statistical
  edge-reversal hypotheses.

External references checked:

- `https://docs.polymarket.com/trading/overview` — CLOB orders are signed
  messages; matching settles onchain.
- `https://docs.polymarket.com/api-reference/trade/post-a-new-order` — posting
  creates an order in the order book.
- `https://docs.polymarket.com/trading/clients/l2` — L2 methods place/cancel
  buy or sell orders and require trading credentials.

## Phase 1 - Boundary Selection

| Candidate | Live-money relevance | Material values | Bypass/legacy risk | Patch safety |
| --- | --- | --- | --- | --- |
| Statistical exit decision -> live exit intent | Direct sell command risk | `trigger`, `reason`, entry `DecisionEvidence`, exit burden | Existing D4 code only logs asymmetry then proceeds | Safe if scoped to statistical triggers before intent construction |
| D3 cost basis continuity | Sizing/report/replay quality | execution cost, fill cost, edge | broad downstream float consumers | Next wave; larger lineage |
| RED direct side-effect SLA | direct live side effects | cancel/sweep commands | needs venue/operator approval | OPERATOR_DECISION_REQUIRED |

Selected: statistical exit decision -> live exit intent. The current code has a
known audit-only D4 path for `EDGE_REVERSAL`, `BUY_NO_EDGE_EXIT`, and
`BUY_NO_NEAR_EXIT`. These are ordinary statistical exit hypotheses; letting them
continue after weaker evidence changes a model/replay-grade fact into a live
sell instruction.

## Phase 2 - Material Value Lineage

| Value | Real object denoted | Origin | Source authority | Evidence class | Unit/side/time | Transform | Persistence | Downstream consumers | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `entry_evidence` | entry decision statistical burden | `load_entry_evidence(conn, trade_id)` | canonical `ENTRY_ORDER_POSTED` event | live entry evidence | entry decision time | JSON envelope -> `DecisionEvidence` | `position_events` | D4 gate | Preserved when present; missing is authority loss |
| `exit_evidence` | current statistical exit burden | `cycle_runtime` D4 template | runtime monitor code | monitor statistical evidence | monitor/exit decision time | construct `DecisionEvidence(evidence_type="exit")` | not persisted by this wave | D4 gate | Weak by current design |
| `exit_trigger` | reason class for desired exit | `Position.evaluate_exit()` | runtime monitor decision | statistical or force-majeure | monitor time | trigger classification | monitor artifact / position fields if allowed | exit lifecycle | Broken if statistical trigger bypasses D4 gate |
| `exit_intent` | executable sell intent | `build_exit_intent()` | exit lifecycle | live venue command intent | submit time | converts position/context into sell object | venue command/event tables after executor | CLOB submit path | Must not materialize for D4-blocked statistical exits |

## Phase 3 - Failure Classification

### W31-F1 - Audit-only D4 asymmetry can still create a live sell path

Severity: S0/S1. It can directly authorize live exit sells from weaker evidence
than entry, and can also corrupt monitor/report/replay interpretation of why a
position exited.

Object meaning that changes:

An exit-side two-cycle point-estimate confirmation is treated as equivalent to a
bootstrap/FDR entry-grade decision evidence object.

Boundary:

`Position.evaluate_exit()` statistical trigger -> `cycle_runtime` exit intent.

Code path:

- `src/engine/cycle_runtime.py` loads entry evidence and logs
  `exit_evidence_asymmetry`, but the comment and implementation explicitly
  allow the exit to proceed.

Economic impact:

A weak statistical hypothesis can close real held exposure. Polymarket sell
orders are submitted as signed CLOB orders and matched trades can settle onchain;
this is not a reversible diagnostic action.

Reachability:

Active monitor/exit path for ordinary statistical exit triggers.

## Phase 4 - Repair Design

Invariant restored:

Statistical exit evidence may become a live exit intent only when it preserves
or explicitly satisfies the entry-side evidence burden. If entry evidence is
missing/malformed or the exit burden is weaker, the monitor degrades to hold /
review and does not build or execute an exit intent.

External-reality constraint:

Do not apply this statistical symmetry gate to force-majeure exits whose real
authority is not a statistical entry/exit comparison: RED/risk sweep,
settlement-imminent, chain/venue state, orderbook toxicity, flash-crash/panic,
vig, and future observation-authority exits. Those need their own evidence
contracts; blocking them with D4 would be a false model of the outside world.

Durable mechanism:

- Central helper in `cycle_runtime.py` classifies D4 statistical triggers.
- The helper loads entry evidence, constructs current exit evidence, and calls
  `DecisionEvidence.assert_symmetric_with`.
- Missing entry evidence, load exceptions, or `EvidenceAsymmetryError` block
  only statistical triggers before `MonitorResult`, `build_exit_intent()`, and
  `execute_exit()`.
- Structured summary/log counters distinguish missing evidence from asymmetry.
- Relationship tests prove weak statistical exits do not call execution while
  force-majeure exits still pass.

## Phase 5 - Verification Plan

- Relationship tests through `execute_monitoring_phase()`:
  - weak D4 statistical trigger + strong entry evidence -> no exit execution;
  - D4 statistical trigger + missing entry evidence -> no exit execution;
  - force-majeure trigger -> execution still reachable.
- Existing `DecisionEvidence` read-path tests.
- Compile touched files.
- Planning-lock and map-maintenance closeout.

## Implemented Repair

- `src/engine/cycle_runtime.py`
  - Replaced D4 audit-only logging with `_exit_evidence_gate_allows_statistical_exit()`.
  - Gate applies only to `EDGE_REVERSAL`, `BUY_NO_EDGE_EXIT`, and
    `BUY_NO_NEAR_EXIT`.
  - Missing DB/entry envelope, entry evidence load failure, or
    `EvidenceAsymmetryError` rewrites the monitor decision to `should_exit=False`
    before `MonitorResult`, `build_exit_intent()`, or `execute_exit()`.
  - Structured logs/summaries now use `exit_evidence_gate_blocked`,
    `exit_evidence_asymmetry_blocked`, and `exit_evidence_missing_blocked`.
- `src/state/decision_chain.py`, `src/engine/lifecycle_events.py`,
  `src/execution/exit_lifecycle.py`
  - Updated D4 comments from Phase1/audit-only semantics to Wave31 hard-gate
    semantics; no execution behavior changed in these files.
- Tests
  - Added monitor-loop relationship tests for asymmetric statistical exit,
    missing entry evidence, and force-majeure pass-through.
  - Updated static/call-site evidence tests and stale fixtures to current
    env/evidence authority.

## Verification Results

- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/engine/cycle_runtime.py tests/test_runtime_guards.py tests/test_exit_evidence_audit.py docs/operations/task_2026-05-08_object_invariance_wave31/PLAN.md --plan-evidence docs/operations/task_2026-05-08_object_invariance_wave31/PLAN.md` -> pass.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m py_compile src/engine/cycle_runtime.py tests/test_runtime_guards.py tests/test_exit_evidence_audit.py` -> pass.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_runtime_guards.py::test_d4_gate_blocks_asymmetric_statistical_exit_before_execution tests/test_runtime_guards.py::test_d4_gate_blocks_statistical_exit_without_entry_evidence tests/test_runtime_guards.py::test_d4_gate_does_not_block_force_majeure_exit tests/test_runtime_guards.py::test_monitor_execution_failure_does_not_become_chain_missing tests/test_runtime_guards.py::test_monitoring_phase_persists_live_exit_telemetry_chain_with_canonical_entry_baseline tests/test_exit_evidence_audit.py -q --tb=short` -> `16 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_entry_exit_symmetry.py tests/test_decision_evidence_runtime_invocation.py tests/test_exit_evidence_audit.py tests/test_runtime_guards.py::test_d4_gate_blocks_asymmetric_statistical_exit_before_execution tests/test_runtime_guards.py::test_d4_gate_blocks_statistical_exit_without_entry_evidence tests/test_runtime_guards.py::test_d4_gate_does_not_block_force_majeure_exit tests/test_runtime_guards.py::test_monitor_execution_failure_does_not_become_chain_missing tests/test_runtime_guards.py::test_monitoring_phase_persists_live_exit_telemetry_chain_with_canonical_entry_baseline -q --tb=short` -> `35 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_runtime_guards.py -q --tb=short` -> `236 passed, 2 skipped`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_decision_evidence_entry_emission.py tests/test_entry_exit_symmetry.py tests/test_decision_evidence_runtime_invocation.py tests/test_exit_evidence_audit.py -q --tb=short` -> `42 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m py_compile src/engine/cycle_runtime.py src/state/decision_chain.py src/engine/lifecycle_events.py src/execution/exit_lifecycle.py tests/test_runtime_guards.py tests/test_exit_evidence_audit.py tests/test_entry_exit_symmetry.py tests/test_decision_evidence_runtime_invocation.py tests/test_decision_evidence_entry_emission.py` -> pass.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/engine/cycle_runtime.py src/state/decision_chain.py src/engine/lifecycle_events.py src/execution/exit_lifecycle.py tests/test_runtime_guards.py tests/test_exit_evidence_audit.py tests/test_entry_exit_symmetry.py tests/test_decision_evidence_runtime_invocation.py tests/test_decision_evidence_entry_emission.py docs/operations/AGENTS.md docs/operations/task_2026-05-08_object_invariance_wave31/PLAN.md docs/to-do-list/known_gaps.md --plan-evidence docs/operations/task_2026-05-08_object_invariance_wave31/PLAN.md` -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files src/engine/cycle_runtime.py src/state/decision_chain.py src/engine/lifecycle_events.py src/execution/exit_lifecycle.py tests/test_runtime_guards.py tests/test_exit_evidence_audit.py tests/test_entry_exit_symmetry.py tests/test_decision_evidence_runtime_invocation.py tests/test_decision_evidence_entry_emission.py docs/operations/AGENTS.md docs/operations/task_2026-05-08_object_invariance_wave31/PLAN.md docs/to-do-list/known_gaps.md` -> pass.
- `git diff --check` -> pass.

## Downstream Sweep

- Runtime monitor path: D4-blocked statistical exits now emit a monitor result
  with `should_exit=False` and do not mutate position exit fields.
- Exit path: D4-blocked statistical exits do not call `build_exit_intent()` or
  `execute_exit()`.
- Force-majeure path: non-D4 triggers still reach execution; tested with
  `SETTLEMENT_IMMINENT`.
- Read-side/comment contamination: stale Phase1/audit-only D4 wording removed
  from active source/test comments touched by the entry/exit evidence path.
- Residual: legacy/backfilled positions without entry evidence are held/reviewed
  for D4 statistical exits; this wave does not relabel or promote legacy data.
- Residual noted by critic: `src/execution/exit_triggers.py` still names D4
  legacy triggers, but that API is guarded out of live monitor runtime source and
  is not the path repaired by this wave.

## Critic Loop

- Wave31 critic verdict: APPROVE.
  - Confirmed D4 class is limited to `EDGE_REVERSAL`, `BUY_NO_EDGE_EXIT`, and
    `BUY_NO_NEAR_EXIT`.
  - Confirmed `conn=None`, entry load failure, missing/malformed evidence, and
    weaker evidence fail closed before execution.
  - Confirmed gate position is before `MonitorResult`, position exit-field
    mutation, `build_exit_intent()`, and `execute_exit()`.
  - Confirmed force-majeure exits remain outside the D4 statistical class;
    tested `SETTLEMENT_IMMINENT` still reaches execution.

## Topology Notes

- `cycle_runtime.py` remained out-of-scope under the navigation profile even
  though it is the active monitor/exit boundary. Planning-lock accepted the
  local source/test slice.
- Packet registry maintenance for `docs/operations/AGENTS.md` was also routed
  advisory/out-of-scope despite the registry's local rule requiring new packet
  rows. This is route friction, not a semantic blocker.

## Stop Conditions

Stop and request operator decision if repair requires:

- live/prod DB mutation, backfill, relabel, migration, or settlement harvest;
- changing RED/settlement/chain/venue force-exit semantics;
- adding new venue/API facts beyond official docs and repo source authority;
- promoting legacy positions without entry evidence into corrected live truth.
