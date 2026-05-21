# Created: 2026-05-21
# Last reused/audited: 2026-05-21
# Authority basis: AGENTS.md money-path semantic CI directive; architecture/test_topology.yaml trust policy

# Money-Path Semantic CI Plan

## Objective

Turn CI from a fixed regression bundle into a semantic gate for money-path
objects. Any diff that creates or changes a DB column/table, state, side-effect
call, external source, strategy key, or economic field must route to registered
invariants and trusted relationship tests.

## Scope

- Add `architecture/money_path_objects.yaml` as the semantic object registry.
- Add `architecture/money_path_ci.yaml` as invariant/test routing.
- Add `architecture/test_quality.yaml` as money-path falsifying-proof metadata.
- Add CI helper scripts under `scripts/ci/`.
- Add deterministic money-path tests under `tests/money_path/`.
- Add `.github/workflows/money-path-required.yml` and manual
  `.github/workflows/live-release-gate.yml`.
- Add an empty `ci/baseline_failures.json` schema for future full-sweep
  failure-ID baselining.

## Non-Scope

- No live DB reads.
- No external API calls.
- No runtime trading source changes.
- No branch-protection mutation from this PR.

## Required Proof

- `python -m py_compile scripts/ci/*.py`
- `python scripts/ci/assert_test_quality.py`
- `pytest -q tests/test_money_path_semantic_ci.py tests/money_path`
- `python scripts/ci/semantic_diff_classifier.py --diff-file <current diff> --fail-on-unregistered`
- `python scripts/ci/assert_invariant_coverage.py` on classifier output when
  selected invariants exist.
- `python scripts/topology_doctor.py --planning-lock ... --plan-evidence this file`
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory`

## Merge Discipline

Open a PR against `main`, wait for required checks and review-thread state, and
only merge after the PR is clean. Checks alone are necessary but not sufficient.
