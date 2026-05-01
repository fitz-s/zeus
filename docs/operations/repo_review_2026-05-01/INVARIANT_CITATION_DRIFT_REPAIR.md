# Invariant citation drift — operator repair proposal

**Status**: AWAITING-OPERATOR-EDIT (requires `ARCH_PLAN_EVIDENCE`)
**Filed**: 2026-05-01 by team-lead during ultrareview25_remediation P1-8 work
**Related**: `tests/test_invariant_citations.py` (the gate that surfaces these), `scripts/check_invariant_test_citations.py` (the resolver)

## TL;DR

`scripts/check_invariant_test_citations.py` parses `architecture/invariants.yaml` and verifies every `tests:` citation resolves to a real pytest node. As of 2026-05-01 there are **6 unresolved citations across 4 invariants**. The script is in place and gated by `tests/test_invariant_citations.py` against any NEW drift; this file lists the 6 cleanups the operator needs to apply (architecture/ edits require `ARCH_PLAN_EVIDENCE` per the pre-edit-architecture hook).

After applying the 6 fixes below, also remove the matching tuples from `KNOWN_BROKEN` in `tests/test_invariant_citations.py:55-93` so the baseline shrinks and the gate tightens.

## The 6 unresolved citations

### INV-13 — single bare-path cite (no pytest node id)

**Current** (`architecture/invariants.yaml`, INV-13's `enforced_by.tests`):
```yaml
- tests/test_provenance_enforcement.py
```

**Issue**: pytest cites must be `path::node_id`, not just a path. The file exists and contains tests; pick the canonical one (or list multiple).

**Recommended** (operator picks the load-bearing test):
```bash
.venv/bin/python -c "
import ast
tree = ast.parse(open('tests/test_provenance_enforcement.py').read())
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name.startswith('test_'):
        print(f'  - tests/test_provenance_enforcement.py::{node.name}')
    elif isinstance(node, ast.ClassDef) and node.name.startswith('Test'):
        for m in node.body:
            if isinstance(m, ast.FunctionDef) and m.name.startswith('test_'):
                print(f'  - tests/test_provenance_enforcement.py::{node.name}::{m.name}')
"
# Use the output to fill in invariants.yaml
```

### INV-30 — two test methods renamed (state → side_effect)

**Current** cites:
```yaml
- tests/test_executor_command_split.py::TestLiveOrderCommandSplit::test_submit_unknown_writes_event_with_state_unknown
- tests/test_executor_command_split.py::TestExitOrderCommandSplit::test_exit_submit_unknown_writes_event_with_state_unknown
```

**Reality** (from script's nearest-match suggestion): the tests were renamed `_with_state_unknown` → `_with_side_effect_unknown`.

**Recommended replacement**:
```yaml
- tests/test_executor_command_split.py::TestLiveOrderCommandSplit::test_submit_unknown_writes_event_with_side_effect_unknown
- tests/test_executor_command_split.py::TestExitOrderCommandSplit::test_exit_submit_unknown_writes_event_with_state_acked  # operator: confirm canonical "exit unknown" antibody name
```

(Operator should re-run the script after edit to confirm both resolve.)

### INV-32 — three method-style cites missing class prefix

**Current** cites:
```yaml
- tests/test_discovery_idempotency.py::test_materialize_skipped_for_submitting_command
- tests/test_discovery_idempotency.py::test_materialize_skipped_for_unknown_command
- tests/test_discovery_idempotency.py::test_materialize_runs_for_acked_command
```

**Reality**: tests are inside `class TestMaterializePositionGate:` — pytest needs `path::Class::method` form.

**Recommended replacement**:
```yaml
- tests/test_discovery_idempotency.py::TestMaterializePositionGate::test_materialize_skipped_for_submitting_command
- tests/test_discovery_idempotency.py::TestMaterializePositionGate::test_materialize_skipped_for_unknown_command
- tests/test_discovery_idempotency.py::TestMaterializePositionGate::test_materialize_runs_for_acked_command
```

## Apply the fixes

```bash
# 1. Set plan evidence for the architecture/ edit
export ARCH_PLAN_EVIDENCE=docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md

# 2. Edit architecture/invariants.yaml per the recommendations above

# 3. Re-run the script — should report "OK"
.venv/bin/python scripts/check_invariant_test_citations.py

# 4. Remove the corresponding tuples from KNOWN_BROKEN in
#    tests/test_invariant_citations.py:55-93  (no architecture/ gate; plain edit)

# 5. Re-run the test
.venv/bin/python -m pytest tests/test_invariant_citations.py -v

# 6. Bump BASELINE_PASSED in .claude/hooks/pre-commit-invariant-test.sh
#    if the operator chose to add tests/test_invariant_citations.py to TEST_FILES.
```

## Why this is filed instead of fixed inline

I (team-lead) cannot edit `architecture/invariants.yaml` without `ARCH_PLAN_EVIDENCE` set, per `.claude/hooks/pre-edit-architecture.sh`. The hook is correct — invariants.yaml is law-layer. The script + test wrapper are landed; the YAML diff is operator-side once they decide which cite shape they want for INV-13 (multiple candidate tests in test_provenance_enforcement.py) and INV-30 (confirm the canonical "exit unknown" antibody name).

After repair, the citation gate becomes a true zero-broken invariant, and the next stale citation (INV-05-shaped) will fail in pre-commit before it ships.
