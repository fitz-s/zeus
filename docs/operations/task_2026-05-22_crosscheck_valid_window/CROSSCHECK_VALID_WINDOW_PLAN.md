# Crosscheck Valid Window Repair Plan

## Current Failure

Post-PR286 live verification reached evaluator candidates, then rejected both latest `opening_hunt` candidates before family/execution:

`SOURCE_COMPARABILITY_FAILED` because `crosscheck_valid_window` was populated but `primary_valid_window` was `["", ""]`.

## Broken Relationship

`ExecutableForecastBundle.to_ens_result()` exports local-calendar-day extrema as the primary probability object, but it does not export the local-day UTC window used to build those extrema. The evaluator comparability gate therefore cannot prove that ECMWF primary and GFS crosscheck describe the same local target-day forecast object.

## Repair Scope

- Preserve the executable forecast target-day UTC window in the bundle result.
- Teach evaluator valid-window extraction to consume that explicit window before falling back to hourly `times`.
- Add relationship tests proving reader output and evaluator comparability agree for executable primary bundles.

## Verification

- Focused executable forecast reader tests.
- Focused evaluator/model comparability tests.
- Live probe after deploy must show code-plane aligned, health green, and no `primary_missing_target_day_valid_window` for new post-deploy candidates.
