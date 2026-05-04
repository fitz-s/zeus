# Plan: Live Entry Data Contract Structural Fix
> Created: 2026-05-02 | Status: PROPOSED - CRITIC AMENDMENTS APPLIED

## Goal
Opening-hunt entries use an executable, DB-backed forecast source whose row coverage, source-run provenance, and policy authority are all true at trade time.

## Current Findings
- PR46 is clean and unrelated: `healthcheck-riskguard-live-label-2026-05-02` only fixes live RiskGuard label detection in `scripts/healthcheck.py` and `tests/test_healthcheck.py`.
- `review-crash-remediation-2026-05-02` is unique architecture remediation work, not an ancestor of PR46/main and not conflicting with PR46.
- `source-contract-protocol-slim-clean-2026-05-02` has two unique PR39 evidence/audit commits plus one patch-equivalent commit already in main; it is unrelated to PR46.
- Latest live cycles are healthy but all entries reject before edge computation because `ensemble.primary = ecmwf_ifs025` maps to `openmeteo_ensemble_ecmwf_ifs025`, which is intentionally not authorized for `entry_primary`.
- `src.ingest_main` owns the data daemon. Its TIGGE path is MARS archive/backfill and cannot be the same-day live source because of the public archive embargo.
- The intended same-day live source is ECMWF Open Data, but `ensemble_snapshots_v2` currently has zero `ecmwf_opendata_%` rows.
- Open Data logs show two producer failures: early 18Z selection before the latency window, and 12Z extractor timeout at 300 seconds despite raw GRIBs existing.
- `source_health.json` green only proves upstream probe health. It does not prove row coverage or entry readiness.

## Structural Diagnosis
The broken relationship is not a single missing flag. The producer, source policy, reader, evaluator, and health surfaces encode different truths:

- Data daemon says Open Data is same-day live source.
- Registry says Open Data is diagnostic/non-executable.
- Evaluator reads `ensemble.primary` through `fetch_ensemble`, so it asks Open-Meteo for entry-primary and is correctly blocked.
- TIGGE DB reader only reads `tigge_%` versions, not Open Data versions.
- Healthcheck/status can stay green while executable forecast coverage is zero.

## Design Decisions
1. Open-Meteo ensemble remains non-entry fallback. Do not authorize `openmeteo_ensemble_ecmwf_ifs025` for money-lane entry.
2. TIGGE MARS remains archive/backfill unless a separate live-causal TIGGE path is proven. Do not use T-2 archive to unblock same-day live trading.
3. ECMWF Open Data may become entry-primary only through verified DB rows, not direct API fetches.
4. Entry readiness must be row-scoped: `(source_id, city, target_date, temperature_metric, data_version)` plus fresh source-run provenance.
5. Source health, data coverage, and executable entry readiness must be displayed as separate signals.
6. Entry source identity must be separate from API model identity. `ensemble.primary = ecmwf_ifs025` is ambiguous because it names both an Open-Meteo model and the intended ECMWF authority family; introduce an explicit entry forecast source/config seam instead of relying on model aliases.
7. Calibration authority is a live-entry gate. Open Data inference may not size orders until a named calibration policy is explicit in evidence and tests.
8. Registry authorization must be transport-aware. A scheduled DB snapshot source can be executable only through the snapshot reader; static role authorization alone is insufficient.
9. Decision snapshot persistence must preserve forecast provenance. Open Data inputs must never be persisted under TIGGE data versions or metric identities.

