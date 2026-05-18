# Cron Proposals — F32 + F35: bridge_oracle_to_calibration scheduling

**Created**: 2026-05-17
**Authority**: post_pr126_audit/CONSOLIDATED_FINDINGS_DOSSIER.md §F32/F35;
  task_2026-05-17_post_karachi_remediation/WAVE_2_PLAN.md #46 F35
**Status**: Pending operator addition to `cron/jobs.json`. DO NOT edit jobs.json directly — operator action required.

## Background

`scripts/bridge_oracle_to_calibration.py` bridges oracle error-rate artifacts
into the calibration pipeline. It was never scheduled in cron/jobs.json and has
never run in production. PR #137 (feat/post-karachi-remediation-wave-2026-05-17)
fixed the K1 repoint (F40), restoring the script's correctness.

The WAVE_2_PLAN.md prerequisite: manually run `bridge_oracle_to_calibration.py --dry-run`
first. If non-zero cities are reported (vs prior 0-stub), proceed with cron addition.

## Proposed cron entries

Add to `cron/jobs.json` (workspace: venus, zeus venv):

```json
{
  "id": "bridge_oracle_to_calibration",
  "description": "Bridge oracle error-rate artifacts into calibration pipeline (F32/F35)",
  "command": "cd /Users/leofitz/.openclaw/workspace-venus/zeus && .venv/bin/python scripts/bridge_oracle_to_calibration.py",
  "schedule": "5 10 * * *",
  "enabled": true,
  "workspace": "venus"
}
```

**Schedule rationale**: 10:05 UTC daily — after oracle data is expected available
(oracle runs ~09:00 UTC), before the 06:00 CT / 11:00 UTC calibration ETL window.
From WAVE_2_PLAN.md #46: `5 10 * * *`.

## Verification after scheduling

```bash
# Confirm artifact freshness < 25h after first scheduled run:
ls -la data/oracle_error_rates.json

# Confirm oracle penalty reloaded in next daemon cycle:
grep oracle_penalty_reloaded logs/zeus-live.log | tail -5
```

## Dependencies

- F44 (observation_instants_v2 writer): bridge queries v2 for target_date >= 2026-05-11.
  Verify F44 is resolved before enabling this cron, otherwise bridge gets 0 rows.
- F40 (K1 repoint): fixed by PR #137. Script now reads from correct DB.
