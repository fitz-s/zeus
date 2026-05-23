# Session Goal — current_live_recovery

Package: `current_live_recovery`
Status: active
Created: 2026-05-22
Authority: docs/operations/current/package.yaml + docs/operations/current/task.md

## Active objective

Live must continuously refresh source/forecast/settlement/evaluator/sizing/venue/reconcile/redeem
and produce verifiable real trading profit.
Process liveness, one green healthcheck, one order/fill, or a merged PR is not completion.

## Active blocker

`opening_hunt` rejects candidates as `SOURCE_COMPARABILITY_FAILED`:
Open-Meteo GFS025 diagnostic payloads do not expose true `issue_time`.
Current branch allows source-limited crosscheck only when target-day valid windows match
and diagnostic fetch/capture time is fresh relative to primary issue.

## Active subtasks

- `live_math_frontier` — evaluator math/strategy frontier (blocks live completion)
  Plan: docs/operations/current/plans/live_math_frontier/PLAN.md
- `crosscheck_valid_window` — evaluator source comparability (blocks live completion)
  Plan: docs/operations/current/plans/crosscheck_valid_window/PLAN.md

## Single operations home

All planning, goals, and ongoing-operation files live in `docs/operations/current/`.
- Goal anchor: `docs/operations/current/GOAL.md` (this file)
- Plans: `docs/operations/current/plans/<name>.md` (flat files)
- Nothing in `.omc/`, `.claude/`, `.omx/`, or repo root
- H7 (`planning_file_outside_operations`) and H8 (`session_goal_not_in_current`) enforce this
- See docs/operations/AGENTS.md for rules