## Critic Review Amendments
- Critical: the plan must not assume PR45 readiness tables are already operational. `source_run` and `readiness_state` helpers exist, but producer integration appears incomplete; Phase 1 must write and validate those rows before the reader trusts them.
- Critical: Open Data startup currently logs child failures but can still report scheduler success because `_opendata_startup_catch_up()` does not aggregate and return child results. Add a relationship test for this exact false-green class.
- Critical: do not make `ecmwf_open_data` broadly pass `gate_source_role(..., "entry_primary")` unless the call path is DB-backed. A direct `fetch_ensemble` path for `ecmwf_open_data` would recreate the same semantic leak in a new name.
- High: the reader strategy needs an explicit compatibility decision. Current evaluator expects `times` plus `members_hourly`; existing TIGGE code solves daily high/low rows by building a symmetric high/low hourly grid. The first implementation should reuse or extract that compatibility adapter, then consider an extrema-native evaluator refactor later.
- High: extractor timeout is a symptom, not the design. The durable fix should either make extraction resumable/chunked or run it as a long worker with progress/status, locks, and no launchd crash loop. A larger timeout alone is allowed only as a temporary operational mitigation.
- High: calibration policy is not optional for live entry. Before any orders, Open Data inference must either use a named transition calibration policy with evidence or have source-specific calibration promoted. Silent TIGGE-trained calibration reuse is not acceptable.
- Medium: scheduler config has two truths today: `config/settings.json` lists `ecmwf_open_data_times_utc`, while `src/ingest_main.py` hardcodes 07:30/07:35 UTC. The implementation must collapse this into one release calendar authority.
- Medium: after producer fixes, use the existing 2026-05-02 12Z raw GRIBs as a shadow backfill probe to prove extraction/ingest before touching evaluator.

## Independent Critic Blocking Amendments
- Reject condition: do not implement from this plan until these amendments are incorporated into the task breakdown and tests.
- Calibration must move to Phase 0. Choose either `SHADOW_ONLY` for Open Data until source-specific calibration exists, or a named transition policy such as `ecmwf_open_data_uses_tigge_localday_cal_v1` with evidence. Decision evidence must include `forecast_source_id`, `forecast_data_version`, `calibration_source_id`, `calibration_data_version`, and `calibration_policy_id`.
- Current evaluator snapshot persistence can mis-stamp Open Data as TIGGE because `_store_ens_snapshot()` uses metric identity data versions. Phase 4 must change persistence so Open Data decisions link to input snapshot IDs/source runs and never write TIGGE `data_version` for Open Data evidence.
- Do not add static `entry_primary` to `ecmwf_open_data` in a way that lets `fetch_ensemble(model="ecmwf_open_data", role="entry_primary")` pass the old direct-fetch path. Add a reader/transport-specific gate, or extend the registry spec with allowed executable transports.
- Define readiness scope before implementation. Existing `get_entry_readiness()` requires strategy/market/condition fields; producer-only city/date/metric readiness will not satisfy it unless evaluator composes market readiness explicitly.
- Synthetic hourly timestamps must be local-day-correct. Existing TIGGE-style `YYYY-MM-DDTHH:00:00+00:00` rows can map to the wrong local date for non-UTC cities. The adapter must build local timestamps in the city timezone and convert to UTC, or the first implementation must be extrema-native.
- Health/status changes need closed contracts: enum values, blocker window, candidate denominator, counted rejection stages, and output fields. Add tests before changing healthcheck behavior.
- `ingest_status_writer` must report `ensemble_snapshots_v2`, source-run, and readiness coverage. Legacy `ensemble_snapshots` counts are not executable coverage.
- Implementation must happen in a fresh worktree/branch from the chosen base, not on the PR46 healthcheck branch. Current branch has unrelated untracked docs and live plan artifacts.
- Add a rollout kill switch/default block so orders remain disabled until producer rows, reader shadow checks, calibration policy, and health/status readiness all pass.

## Phase 0: Isolation And Authority Decisions
- Files: new implementation worktree, `config/settings.json`, `src/config.py`, calibration manager/evidence docs, tests covering calibration identity.
- What:
  - Create a fresh worktree/branch from `main` or another explicit operator-approved base.
  - Decide and encode calibration policy before evaluator entry changes.
  - Define explicit entry forecast source config separate from `ensemble.primary`.
  - Define readiness scope: producer city/date/metric readiness plus evaluator-composed market readiness, or fully market-scoped readiness with a named writer.
  - Keep order submission blocked by default for the new source until rollout gates pass.

