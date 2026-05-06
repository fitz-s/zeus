# Inverted-slope Platt quarantine ledger — 2026-05-06

**Authority basis**: live-launch readiness audit 2026-05-06; calibration
quality validation report (scientist agent acc6fb8aba109c4f5).

**Trigger**: 12 active VERIFIED Platt v2 models with `param_A < 0`
(strict inversion) detected on `state/zeus-world.db::platt_models_v2`.
A negative `param_A` inverts the forecast signal: higher forecast
probability → lower calibrated probability. This is a degenerate fit
(typical cause: extreme-low base rate climates where the in-sample
sigmoid latches onto noise). Live serving these would lose money.

## Action taken

1. **DB UPDATE (operator-equivalent)**: `authority='QUARANTINED'` on the
   12 rows. Loader (`load_platt_model_v2`) filters on `authority='VERIFIED'`
   so quarantined rows fall through to the manager's hierarchical
   fallback chain (cluster+season → season → global → uncalibrated).
   Reversible: `UPDATE platt_models_v2 SET authority='VERIFIED' WHERE …`.

2. **Code fit-time guard**: `scripts/refit_platt_v2.py::_fit_bucket`
   now checks `cal.A < 0` post-fit and saves with
   `authority='QUARANTINED'` (instead of `VERIFIED`). Future bad fits
   never go live; operator must explicitly review and either accept the
   fallback or refit with tighter regularisation.

3. **Threshold rationale**: `A < 0` (strict inversion) is the
   unambiguous pathology. `A in [0, 0.3)` is weak but mathematically
   valid (model gives less weight to forecast than it should — possibly
   correct in extreme climates with very narrow bin distributions). 21
   buckets in the weak-but-positive range are kept VERIFIED.

## Quarantined buckets (12)

| Cluster | Season | Metric | Cycle | A | C | n | City-BLOCKED upstream? |
|---|---|---|---|---|---|---|---|
| Jeddah | JJA | high | 00 | -1.402 | -11.028 | 1472 | YES (TRANSFER_UNSAFE) |
| Jeddah | MAM | high | 12 | -1.186 | -10.038 | 504 | YES |
| Jeddah | DJF | high | 12 | -0.904 | -8.746 | 196 | YES |
| Jeddah | SON | high | 00 | -0.557 | -7.159 | 1456 | YES |
| Kuala Lumpur | DJF | high | 12 | -0.329 | -6.102 | 196 | YES |
| Jeddah | MAM | high | 00 | -0.283 | -5.899 | 1965 | YES |
| Jakarta | JJA | low | 00 | -0.153 | -5.308 | 53 | YES |
| NYC | DJF | low | 12 | -0.140 | -5.144 | 20 | NO (now safe via quarantine) |
| Busan | DJF | low | 12 | -0.118 | -5.146 | 48 | NO (now safe via quarantine) |
| Busan | DJF | low | 00 | -0.084 | -4.993 | 298 | NO (now safe via quarantine) |
| Beijing | DJF | low | 12 | -0.072 | -4.941 | 53 | NO (now safe via quarantine) |
| NYC | SON | low | 00 | -0.065 | -4.804 | 305 | NO (now safe via quarantine) |

## Verification

```bash
# Pre-quarantine: 12 VERIFIED inverted-A → 0 after UPDATE
sqlite3 state/zeus-world.db "
  SELECT COUNT(*) FROM platt_models_v2
   WHERE is_active=1 AND authority='VERIFIED' AND param_A < 0
"  # expect 0

# Loader probe (must return None for quarantined buckets)
.venv/bin/python -c "
from src.calibration.store import load_platt_model_v2
from src.types.metric_identity import LOW_LOCALDAY_MIN
import sqlite3
conn = sqlite3.connect('state/zeus-world.db', timeout=30.0)
conn.row_factory = sqlite3.Row
res = load_platt_model_v2(conn,
    temperature_metric=LOW_LOCALDAY_MIN.temperature_metric,
    cluster='NYC', season='DJF', cycle='12',
    data_version=LOW_LOCALDAY_MIN.data_version,
    input_space='width_normalized_density',
    source_id='tigge_mars', horizon_profile='full',
)
print('NYC low DJF cyc=12:', 'QUARANTINED (None)' if res is None else f'A={res[chr(65)]}')
"
```

## Open follow-up (separate task, not blocking launch)

Refit the 6 city-NOT-BLOCKED rows (NYC low DJF/SON, Busan low DJF×2,
Beijing low DJF) with stronger regularisation toward A=1.0 prior, so
they produce healthy calibrators rather than relying on fallback.
Estimated: ~30 min once new pairs accumulate.
