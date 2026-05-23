# F35 + F9: Structural Replacement of Operator Actions

**Date**: 2026-05-17
**Branch**: fix/structural-ingest-scheduler-2026-05-17
**Authority**: MASS_TRIAGE_2026-05-17.md (F35), investigator verdict 2026-05-17 (F9)

---

## F35 — Oracle Bridge Scheduled via `ingest_main`

### What was wrong

`scripts/bridge_oracle_to_calibration.py` was never scheduled. It had no entry in
`~/.openclaw/cron/jobs.json` and no daemon tick. It required manual operator invocation
to populate `data/oracle_error_rates.json`.

### What was fixed

`_bridge_oracle_tick` added to `src/ingest_main.py` (job ID `ingest_oracle_bridge`).
Fires daily at **10:05 UTC** via APScheduler cron. Identical pattern to the
`_etl_forecast_skill_tick` precedent (F44).

The bridge script writes `data/oracle_error_rates.json` (file-only, no DB). Plain
`subprocess.run` is sufficient; no write-class lock needed.

### Operator verification

In daemon logs:

```
grep BRIDGE_ORACLE_TICK <log>
# Expect: [BRIDGE_ORACLE_TICK] OK (exit=0)
```

Manual dry-run (any time):

```bash
source .venv/bin/activate
python scripts/bridge_oracle_to_calibration.py --dry-run
```

### Cross-repo cron note

No cron entry for `bridge_oracle_to_calibration` existed in `~/.openclaw/cron/jobs.json`
before this fix. No cron removal is required.

---

## F9 — Auto-Promote Calibration Pairs v2 via Readiness Gate

### What was wrong

Promoting `calibration_pairs_v2` from a staging DB to production required manual operator
invocation of `scripts/promote_calibration_pairs_v2.py`. No automated gate existed.

### What was fixed

`_calibration_auto_promote_tick` added to `src/ingest_main.py`
(job ID `ingest_calibration_auto_promote`). Fires weekly on **Sunday at 04:30 UTC**.

Gate logic:
1. Runs `promote_calibration_pairs_v2.py inspect --stage-db $STAGE_DB` (read-only).
2. If inspect exits 0 (all sentinels `complete`): invokes `promote ... --commit` via
   `subprocess_run_with_write_class(..., WriteClass.BULK)` to serialise with other DB writers.
3. If inspect exits non-zero: logs `[AUTO_PROMOTE] gate NOT READY` and skips promote.

### Env flags (both required to enable)

| Variable | Required value | Default |
|---|---|---|
| `ZEUS_CALIBRATION_AUTO_PROMOTE_ENABLED` | `true` | OFF |
| `ZEUS_CALIBRATION_STAGE_DB_PATH` | absolute path to the stage DB produced by `rebuild_calibration_pairs_v2.py` | unset |

**Default is OFF.** Enable only after the first successful manual promotion validates the
gate. The tick aborts with a warning log if either flag is missing.

### How to enable (after manual validation)

```bash
# In the launchd plist for the ingest daemon, add:
ZEUS_CALIBRATION_AUTO_PROMOTE_ENABLED=true
ZEUS_CALIBRATION_STAGE_DB_PATH=/absolute/path/to/stage.db

# Then reload:
launchctl unload ~/Library/LaunchAgents/com.zeus.ingest.plist
launchctl load  ~/Library/LaunchAgents/com.zeus.ingest.plist
```

### Operator verification

```
grep AUTO_PROMOTE <log>
# Gate NOT READY: "[AUTO_PROMOTE] gate NOT READY (inspect exit=1)"
# Gate READY + promote ran: "[AUTO_PROMOTE] SUCCESS (exit=0)"
```

Manual dry-run to confirm gate status:

```bash
source .venv/bin/activate
ZEUS_CALIBRATION_STAGE_DB_PATH=/path/to/stage.db \
  python scripts/promote_calibration_pairs_v2.py inspect --stage-db /path/to/stage.db
```

---

## Test antibodies

`tests/test_ingest_scheduler_jobs.py` — 6 tests covering:

- `ingest_oracle_bridge` registered in scheduler at startup (F35)
- `ingest_calibration_auto_promote` registered in scheduler at startup (F9)
- Tick skips when `ZEUS_CALIBRATION_AUTO_PROMOTE_ENABLED` not set (F9-a)
- Tick skips when `ZEUS_CALIBRATION_STAGE_DB_PATH` not set (F9-a)
- Tick does NOT call promote when inspect exits non-zero — gate NOT READY (F9-a)
- Tick DOES call promote `--commit` when inspect exits 0 — gate READY (F9-b)
