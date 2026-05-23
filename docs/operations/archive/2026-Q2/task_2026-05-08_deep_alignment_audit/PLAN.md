# Ongoing Deep Alignment Audit

Created: 2026-05-08
Last updated: 2026-05-08
Authority basis: root `AGENTS.md`, `.claude/CLAUDE.md`, `docs/operations/current_state.md`, `docs/operations/current_data_state.md`, `docs/operations/current_source_validity.md`, `architecture/invariants.yaml`, module `AGENTS.md`, and topology navigation for this task.

## Purpose

This is a living, read-only project audit for mismatch classes that ordinary code review is bad at finding.

Implementation tracking split: safe-to-open implementation work is now tracked in `docs/operations/task_2026-05-08_alignment_safe_implementation/PLAN.md`. Keep this file as the evidence/audit source; use the tracker file as the task list.

The target is not style, local code smell, or isolated bugs. The target is belief-reality drift across Zeus's money path:

`contract semantics -> source truth -> forecast signal -> calibration -> edge -> execution -> monitoring -> settlement -> learning`

The audit is continuous. Each run updates this matrix with evidence, status, and next probes rather than producing a one-time review artifact.

## Operating Mode

- Read-only evidence by default: no source edits, no DB writes, no live venue calls, no config mutation.
- Report updates are allowed only in this packet after topology admission.
- Data claims must distinguish authority, current planning fact, historical evidence, and derived context.
- A finding is not complete until it names the cross-module relationship that failed.
- A proposed fix is out of scope for this packet unless the operator explicitly opens an implementation packet.

## Current Baseline

- Current date: 2026-05-08.
- Current worktree observed: `fix/263-source-disagreement-isolation-2026-05-08`.
- `main..HEAD` was empty during planning, so this audit should align to current `main` law while treating local uncommitted files as unaudited worktree state.
- `current_data_state.md` is still within its 14-day planning freshness window from 2026-04-28, but it does not authorize live/prod DB mutation.
- `current_source_validity.md` includes 2026-05-03 Paris conversion completion and still treats Hong Kong/HKO as an explicit caution path requiring fresh evidence for present-tense truth claims.
- Topology route for read-only orientation: T0/generic admitted. Topology route for this packet admitted `PLAN.md`; `REPORT.md` and `scope.yaml` were not admitted and are intentionally not created yet.

## Continuous Update Protocol

Each audit pass should update the matrix below instead of appending a detached narrative.

For each axis, record:

- `Status`: NOT_STARTED, PASS, WARN, FAIL, BLOCKED, or NEEDS_FRESH_FACTS.
- `Evidence`: exact read-only query, command, file, or table inspected.
- `Mismatch`: the belief-reality mismatch, if any.
- `Impact`: economic, statistical, data, or operational consequence.
- `False-positive boundary`: what evidence would refute the concern.
- `Antibody candidate`: relationship test, type, manifest rule, source gate, or query monitor that would make recurrence harder.

## Audit Matrix

| Axis | Blind spot | Why code review misses it | Primary evidence surfaces | Read-only checks | Status |
|---|---|---|---|---|---|
| A0 Authority freshness | Docs, current facts, packets, and code disagree about what is current. | Review sees changed lines, not which surface is authority today. | `docs/operations/current_state.md`, current fact docs, `architecture/**`, active packets. | Compare staleness windows, active packet rows, branch baseline, and topology route. | FAIL |
| A1 Weather source non-equivalence | WU, HKO, Ogimet/NOAA proxy, Open-Meteo, and TIGGE each mean different truth planes. | API calls can look correct while using the wrong semantic source. | `current_source_validity.md`, `city_truth_contract.yaml`, `config/cities.json`, source-run/readiness repos. | City-by-city source role table: settlement, Day0, forecast, hourly, calibration. Flag any fallback treated as equivalent truth. | FAIL |
| A2 Station identity drift | Same city/provider can change settlement station, e.g. Paris LFPG -> LFPB. | Code can pass with valid station strings while using stale settlement identity. | Gamma market descriptions, WU station URLs, source-contract quarantine ledger, observations provenance. | Compare configured station, market-described station, observation station, and settlement provenance for all active cities. | WARN |
| A3 Spatial representativeness | Grid forecast point, airport station, city station, and market settlement location are not interchangeable. | Review rarely asks whether two geographies represent the same weather reality. | `config/cities.json`, forecast extraction metadata, observations, station coordinates if present. | Build city/source coordinate or station-class matrix; flag forecast-grid vs settlement-station mismatches by city/cluster. | WARN |
| A4 Temporal boundary mismatch | Local day, UTC day, issue time, release availability, and DST can select different physical data. | Timestamp code may be locally correct but semantically off by one day/hour. | `observation_instants`, `ensemble_snapshots`, release calendar, DST flags. | Query DST dates, 23/25-hour days, local-day extrema windows, `available_at <= decision_time`, and lead_days derivation. | FAIL |
| A5 High/Low track contamination | HIGH and LOW share dates/cities but not physical quantity, observation field, or calibration family. | Diffs show column names, not whether training/inference identities stayed separate. | `temperature_metric`, `physical_quantity`, `observation_field`, `data_version`, platt buckets. | Count mixed metrics per key, cross-track calibration rows, and LOW rows inheriting HIGH provenance. | WARN |
| A6 Unit and rounding semantics | Fahrenheit finite ranges, Celsius point bins, shoulders, WMO half-up, and HKO truncation differ. | Numeric code can be syntactically correct while settling in the wrong support geometry. | `settlement_semantics.py`, market topology tables, settlements, bin metadata. | Check discrete cardinality by unit, shoulder handling, non-integer settlements, and forbidden rounding functions on settlement paths. | WARN |
| A7 Forecast model and release regime | TIGGE, Open-Meteo ECMWF, and dissemination step grids have different availability and authority. | A forecast vector of 51 numbers looks fine without provenance. | `ensemble_snapshots`, forecast source registry, TIGGE ingest evidence, release calendar. | Group by `source_model_version`, authority, issue_time, available_at, lead_hours, city; flag training/live source drift. | WARN |
| A8 Coverage holes and legitimate gaps | Missing data, legitimate gaps, retry gaps, and quarantines have different meanings. | Review cannot see absence unless absence is first-class. | `data_coverage`, observations, forecasts, settlements, readiness repo. | Produce gap taxonomy by source/city/date/metric; separate MISSING, FAILED, LEGITIMATE_GAP, QUARANTINED, and no-row. | WARN |
| A9 Calibration corpus provenance | Calibration pairs may be generated from the wrong source, stale snapshot, or unverified outcome. | A fit can run and tests can pass while learning from polluted labels. | `calibration_pairs_v2`, `settlements`, `venue_trade_facts`, provenance JSON. | Join pairs to settlement/observation authority; count orphan pairs, unverified rows, stale snapshots, and missing decision lineage. | WARN |
| A10 Statistical region contamination | Geographic clusters, seasons, units, and market regimes can pollute each other's sample pools. | Review sees bucket keys, not whether the bucket is statistically coherent. | Platt buckets, cluster maps, seasons, city config, performance slices. | For each bucket, report city mix, unit mix, metric mix, source mix, climate-region spread, and outcome imbalance. | PASS |
| A11 Sample independence inflation | Row counts can overstate independent evidence when many bins share one market/date/snapshot. | Tests often assert counts, not exchangeability. | `decision_group_id`, market ids, target dates, snapshot ids, calibration rows. | Compare raw row count vs unique decision groups; flag bootstrap or maturity gates using row n where group n is required. | PASS |
| A12 Training/inference parity | Live P_raw generation can differ from offline calibration rebuilds. | Both paths can be individually correct but incompatible. | `ensemble_signal.py`, calibration rebuild scripts, pair builders, p_raw provenance. | Verify same members, metric, local-day extrema, Monte Carlo, sigma, rounding, bin support, and bias-correction flags. | WARN |
| A13 FDR tested-family boundary | BH can be applied to the wrong candidate family or after prefiltering. | Local code review sees BH math, not the denominator's semantic scope. | `market_analysis_family_scan.py`, `fdr_filter.py`, decision artifacts. | Reconstruct attempted hypotheses per snapshot/family; compare selected edges to all tested candidates, not only positive edges. | WARN |
| A14 Price semantics and microstructure | VWMP, raw mid, implied probability, fee-adjusted cost, and executable price are distinct. | A float price has no visible provenance. | `ExecutionPrice`, orderbook snapshots, executable market snapshots, trade decisions. | Trace price fields across edge, Kelly, executor; flag bare floats, stale book snapshots, and fee/slippage mismatches. | WARN |
| A15 Lifecycle truth feedback | Chain, chronicler, projection, JSON, exit, settlement, and learning can drift. | Review sees state transitions, not whether truth hierarchy is preserved end to end. | `position_events`, `position_current`, chain reconciliation, portfolio exports. | Compare event/projection/export counts and phases; search for exit-as-close, settlement-as-exit, and chain-unknown-as-empty symptoms. | WARN |
| A16 Learning loop completeness | Settled outcomes may fail to feed calibration, attribution, and strategy health. | Code review does not notice silent non-learning. | Harvester, settlements, calibration pairs, strategy health, attribution drift docs. | For each settled market/position, verify outcome, settlement source, calibration pair, strategy attribution, and learning record. | WARN |
| A17 Observability false confidence | Dashboards and summaries can report green while authority is stale or degraded. | Derived reports are easy to trust because they look complete. | Status summaries, readiness tables, observability docs, logs. | Cross-check displayed readiness against canonical DB/source facts; flag derived JSON or summaries that omit degraded truth. | FAIL |
| A18 Known-gap closure drift | Historical gaps can be marked mitigated while residuals remain. | Review tends to trust closure labels. | `known_gaps.md`, archive, tests, current code, current facts. | Sample closed/mitigated gaps and verify the claimed antibody still exists and covers the current failure class. | WARN |