## Phases

### 1. Make Open Data Producer Truthful
- Files: `src/data/ecmwf_open_data.py`, `src/ingest_main.py`, `tests/test_ingest_boot_time_semantics.py`, `tests/test_opendata_writes_v2_table.py`
- What:
  - Fix `_default_cycle()` so it never selects an ECMWF run before the configured latency window has elapsed.
  - Use `config/settings.json` `discovery.ecmwf_open_data_times_utc` or an explicit run calendar instead of hardcoding only 07:30/07:35 UTC.
  - Increase or restructure extractor timeout for ~500MB mx2t6/mn2t6 GRIBs, or split extraction so progress is bounded and resumable.
  - Make Open Data startup catch-up return a failed aggregate status when either track fails, not a success wrapper around child failures.
  - Record download/extract/ingest stage detail in scheduler health and/or source-run tables.
  - Write source-run rows for attempted, failed, partial, and successful Open Data runs, then write readiness rows only after matching snapshot rows exist.

### 2. Add Executable Forecast Snapshot Reader
- Files: new `src/data/executable_forecast_reader.py`, `src/data/ecmwf_open_data.py`, `src/data/tigge_db_fetcher.py`, `src/contracts/snapshot_ingest_contract.py`, `src/state/schema/v2_schema.py`
- What:
  - Read `ensemble_snapshots_v2` by city, target local date, and high/low metric.
  - Prefer `ecmwf_opendata_*` data versions for live inference; fall back only to policy-approved archive rows when explicitly allowed.
  - Validate member count, unit, authority, causality, target-date scope, issue/available/fetch/recorded times, and freshness.
  - Return evaluator-compatible evidence fields: `source_id`, `forecast_source_role`, `degradation_level`, `authority_tier`, `raw_payload_hash`, `captured_at`, and member vectors.
  - Reuse or extract the existing high/low daily-row compatibility adapter from `src/data/tigge_client.py`, but fix timestamp generation so synthetic hourly columns represent the city local target date converted to UTC.
  - Keep an extrema-native evaluator interface as a later refactor, not the first live unblock.

### 3. Promote DB-Backed Open Data In Registry
- Files: `src/data/forecast_source_registry.py`, `tests/test_forecast_source_registry.py`, `tests/test_ensemble_client.py`
- What:
  - Keep Open-Meteo blocked for `entry_primary`.
  - Represent `ecmwf_open_data` as an executable scheduled collector only when read through the DB-backed reader or transport-specific gate.
  - Avoid adding a direct `fetch_ensemble(model="ecmwf_open_data")` API path unless it routes exclusively through the verified DB reader.
  - Add tests proving `ecmwf_open_data` cannot be authorized through the old Open-Meteo/direct-fetch path.

### 4. Wire Entry Evaluation To Reader
- Files: `src/engine/evaluator.py`, `src/config.py`, `config/settings.json`, `tests/test_runtime_guards.py`, `tests/test_decision_evidence_runtime_invocation.py`
- What:
  - Introduce an explicit entry forecast source setting, separate from `ensemble.primary`, so API model aliases cannot choose money-lane authority.
  - Replace the entry-primary path that currently calls `fetch_ensemble(... model=ensemble_primary_model(), role="entry_primary")` with the executable snapshot reader.
  - Missing or stale rows reject as `DATA_UNAVAILABLE` or `ENTRY_READINESS_MISSING`, not `SourceNotEnabled`.
  - Complete Open Data rows reach p_raw/p_cal/edge computation with `source_id=ecmwf_open_data`, role `entry_primary`, and degradation `OK`.
  - Keep crosscheck/diagnostic fallback separate from executable entry source.
  - Attach explicit calibration identity/source policy to every executable decision before order sizing.
  - Update decision snapshot persistence so forecast `data_version`, `source_id`, `input_snapshot_id`, and `source_run_id` are preserved and Open Data is never written as TIGGE.

