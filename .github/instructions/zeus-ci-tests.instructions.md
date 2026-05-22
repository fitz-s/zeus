---
applyTo: ".github/workflows/**/*.yml,scripts/ci/**/*.py,architecture/money_path_ci.yaml,architecture/test_topology.yaml,tests/**/*.py"
---

# Zeus CI + test surface review

Changes to CI workflows, invariant scripts, routing yaml, and the
test bed have outsized blast radius — a broken CI gate silently lets
money-path regressions through.

## Invariant and contract tests

Weakening `tests/test_architecture_contracts.py` or any
`tests/test_*invariant*.py` is at least Important, often Critical.
"Activating" an `xfail` or `skip` requires citing the exact mark
being removed. "Extending" a contract test requires the new assertion
to be named explicitly.

Tests must be antibodies, not smoke tests. An antibody test must
demonstrably fail when the bug it guards against is reintroduced.
Flag any test where removing the production-code fix would not cause
the test to fail (i.e. the test asserts a side effect that passes
even when the main invariant is broken).

## test_topology.yaml registration

Every new test file that touches money-path surfaces must be registered
in `architecture/test_topology.yaml` with the correct `trust_tier`
and `covers` annotations. An unregistered test file in `tests/money_path/`,
`tests/contracts/`, or `tests/analysis/` is Important.

## money_path_ci.yaml routing

New invariants must be added to `architecture/money_path_ci.yaml` under
the correct segment and with required_tests pointing to actual test files.
An invariant registered without a required_test, or with a required_test
that doesn't exist on disk, is Important.

## Workflow required vs advisory

Jobs in `money-path-required.yml` are in the merge-blocking lane.
Any job added there that can produce false positives on unrelated
changes (e.g. a style check that flags docs changes) is Important —
it degrades CI signal. New checks should land in `architecture_advisory_gates.yml`
first, with a plan to promote after one clean week.

## Budget check

`check_copilot_instruction_budget.py` enforces ≤3600 chars per
instruction file and requires `applyTo` frontmatter on all path-specific
files. A CI pass that skips these checks (e.g. via `--no-verify`
equivalent) is Important.