## First Execution Pass

The first pass should be deliberately broad and shallow across all axes, then deepen only where evidence disagrees.

1. Build an authority/freshness table for A0.
2. Build city/source/metric/track matrices for A1-A8.
3. Build calibration and statistical-coherence matrices for A9-A13.
4. Build money-path feedback matrices for A14-A17.
5. Sample known-gap closure claims for A18.

The first report should prioritize mismatches that have no obvious code diff footprint: source-role equivalence, spatial/station drift, statistical-region contamination, sample-independence inflation, missingness hidden as absence, and derived-report false confidence.

## Run 1 Findings - 2026-05-08 Broad/Shallow Pass

Status summary for the current matrix snapshot: FAIL=4, WARN=13, PASS=2, NEEDS_FRESH_FACTS=0, BLOCKED=0, NOT_STARTED=0.

This pass used only read-only DB opens (`file:...mode=ro`), file reads, and grep. No source, config, runtime, or DB mutation was performed.

### F1 - A0 Authority Freshness - FAIL

Belief: `docs/operations/current_data_state.md` is within its 14-day planning freshness window and claims a 1,609-row `settlements` baseline from 2026-04-28.

Reality: current `state/zeus-world.db` has 5,570 `settlements` rows. The same current-fact file says it is invalidated if any mutation changes the 1,609-row baseline. `docs/operations/current_source_validity.md` also records a 2026-05-03 Paris conversion with 853 HIGH + 853 LOW settlement rows rebuilt, which post-dates the 2026-04-28 data-state audit.

Impact: data-readiness, settlement-count, and calibration/backfill planning claims cannot rely on `current_data_state.md` until refreshed. This is not a code bug; it is authority/current-fact drift.

False-positive boundary: a newer admitted current-data audit that supersedes the 1,609 baseline and reconciles the 5,570 rows.

Antibody candidate: a read-only current-fact freshness checker that compares documented row-count baselines against canonical DB counts before any data or calibration planning claim.

### F2 - A4 Temporal Boundary - FAIL

Belief: hourly observation tables encode DST semantics with `is_ambiguous_local_hour` / `is_missing_local_hour`, and hourly rows represent local-day physical coverage.

Reality: legacy `observation_instants` has 45 row-count anomalies against ZoneInfo expected local-day duration, especially fall-back days with 24 rows where 25 are expected and zero ambiguous flags. Canonical `observation_instants_v2` has source-separated rows; when filtered to `source_role='historical_hourly' AND training_allowed=1`, the WU surface has 3,731 city/date/source row-count anomalies, including extreme partial days such as 2 rows on spring-forward dates and 22/23 rows on fall-back dates. `observation_instants_v2` also has NULL `temperature_metric`, `physical_quantity`, and `observation_field` on all 1,826,367 rows.

Impact: any downstream use treating this surface as complete hourly local-day truth can miscompute extrema, coverage, or temporal confidence. A code trace found `observation_instants_v2` is not the calibration/extrema/settlement training source today; it is currently read for Day0 coverage and fallback/latest observation context. So the confirmed impact is coverage/observability contract drift, while future live impact appears if a metric-aware consumer treats the table as canonical hourly truth.

False-positive boundary: proof that no live/training path consumes `observation_instants_v2` or legacy `observation_instants` for extrema/coverage without a separate completeness gate; or a table-level contract saying sparse rows are expected and not hourly coverage.

Antibody candidate: relationship test `observation_instants_v2(training_allowed=1, source_role=historical_hourly) -> complete local-day coverage OR explicit incomplete status`, keyed by ZoneInfo expected hours and source/date.

### F3 - A17 Observability Contamination - FAIL

Belief: observability fact tables are current runtime evidence.

Reality: `availability_fact` contains repeated synthetic/probe-looking rows in the canonical world DB: 498 rows for `Not A City/2026-01-15`, 149 rows containing `-200.0`, 925 rows containing `160.0` or `200.0`, and 496 rows for future June/July 2026 target scopes. Recent examples were written on 2026-05-08. These include `UnitConsistencyViolation`, `UnknownCityViolation`, `PhysicalBoundsViolation`, `CollectionTimingViolation`, and `DstBoundaryViolation` rows.

Impact: dashboards or summaries that aggregate `availability_fact` without environment/provenance/test-run filtering can report false current blockers or noisy risk posture. This is exactly the kind of data-plane contamination ordinary code review will not see.

False-positive boundary: documented rule that `availability_fact` is intentionally a mixed test/probe/runtime table and all runtime consumers filter by run/provenance/environment before display or gating.

Antibody candidate: add explicit `env`/`run_type`/`authority` fields or a runtime-view query that excludes synthetic/probe rows; then test status summaries against the filtered view.

### F4 - A9/A17 Calibration Active Flag Ambiguity - WARN

Belief: `is_active=1` means a Platt row is live-servable.

