# Function Naming and Freshness Metadata Plan

Date: 2026-04-16
Branch: data-improve

## Objective

Make script/test reuse less speculative by requiring changed or reused files to
declare when they were created, last reviewed, and last reused. Add a small
function naming rule so helpers expose their semantic contract instead of
inviting agents to infer meaning from stale generic names.

## Scope

- Root, scripts, and tests AGENTS guidance.
- `architecture/naming_conventions.yaml` as the canonical naming/freshness map.
- `architecture/script_manifest.yaml` as script lifecycle registry that points to the naming map instead of redefining naming rules.
- `topology_doctor` changed-file freshness metadata lane.
- Targeted tests for the new lane and closeout integration.

## Non-Goals

- Do not mass-edit all historical scripts/tests in this package.
- Do not treat old tests/scripts as invalid; treat unknown freshness as
  review-required before reuse.
- Do not add a hard AST function-name linter yet. Start with policy and
  changed-file freshness enforcement.
