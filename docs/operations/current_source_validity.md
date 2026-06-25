# Current Source Validity

Status: CURRENT_FOR_LIVE - replacement forecast shadow/veto simple-switch fact refresh
Last audited: 2026-06-07T00:50:34.580045+00:00
Max staleness: 14 days for replacement forecast simple-switch planning
Authority status: not authority law; audit-bound current fact only

## Replacement Forecast Simple-Switch Evidence

- Open-Meteo ECMWF IFS 9km endpoint verified: run=2026-06-06T06:00 target_local_date=2026-06-07 samples=24 high_c=28.60 low_c=19.40 url=https://single-runs-api.open-meteo.com/v1/forecast?latitude=31.2304&longitude=121.4737&hourly=temperature_2m&models=ecmwf_ifs&run=2026-06-06T06%3A00&forecast_hours=72&temperature_unit=celsius&timezone=Asia%2FShanghai
- AIFS GRIB metadata verified: .local/replacement_raw/aifs_ens_20260605_00z_step0_pf_member001_2t.meta.json sha256=eae7da9d1542fdc34ebf420709a9ac7ff4836cb673f11f4813279316372eb51d
- AIFS sampled-2t identity verified from .local/replacement_raw/aifs_ens_20260605_00z_step0_pf_member001_2t.meta.json and .local/replacement_raw/aifs_sample_points_from_implemented_materializer.json
- Settlement source routing document inspected; replacement path does not propose source-route changes
- Live root pre-existing read files exist under /Users/leofitz/zeus; refit handoff is supplied by simple-switch install plan
- Replacement schema dry-run verified from .local/replacement_reports/replacement_schema_dry_run.json: committed=false created=['raw_forecast_artifacts', 'deterministic_forecast_anchors', 'forecast_posteriors']
- Materialization seed builder verified: market bins and baseline source-run coverage are converted into validated seed JSON
- Materialization seed discovery verified: live shadow can generate seed JSON from forecast DB targets plus raw manifests
- Materialization request builder verified: seed JSON is validated before entering the shadow queue
- EMOS product identity verified: replacement product-keyed cell ready and legacy city|season|metric cell blocked
- Refit gate verified: baseline EMOS reuse blocks promotion and product-specific refit stays non-live without promotion request
- Fine-tune artifact builder verified: nested Brier/log loss folds and selected soft-anchor parameter are written as durable JSON
- Refit handoff builder verified: fine-tune output is converted into product-keyed non-live EMOS/data-refit handoff JSON
- Refit handoff install planner verified: ready handoff artifacts are dry-run validated before optional live-root write
- Promotion evidence composer verified: runtime promotion evidence is composed from before/after, same-CLOB, q_lcb, fine-tune, and refit reports
- Full replacement test suite verified from .local/replacement_reports/replacement_full_suite_pytest.json: 309 passed in 23.48s
- Event reactor no-bypass suite verified from .local/replacement_reports/event_reactor_no_bypass_pytest.json: 95 passed, 1 xfailed in 7.59s

## Notes

- Generated read-only; missing evidence remains false.

## Guardrails

- This current-fact refresh authorizes shadow/veto readiness only.
- It does not authorize live trade authority, Kelly increase, direction flip, settlement rewrites, calibration refit promotion, or source-route changes.