Reality: `platt_models_v2` contains `UNVERIFIED` and `QUARANTINED` rows with `is_active=1` (28 UNVERIFIED active LOW rows and 12 QUARANTINED active negative-slope rows in this pass). Source code loaders in `src/calibration/store.py` do filter `is_active=1 AND authority='VERIFIED'`, so immediate runtime leakage was not established.

Impact: status/reporting surfaces that count active models without authority filtering can overstate live calibration health. The runtime seam looks guarded; the observability semantics remain ambiguous.

False-positive boundary: all human-facing and machine-facing model-health views use the same `is_active=1 AND authority='VERIFIED'` filter as the runtime loader.

Antibody candidate: define a single `servable_platt_models_v2` view or helper and forbid bare `is_active` counts in reporting.

### F5 - A1/A2 Source Identity Normalization - WARN

Belief: settlement source identity is canonical and comparable across tables.

Reality: `settlements.settlement_source_type` mixes literals such as `WU` and `wu_icao` for the same WU source family. Examples include NYC, Paris, Seoul, Shanghai, and Tokyo. Observation station ids also commonly appear as both `ICAO` and `ICAO:CC`; this looks like aliasing rather than confirmed station drift, but it must be normalized before station equality can be audited safely.

Impact: naive source/station joins can produce false drift or hide real drift. This matters for source-contract monitoring and city-level quarantine logic.

False-positive boundary: canonical normalization function or table proving `WU == wu_icao` and `ICAO == ICAO:CC` before any comparison.

Antibody candidate: a source/station identity normalizer with relationship tests against Paris LFPG->LFPB and NOAA/HKO exception cities.

### F6 - A8 Coverage/Missingness - WARN

Belief: missingness is first-class and separable from legitimate gaps.

Reality: `data_coverage` does separate `WRITTEN`, `MISSING`, `FAILED`, and `LEGITIMATE_GAP`. However, it still contains large historical `MISSING` surfaces for forecast previous-runs: 41,815 each for `ecmwf_previous_runs`, `gfs_previous_runs`, `icon_previous_runs`, and `openmeteo_previous_runs`, plus 31,467 for `ukmo_previous_runs`. Current `source_run_coverage` for `ecmwf_open_data` is coherent for current live horizons (459 HIGH and 426 LOW live-eligible; horizon-out-of-range blocks are explicit).

Impact: historical forecast coverage remains a major missingness field and must not be confused with current live OpenData readiness. This is a report-splitting issue, not necessarily a trading blocker.

False-positive boundary: an admitted coverage doctrine that these historical MISSING rows are intentionally dormant/backtest-only and excluded from live readiness claims.

Antibody candidate: separate live-readiness coverage from historical/backtest coverage in status summaries and audit queries.

### F7 - A11 Sample Independence - WARN

Belief: calibration sample counts represent independent evidence.

Reality: `calibration_pairs_v2` has 53,490,902 rows but 537,516 unique `decision_group_id` values in the verified/training-allowed TIGGE full-horizon corpus. Rows per decision group are about 99.8 for HIGH and 98.7 for LOW. This may be expected because one decision group emits many bin rows, but any maturity gate, CI, or effective-sample logic using row count instead of decision groups would inflate evidence.

Impact: no direct failure was proven; this pass establishes the denominator hazard and the scale of possible inflation.

False-positive boundary: verification that all maturity gates, bootstraps, and sample-size reports use `decision_group_id` / effective sample size rather than pair rows where independence is required.

Antibody candidate: relationship test comparing row-count n vs decision-group n for each Platt bucket and failing any independence-sensitive report that uses raw rows.

### F8 - A5/A6 Unit and Track Identity - PASS for queried tables

Evidence checked: `settlements` and `calibration_pairs_v2` had zero non-integer settlement values. HIGH rows matched `mx2t6_local_calendar_day_max` / `high_temp`; LOW rows matched `mn2t6_local_calendar_day_min` / `low_temp`. Pair sanity checks found zero out-of-bounds `p_raw`, zero null `p_raw`, zero non-binary outcomes, zero missing `decision_group_id`, zero missing `snapshot_id`, and zero verified rows with `training_allowed != 1`.

Residual risk: `observation_instants_v2` has NULL metric/quantity/field identity, so the PASS is limited to settlement and calibration-pair tables, not all observation surfaces.

### F9 - A7 Forecast Snapshot Regime - PASS for current `ensemble_snapshots`

Evidence checked: `ensemble_snapshots` has 116 rows, all `authority='VERIFIED'`, model_version `tigge`, correct high/low data_version split, no member-count anomalies, and no null timing fields in this pass. HIGH has 100 rows across 50 cities for 2026-05-03..2026-05-04; LOW has 16 rows across 8 cities for the same target window.

Residual risk: live readiness currently points at `ecmwf_open_data` in `source_run_coverage`, while `ensemble_snapshots` evidence is TIGGE. Training/inference source-regime parity still needs a focused OpenData-vs-TIGGE handoff check.

### F10 - A13 Selection/FDR Observability - WARN after F16/F25 refinement

Evidence checked: `selection_family_fact`, `selection_hypothesis_fact`, `probability_trace_fact`, and `opportunity_fact` all had zero rows in `zeus-world.db` during this pass. `position_events`, `position_current`, `trade_decisions`, `venue_trade_facts`, and `executable_market_snapshots` were also empty in both world/trades DB probes.

Impact: this DB state cannot prove FDR denominator correctness, selected-hypothesis lineage, or execution feedback. It may be correct for an idle/no-live-trades state; it is not sufficient evidence for live trading quality claims.

False-positive boundary: a separate current runtime DB or evidence packet with populated selection/decision/trace facts.

Antibody candidate: before any live-readiness claim, require non-empty or explicitly idle-certified selection-family/probability-trace evidence.

### F11 - A16 Learning Loop Completeness - WARN after F25 refinement

Evidence checked: `outcome_fact`, `execution_fact`, `trade_decisions`, and `venue_trade_facts` are empty in the probed DBs. `settlements` and `calibration_pairs_v2` are heavily populated.

Impact: this pass can audit settlement/calibration corpus shape but cannot prove position-level learning loop completeness from execution through settlement/outcome.

False-positive boundary: an active trade DB or archived packet proving the execution/outcome facts live elsewhere.

Antibody candidate: canonical report query that starts from settled positions, not only settled markets, and proves each has execution, outcome, calibration, and strategy-health linkage.

### F12 - A6 Rounding Static Probe - WARN

Evidence checked: canonical `SettlementSemantics` implements WMO half-up as `floor(x + 0.5)` and HKO truncation as a distinct policy. A broad grep found one settlement-adjacent legacy harvester path using Python `round(float(settlement_value))` before `add_calibration_pair`. This was not proven to be on the active v2/live path.

Impact: possible stale-path rounding drift; low confidence until the harvester path is routed against current live settlement/calibration writes.

False-positive boundary: proof that the rounded legacy `add_calibration_pair` path is dead, test-only, or never used for current v2 calibration/learning.

Antibody candidate: forbid Python `round()` on any variable named `settlement_value` outside display/report formatting, with exemptions documented by path.

## Run 1 Addendum - A10/A13/A4 Refinement

### F13 - A9/A10 Source-Truth Split - WARN

Belief: if a calibration pair is `authority='VERIFIED'`, the same `(city, target_date, temperature_metric)` should not be quarantined on the settlement truth surface unless the report explicitly distinguishes the target truth source.

