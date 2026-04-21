# src/types AGENTS

Zone: K0_frozen_kernel — cross-cutting unit safety

## What this code does (and WHY)

Temperature unit confusion was the #1 class of bugs in the legacy predecessor system. Zeus prevents this with typed containers: `Temperature` (absolute: 72°F) and `TemperatureDelta` (differences: σ=0.3°F). Cross-unit operations raise `UnitMismatchError` at runtime. Values stay in their native unit (Dallas=°F, London=°C) — there is NO single-unit refactor. Conversions are explicit via `.to(target_unit)`.

## Key files

| File | Purpose | Watch out for |
|------|---------|---------------|
| `temperature.py` | Temperature and TemperatureDelta typed containers with unit safety | Conversion has offset for absolute, scale-only for delta |
| `market.py` | Market-related type definitions | — |
| `solar.py` | Solar/diurnal type definitions | — |

## Domain rules

- Temperature vs TemperatureDelta are DISTINCT types: absolute values have offset in conversion (+32), deltas are scale-only (1°C = 1.8°F)
- Cross-unit arithmetic raises `UnitMismatchError` — this is intentional, not a bug
- Polymarket bins use native units — do NOT convert to a canonical unit
- All historical calibration data is native-unit

## Common mistakes agents make here

- Treating TemperatureDelta like Temperature (applying offset to a std dev — wrong)
- Converting everything to °F or °C "for simplicity" (breaks calibration data)
- Catching UnitMismatchError to suppress it (the error IS the safety mechanism)

## References
- Root rules: `../../AGENTS.md`
- Settlement semantics (where units matter most): `../contracts/settlement_semantics.py`
