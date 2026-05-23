# Current Frontier Status

authority: false
role: pointer_and_evidence (current fact; not architecture law)

Last updated: 2026-05-22
Source: task.md §8 + live evidence

## Active frontier

- Class: source_math_strategy
- Terminal reason: SOURCE_COMPARABILITY_FAILED
- Last non-empty frontier: evaluator_source_comparability
- Detail: opening_hunt rejects candidates as CROSSCHECK_UNAVAILABLE because
  Open-Meteo GFS025 diagnostic payloads do not expose true issue_time.
  Current branch allows source-limited crosscheck only when target-day
  valid windows match and diagnostic fetch/capture time is fresh relative
  to primary issue; still refuses stale, shifted-window, or non-Open-Meteo
  missing-issue crosschecks.

## Active subtasks

| Subtask | Plan path | Scope |
|---------|-----------|-------|
| live_math_frontier | docs/operations/current/plans/live_math_frontier/PLAN.md (T2) | evaluator_math_strategy |
| crosscheck_valid_window | docs/operations/current/plans/crosscheck_valid_window/PLAN.md (T2) | evaluator_source_comparability |

Both subtasks block live completion (blocks_live_completion: true).

## See also

- task.md §8 (implementation status, live evidence)
- task.md §9 (verification evidence)