Reality: verified/training calibration pairs are built from `observations` via `_fetch_verified_observation()` in `scripts/rebuild_calibration_pairs_v2.py`, not directly from `settlements` or `settlements_v2`. In the current DB, `observations` has VERIFIED rows for pair labels while `settlements_v2` has 415 matching keys marked `QUARANTINED`; those keys account for 459,262 VERIFIED calibration-pair rows and 4,701 decision groups. The older `settlements` table shows the same pattern at larger scale: 462,832 VERIFIED pair rows join to non-VERIFIED settlement rows. A same-day operations packet (`task_2026-05-08_obs_outside_bin_audit`) says many of these quarantines are real source disagreements: WU/HKO/NOAA observed values can disagree with UMA winning bins.

Additional quantification: the overlap is overwhelmingly HIGH-track: 457,528 HIGH pair rows / 4,684 decision groups / 413 keys, versus 1,734 LOW pair rows / 17 groups / 2 keys. Largest affected city/metric slices by pair rows were London HIGH (100,368 rows, 984 groups, 123 keys), Paris HIGH (40,800 / 400 / 25), Seattle HIGH (40,020 / 435 / 42), NYC HIGH (39,928 / 434 / 40), Dallas HIGH (28,704 / 312 / 32), Shenzhen HIGH (27,540 / 270 / 17), Miami HIGH (25,760 / 280 / 22), Atlanta HIGH (24,656 / 268 / 25), Taipei HIGH (24,480 / 240 / 15), and Chicago HIGH (23,184 / 252 / 21).

Bucket impact: this is not only a small tail of orphan rows. Some active stratified buckets have high overlap with settlement-quarantined keys: NYC HIGH DJF cycle 12 has 114/196 contaminated decision groups (58.16%); Miami HIGH DJF cycle 12 104/196 (53.06%); Seattle HIGH DJF cycle 12 99/196 (50.51%); Chicago HIGH DJF cycle 12 84/196 (42.86%); Atlanta HIGH DJF cycle 12 68/196 (34.69%); Paris HIGH MAM cycle 12 152/504 (30.16%); Dallas HIGH DJF cycle 12 56/196 (28.57%); Shenzhen HIGH MAM cycle 12 136/504 (26.98%); Paris HIGH DJF cycle 12 48/196 (24.49%); Taipei HIGH MAM cycle 12 120/504 (23.81%). If promotion-grade refits use market-resolution truth, these buckets need exclusion/refit policy before promotion.

Impact: the system can simultaneously learn from VERIFIED weather-source observations and quarantine the corresponding market-settlement row. That may be valid, but it must be an explicit two-truth doctrine: weather-source calibration truth vs market-resolution truth. Without that boundary, calibration health reports and replay/settlement reports can appear to contradict each other while both are locally correct.

Refinement after source-truth policy trace: this split is more formalized than the first-pass wording implied. `src.types.truth_authority`, `architecture/settlement_dual_source_truth_2026_05_07.yaml`, and the harvester truth writer distinguish VERIFIED weather-source observations from quarantined market-resolution/source-disagreement rows. The remaining risk is not “silent pollution” by default; it is promotion/reporting ambiguity when a repair packet or calibration-health report forgets which truth domain it is evaluating.

False-positive boundary: an admitted contract stating calibration learns source-specific weather outcomes and is intentionally allowed to include keys whose market-resolution/UMA settlement row is quarantined for source disagreement.

Antibody candidate: add a `truth_domain`/`label_authority_source` relationship test: every VERIFIED calibration pair must either match a VERIFIED settlement row in the same truth domain or carry explicit evidence that it is weather-source training truth, not market-resolution truth.

### F14 - A10 K3 Bucket Homogeneity - PASS/WATCH

Evidence checked: verified/training `calibration_pairs_v2` has zero buckets with more than one city and zero rows where `cluster != city`. This supports the K3 collapse claim that cluster is city identity, not a broad regional pool.

Residual watch: active VERIFIED `platt_models_v2` has 182 bucket groups with multiple active variants when grouped without cycle; this is explained by cycles `00` and `12` with source_id `tigge_mars` and horizon_profile `full`. Exact active duplicates for the full stratification tuple were zero.

Antibody candidate: model-health summaries must group by the full serving key `(metric, cluster, season, data_version, input_space, cycle, source_id, horizon_profile)`, not by the older pre-stratification key.

### F15 - A11 Decision-Group Independence - PASS/WATCH

Evidence checked: verified/training decision groups have stable per-group row counts and no cross-identity mixing. HIGH: 379,558 groups, min 92, max 102, average 99.84 rows/group, zero multi-city/date/snapshot groups. LOW: 157,958 groups, min 92, max 102, average 98.74 rows/group, zero multi-city/date/snapshot groups.

Residual watch: raw row count is still about 100x independent decision-group count because each group contributes many range-bin rows. Maturity/effective-sample code must continue using decision groups or effective sample size, not row count.

### F16 - A13/A16 Fact Observability Path - WARN

Belief: empty fact tables in `zeus_trades.db` prove no selection/execution/outcome activity.

Reality: writer functions for probability/selection/opportunity/execution/outcome facts gracefully skip when tables are missing or connections are unavailable (`skipped_missing_table` / `skipped_no_connection`). Current probed DB tables are empty, but `evidence/replay_baseline/2026-05-06.json` records 1,768 projected `opportunity_fact::opportunity_evaluated` events in a replay baseline.

Impact: current DB emptiness is not sufficient to distinguish idle live state, missing instrumentation, diagnostic/replay-only projection, or alternate DB path. FDR/selection/execution completeness cannot be proven from the current tables alone.

Refinement after FDR lineage trace: the denominator design exists in code. `scan_full_hypothesis_family()` builds the executable bin/direction family, `apply_familywise_fdr()` applies BH over that family, and `selection_family_fact` / `selection_hypothesis_fact` are the durable lineage surfaces when writers run. The unresolved issue is present-tense evidence mode, not a confirmed BH denominator bug.

False-positive boundary: a current runtime packet that declares the system idle and maps all fact writers to the expected DB path; or populated live fact rows with lineage to decision cycles.

Antibody candidate: a status contract that reports fact-writer mode (`live_db`, `diagnostic_projection`, `skipped_missing_table`, `idle_certified`) alongside counts.

### F17 - A2/A3 City Station Config Self-Consistency - PASS

Evidence checked: using the repository `load_cities()` loader, all 52 city configs loaded successfully; source-type counts were 48 `wu_icao`, 3 `noaa`, and 1 `hko`. There were zero cases where `cluster != name`, zero duplicate WU station ids, and zero cases where a configured WU station was absent from the configured settlement source URL. The raw JSON has US coordinates nested under `noaa.lat/lon`; this is intentional and handled by the loader.

Residual risk: this does not prove Polymarket has not changed source text since the config was last updated. It proves only internal config/source URL/station identity self-consistency.

Antibody candidate: monthly active-market-description scrape comparing live settlement source/station/unit text to `config/cities.json`.

### F18 - A14 Price Semantics / Orderbook Linkage - WARN

Evidence checked: `state/zeus_trades.db.market_price_history` has 37,840 rows, all `market_price_linkage='price_only'` from `GAMMA_SCANNER`; every row has NULL `best_bid`, NULL `best_ask`, and NULL `snapshot_id`. `token_price_log` has 2,760 rows with bounded prices and sane bid/ask spreads, but it is a lighter token log rather than the full executable orderbook snapshot surface. `executable_market_snapshots` is empty in both world and trades DBs.

