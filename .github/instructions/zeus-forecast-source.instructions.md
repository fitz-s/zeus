---
applyTo: "src/data/**/*.py,src/ingest/**/*.py,src/engine/evaluator.py"
---

# Zeus forecast + source truth review

These paths ingest external truth (weather observations, forecasts,
market snapshots) and convert it to Zeus probability signals. Data
provenance failures here produce silent systematic errors.

## Source identity

Every observation row must carry `source` (e.g. `wunderground`,
`noaa`, `ecmwf`) and `data_version`. Rows missing either field cannot
be traced back to an authority and should fail validation, not silently
default to a fallback source.

`observation_field` (e.g. `max_temp_c`, `precip_mm`) must be an
explicit enum value, not an inferred string. Mixed-unit observations
(°C vs °F, mm vs inches) must be unit-tagged; any arithmetic on
mixed-unit fields without explicit conversion is Critical.

## Timezone and DST

`observation_available_at` and `settlement_time` must be UTC. Local
time must never enter DB writes. Any code that converts to local time
before writing, or that inherits a naive datetime from an upstream
source, is Important. DST transitions: verify the source timezone
object is applied to the source's local zone, not the inference zone.

## Forecast ensemble semantics

ECMWF ensemble members have a minimum floor
(`ECMWF_MIN_MEMBERS`). An ensemble count below the floor must be
rejected, not averaged with a sparse subset. Check that new forecast
readers propagate member counts and that no reader accepts partial
ensembles silently.

## Market scanner provenance

`data/market_scanner.py` produces market metadata rows. Each row must
declare its Gamma source and not mix condition_id surfaces with token_id
surfaces. Market rows with `archived=true` must not be evaluated for
live candidate selection.

## Calibration pair writes

Calibration pair writes must include `data_version` matching the signal
that produced them. A calibration trained on v1 signals must not be
applied to v2 signals without explicit version reconciliation.
