# 10 Downstream Usage Audit

## Calibration

Current DB cannot support calibration: `calibration_pairs` and `calibration_pairs_v2` are empty, and the forecast/ensemble inputs they require are empty. Rebuild code has useful v2 gates but must not run against mixed/fallback observations without source-role and settlement alignment.

## Replay

Replay is not supported. `market_events`, `market_events_v2`, `market_price_history`, `probability_trace_fact`, `outcome_fact`, `replay_results`, and decision/trade tables are empty. Settlement rows lack market slug. A replay engine would be replaying a city/date label approximation, not the market Zeus actually saw.

## Settlement reconstruction

Settlement reconstruction is partially possible as evidence for high-temperature values. Exact replay is unsafe because `settlements_v2` is empty, `market_slug` is null, and `settlements` is high-only/city-date keyed. HKO rows prove that observation value and settlement value may differ by oracle transform.

## Live monitoring

Live monitoring can use `observation_instants_v2` as evidence if source-role/eligibility filters are added. It must not use `hourly_observations` as canonical. Day0 fallback data can support dashboards and anomaly detection but should be tagged runtime-only unless the market source matches.

## Daily observations

Daily observations are useful but not canonical. WU rows require provenance retrofit. HKO and Ogimet rows need source-specific treatment. Consumers must check station/source/unit and not assume city/date means market alignment.

## Hourly observations

The v2 table is the only plausible hourly evidence table. Legacy `hourly_observations` is lossy and should be compatibility-only. The ETL that creates it must not be treated as a proof of DST or settlement correctness.

## Historical forecasts

Historical forecast tables are empty. Any future use must record source-issued issue/availability times. Reconstructed availability belongs in a non-canonical lane unless externally verified.

## Oracle penalty / settlement risk

Oracle penalty models need exact source/station/finalization metadata and market-specific settlement transforms. Current settlement table can help discover penalties but cannot train a reliable oracle-risk model without market identity and source-role separation.

## Logic hole: “some data exists”

The largest risk is downstream code treating row existence as data safety. Readiness checks must require source-role, authority, provenance, metric, causality, local-day geometry, and market identity depending on use case.