Impact: current stored price history cannot support execution-grade economics, slippage, or orderbook-depth claims. This is consistent with `src/backtest/economics.py`, which explicitly blocks readiness when there are no full-linkage rows and keeps the economics engine tombstoned.

False-positive boundary: live executor calls may still fetch CLOB bid/ask directly at decision/monitor time; this finding is about persisted evidence and replay/economics readiness, not proof that live order placement lacks bid/ask.

Antibody candidate: require every economics/replay report to declare `market_price_linkage` mode and fail any execution-quality claim when only `price_only` Gamma scanner data is present.

### F19 - A17 Market Event Synthetic Contamination - WARN

Evidence checked: `state/zeus-world.db.market_events_v2` contains 4,590 rows and 3 synthetic-looking Los Angeles rows with `token_id` values like `token_yes_low` and condition ids like `cond-low`. `state/zeus_trades.db.market_events_v2` has 2,002 rows and zero such synthetic-looking token/condition ids.

Impact: the contamination is small and isolated in the world DB, but it is another example of canonical data surfaces containing probe/test artifacts. Any topology or replay query that reads world `market_events_v2` without filtering can ingest fake token identifiers.

False-positive boundary: an admitted fixture/probe namespace contract for these Los Angeles rows and proof that runtime/replay use trades `market_events_v2` or filter synthetic ids.

Antibody candidate: enforce token/condition id shape validation on world-market-event ingestion, with an explicit fixture namespace if synthetic rows are intentionally retained.

### F20 - A12 Training/Inference P_raw Parity - PASS/WARN

Evidence checked: live evaluator and offline calibration rebuild both call `src.signal.ensemble_signal.p_raw_vector_from_maxes()`. That function centralizes member-extrema input validation, city-specific instrument sigma, settlement semantics rounding, Monte Carlo noise, bin counting, and normalization. `EnsembleSignal.p_raw_vector()` delegates to the same function, and `scripts/rebuild_calibration_pairs_v2.py` also calls it when writing calibration pairs.

Residual warning: persisted `ensemble_snapshots_v2.p_raw_json` coverage is partial. TIGGE HIGH has 384,672 rows but 73,588 with `p_raw_json`; TIGGE LOW has 384,099/348,706 rows depending data_version but 24,065 with `p_raw_json`; ECMWF OpenData HIGH/LOW rows have zero persisted `p_raw_json` in this pass. This is not a formula-parity failure because calibration pairs store their own `p_raw`, but it limits provenance/replay claims from snapshot rows alone.

False-positive boundary: an admitted rule that `p_raw_json` is only persisted for decision-time snapshots, while calibration-pair `p_raw` is the training provenance surface.

Antibody candidate: a relationship test asserting rebuild and evaluator P_raw for the same `(snapshot_id, city, metric, bins, n_mc, seed)` are equal within Monte Carlo tolerance, plus a report field distinguishing `decision_time_p_raw_persisted` from `training_pair_p_raw`.

### F21 - A18 Known-Gap Closure Boundary - WARN

Evidence checked: `docs/to-do-list/known_gaps_archive.md` records `[CLOSED - 2026-04-30] v2 row-count observability prefers world truth over trade shadow`, with an antibody in `status_summary.py::_get_v2_row_counts()`. Current `known_gaps.md` still keeps broader observability as PARTIAL and explicitly says status summaries are non-authority. The active gap for empty `executable_market_snapshots` also still matches current DB evidence.

Verdict: the sampled closed antibody appears scoped correctly; it solves a narrow row-count schema-preference failure, not the broader A17 issues found here (synthetic/probe `availability_fact`, synthetic world `market_events_v2`, skipped fact writers, or price-only market history). The risk is not immediate antibody failure; it is closure drift if future reports treat the closed row-count item as a general observability fix.

Antibody candidate: every closed known-gap entry should have an explicit `does_not_cover` line for adjacent residuals when the active register keeps the broader segment PARTIAL.

### F22 - A7 Forecast Regime Stratification - WARN

Evidence checked: `ensemble_snapshots_v2` has five main data-version regimes in this pass. TIGGE HIGH `tigge_mx2t6_local_calendar_day_max_v1`: 384,672 rows, all 51 members; 384,222 rows are `model_version='ecmwf_ens'`, `authority='VERIFIED'`, `training_allowed=1`, `causality_status='OK'`, but `source_id` is NULL for that historical block. TIGGE LOW v1 and contract-window v2 have large rejected-boundary-ambiguous surfaces: 300,547 LOW v1 rows and 273,217 LOW contract-window rows with `training_allowed=0` / `REJECTED_BOUNDARY_AMBIGUOUS`; their training-allowed OK subsets are 83,536 and 75,473 rows respectively. ECMWF OpenData v2 rows exist only for 2026-05-08..2026-05-17 HIGH (1,060 rows, 983 training_allowed OK) and 2026-05-08..2026-05-13 LOW (508 rows, mostly rejected-boundary-ambiguous). All checked regimes had exactly 51 members per row.

Impact: member cardinality is healthy, but forecast-source stratification is uneven. Historical TIGGE rows often have NULL `source_id` while newer OpenData rows carry `source_id='ecmwf_open_data'`; persisted `p_raw_json` is partial for TIGGE and absent for OpenData. Live readiness currently references OpenData coverage, while calibration corpus and active Platt evidence are TIGGE-heavy. That makes training/live source-regime claims dependent on explicit stratification keys and fallback rules.

False-positive boundary: a serving contract proving OpenData live inference is either calibrated by OpenData-specific models or deliberately routed through a TIGGE-compatible fallback with documented degradation.

Antibody candidate: source-regime readiness query keyed by `(temperature_metric, data_version, source_id, cycle, horizon_profile)` that reports row count, training_allowed count, p_raw provenance count, Platt model availability, and live-serving eligibility together.

### F23 - A7/A12 OpenData Calibration Serving Contract - WARN after current-main hardening

Evidence checked: `config/settings.json::entry_forecast` currently names `source_id='ecmwf_open_data'`, `rollout_mode='live'`, and `calibration_policy_id='ecmwf_open_data_uses_tigge_localday_cal_v1'`. `source_run_coverage` has OpenData LIVE_ELIGIBLE rows in the current world DB: 459 HIGH rows and 426 LOW rows for 2026-05-08..2026-05-13, all `completeness_status='COMPLETE'`. `platt_models_v2` has zero active VERIFIED `ecmwf_opendata_%` models. Active VERIFIED Platt coverage is TIGGE only: HIGH 00z has 201 buckets, HIGH 12z 99 buckets, LOW contract-window v2 00z 175 buckets, LOW v1 00z 196 buckets, and LOW v1 12z 83 buckets. Current opportunity evidence is consistent with fail-closed behavior: all 1,768 `opportunity_fact` rows in `state/zeus_trades.db` have `should_trade=0`, including 406 `CALIBRATION_IMMATURE/unavailable` rows.

Current-main code path checked from disk: `src/engine/evaluator.py` now imports `derive_phase2_keys_from_ens_result()`, derives `(cycle, source_id, horizon_profile)` from `ens_result`, maps forecast source ids through `calibration_source_id_for_lookup()`, fails closed on unsupported non-empty source ids, and passes the three keys into `get_calibrator()`. `src/engine/monitor_refresh.py` mirrors the same helper usage for monitor recalibration. Existing relationship tests in `tests/test_phase2_review_round1_fixes.py` assert the helper and evaluator/monitor usage. This means the earlier silent-schema-default concern is not current-main behavior.