### 5. Align Monitor Refresh
- Files: `src/engine/monitor_refresh.py`, `tests/test_runtime_guards.py`
- What:
  - Monitor refresh should share the executable reader when it needs the same authority as entry.
  - If monitor uses degraded fallback, label it explicitly as monitor-only and non-executable.

### 6. Surface Readiness In Health/Status
- Files: `src/data/ingest_status_writer.py`, `scripts/healthcheck.py`, `src/control/freshness_gate.py`, `state/status_summary.json` writer path, `tests/test_healthcheck.py`
- What:
  - Add/validate closed rejection/blocker enum contracts before emitting new status values.
  - Show `upstream_reachable`, `rows_written`, `source_run_complete`, and `entry_ready_coverage` separately.
  - Report `ensemble_snapshots_v2` coverage by source/data_version/metric/target date; do not use legacy `ensemble_snapshots` as executable coverage.
  - Healthcheck should flag all-candidate source-policy/coverage rejection as a live-alpha blocker even when daemon and RiskGuard are green.
  - Define healthcheck blocker logic exactly: lookback window, candidate denominator, rejection stages/reasons counted, and output fields.
  - Source health green must never imply trading readiness.

## Relationship Tests
- `source_health` green plus zero Open Data rows does not authorize entry.
- Open Data rows without complete source-run/readiness provenance do not authorize entry.
- Open Data startup catch-up with one failed child track writes scheduler/source-run failure, not OK.
- Fresh complete Open Data high/low rows authorize p_raw computation for their matching metric/date.
- Open-Meteo ensemble remains blocked for `entry_primary`.
- `ensemble.primary = ecmwf_ifs025` cannot silently select entry authority; entry source comes from the explicit entry-source config seam.
- `fetch_ensemble(model="ecmwf_open_data", role="entry_primary")` remains blocked unless it routes through the verified DB-reader transport.
- Open Data evidence is never persisted with a TIGGE `data_version` or source identity.
- Synthetic daily-row adapter preserves local target date for New York DST, London DST, Tokyo, Sydney, and UTC cities.
- TIGGE archive cannot serve same-day live unless an explicit live-causal proof exists.
- Partial members, stale `recorded_at`, wrong unit, wrong local target date, unverified authority, or failed source run fail closed.
- Calibration identity/source mismatch fails closed or marks the decision shadow-only.
- `ingest_status_writer` reports zero executable v2 coverage when only legacy snapshots exist.
- Healthcheck reports a blocker when every candidate rejects at forecast source policy or coverage before edge computation.

## Live Rollout
1. Fix Open Data producer and run in shadow until `ecmwf_opendata_%` rows exist for current forward target dates.
2. Add reader in shadow and compare probability vectors against diagnostic paths without placing orders.
3. Enable evaluator reader behind a feature/config gate with entries still observable and capped.
4. Enable monitor refresh alignment.
5. Promote health/status readiness signals.
6. Only then allow normal opening-hunt entry flow.

## Do Not Do
- Do not make Open-Meteo ECMWF entry-primary.
- Do not switch live to TIGGE as a same-day source while only T-2 MARS archive is available.
- Do not bypass evaluator evidence checks.
- Do not treat `source_health.json` as an entry-readiness proof.
- Do not merge PR39/source-contract branches wholesale into this fix; cherry-pick only proven relevant contracts if needed.

## Open Questions
- Should Open Data inference initially reuse existing TIGGE-trained calibration with explicit metadata, or require separate calibration promotion before entries?
- Should entry use only Open Data 00Z/12Z, or also 18Z after the full latency window?
- Where should the row-scoped readiness proof live: existing `readiness_state` tables, source-run tables, or a compact materialized coverage table?
