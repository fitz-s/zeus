# 05 Time Geometry and DST Audit

## Ruling

Zeus has strong time-geometry intent in `observation_instants_v2`: UTC timestamp, local timestamp, timezone, UTC offset, DST active flag, ambiguous-hour flag, missing-hour flag, and time basis are recorded. That is a sound direction. But downstream compatibility tables and some daily/forecast paths still undermine time safety.

## DB-confirmed facts

- `observation_instants_v2` has 1,813,662 rows and zero nulls in timezone/local/UTC/unit/authority/data-version/provenance fields.
- `observations` has timezone and collection windows populated for all 42,749 rows, but 6 rows have nonpositive collection windows.
- `hourly_observations` has no timezone, UTC timestamp, offset, DST state, ambiguous/missing-hour flags, or station identity.
- `observation_instants` legacy has time geometry but lacks v2 authority/provenance/data-version fields.

## DST-specific failure modes still present

- A 25-hour fall-back local day cannot be safely represented in `hourly_observations` because uniqueness is `(city,obs_date,obs_hour,source)`; two local 01:00 hours conflict.
- A 23-hour spring-forward day can be hidden if a consumer expects 24 local hours.
- DST repair scripts can fill gaps with Ogimet/Meteostat fallback rows, but the DB does not make fallback eligibility first-class for downstream consumers.
- Daily observations rely on collection window metadata, but rows with nonpositive windows must be quarantined.

## Required hardening

1. Ban `hourly_observations` from canonical training/replay paths.
2. Make every daily observation expose computed `local_day_start_utc` and `local_day_end_utc` as validated fields, not only generic collection windows.
3. Add tests for spring-forward and fall-back days across multiple hemispheres/timezones.
4. Require source-specific backfills to report expected local-hour counts per day.
5. Add a readiness query that fails if any canonical row has missing/ambiguous/unexplained local-day geometry.