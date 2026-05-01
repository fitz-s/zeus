# required_repo_reads.md

## Authority and workspace files read or checked

- `AGENTS.md` — root source/settlement/high-low warnings and graph-derived-context caveat.
- `workspace_map.md` — workspace topology.
- `docs/operations/current_state.md` — active operational state.
- `docs/authority/zeus_current_architecture.md` — current architecture and source roles.
- `docs/authority/zeus_current_delivery.md` — delivery/packet state.
- `docs/reference/zeus_math_spec.md` — settlement rounding and math rules.
- `architecture/invariants.yaml` — point-in-time, missing-data, metric-spine, and v2 laws.
- `architecture/source_rationale.yaml` — source hazards and repair/write-path guidance.
- `architecture/script_manifest.yaml` — script lifecycle and dry-run/apply expectations.
- `architecture/test_topology.yaml` — test routing.
- `architecture/history_lore.yaml` — active lessons: archive non-promotion, WMO rounding, VERIFIED-as-contract, diagnostic backtest non-promotion.
- `architecture/negative_constraints.yaml` — no JSON authority, no bare unit assumptions, no daily-low legacy rows, no high/low mixing.
- `scripts/AGENTS.md` — scripts must be manifest-governed; diagnostics must not write canonical truth; ETL/repair must declare dry-run/apply.
- `src/data/AGENTS.md` — endpoint availability is not source correctness; feed families must not collapse; Open-Meteo grid is not settlement-adjacent truth.
- `src/state/AGENTS.md` — canonical DB/event truth outranks JSON/CSV/status exports.
- `src/contracts/AGENTS.md` — `SettlementSemantics` is mandatory; HKO oracle truncation is restricted to HKO-style contracts.

## Major source/script files read or checked

- `src/state/db.py`
- `src/contracts/settlement_semantics.py`
- `src/data/tier_resolver.py`
- `src/data/observation_instants_v2_writer.py`
- `src/data/observation_client.py`
- `src/data/daily_obs_append.py`
- `src/data/hourly_instants_append.py`
- `src/data/forecasts_append.py`
- `src/data/rebuild_validators.py`
- `src/data/wu_hourly_client.py`
- `src/data/ogimet_hourly_client.py`
- `src/data/meteostat_bulk_client.py`
- `src/data/openmeteo_client.py`
- `src/data/polymarket_client.py`
- `src/data/ecmwf_open_data.py`
- `src/data/ensemble_client.py`
- `src/data/ingestion_guard.py`
- `src/data/hole_scanner.py`
- `scripts/backfill_obs_v2.py`
- `scripts/fill_obs_v2_dst_gaps.py`
- `scripts/fill_obs_v2_meteostat.py`
- `scripts/backfill_hko_daily.py`
- `scripts/backfill_ogimet_metar.py`
- `scripts/backfill_wu_daily_all.py`
- `scripts/backfill_hourly_openmeteo.py`
- `scripts/backfill_tigge_snapshot_p_raw.py`
- `scripts/backfill_tigge_snapshot_p_raw_v2.py`
- `scripts/rebuild_calibration_pairs_v2.py`
- `scripts/rebuild_calibration_pairs_canonical.py`
- `scripts/etl_historical_forecasts.py`
- `scripts/etl_hourly_observations.py`
- `scripts/etl_tigge_ens.py`
- `scripts/extract_tigge_mn2t6_localday_min.py`
- `scripts/extract_tigge_mx2t6_localday_max.py`
- `scripts/audit_city_data_readiness.py`
- `scripts/data_completeness_audit.py`

## Relevant tests sampled

- `tests/test_obs_v2_writer.py` — confirms missing-provenance/source-tier validator antibodies, but uses in-memory fixtures.
- `tests/test_tier_resolver.py` — confirms 47 WU + 3 Ogimet + 1 HKO source split and no Open-Meteo tier, but does not make the DB safe by itself.
- `tests/test_hourly_clients_parse.py` — confirms extremum-preserving WU/Ogimet hourly aggregation fixtures.
- `tests/test_backfill_openmeteo_previous_runs.py` — present but content was not useful in the remote snapshot opened during audit.
- Additional tests listed by `src/data/AGENTS.md` and graph.db should be run locally.

LOCAL_VERIFICATION_REQUIRED: Run exact local `git grep`, test discovery, and full pytest because remote raw-file review cannot prove hidden paths are absent or that tests constrain the real uploaded DB.
