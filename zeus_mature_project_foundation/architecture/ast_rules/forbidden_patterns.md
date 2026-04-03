# Zeus forbidden patterns

This file is the human-readable map for machine checks.  
If a pattern is forbidden here, it should either already be machine-enforced or be scheduled for a gate stage.

## Immediate forbidden patterns

1. `close_position(...)` or `void_position(...)` from engine/orchestration code  
   Rationale: orchestration must emit lifecycle intent, not directly terminalize positions.

2. `_control_state[...]` reads/writes outside `src/control/control_plane.py`  
   Rationale: memory state is not durable policy.

3. strategy fallback to `"opening_inertia"` when attribution is missing  
   Rationale: governance contamination masquerades as completeness.

4. new writes to `positions.json` or `status_summary.json` outside designated export modules  
   Rationale: derived exports must not become shadow authority.

## Strict-after-P1 patterns

1. raw `phase` / `state` string assignment outside lifecycle fold/manager/projection
2. `1 - p` complements in engine/state/execution code paths
3. fallback from missing decision snapshot to latest snapshot
4. imports from K3 extension modules into K0/K1 lifecycle-authority internals

## Reviewer rule

If a patch introduces a new pattern not covered here but violating `architecture/negative_constraints.yaml`, add it here and add or schedule a machine check.
