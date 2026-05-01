# 11 Data Readiness Ruling

## Safe now

- Read-only forensic auditing.
- Evidence exploration of station observations with caveats.
- Gap analysis using `data_coverage`.
- Development of v2 migrations/guards using existing empty v2 scaffolding.

## Unsafe now

- Live trading decisions that treat current DB rows as canonical settlement truth.
- Calibration/training from uploaded DB.
- Forecast probability replay.
- Exact Polymarket market replay.
- Settlement reconstruction where market identity, source finalization, or station mapping must be proven.
- Any canonical use of `hourly_observations`.

## Usable only as evidence

- `observations` WU/HKO/Ogimet daily rows.
- `settlements` high-only rows.
- `observation_instants_v2` hourly rows until source-role and training/causality fields are added.
- `observation_instants` and `hourly_observations` legacy rows.
- Derived feature tables whose upstream lineage is not explicit.

## Requires provenance hardening

- WU daily observations with empty provenance.
- All rows lacking payload hash/source URL/parser version.
- Settlement rows lacking market slug/condition id.
- Backfill scripts with `INSERT OR REPLACE` but no hash/semantic replacement check.

## Requires source-tier redesign

- Fallback rows in `observation_instants_current`.
- Meteostat/Ogimet fills.
- Open-Meteo archive/previous-runs lane.
- WU/HKO exact settlement source separation.

## Requires backfill redesign

- Daily WU backfill.
- Ogimet and Meteostat fill scripts.
- Open-Meteo historical forecast ETL.
- TIGGE snapshot population.
- Settlement v2 migration.

## Requires downstream guardrails

- Calibration rebuilds.
- Replay engine.
- Live decision loop.
- Oracle penalty calculations.
- Data readiness audits.