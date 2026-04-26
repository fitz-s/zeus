# Zeus forbidden patterns

This file is the human-readable map for machine checks.  
If a pattern is forbidden here, it should either already be machine-enforced or be scheduled for a gate stage.

Canonical source: `architecture/negative_constraints.yaml` (NC-01 through NC-10)
Architecture-level definitions: `docs/authority/zeus_current_architecture.md` §10 (FM-01 through FM-10)

## Immediate forbidden patterns

1. **FM-04** — `close_position(...)` or `void_position(...)` from engine/orchestration code  
   Rationale: orchestration must emit lifecycle intent, not directly terminalize positions.  
   Enforcement: semgrep `zeus-no-direct-close-from-engine` (NC-04)

2. **FM-06** — `_control_state[...]` reads/writes outside `src/control/control_plane.py`  
   Rationale: memory state is not durable policy.  
   Enforcement: semgrep `zeus-no-memory-only-control-state` (NC-06)

3. **FM-03** — strategy fallback to `"opening_inertia"` or any default when attribution is missing  
   Rationale: governance contamination masquerades as completeness. If attribution doesn't exist, fail — never guess.  
   Enforcement: semgrep `zeus-no-strategy-default-fallback` (NC-03)

4. **FM-02** — new writes to `positions.json`, `status_summary.json`, or `strategy_tracker.json` outside designated export modules  
   Rationale: derived exports must not become shadow authority.  
   Enforcement: semgrep `zeus-no-json-authority-write` (NC-02)

5. **FM-08** — bare implicit unit assumptions (`F` default, `C` default) in semantic code paths  
   Rationale: °F and °C cities have different settlement semantics (2°F range vs 1°C point bins). Hardcoded unit assumptions silently produce wrong probabilities for the wrong city set.  
   Enforcement: semgrep + test (NC-08)

6. **FM-NC-16** — `place_limit_order(...)` calls outside the gateway boundary
   Rationale: live order submission must flow through the executor seam so V2
   endpoint preflight (INV-25) and durable command persistence (planned P1)
   land together. Direct SDK calls bypass both gates.
   Allowed callers: `src/execution/executor.py` (gateway), `src/data/polymarket_client.py` (SDK wrapper), `scripts/live_smoke_test.py` (operator path that calls v2_preflight() itself).
   Enforcement: semgrep `zeus-place-limit-order-gateway-only` + test `tests/test_p0_hardening.py::test_place_limit_order_gateway_only` (NC-16)

## Strict patterns (always enforced)

1. **FM-07** — raw `phase` / `state` string assignment outside lifecycle fold/manager/projection  
   Enforcement: semgrep `zeus-no-direct-phase-assignment` (NC-07)

2. **FM-09** — `1 - p` complements in engine/state/execution code paths  
   Rationale: ad hoc probability complements across architecture boundaries break when semantic contracts exist (e.g., `HeldSideProbability` vs `NativeSidePrice`).  
   Enforcement: semgrep `zeus-no-ad-hoc-probability-complement` (NC-09)

3. **FM-05** — fallback from missing decision snapshot to latest snapshot  
   Rationale: violates point-in-time truth (INV-06). Learning must preserve what was knowable at decision time.  
   Enforcement: test + forbidden_patterns check (NC-05)

4. **FM-01** — broad edits spanning K0 and K3 in the same patch without explicit packet justification  
   Rationale: zone boundary discipline. Math code (K3) must not redefine kernel truth (K0) in a single uncommitted patch.  
   Enforcement: packet review (NC-01)

5. **FM-10** — new shadow persistence surface without explicit deletion or demotion plan  
   Rationale: every new file that stores state creates a potential parallel truth surface that drifts from DB authority.  
   Enforcement: packet review (NC-10)

## Reviewer rule

If a patch introduces a new pattern not covered here but violating `architecture/negative_constraints.yaml`, add it here and add or schedule a machine check.
