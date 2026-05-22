# Crosscheck Valid Window Plan

Status: active
Authority: false (advisory file management, not architecture law)
Scope: evaluator_source_comparability
Blocks live completion: true

## Objective

Fix the GFS025 crosscheck valid-window comparability check so `opening_hunt` does not
reject candidates as CROSSCHECK_UNAVAILABLE / SOURCE_COMPARABILITY_FAILED when
Open-Meteo diagnostic payloads lack true issue_time but target-day valid windows
match and diagnostic fetch/capture time is fresh relative to primary issue.

## Legacy source packet

`docs/operations/task_2026-05-22_crosscheck_valid_window/` — full implementation
context, sub-scope breakdown, and verification evidence. Read that packet for detail;
this PLAN.md is the canonical current-package pointer.
