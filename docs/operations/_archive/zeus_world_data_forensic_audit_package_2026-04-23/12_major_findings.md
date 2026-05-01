# 12 Major Findings

## 1. Empty causal forecast/training/replay spine

- **Severity:** Critical
- **Evidence label:** [DB confirmed]
- **Tables/files involved:** forecasts, historical_forecasts(_v2), ensemble_snapshots(_v2), calibration_pairs(_v2), platt_models(_v2), replay_results, market_events(_v2)
- **Failure mode:** No causal forecast or market replay rows exist.
- **Why naive review misses it:** Reviewers see schemas and tests and assume pipeline is ready.
- **Real-world effect:** No probability calibration or causal replay can be trusted.
- **Blast radius:** All modeling/trading/replay paths.
- **Minimal fix direction:** Fail closed; populate v2 with verified issue/available times.
- **Verification needed:** Readiness query and row-count gates.
- **Rollback/containment:** Disable calibration/replay/live use of DB as truth.

## 2. Coverage ledger confirms forecast missingness

- **Severity:** Critical
- **Evidence label:** [DB confirmed]
- **Tables/files involved:** data_coverage
- **Failure mode:** Forecast rows are marked MISSING at large scale.
- **Why naive review misses it:** A table-count-only review misses coverage statuses.
- **Real-world effect:** Forecast silence can be mistaken for no data needed.
- **Blast radius:** Forecast ingest, hole scanner, readiness.
- **Minimal fix direction:** Treat MISSING as blocker; extend coverage to v2.
- **Verification needed:** Compare physical rows to coverage ledger.
- **Rollback/containment:** Fail forecast readiness until resolved.

## 3. Legacy settlements cannot represent high/low or multiple markets

- **Severity:** Critical
- **Evidence label:** [DB confirmed]
- **Tables/files involved:** settlements, settlements_v2
- **Failure mode:** Unique key city/date and high-only rows.
- **Why naive review misses it:** Rows exist and look settlement-like.
- **Real-world effect:** Low markets and duplicate city/date markets impossible.
- **Blast radius:** Settlement/replay/training labels.
- **Minimal fix direction:** Use settlements_v2 keyed by metric/market.
- **Verification needed:** Backfill v2 and enforce uniqueness.
- **Rollback/containment:** Mark v1 settlements evidence-only.

## 4. Settlement rows lack market identity

- **Severity:** Critical
- **Evidence label:** [DB confirmed]
- **Tables/files involved:** settlements
- **Failure mode:** All market_slug values are null.
- **Why naive review misses it:** Naive review focuses on settlement_value.
- **Real-world effect:** Cannot replay exact Polymarket contracts.
- **Blast radius:** Replay, market rules, oracle penalty.
- **Minimal fix direction:** Populate market slug/condition/token/rule IDs.
- **Verification needed:** Join settlements to market_events_v2.
- **Rollback/containment:** No exact replay from v1.

## 5. WU daily observations have empty provenance

- **Severity:** Critical
- **Evidence label:** [DB confirmed]
- **Tables/files involved:** observations
- **Failure mode:** Most WU rows are VERIFIED but provenance_metadata empty.
- **Why naive review misses it:** Authority stamp looks reassuring.
- **Real-world effect:** Rows cannot be reproduced or source-audited.
- **Blast radius:** Daily observations/training/settlement evidence.
- **Minimal fix direction:** Retrofit payload hashes/source URLs/parser version or quarantine.
- **Verification needed:** Provenance coverage SQL must return zero empty canonical rows.
- **Rollback/containment:** Exclude from training until fixed.

## 6. Legacy hourly table is not time-safe

- **Severity:** High
- **Evidence label:** [DB confirmed]
- **Tables/files involved:** hourly_observations
- **Failure mode:** No UTC/timezone/provenance/station/DST fields.
- **Why naive review misses it:** Huge row count creates confidence.
- **Real-world effect:** DST duplicate hours and station identity lost.
- **Blast radius:** Hourly monitoring/training.
- **Minimal fix direction:** Ban from canonical; use v2 only.
- **Verification needed:** Consumer tests reject hourly_observations.
- **Rollback/containment:** Compatibility-only view.

## 7. V2 hourly observations lack metric/training/causality fields

