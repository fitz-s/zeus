# Live Entry Forecast — Current Rollout Mode (Single Source of Truth)

> **Antibody anchor**. The canary test
> `tests/test_entry_forecast_config.py::test_settings_json_rollout_mode_matches_plan_declaration`
> reads this file and asserts that the value below matches
> `config/settings.json:entry_forecast.rollout_mode`. If you change the
> on-disk value, update this file in the same commit.

## Declared rollout mode

```yaml
rollout_mode: live
declared_on: 2026-05-03
declared_by: operator (Fitz)
unblock_authority_basis: |
  Operator manual unblock. PR46+PR47 stack landed structural data-chain
  pieces; operator authorized live runtime activation in commit
  cb4beb6c "fix: riskguard proxy bypass + unblock entry forecast rollout".
runtime_safety_today: |
  System is fail-closed by construction: get_entry_readiness() in
  src/data/executable_forecast_reader.py queries readiness_state rows
  with strategy_key='entry_forecast' that no daemon path writes (only
  strategy_key='producer_readiness' is written, by
  src/data/producer_readiness.py:97 and :137). The live path therefore
  recurrently blocks at ENTRY_READINESS_MISSING. Verified 2026-05-03 PM
  CDT: SELECT COUNT(*) FROM readiness_state WHERE
  strategy_key='entry_forecast' = 0.
operator_acknowledgement: |
  Operator authorized this state explicitly. The full deep plan in
  docs/operations/task_2026-05-02_full_launch_audit/REMEDIATION_PLAN_2026-05-03.md
  Phases A and B land structural completion in this PR with zero new
  daemon import sites. Phase C (operator-controlled activation: env
  flags ZEUS_ENTRY_FORECAST_ROLLOUT_GATE / _CALIBRATION_GATE /
  _READINESS_WRITER / _HEALTHCHECK_BLOCKERS) is deferred to a separate
  authorized PR.
```

## Allowed values

| Value | Meaning |
|---|---|
| `blocked` | No live entry-forecast orders may be sized or submitted. Default safe state. |
| `shadow` | Forecast bundles computed and persisted but never sized into live orders. |
| `canary` | Live orders allowed only on canary subset, with operator approval evidence. |
| `live` | Full live entry-forecast orders allowed. **Requires** Phase C activation flags ON. |

## How to change this file

1. Update the `rollout_mode:` line in this file.
2. Update `config/settings.json:entry_forecast.rollout_mode` to the same value.
3. Update `declared_on`, `declared_by`, `unblock_authority_basis`.
4. Run `pytest tests/test_entry_forecast_config.py::test_settings_json_rollout_mode_matches_plan_declaration` — must pass.
5. Commit both files together with a message that names which Phase C activation flags (if any) are flipping with this change.

If you change `config/settings.json:entry_forecast.rollout_mode` without updating this file, the canary test will fail and CI will block the merge. That is intentional.
