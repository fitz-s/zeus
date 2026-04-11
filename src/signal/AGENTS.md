# src/signal AGENTS — Zone K3 (Math/Data)

## WHY this zone matters

Signal is where Zeus converts 51 raw ensemble members into tradeable probability vectors. The critical insight: WU settles on integers, so probability mass concentrates at bin boundaries. Simple member-counting ignores measurement uncertainty — Zeus's Monte Carlo simulates the full chain: `atmosphere → NWP member → ASOS sensor noise (σ ≈ 0.2–0.5°F) → METAR rounding → WU integer display`.

If you break the Monte Carlo or remove the sensor noise, P_raw becomes systematically wrong at every bin boundary.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `ensemble_signal.py` | 51 members → P_raw via Monte Carlo | HIGH — core probability engine |
| `day0_signal.py` | Day-0 observation replaces forecast | MEDIUM — hard floor logic |
| `day0_window.py` | When to enter day-0 mode | LOW |
| `forecast_uncertainty.py` | Bootstrap σ sources for CI | MEDIUM — feeds double-bootstrap |
| `model_agreement.py` | Inter-model agreement scoring | LOW |
| `diurnal.py` | Diurnal cycle adjustments | LOW |

## Domain rules

- Monte Carlo N is configurable (`ensemble_n_mc`) — don't hardcode
- Instrument noise σ is per-unit (°C calibrated independently, not °F/1.8)
- Bimodal detection uses KDE, not simple range checks
- `SettlementSemantics` from `src/contracts/` must round all simulated values — never do raw rounding here

## Common mistakes

- Removing or reducing Monte Carlo iterations "for speed" → destroys bin-boundary accuracy
- Using mean instead of per-member daily max → wrong physical quantity
- Ignoring unit-specific sensor noise → °C cities get wrong σ
- Forgetting timezone handling for `select_hours_for_target_date` → wrong day's max
