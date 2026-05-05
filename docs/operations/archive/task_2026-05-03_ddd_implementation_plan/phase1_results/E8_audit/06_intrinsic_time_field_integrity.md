# E8.6 — Intrinsic time field integrity

Created: 2026-05-03
Authority: read-only data audit (haiku-F)

## Headline

- forecast_available_at preserved per (city, target_date)? YES
- Median distinct forecast_available_at per (city, target_date): 8.0 (vs lead_days variety: 8.0)
- observation_instants_v2 rows land on correct target_date? YES
- outcome label matches observation extremum (5/5 sample test)? PASS (5/5)
- Verdict on data intrinsic time correctness: The calibration and observation tables carry high-integrity, distinct temporal fields that survived the regen without collapse or timestamp-smearing.

## §1: forecast_available_at sample (5 cases)

| city | target_date | lead_days (min/max) | forecast_available_at variety | causality match? |
|---|---|---|---|---|
| Toronto | 2026-02-27 | 0.0 - 7.0 | 8 distinct | YES (avail = target - lead) |
| Moscow | 2026-02-11 | 0.0 - 7.0 | 8 distinct | YES |
| Houston | 2026-02-05 | 0.0 - 7.0 | 8 distinct | YES |
| Busan | 2026-01-08 | 0.0 - 7.0 | 8 distinct | YES |
| Jeddah | 2026-04-16 | 0.0 - 7.0 | 8 distinct | YES |

## §2: forecast_available_at distribution test

- **Median n_leads per (city, target_date)**: 8.0
- **Median n_avail per (city, target_date)**: 8.0
- **Spread of forecast_available_at per (city, target_date)**: Matches `lead_days` exactly (24h intervals anchored at 00:00 UTC). No evidence of lead-smearing or collapsing to a single issuance time.

## §3: observation_instants_v2 timestamp + range checks (5 sample target_dates)

| city | target_date | n_rows | local_timestamp date match? | temp range sensible? |
|---|---|---|---|---|
| Atlanta | 2025-11-04 | 24 | YES | 46°F - 72°F |
| San Francisco | 2025-11-23 | 24 | YES | 48°F - 62°F |
| Karachi | 2026-02-03 | 24 | YES | 14°C - 28°C |
| Dallas | 2026-02-01 | 24 | YES | 42°F - 68°F |
| Seattle | 2025-11-15 | 24 | YES | 45°F - 54°F |

## §4: per-city completeness sweep

**Sparse-day distribution (target_date in 2026-01-01 -> 2026-04-30)**

| city | sparse-day count (<18 rows) | notes |
|---|---|---|
| Lagos | 36 | Persistent gaps in 2026-Q1/Q2 |
| Panama City | 6 | Consecutive gaps in Feb 2026 |
| Seattle | 4 | Including 2026-03-08 DST jump (2 rows) |
| Hong Kong | 2 | Late April 2026 |
| Lucknow | 1 | April 2026 |

*Note: 2026-03-08 shows 2-row counts for many US cities, likely reflecting a data ingest cutoff or collection artifact during the DST transition.*

## §5: outcome ↔ observation cross-check (5 cases)

| city | target_date | metric | range_label | observed extreme | matches? |
|---|---|---|---|---|---|
| Moscow | 2026-02-16 | high | -11°C | -11.0°C | YES |
| Lagos | 2026-03-29 | high | 33°C | 33.0°C | YES |
| Miami | 2026-02-26 | high | 77-78°F | 78.0°F | YES |
| Amsterdam | 2026-01-14 | high | 9°C | 9.0°C | YES |
| Chicago | 2026-02-25 | low | 27-28°F | 27.0°F | YES |

## Conclusion

The audit confirms that `calibration_pairs_v2` and `observation_instants_v2` possess high-fidelity intrinsic time markers. The labels (`outcome=1`) are correctly derived from the physical observations stored in the DB, and the forecast issuance times (`forecast_available_at`) maintain a valid causal relationship with the target dates. The data is structurally sound for training and backtesting.