- **Severity:** High
- **Evidence label:** [DB confirmed]
- **Tables/files involved:** observation_instants_v2
- **Failure mode:** Good provenance but incomplete eligibility contract.
- **Why naive review misses it:** V2 name implies safe.
- **Real-world effect:** Fallback/source-mixed rows can enter training.
- **Blast radius:** Hourly evidence/Day0 features.
- **Minimal fix direction:** Add fields or eligibility view.
- **Verification needed:** Schema/readiness tests.
- **Rollback/containment:** Evidence only until hardened.

## 8. Current v2 observation view mixes fallback sources

- **Severity:** High
- **Evidence label:** [Cross-check confirmed]
- **Tables/files involved:** observation_instants_v2, zeus_meta, tier_resolver
- **Failure mode:** Data version filter does not separate source roles.
- **Why naive review misses it:** Rows share v1.wu-native stamp.
- **Real-world effect:** Fallback contamination of canonical family.
- **Blast radius:** Day0/live/training.
- **Minimal fix direction:** Add source_role and training/live/settlement eligibility.
- **Verification needed:** Source distribution SQL and consumer tests.
- **Rollback/containment:** Exclude fallbacks by default.

## 9. Settlement source cannot join directly to observation source

- **Severity:** High
- **Evidence label:** [DB confirmed]
- **Tables/files involved:** settlements, observations
- **Failure mode:** URL source vs short tag source; exact source join has zero matches.
- **Why naive review misses it:** Station parsing hides issue.
- **Real-world effect:** Station/source mismatch can go unnoticed.
- **Blast radius:** Settlement alignment.
- **Minimal fix direction:** Normalize source/station registry.
- **Verification needed:** Join by registry keys, not strings.
- **Rollback/containment:** Do not trust direct city/date only.

## 10. HKO observation and settlement semantics differ

- **Severity:** High
- **Evidence label:** [Cross-check confirmed]
- **Tables/files involved:** settlements, observations, settlement_semantics.py
- **Failure mode:** Decimal HKO observation vs integer settlement values.
- **Why naive review misses it:** Looks like mismatch/noise.
- **Real-world effect:** Wrong labels if raw obs used as settlement.
- **Blast radius:** Hong Kong and any oracle-transform market.
- **Minimal fix direction:** Store oracle transform and settlement value separately.
- **Verification needed:** Unit/rounding tests.
- **Rollback/containment:** Separate observation truth from settlement truth.

## 11. Forecast available_at may be reconstructed

- **Severity:** High
- **Evidence label:** [Code confirmed]
- **Tables/files involved:** forecasts_append.py, etl_historical_forecasts.py
- **Failure mode:** Issue/available time can be absent or heuristically derived.
- **Why naive review misses it:** Code comments claim point-in-time.
- **Real-world effect:** Hindsight leakage risk.
- **Blast radius:** Forecast training/replay.
- **Minimal fix direction:** Only verified source issue/available times in canonical rows.
- **Verification needed:** Tests seed missing issue_time and expect rejection.
- **Rollback/containment:** Mark reconstructed rows non-canonical.

## 12. TIGGE/v2 forecast architecture exists but no rows

- **Severity:** Medium
- **Evidence label:** [Code confirmed] [DB confirmed]
- **Tables/files involved:** extract_tigge_*.py, ensemble_snapshots_v2
- **Failure mode:** Strong extractor intent but empty DB.
- **Why naive review misses it:** Docs/code look advanced.
- **Real-world effect:** No actual ensemble signal in uploaded DB.
- **Blast radius:** Probability engine.
- **Minimal fix direction:** Run verified TIGGE ingest after source access validation.
- **Verification needed:** Row-count and sample manifest checks.
- **Rollback/containment:** Do not claim model ready.

## 13. Market event tables are empty

- **Severity:** Critical
- **Evidence label:** [DB confirmed]
- **Tables/files involved:** market_events, market_events_v2, market_price_history
- **Failure mode:** No market book/rules/prices.
- **Why naive review misses it:** Settlement rows mask absence.
- **Real-world effect:** Trading replay impossible.
- **Blast radius:** Replay/live execution.
- **Minimal fix direction:** Ingest Polymarket rules and prices.
- **Verification needed:** Market-event completeness checks.
- **Rollback/containment:** Disable replay.

## 14. Data coverage omits canonical v2 forecast/settlement families