Impact: OpenData forecast coverage/readiness and OpenData calibration availability remain distinct. Current main appears to prevent OpenData candidates from silently using a schema-default TIGGE 00z/full bucket; with no active OpenData Platt rows, candidates should fail closed or remain calibration-immature unless an explicit transfer/promotion surface supplies authority. The remaining audit gap is observability: operator/status surfaces need to show the requested forecast domain, requested calibration domain, served model key, route, and terminal status together so `LIVE_ELIGIBLE forecast` is never mistaken for `LIVE_ELIGIBLE calibrated trade`.

False-positive boundary: a runtime decision trace showing, for each OpenData candidate, `forecast_source_id`, `forecast_data_version`, `forecast_cycle`, `calibration_policy_id`, `requested_calibration_source_id`, `requested_calibration_data_version`, `requested_calibration_cycle`, `requested_horizon_profile`, `served_calibrator_model_key`, route, and terminal status, with either exact domain match, explicitly authorized transfer, or RAW/CALIBRATION_IMMATURE fail-closed behavior.

Antibody candidate: keep the current relationship tests and add a serving-bucket status report keyed by `(forecast_source_id, forecast_data_version, forecast_cycle, requested_calibration_source_id, requested_calibration_data_version, requested_cycle, requested_horizon_profile)`. The report should fail if any OpenData candidate reaches a schema-default TIGGE bucket without an explicit transfer authority result, and it should separately count fail-closed calibration-immature candidates.

### F24 - A2/A3 Market Source-Proof Persistence Gap - WARN

Evidence checked: config/source identity and current DB station provenance. `config/cities.json` resolves to 48 WU/ICAO cities, 3 NOAA/Ogimet proxy cities (Istanbul LTFM, Moscow UUWW, Tel Aviv LLBG), and 1 HKO city. For WU-configured cities, observed `observations.station_id` values matched configured stations in this pass after allowing `ICAO:country` aliases. Non-WU samples matched the intended source roles: Hong Kong uses HKO rows, and Istanbul/Moscow/Tel Aviv use Ogimet METAR rows for their NOAA-configured station ids, with small older WU alias rows in Moscow/Tel Aviv. `settlements` source literals still mix `WU` and `wu_icao`, but after folding aliases there was only one historical config disagreement group: Taipei CWA QUARANTINED on 2026-03-16; latest settlement-source rows had zero config disagreements.

Code path checked: `market_scanner._check_source_contract()` can parse structured Gamma source fields or market-description URLs, infer WU/HKO/NOAA/CWA family, extract station ids, and reject MISSING/AMBIGUOUS/MISMATCH/UNSUPPORTED source contracts. However `_persist_market_events_to_db()` writes only parsed market topology (`market_slug`, `city`, `target_date`, `temperature_metric`, condition/token/range fields) into `market_events_v2`. The parsed `resolution_sources` / `source_contract` object returned by `_parse_event()` is not persisted into the market-events table, and raw market description prose is not stored.

Impact: current config and observation provenance do not show active station drift, but Zeus cannot later prove from the DB alone that the active market description source proof matched config at scan time. This makes monthly source/station audits and station-migration postmortems depend on re-fetching Gamma or trusting ephemeral logs rather than durable evidence. It is a provenance hole, not a current wrong-station finding.

False-positive boundary: a separate durable audit log, archive, or event payload store that records the raw market description plus parsed `source_contract` for every active market scan.

Antibody candidate: persist a `market_source_contract_fact` keyed by `(market_slug, city, target_date, temperature_metric, scan_time)` with raw/structured resolution sources, parsed family/station, configured family/station, status, and reason. Runtime can keep `market_events_v2` compact, but the audit surface needs source-proof lineage.

### F25 - A13/A15/A16 Decision-to-Lifecycle Chain Stops Before Orders - WARN

Evidence checked: current `state/zeus_trades.db` has live decision/opportunity evidence but no order/position/outcome evidence. Counts in this pass: `decision_log` has 277 live cycles from 2026-05-02..2026-05-04; `opportunity_fact` has 1,768 rows; `selection_family_fact` has 14 rows; `selection_hypothesis_fact` has 154 rows. But `trade_decisions`, `venue_commands`, `venue_order_facts`, `venue_trade_facts`, `execution_fact`, `position_events`, `position_current`, `position_lots`, and `outcome_fact` all have zero rows in the probed trade DB. `opportunity_fact.should_trade=1` had zero rows; parsed `decision_log` artifacts had zero cycles with `trade_cases` and 34 cycles with `no_trade_cases`.

More detail: `opportunity_fact` rows are all rejected or unavailable in this pass: 696 stale signal-quality rows, 406 calibration-immature rows, 355 unavailable signal-quality rows, 155 signal-quality/ok rows, 113 oracle-evidence-unavailable rows, 40 market-liquidity rows, and 3 FDR-filtered rows. The FDR lineage surface is partially populated: 59 `selection_hypothesis_fact` rows have `selected_post_fdr=1`, but all 154 hypothesis rows have `decision_id IS NULL`. Candidate-id joins show selected candidates later appear in opportunity facts with `should_trade=0`, mostly `SIGNAL_QUALITY`, but the exact selected-hypothesis -> opportunity/rejection link is candidate-level rather than decision-id-level.

Impact: the empty execution/position/outcome tables are consistent with “no eligible live orders submitted” rather than proof of a missing executor. However, the chain is not fully auditable: selected FDR hypotheses are not durably tied to the exact decision/opportunity row that rejected them, and there is no position-level learning/settlement surface to inspect because no trades reached that stage. This narrows A15/A16 from “unknown DB path” to “pre-order stop with incomplete selected-edge lineage.”

False-positive boundary: a current lifecycle report proving no `should_trade=1` decisions for the same runtime window, plus a selection/opportunity schema or query that links every selected FDR hypothesis to a concrete decision id and terminal rejection/no-order reason.

Antibody candidate: require `selection_hypothesis_fact.decision_id` or a stable `opportunity_decision_id` foreign key for every selected hypothesis before reporting FDR/selection completeness. Add a lifecycle summary that reports counts by stage: evaluated -> selected_post_fdr -> opportunity rejected -> should_trade -> order submitted -> fill -> position -> settlement/outcome -> calibration learning.

## Repair Impact Map - First Wave

This section is intentionally more repair-oriented than the finding log. It separates confirmed impact from tempting but unproven extrapolations so a later fix packet does not patch the wrong layer.

### F1 Authority Freshness

Confirmed affected surfaces:
- Planning documents and operator claims that cite the 1,609-row `settlements` baseline.
- Any task packet using `current_data_state.md` as a freshness authority for settlement volume, data readiness, or backfill completeness.
- Calibration/backfill scoping that assumes pre-Paris-conversion row counts.

Not yet proven affected:
- Runtime code paths. The issue is authority drift in the documentation/current-fact layer, not evidence that live trading reads the stale doc.

Repair shape:
- Refresh `current_data_state.md` from read-only canonical DB queries and explicitly supersede the 1,609 baseline.
- Add a row-count assertion for current-fact docs: if a documented DB baseline differs from canonical DB by more than an admitted mutation packet, the doc must mark itself stale.
- Do not repair by changing DB contents; the DB is the newer reality in this finding.

### F2 Temporal / Hourly Observation Contract

Confirmed affected surfaces:
- `observation_instants` and `observation_instants_v2` as hourly observability surfaces.
- Day0 coverage confidence if it interprets sparse `observation_instants_v2` rows as complete source coverage without an explicit incomplete status.
- Future metric-aware readers because `observation_instants_v2.temperature_metric`, `physical_quantity`, and `observation_field` are NULL across all rows in this pass.

