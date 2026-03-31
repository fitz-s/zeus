# Plan: Zeus Critical Validation And Runtime Fixes
> Created: 2026-03-30 | Status: COMPLETED

## Goal
Validate the registry’s claimed fixes against live Zeus code, then fix the remaining critical/runtime issues and add regression tests.

## Context
- The user wants the documented Zeus critical bugs checked against the current repo, with any still-live issues fixed.
- The highest-priority registry items are CR1-CR4 in the issue registry plus fresh review comments on scheduler/config compatibility and evaluator exposure handling.
- Some new review comments appear stale because `config/settings.json` already contains the referenced keys, so the work needs validation before patching.
- Relevant systems live in `src/engine/`, `src/state/`, `src/strategy/`, `src/main.py`, and `tests/`.

## Approach
Audit the current runtime paths first so we only patch real defects. Then fix semantic-boundary bugs in orchestration, position reconciliation, and evaluator timing/exposure logic, followed by targeted tests that prove the regressions stay closed.

## Tasks

- [x] 1. Validate claimed bugs against live code
  - Files: `src/main.py`, `src/engine/cycle_runner.py`, `src/engine/evaluator.py`, `src/state/chain_reconciliation.py`, `src/strategy/fdr_filter.py`, `src/strategy/market_fusion.py`, `src/state/portfolio.py`, `config/settings.json`
  - What: Confirm which registry criticals and new review comments are still real versus already fixed/stale.

- [x] 2. Patch confirmed runtime bugs
  - Files: `src/main.py`, `src/engine/cycle_runner.py`, `src/engine/evaluator.py`, `src/state/chain_reconciliation.py`, related helpers if needed
  - What: Keep monitoring active at elevated risk, repair strategy attribution, stop faking quarantined direction, remove local-date lead-day drift, and fix projected exposure checks if still stale.

- [x] 3. Add regression coverage and verify
  - Files: `tests/test_runtime_guards.py`, `tests/test_pnl_flow_and_audit.py`, other focused tests as needed
  - What: Add tests for every confirmed fix and run targeted pytest coverage on the touched paths.

## Risks / Open Questions
- Strategy classification must follow the project’s intended four-strategy model rather than ad hoc edge heuristics.
- Quarantined positions may need a new explicit unknown direction/state that downstream code can safely tolerate.
- Lead-day fixes should use decision-time semantics, not a different local-time shortcut that still drifts around day boundaries.