- **Severity:** Medium
- **Evidence label:** [Code confirmed] [DB confirmed]
- **Tables/files involved:** data_coverage, hole_scanner.py
- **Failure mode:** Coverage ledger tracks limited table set.
- **Why naive review misses it:** Coverage table exists.
- **Real-world effect:** v2 gaps invisible to readiness.
- **Blast radius:** Audits/backfills.
- **Minimal fix direction:** Extend coverage schema or add v2 coverage ledger.
- **Verification needed:** Coverage queries include v2.
- **Rollback/containment:** Treat current coverage as partial.

## 15. Backfills can silently partially succeed

- **Severity:** High
- **Evidence label:** [Code confirmed]
- **Tables/files involved:** backfill_obs_v2.py, Ogimet/Meteostat scripts
- **Failure mode:** Scripts can continue after failed chunks/windows.
- **Why naive review misses it:** Rows still appear.
- **Real-world effect:** Partial local days contaminate features.
- **Blast radius:** Backfill truth.
- **Minimal fix direction:** Require manifests, expected counts, fail thresholds.
- **Verification needed:** Run consistency checks per day/city/source.
- **Rollback/containment:** Quarantine partial days.

## 16. INSERT OR REPLACE can erase audit history

- **Severity:** Medium
- **Evidence label:** [Code confirmed]
- **Tables/files involved:** observation_instants_v2_writer.py, backfill scripts
- **Failure mode:** Replacement semantics not tied to payload hash equality.
- **Why naive review misses it:** Idempotence assumed.
- **Real-world effect:** Revision/source drift hidden.
- **Blast radius:** All backfilled row families.
- **Minimal fix direction:** Compare hashes before replace; keep revision table.
- **Verification needed:** Test conflicting replacements.
- **Rollback/containment:** Avoid destructive replacement.

## 17. Derived features may inherit unsafe labels

- **Severity:** Medium
- **Evidence label:** [DB confirmed]
- **Tables/files involved:** diurnal_curves, diurnal_peak_prob, temp_persistence, solar_daily
- **Failure mode:** Derived tables populated while upstream trust incomplete.
- **Why naive review misses it:** Derived rows look model-ready.
- **Real-world effect:** Models learn from mixed/unsafe sources.
- **Blast radius:** Feature store.
- **Minimal fix direction:** Attach upstream lineage/data version.
- **Verification needed:** Feature lineage audit.
- **Rollback/containment:** Use only after input eligibility views.

## 18. Open-Meteo used as fallback/model but not settlement authority

- **Severity:** High
- **Evidence label:** [External source conflict]
- **Tables/files involved:** observation_client.py, hourly_instants_append.py, forecasts_append.py
- **Failure mode:** Gridded/model/archive data can be confused with station settlement.
- **Why naive review misses it:** High-quality API appears authoritative.
- **Real-world effect:** Wrong labels/Day0 features.
- **Blast radius:** Runtime/forecast/obs fallback.
- **Minimal fix direction:** Segregate Open-Meteo as model/fallback only.
- **Verification needed:** Source-role tests.
- **Rollback/containment:** Training_allowed=0 unless intended.

## 19. Meteostat bulk lag/aggregation not encoded in eligibility

- **Severity:** Medium
- **Evidence label:** [External source conflict]
- **Tables/files involved:** meteostat_bulk_client.py, observation_instants_v2
- **Failure mode:** Bulk fallback rows can appear verified.
- **Why naive review misses it:** Rows have station IDs and temps.
- **Real-world effect:** Stale or filled data used as truth.
- **Blast radius:** Gap fills/training.
- **Minimal fix direction:** Tag fallback/staleness.
- **Verification needed:** Date lag and provider checks.
- **Rollback/containment:** Evidence only.

## 20. Polymarket finalization/revision policy not in settlement key

- **Severity:** High
- **Evidence label:** [External source conflict]
- **Tables/files involved:** settlements, market_events_v2
- **Failure mode:** Market rules about final values and revisions not stored per market.
- **Why naive review misses it:** Settlement value exists.
- **Real-world effect:** Late revisions or wrong finalization contaminate labels.
- **Blast radius:** Settlement/replay.
- **Minimal fix direction:** Persist finalization/revision policy.
- **Verification needed:** Compare market rules to rows.
- **Rollback/containment:** Do not use v1 as final truth.