Not yet proven affected:
- Calibration labels: rebuild code fetches VERIFIED daily observations from `observations`, not hourly v2.
- Runtime extrema generation: no current training/extrema consumer was found reading v2 as canonical extrema truth.

Repair shape:
- Decide whether `observation_instants_v2` is a sparse evidence log or a complete hourly table. The current name/source_role implies more completeness than the rows provide.
- If sparse evidence: add explicit `coverage_status`, `expected_local_hours`, `observed_local_hours`, and `incomplete_reason` fields or a derived view.
- If complete hourly: rebuild/ingest fall-back DST days as 25-hour days and spring-forward as 23-hour days with correct ambiguous/missing flags.
- Add a relationship test from ZoneInfo expected hours to row counts per `(city, target_date, source)`.

### F3/F19 Observability Contamination

Confirmed affected surfaces:
- `availability_fact` contains repeated probe-like blockers (`Not A City`, `-200.0`, `160.0`/`200.0`, future dates) with recent timestamps.
- `state/zeus-world.db.market_events_v2` contains 3 synthetic-looking token/condition rows.
- Any dashboard/status/report that aggregates these world tables without provenance filtering can display false blockers or fake market topology.

Not yet proven affected:
- Live trading decisions. Current evidence shows contamination in observability/world fact surfaces, not direct execution consumption.

Repair shape:
- Do not delete first. Add provenance classification (`runtime`, `test`, `probe`, `fixture`, `diagnostic`) and expose a runtime-only view.
- Backfill existing suspicious rows into a non-runtime class after confirming they are probes/fixtures.
- Add status queries that show both raw count and runtime-filtered count so contamination is visible rather than silently hidden.
- Add insertion guards for obviously fake city/token/condition ids unless an explicit fixture namespace is active.

### F13 Source-Truth Split

Confirmed affected surfaces:
- Calibration pairs can be VERIFIED from `observations` while same `(city, date, metric)` settlement rows in `settlements_v2` are QUARANTINED.
- This affects 415 `settlements_v2` keys, 459,262 calibration-pair rows, and 4,701 decision groups in this pass.
- Repair packets touching calibration, settlement, source-validity, replay, or promotion criteria must understand which truth domain they are operating in.

Not yet proven affected:
- The existence of those pairs is not automatically wrong. If Zeus intentionally trains on source-specific weather truth, VERIFIED observation labels can coexist with QUARANTINED market-resolution rows.

Repair shape:
- Treat the current source-truth split as intentional unless a packet proves otherwise: calibration labels are weather-source truth; `settlements_v2` can quarantine market-resolution/source-disagreement truth for the same key.
- Add `truth_domain` / `label_authority_source` to calibration-pair provenance and require replay/calibration reports to state which domain they evaluate.
- If market-resolution truth is required for promotion: pairs overlapping QUARANTINED settlements must be excluded, refit in a separate corpus, or explicitly downweighted from promotion-grade refits.
- Do not simply downgrade all overlapping pairs; that would erase valid source-specific weather learning and patch the symptom at the wrong boundary.

### F16 Fact Observability Mode

Confirmed affected surfaces:
- Selection/FDR/opportunity/execution/outcome DB tables cannot alone prove live activity or inactivity because writers can return `skipped_missing_table` / `skipped_no_connection`.
- Replay baseline evidence can contain projected opportunity facts while current live/trade DB tables are empty.

Not yet proven affected:
- FDR math. The code-level denominator path appears present: full-family scan -> BH -> `selection_family_fact` / `selection_hypothesis_fact`. The issue is missing current durable evidence in the probed DBs, not proof that BH calculation is wrong.

Repair shape:
- Add a fact-writer mode report: `written_live`, `written_projection`, `skipped_missing_table`, `skipped_no_connection`, `idle_certified`.
- Before any live-readiness claim, require either populated lineage facts or an explicit idle certification with cycle timestamps.
- For replay/projection artifacts, include DB path and seed window so they are not confused with live facts.

### F18 Price Semantics / Orderbook Linkage

Confirmed affected surfaces:
- Persisted `market_price_history` is price-only Gamma scanner data: no best bid, best ask, orderbook hash, or snapshot id.
- `executable_market_snapshots` is empty in the probed DBs.
- Backtest/economics readiness is blocked by design until full linkage exists.

Not yet proven affected:
- Live executor CLOB calls. Live code may still fetch bid/ask directly at decision time; the finding is about persisted evidence and replay/economics claims.

Repair shape:
- Do not use `market_price_history.price` as executable price in economics or replay.
- Persist full CLOB linkage rows with `market_price_linkage='full'`, bid/ask, orderbook hash, fee/tick/min-order/neg-risk facts, and snapshot id before making execution-quality claims.
- Keep economics tombstoned until full-linkage rows exist and tests prove price fields are not bare floats.

### F20 P_raw Parity

Confirmed affected surfaces:
- Formula parity is good: live inference and offline calibration rebuild share `p_raw_vector_from_maxes()`.
- Snapshot-level persisted `p_raw_json` is partial and absent for ECMWF OpenData v2 rows in this pass.

Not yet proven affected:
- Calibration-pair `p_raw` values. Rebuild writes pair-level `p_raw`; snapshot `p_raw_json` coverage is a provenance/replay limitation, not proof of bad pairs.

Repair shape:
- Add a seeded parity test comparing evaluator and rebuild P_raw for the same snapshot/bins.
- Add status language distinguishing `pair_p_raw_available` from `decision_snapshot_p_raw_available`.
- If OpenData becomes live-serving, decide whether snapshot-level p_raw persistence is mandatory for promotion/replay.

### F22 Forecast Regime Stratification

Confirmed affected surfaces:
- Forecast readiness and calibration readiness are not the same thing: current OpenData source-run coverage can be live-ready while most training/calibration evidence remains TIGGE-derived.
- `ensemble_snapshots_v2` source stratification is mixed: old TIGGE blocks often have NULL `source_id`, newer OpenData rows have `ecmwf_open_data`, and LOW has large boundary-ambiguous rejected surfaces.

Not yet proven affected:
- OpenData-specific Platt availability. Current DB has no active VERIFIED `ecmwf_opendata_%` Platt rows, so any live OpenData calibration is either the named TIGGE transfer policy or raw/uncalibrated fallback.

Current-main refinement:
- Active serving selection now threads forecast `cycle`, `source_id`, and `horizon_profile` into `get_calibrator()` in evaluator and monitor paths.
- The remaining issue is report/decision-evidence granularity: forecast readiness, requested calibration domain, served model key, and fail-closed reason are still not one easy-to-query operator surface.

Repair shape:
- Build one report row per serving bucket: forecast data_version/source_id/cycle/horizon, training pair count, active Platt availability, live source-run coverage, and degraded/fallback status.
- Do not collapse TIGGE and OpenData under generic `ecmwf_ens` in status or promotion language.
- Treat LOW boundary-ambiguous rows as a first-class rejection family, not just missing volume.

### F23 OpenData Calibration Serving Contract

Confirmed affected surfaces:
- Live/readiness configuration is OpenData (`ecmwf_open_data`) and the current source coverage table contains OpenData LIVE_ELIGIBLE rows.
- Active Platt models are TIGGE-only in this pass; there are no active VERIFIED OpenData Platt rows.
- Current-main evaluator and monitor paths consume forecast source/cycle/horizon keys, so the old silent schema-default route is not the current repair target.
- Operator-facing evidence still does not collapse forecast readiness, requested calibration domain, served model key, route, and terminal status into a single serving-bucket row.

