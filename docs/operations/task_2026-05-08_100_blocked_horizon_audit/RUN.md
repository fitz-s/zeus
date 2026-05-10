# RUN — fix/#134: extend ECMWF Opendata STEP_HOURS to D+10

Created: 2026-05-08
Branch: `fix/134-step-hours-extension-2026-05-08`
PR: https://github.com/fitz-s/zeus/pull/94

---

## pre-state

| Item | Value |
|---|---|
| `STEP_HOURS` | `range(3, 279, 3)` — max 276h |
| `live_max_step_hours` (HIGH full) | 276 |
| `live_max_step_hours` (LOW full) | 276 |
| BLOCKED readiness rows | 100 rows for 2026-05-13/14 with `SOURCE_RUN_HORIZON_OUT_OF_RANGE` |
| Root cause | Snapshot rows from pre-mx2t3 era have `step_horizon_hours=240.0`; UTC+12 cities require steps up to 252h for D+10; `evaluate_horizon_coverage` blocks when `max(required_steps) > live_max_step_hours` |
| Sample BLOCKED city | AMSTERDAM: expected_steps=[228,234,240,246,252], observed_steps=[228,234,240] |

**ECMWF API ceiling:** The 240h ceiling in `ecmwf.opendata.client.py:386` applies only to `type=ep`. Zeus uses `type=["cf", "pf"]` — no API ceiling for steps > 240h.

---

## changes

| File | Lines | Change |
|---|---|---|
| `src/data/ecmwf_open_data.py:91-97` | 4 removed / 6 added | `STEP_HOURS = list(range(3, 285, 3))` — max 276h → 282h |
| `config/source_release_calendar.yaml:17` | 1 changed | `expected_step_rule` HIGH: `..._up_to_276h` → `..._up_to_282h` |
| `config/source_release_calendar.yaml:28` | 1 changed | `live_max_step_hours` HIGH full: 276 → 282 |
| `config/source_release_calendar.yaml:57` | 1 changed | `expected_step_rule` LOW: `..._up_to_276h` → `..._up_to_282h` |
| `config/source_release_calendar.yaml:68` | 1 changed | `live_max_step_hours` LOW full: 276 → 282 |

---

## tests added

| File | Tests | What they verify |
|---|---|---|
| `tests/test_ecmwf_open_data_step_hours.py` | 8 | STEP_HOURS max=282, 3h stride, D+10 range 228-252 present, regression anchor (old 240h blocks 246/252), `evaluate_horizon_coverage` passes at live_max=282 for dossier sample rows |
| `tests/test_forecast_target_contract_horizon.py` | 7 | `evaluate_horizon_coverage` passes at 252h and 282h with live_max=282; blocks at 283h; blocks empty tuple; regression anchors pre-fix for steps 246 and 252 |

**Result:** 15/15 passed in 0.08s.

---

## post-state (after merge + operator download trigger)

| Item | Expected value |
|---|---|
| `STEP_HOURS` | `range(3, 285, 3)` — max 282h |
| `live_max_step_hours` (both tracks, full horizon) | 282 |
| Fresh mx2t3 snapshot `step_horizon_hours` | 282.0 |
| `evaluate_horizon_coverage` for D+10 UTC+12 cities | `LIVE_ELIGIBLE` |
| BLOCKED rows for 2026-05-13/14 | 0 (after download runs) |

---

## operator-handoff

**After PR #94 is merged**, trigger a fresh mx2t3/mn2t3 download for the current 00Z or 12Z ECMWF cycle:

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
source .venv/bin/activate

# Option A — let daemon pick up next cycle automatically (preferred, no manual action needed):
ZEUS_MODE=live python -m src.main

# Option B — manual in-process trigger for immediate resolution:
ZEUS_MODE=live python - <<'PYEOF'
from datetime import datetime, timezone
from src.data.ecmwf_open_data import collect_open_ens_cycle

now = datetime.now(timezone.utc)
for track in ("mx2t6_high", "mn2t6_low"):
    result = collect_open_ens_cycle(track=track, now_utc=now)
    print(track, result.get("status"), result.get("snapshots_inserted"))
PYEOF
```

**Verify unblocked rows:**
```bash
sqlite3 state/zeus-world.db "
  SELECT eligibility, COUNT(*)
  FROM readiness_state
  WHERE target_date IN ('2026-05-13','2026-05-14')
  GROUP BY 1;
"
```
Expected: zero rows with `eligibility='BLOCKED'` and `blocked_reason='SOURCE_RUN_HORIZON_OUT_OF_RANGE'`.