Not yet proven affected:
- Actual submitted orders. Current opportunity facts have no `should_trade=1` rows in the probed window.
- The physical equivalence claim between OpenData and TIGGE. The finding does not dispute that policy; it says calibration availability and transfer authority need to be visible at the serving-bucket surface.

Repair shape:
- Do not open a broad evaluator-threading packet for current main; that structural fix is already present.
- Add/extend a serving-bucket status report that joins forecast source coverage, requested calibration domain, active Platt availability, route, and terminal reason.
- If TIGGE transfer remains the launch policy, make the transfer authority visible in that report and in decision evidence; otherwise leave OpenData candidates fail-closed until OpenData-specific Platt rows are promoted.

### F24 Market Source-Proof Persistence Gap

Confirmed affected surfaces:
- Offline station/source audits cannot reconstruct active market source proof from `market_events_v2`; only parsed topology is durable there.
- Source-contract parser output exists at scan time but is not persisted with the market event rows.

Not yet proven affected:
- Current station configuration. This pass found no WU observation station mismatches and no latest-settlement config/source disagreement after alias folding.
- Runtime scanning. The parser can reject mismatches at scan time; the finding is about durable evidence after the scan.

Repair shape:
- Add a compact source-contract fact table or JSON artifact rather than bloating market topology rows with raw descriptions.
- Store both raw/structured sources and parsed source-family/station/status so future audits can detect Polymarket source changes without re-fetching historical Gamma payloads.
- Keep `WU`/`wu_icao` and `ICAO`/`ICAO:country` normalization explicit in the audit query to avoid false drift.

### F25 Decision-to-Lifecycle Chain

Confirmed affected surfaces:
- Trade DB contains live decision/opportunity/selection evidence but no submitted-trade, venue, position, execution, or outcome rows in this pass.
- All current opportunity facts are terminally non-trading (`should_trade=0`), so empty position/outcome tables are plausible rather than inherently suspicious.
- Selected FDR hypotheses lack `decision_id`, so the selected-edge lineage cannot be replayed as one exact decision chain.

Not yet proven affected:
- Live order submission correctness. There were no `should_trade=1` rows in the probed window, so this pass does not prove an order-submission failure.
- Position settlement learning. No positions exist in the probed trade DB, so there is no position-level learning chain to validate or invalidate.

Repair shape:
- Add a lifecycle funnel query/report with stage counts and terminal reasons, using the same DB path as the daemon.
- Link selected FDR hypotheses to opportunity/decision ids, not only candidate ids, so repeated candidate rows cannot create ambiguous lineage.
- Keep `no trades submitted` distinct from `trade tables empty`; the former is a certified state, the latter is just absence.

### F21 Known-Gap Boundary

Confirmed affected surfaces:
- The closed v2 row-count antibody remains scoped to world-vs-trade row-count preference.
- It does not cover probe contamination, skipped fact-writer mode, or price-only market evidence.

Repair shape:
- Add `does_not_cover` text to closed known-gap entries when adjacent residuals remain active.
- Avoid citing a narrow closed gap as proof of general observability health.

## Safe-to-Implement Cut - 2026-05-08

This audit is now far enough to split implementation work without guessing at root cause. The safest next packets are observability/provenance packets, not live-trading behavior changes.

### Packet S1 - Market Source-Proof Persistence (F24)

Status: safe to implement.

Why safe: the scanner already parses and validates source contracts at scan time, and the current station/source audit found no active station mismatch. The missing piece is durable evidence, not a change to market eligibility logic.

Minimal scope:
- Add a compact `market_source_contract_fact` table or artifact keyed by market slug/city/date/metric/scan time.
- Persist raw/structured resolution sources, parsed source family/station, configured source family/station, status, and reason.
- Add tests proving WU/HKO/NOAA/CWA parser outputs are persisted without changing `market_events_v2` topology semantics.

Do not include: retroactive station migration repair, Gamma refetch requirements, or settlement-source policy changes.

### Packet S2 - Lifecycle Funnel Report (F25)

Status: safe to implement for reporting; schema-link changes need one design decision.

Why safe: current DB evidence already distinguishes `no eligible trades submitted` from merely empty trade tables. A read-only funnel report can make that distinction durable without touching order submission.

Minimal scope:
- Add a lifecycle funnel query/report: evaluated -> selected_post_fdr -> opportunity rejected -> should_trade -> order submitted -> fill -> position -> settlement/outcome -> learning.
- Include terminal rejection stages and counts by DB path/runtime window.
- Add tests with fixture rows proving `no trades submitted` is a certified state distinct from `trade tables empty`.

Design decision before schema change: `selection_hypothesis_fact` is written before final `EdgeDecision.decision_id` exists in the current evaluator flow, so blindly requiring `decision_id` there is not safe. Prefer a stable cycle/snapshot/opportunity linkage design unless the write order is changed deliberately.

### Packet S3 - Calibration Serving Status Surface (F22/F23)

Status: safe to implement as observability; evaluator-threading implementation is not needed on current main.

Why safe: current main already derives phase-2 keys and passes them into `get_calibrator()` in evaluator and monitor paths. The remaining risk is that operator/readiness surfaces can conflate forecast coverage readiness with calibration serving readiness.

Minimal scope:
- Add a serving-bucket status report keyed by forecast source/data_version/cycle/horizon and requested calibration source/data_version/cycle/horizon.
- Count active VERIFIED Platt rows, fail-closed RAW/CALIBRATION_IMMATURE candidates, and any explicitly authorized transfer routes.
- Add a regression assertion that OpenData candidates never fall through to schema-default TIGGE `00/tigge_mars/full` without an explicit transfer authority result.

Do not include: OpenData Platt promotion/refit. That is a separate statistical authority packet and should not be smuggled into observability work.

### Packet S4 - Price/Orderbook Evidence Report (F18/A14)

Status: safe to implement as evidence reporting; not safe to claim live price bug from this pass.

Why safe: current live code captures executable market snapshots, links market-price history, reprices from snapshots, and validates final execution intent before submission. The probed DB has zero executable snapshots and zero venue/order rows because no opportunity reached `should_trade=1`, while price-only `market_price_history` and `token_price_log` are populated.

Minimal scope:
- Add a report that declares whether a runtime window has price-only scanner evidence, token refresh evidence, executable snapshot evidence, or venue evidence.
- Require economics/replay reports to state `price_only` vs executable-snapshot-backed mode.

Do not include: order submission logic changes. No submitted-order price defect was proven.

### Not Safe Yet

- F2 hourly observation contract: needs a product decision first (`sparse evidence log` vs `complete hourly table`).
- F13 source-truth split: needs an explicit promotion-domain decision before changing calibration pair inclusion.
- F1 authority freshness: safe as a docs/current-fact refresh, but do not treat it as a runtime repair.

## Report Format

Use this order for every update:

1. Executive status: counts by PASS/WARN/FAIL/BLOCKED/NEEDS_FRESH_FACTS.
2. Critical mismatches: only if evidence shows live-money or core-statistical risk.
3. Matrix updates: one row per axis changed since last pass.
4. Open probes: next read-only checks, ordered by expected information gain.
5. Antibody queue: candidate tests/types/gates for a future implementation packet.
