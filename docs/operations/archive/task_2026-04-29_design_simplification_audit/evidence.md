# Evidence Log

## Local Topology And Scope

- `docs/operations/AGENTS.md` requires new independent multi-file packages to use `task_YYYY-MM-DD_name/` and be registered in the file registry.
- Initial topology navigation classified `docs/operations/task_2026-04-29_design_simplification_audit/*` as unclassified until this packet was registered.
- This packet is packet evidence only and does not change runtime code.

## Forecast Source Authority

### Repository Law

- `docs/reference/zeus_math_spec.md:106-108` says canonical forecast data is ECMWF TIGGE GRIB, Open-Meteo ECMWF ensemble is a temporary live fallback only, and rebuild/training data must use TIGGE GRIB only.
- `docs/authority/zeus_current_architecture.md:175-202` names TIGGE high/low data versions and says high is re-canonicalized onto `tigge_mx2t6_local_calendar_day_max_v1` before low enters live authority.
- `src/data/AGENTS.md:30-34` treats `forecasts_append.py` as high-risk signal source and states Open-Meteo is never a settlement source, only calibration/feature backup.

### Pre-Remediation Code Path

- `src/data/forecast_source_registry.py:72-77` maps `ecmwf_ifs025` to `openmeteo_ensemble_ecmwf_ifs025`, `gfs025` to `openmeteo_ensemble_gfs025`, and only `tigge` to `tigge`.
- `src/data/forecast_source_registry.py:115-126` marks Open-Meteo ECMWF live ensemble as `tier="primary"` and GFS as `tier="secondary"`.
- `src/data/forecast_source_registry.py:127-137` marks TIGGE as `tier="experimental"`, `enabled_by_default=False`, and operator-gated.
- `src/data/forecast_source_registry.py:179-181` defaults a missing ensemble model to `ecmwf_ifs025`, which becomes Open-Meteo ECMWF.
- `src/data/ensemble_client.py:62-81` defaults `fetch_ensemble(..., model="ecmwf_ifs025")`, resolves it through the registry, and gates that source.
- `src/data/ensemble_client.py:92-128` performs Open-Meteo HTTP for non-registered-ingest sources.
- `src/data/ensemble_client.py:150-181` routes registered ingest sources, including TIGGE, through the local ingest class.
- `src/data/tigge_client.py:4-12` states the TIGGE module is a dormant stub and performs no external TIGGE archive I/O.
- `src/data/tigge_client.py:128-132` says open-gate TIGGE reads only a local operator-approved JSON payload, not live TIGGE HTTP/GRIB.
- `src/engine/evaluator.py:870` calls `fetch_ensemble(city, forecast_days=ens_forecast_days)` with no model argument, therefore Open-Meteo ECMWF.
- `src/engine/evaluator.py:1174` calls `fetch_ensemble(..., model="gfs025")`, therefore Open-Meteo GFS.
- `src/engine/monitor_refresh.py:118` and `src/engine/monitor_refresh.py:307` call `fetch_ensemble()` with no model argument, therefore Open-Meteo ECMWF.
- `config/settings.json:29-34` declares ensemble `primary` and `crosscheck`, but runtime calls above do not read those keys for source/model selection.
- `src/engine/evaluator.py:1312-1339` collapses the selected source through `_forecast_source_key(ens_result.get("model"))`, yielding model-family keys like `ecmwf`/`gfs` instead of provider-specific `source_id` values such as `openmeteo_ensemble_ecmwf_ifs025` or `tigge`.

### Current Post-Phase-1D/1E Code Path

- `src/data/forecast_source_registry.py:132-138` marks
  `openmeteo_ensemble_ecmwf_ifs025` as `tier="secondary"`,
  `allowed_roles=("monitor_fallback", "diagnostic")`, and
  `degradation_level="DEGRADED_FORECAST_FALLBACK"`.
- `src/config.py:318-330` exposes strict accessors for
  `settings["ensemble"]["primary"]` and
  `settings["ensemble"]["crosscheck"]`; missing or empty keys fail loud instead
  of silently restoring hardcoded model choices.
- `src/data/ensemble_client.py:70-91` defaults `fetch_ensemble()` to
  `role="entry_primary"` and calls `gate_source_role()` before any
  Open-Meteo HTTP path can execute.
- `src/engine/evaluator.py:887-898` requests `role="entry_primary"` and turns a
  blocked source-role gate into `SIGNAL_QUALITY`, `DATA_STALE`, and
  `forecast_source_policy` before p_raw.
- `src/engine/evaluator.py:903-907` passes the configured primary model into
  the entry-primary fetch instead of relying on the `fetch_ensemble()` default.
- `src/engine/monitor_refresh.py:45-58` builds monitor evidence tokens for
  `forecast_source_id`, `forecast_source_role`, and `forecast_degradation`.
- `src/engine/monitor_refresh.py:139-147` and
  `src/engine/monitor_refresh.py:348-352` explicitly request
  the configured primary model with `role="monitor_fallback"` and preserve the
  fallback provenance for successful ENS and Day0 monitor refreshes.
- `src/engine/evaluator.py:1282-1289` requests the configured crosscheck model
  as `role="diagnostic"` instead of hardcoding `gfs025`.
- `src/engine/evaluator.py:357-373` now separates provider-specific
  `forecast_source_id` from broad `model_family`; `src/engine/evaluator.py:1427-1448`
  uses `source_id` for bias lookup and keeps `model_family` in forecast context.
- DSA-02/DSA-03 are closed for the live evaluator/monitor entry and held
  position probability paths. Remaining source-program work is direct
  TIGGE/ECMWF primary activation, source timing/payload hash completeness, and
  downstream evidence/capability propagation.

### Official Provider Evidence

- Open-Meteo's Ensemble API documentation says the API provides individual ensemble member forecasts for multiple weather models, includes ECMWF IFS 0.25 ensemble and GFS ensemble model options, and returns API data through `/v1/ensemble`. Source: https://open-meteo.com/en/docs/ensemble-api.
- The same Open-Meteo documentation lists ECMWF IFS 0.25 as 51 members and GFS 0.25 as 31 members, aligning with Zeus's `primary_members=51` and `crosscheck_members=31`, but this remains Open-Meteo distribution, not direct TIGGE ingest.
- ECMWF's TIGGE data store says TIGGE is a global medium-range ensemble forecast dataset from multiple NWP centres, available via ECMWF data store/MARS, with GRIB2 data and ensemble sizes up to 51. Source: https://ecds.ecmwf.int/datasets/tigge-forecasts.
- ECMWF's ENS dataset page lists direct model output and 2 metre temperature (`2t`, parameter 167) and also max/min 2 metre temperature products (`mx2t6`, `mn2t6`). Source: https://www.ecmwf.int/en/forecasts/datasets/set-iii.

## Day0 Observation Fallback

- `src/data/observation_client.py:1-7` explicitly defines Priority 1 WU, Priority 2 IEM ASOS for US cities, Priority 3 Open-Meteo hourly as free fallback.
- `src/data/observation_client.py:188-218` implements that order: WU first, IEM ASOS second for US/F, Open-Meteo hourly last, then raises `ObservationUnavailableError`.
- `src/data/observation_client.py:362-422` implements Open-Meteo hourly fallback and stamps `source="openmeteo_hourly"`.
- `src/data/tier_resolver.py:13-15` says historical/settlement-adjacent observation routing deliberately has no Open-Meteo Tier-4 escape hatch.
- `src/data/tier_resolver.py:54-65` contains only WU_ICAO, OGIMET_METAR, and HKO_NATIVE tiers.
- `src/data/tier_resolver.py:299-372` classifies model/grid/Open-Meteo source tags as non-training-eligible.
- `tests/test_tier_resolver.py:88-117`, `tests/test_tier_resolver.py:205-211`, and `tests/test_tier_resolver.py:276-285` lock that no Open-Meteo source is accepted as historical/training observation truth.
- `tests/test_obs_v2_writer.py:234-237` rejects `openmeteo_archive_hourly` for a WU city.

Interpretation: Open-Meteo is correctly final fallback for Day0 monitoring observations and is not accepted as historical/training observation source. This is a false-positive boundary: the Day0 observation chain is not the same problem as the live ENS forecast chain.

## Forecast History And Replay Fallback

- `src/data/forecasts_append.py:1-8` is a live NWP forecast-history appender using the Open-Meteo Previous Runs API.
- `src/data/forecasts_append.py:134-159` calls Open-Meteo Previous Runs.
- `src/main.py:219-231` calls forecast catch-up at startup.
- `src/engine/replay.py:246-354` uses forecast rows as diagnostic historical forecast fallback and filters legacy rows by availability provenance when available.
- `src/backtest/training_eligibility.py:27-49` defines SKILL and ECONOMICS eligibility fragments; ECONOMICS excludes reconstructed rows.
- `tests/test_replay_skill_eligibility_filter.py:71-94` confirms reconstructed Open-Meteo previous-runs rows are excluded, while legacy NULL-provenance Open-Meteo rows remain tolerated for un-migrated diagnostic replay.
- Current Phase 1G status: migrated schemas with an `availability_provenance`
  column no longer admit `openmeteo_previous_runs` rows where that column is
  NULL. Schemas with no provenance column still fall back to legacy diagnostic
  replay behavior.

## ECMWF Open Data Parallel Path

- `src/main.py:236-241` defines an `ecmwf_open_data` scheduler job.
- `src/main.py:693-698` schedules that job using `config/settings.json:17-20`.
- `src/data/ecmwf_open_data.py:1-23` writes ECMWF Open Data ENS member vectors into `ensemble_snapshots` with `DATA_VERSION="open_ens_v1"` and `MODEL_VERSION="ecmwf_open_data"`.
- `src/data/ecmwf_open_data.py:130-150` writes rows directly to the legacy `ensemble_snapshots` table.
- The evaluator and monitor do not read this path for live probability generation.
- Current Phase 1F status: `ecmwf_open_data` is now present in the forecast
  source registry as `allowed_roles=("diagnostic",)` with
  `degradation_level="DIAGNOSTIC_NON_EXECUTABLE"`. The collector gates itself
  through that diagnostic role and writes legacy snapshot rows as
  `authority="UNVERIFIED"` rather than defaulting to `VERIFIED`.

## Mode And Venue Residue

- Pre-Phase 0B evidence: earlier `src/config.py` revisions accepted
  environment input and defaulted missing input to live.
- Current Phase 0B status: `src/config.py` returns live from code authority and
  ignores `ZEUS_MODE` as a runtime selector; `tests/test_k5_slice_l.py::TestGetMode`
  locks that environment input cannot select alternate runtime state.
- `src/main.py` is the live daemon entrypoint; live state authority is not an
  environment selector.
- `src/engine/cycle_runner.py:476` constructs `PolymarketClient()` directly.
- `src/data/polymarket_client.py:60-94` initializes the live CLOB V2 adapter and exposes no alternate execution constructor.
- Pre-Phase 1H evidence: `src/engine/monitor_refresh.py` contained an non-live execution branch that used Gamma current yes price; the exact retired literal is redacted in this active archive copy.
- Phase 1H status: `src/engine/monitor_refresh.py:689-718` now uses only the
  live venue quote shape (`clob.get_best_bid_ask()`), with YES/NO token
  selection, best bid in `day0_window`, and VWMP otherwise.
- `src/venue/AGENTS.md:23-24` says fake behavior belongs in test-only fakes, not production simulated/live venue split paths.
- `src/venue/polymarket_v2_adapter.py:91-97` defines a shared live/simulated-venue adapter protocol for fake venue parity tests.
- Pre-Phase 1I evidence: earlier `src/strategy/benchmark_suite.py` revisions
  still defined `obsolete simulated-venue label`, `SHADOW`, and `LIVE` benchmark environment concepts
  and evaluated simulated-venue metrics from a fake venue.
- Phase 1I status: `src/strategy/benchmark_suite.py` now uses
  `SIMULATED_VENUE`, `READ_ONLY_LIVE`, and
  `PROMOTION_GRADE_ECONOMICS` public concepts; legacy string values remain
  storage labels only.

Interpretation: the non-live execution path is not live-reachable through the main daemon or
Polymarket client. Phase 1H removed the production monitor branch; Phase 1I
renamed strategy benchmark promotion concepts to evidence-grade terms.

## Shadow And Diagnostic Replay Residue

- `src/state/db.py:637-648` defines `shadow_signals` as "Shadow signals for pre-trading validation".
- `src/engine/cycle_runtime.py:1167-1194` writes `shadow_signals` telemetry for every candidate decision.
- `src/state/db.py:650-655` also defines `probability_trace_fact` as durable per-decision probability lineage.
- `src/engine/replay.py:42-46` lists `shadow_signals` as a diagnostic replay reference source.
- `src/engine/replay.py:532-588` can use `shadow_signals` as a fallback when `allow_snapshot_only_reference` is true.
- Pre-Phase 1J evidence: earlier `src/engine/replay.py` revisions set
  `allow_snapshot_only_reference=(allow_snapshot_only_reference or mode != "audit")`,
  so non-audit replay modes automatically allowed snapshot-only references.
- Current Phase 1J status: `run_replay()` now passes only the caller-provided
  `allow_snapshot_only_reference` into `ReplayContext`; counterfactual and
  walk-forward modes no longer auto-enable diagnostic fallback by mode name.

Local read-only DB sample on 2026-04-29:

- `state/zeus_trades.db.shadow_signals` count was 0 at audit time.
- `state/zeus-world.db.ensemble_snapshots` had no grouped `data_version/model_version` rows in the simple count query.
- `state/zeus-world.db.ensemble_snapshots_v2` contained 342312 high TIGGE rows and 342312 low TIGGE rows split across training flags.
- `state/zeus-world.db.forecasts` contained `ecmwf_previous_runs`, `gfs_previous_runs`, `openmeteo_previous_runs`, `icon_previous_runs`, and `ukmo_previous_runs`; current Open-Meteo previous-runs rows were reconstructed, with additional NULL-provenance legacy rows.

## Feature Flags And Dual Semantics

- Pre-Phase 0C evidence: `config/settings.json:145-149` still contained
  `EXECUTION_PRICE_SHADOW`, `CANONICAL_EXIT_PATH`, and
  `HOLD_VALUE_EXIT_COSTS` flags.
- Current Phase 0C status: `config/settings.json` no longer contains
  `EXECUTION_PRICE_SHADOW`; `CANONICAL_EXIT_PATH` and
  `HOLD_VALUE_EXIT_COSTS` remain active staged-migration flags.
- `tests/test_execution_price.py:177-195` asserts `EXECUTION_PRICE_SHADOW`
  has been removed from both `src/engine/evaluator.py` and the operator
  settings surface.
- `src/execution/harvester.py:58-73` reads `CANONICAL_EXIT_PATH`, default false.
- `src/execution/harvester.py:1217-1222` chooses `mark_settled` vs `compute_settlement_close` depending on that flag.
- `src/config.py:463-470` reads `HOLD_VALUE_EXIT_COSTS`, default false.
- `src/state/portfolio.py:619-630` and `src/state/portfolio.py:781-790` keep exit EV behavior feature-flagged.

Interpretation: some feature flags protect deliberate staged migrations, but
first-principles live-money design should converge them into one canonical path
after validation. Phase 0C removed the flag that no longer affected code.

## Market Identity And Executable Snapshot Chain

- `src/contracts/executable_market_snapshot_v2.py:42-78` defines the immutable executable snapshot fields: Gamma market/event identity, CLOB condition/question ids, yes/no token ids, orderbook/tradability flags, tick/order size, neg-risk flag, raw payload hashes, authority tier, capture time, and freshness deadline.
- `src/contracts/executable_market_snapshot_v2.py:164-220` fails closed when the snapshot is missing, stale, non-tradable, token-mismatched, or when intent tick/order-size/neg-risk facts differ from the snapshot.
- `src/execution/executor.py:449-525` has executable snapshot fields in `create_execution_intent()`, but callers must thread them.
- `src/engine/cycle_runtime.py:1301-1318` currently calls `create_execution_intent()` without executable snapshot id, tick size, order size, or neg-risk facts. This is the concrete F03 symptom.
- `src/execution/executor.py:1349-1381` persists the pre-submit envelope and inserts the venue command with the intent's executable snapshot fields.
- `src/venue/polymarket_v2_adapter.py:503-509` still documents a compatibility envelope path with placeholder market identity before full U1 snapshot closure. This is the concrete F04 symptom.

Interpretation: the executable snapshot contract is the right first-principles seam, but the runtime discovery-to-submit path still has caller and adapter compatibility gaps. The repair path should be one market identity contract, not piecemeal field threading.

## Command, Fill, Exposure, And Settlement Truth

- `src/execution/command_bus.py:44-68` defines a closed venue command state grammar, including `ACKED`, `PARTIAL`, `FILLED`, `CANCELLED`, `EXPIRED`, `REJECTED`, and `SUBMIT_UNKNOWN_SIDE_EFFECT`.
- `src/state/db.py:222-242` defines append-only `venue_trade_facts` with trade lifecycle states `MATCHED`, `MINED`, `CONFIRMED`, `RETRYING`, and `FAILED`.
- `src/state/db.py:258-280` defines append-only `position_lots` with optimistic, confirmed, economically closed, settled, and quarantined exposure states.
- `src/state/venue_command_repo.py:939-1029` appends venue trade facts and provenance events.
- `src/state/venue_command_repo.py:1032-1115` appends position lots as the exposure projection.
- `src/state/venue_command_repo.py:1138-1144` only allows calibration training to consume `CONFIRMED` venue trade facts.
- `src/execution/fill_tracker.py:1-8` says live entries become `pending_tracked` before CLOB fill confirmation and that fill tracking owns fill verification; chain reconciliation is rescue only.
- `src/execution/fill_tracker.py:24` treats `FILLED` and `MATCHED` as fill statuses on the legacy polling path. This is the concrete F06 symptom.
- `src/execution/fill_tracker.py:337-358` can void a timed-out pending entry after cancel without materializing a partial fill. This is the concrete F11 symptom.
- `src/execution/exchange_reconcile.py:342-408` appends linkable trade facts from exchange reconciliation and converts partial/full filled sizes into command events.
- `src/execution/exchange_reconcile.py:468-484` reconstructs journal positions from `venue_trade_facts` in `MATCHED`, `MINED`, and `CONFIRMED`.
- `src/execution/command_recovery.py:153-199` can recover unknown-side-effect commands into filled or partial command states.
- `src/engine/cycle_runtime.py:1366-1375` materializes positions only after durable command states `ACKED`, `PARTIAL`, or `FILLED`.

Interpretation: the newer command/trade-fact/lot spine is directionally correct, but legacy fill polling and timeout handling still bypass the richer confirmed-vs-optimistic ledger semantics. The repair path should make confirmed exposure, optimistic exposure, partial remainder, cancel, chain reconciliation, and settlement all projections of one append-only exposure ledger.

## Submit Capability And Risk Controls

- `src/control/cutover_guard.py:4-18` states that executor-facing cutover decisions are computed before venue command rows or SDK side effects; cancel/redemption decisions remain decision surfaces until later paths wire them.
- `src/control/cutover_guard.py:71-86` exposes `allow_submit`, `allow_cancel`, and `allow_redemption` as a cutover decision.
- `src/riskguard/risk_level.py:14-20` defines level actions from normal operation through RED cancel/exit.
- `src/risk_allocator/governor.py:159-194` checks allocation caps, unknown-side-effect markets, reduce-only mode, event/window/correlation caps, and existing confirmed plus optimistic exposure.
- `src/risk_allocator/governor.py:196-227` maps heartbeat/depth/close-time/risk state into maker/taker/no-trade and reduce-only behavior.
- `src/risk_allocator/governor.py:229-243` defines kill-switch reasons from manual kill, heartbeat lost, WS gap, unknown side effects, reconcile findings, and drawdown.
- `src/risk_allocator/governor.py:363-381` fails closed for non-entry submits when the global allocator is missing, kill-switch is active, or reduce-only blocks new risk.
- `src/risk_allocator/governor.py:384-404` selects order type from global governor state, raising when no trade is allowed.
- `src/risk_allocator/governor.py:440-470` refreshes the global allocator from position lots, unknown side effects, reconcile findings, heartbeat, and WS status.

Interpretation: risk and operator controls exist in multiple strong surfaces, but live readiness should expose one composed capability result for entry, exit, cancel, and redeem. Source degradation and executable snapshot validity need to be part of that same capability proof, not separate downstream surprises.

## Evidence Grade, Replay, And Promotion

- `src/engine/replay.py:1-13` states replay is approximate audit only and writes diagnostic, non-promotion authority.
- `src/engine/replay.py:42-46` still carries diagnostic replay fallback source names including `shadow_signals`.
- `src/engine/replay.py:532-588` can use `shadow_signals` as snapshot-only replay fallback when allowed.
- `src/engine/replay.py:1790-1804`, `src/engine/replay.py:2030-2038`, and `src/engine/replay.py:2354-2359` all stamp replay outputs with `promotion_authority=False` and explicit parity limitations.
- `src/backtest/decision_time_truth.py:22-32` defines availability provenance and promotion-grade provenance.
- `src/backtest/decision_time_truth.py:52-79` gates provenance by purpose, refusing weak evidence for economics and reconstructed timestamps for skill.
- `src/backtest/economics.py:1-24` tombstones the economics purpose until market-events, market-price history, and parity contracts exist.
- Pre-Phase 1I evidence: strategy docs still said promotion required replay +
  simulated evidence + shadow evidence, even though non-live execution has no runtime authority.
- Phase 1I status: `docs/reference/modules/strategy.md` states promotion
  requires promotion-grade economics plus supporting diagnostic,
  simulated-venue, and read-only-live evidence.

Interpretation: newer backtest purpose gating is strong. Phase 1I removed the
legacy replay/simulated/shadow promotion vocabulary from the strategy module
reference and benchmark public API; remaining `shadow_signals` naming is a
separate diagnostic replay table/fallback issue.

## Time Causality And Latency Surfaces

- `src/backtest/decision_time_truth.py:1-12` documents an ECMWF ENS dissemination availability schedule and treats availability provenance as a typed fact.
- `src/backtest/decision_time_truth.py:35-45` stores `available_at` and provenance on decision-time truth.
- `src/engine/evaluator.py:1857-1936` stores live ENS snapshots through legacy columns that require issue/valid time, while Open-Meteo parsing can produce missing issue time. This is the concrete F08 symptom.
- `src/data/observation_client.py:188-218` has a Day0 observation chain whose WU/IEM/Open-Meteo fallbacks have different latency and authority.
- `src/engine/replay.py:42-46` allows diagnostic fallback references from snapshot availability and synthetic forecast rows.

Interpretation: issue time, valid time, fetch time, available-at time, observation time, decision time, and venue timestamp are handled in multiple lane-specific contracts. That is safer than no timing metadata, but not yet a single time-causality contract for live source selection, sizing, replay, and learning.

## Economic Alpha Proof Evidence

- `src/backtest/purpose.py:3-8` separates SKILL, ECONOMICS, and DIAGNOSTIC purposes.
- `src/backtest/purpose.py:84-88` defines ECONOMICS parity as Kelly bootstrap sizing, BH-FDR selection, and full market-price linkage.
- `src/backtest/purpose.py:113-118` makes only ECONOMICS promotion-authoritative.
- `src/backtest/economics.py:17-24` refuses to run ECONOMICS until market events, market-price history, and parity contracts are present.
- `src/engine/replay.py:1-13` states replay can score forecast skill or diagnostic divergence but cannot produce dollars without market price linkage.
- `src/engine/replay.py:2351-2359` persists replay limitations with `promotion_authority=False`.
- `docs/operations/task_2026-04-27_backtest_first_principles_review/01_backtest_upgrade_design.md:46-61` lists the missing economics inputs: decision-time posterior, decision-time market price vector, Polymarket fee/tick/neg-risk, realized fill/slippage, live Kelly sizing, and BH-FDR selection.
- `docs/operations/task_2026-04-27_backtest_first_principles_review/03_data_layer_issues.md:286-292` says ECONOMICS is structurally blocked until market events, market price history, resolution-source-match cohort, and full parity exist.

Local read-only DB probe on 2026-04-29:

- `state/zeus_trades.db`: `market_events`, `market_events_v2`, `market_price_history`, `venue_trade_facts`, `position_lots`, `probability_trace_fact`, `shadow_signals`, `trade_decisions`, and `position_events` all had count 0.
- `state/zeus-world.db`: the same economics and live-trace tables also had count 0.
- `state/zeus_backtest.db`: `backtest_runs` had count 1, but that does not provide promotion-grade live economics without the missing market/venue/trace facts above.

Interpretation: Zeus has a good typed contract for economics authority, but the promotion-authoritative economics lane is intentionally tombstoned and the local evidence substrate is empty. This is the final blocker to claiming that no-profit outcomes would be attributable only to model/weather physics rather than statistical, source, execution, or design error.

## Phase 0 Closeout Evidence - 2026-04-29

Scope:

- Phase 0A: strategy benchmark evidence-grade vocabulary and economics-only promotion authority.
- Phase 0B: environment selector authority removed outside inert test metadata.

Changed implementation surfaces:

- `src/strategy/benchmark_suite.py`: adds `EvidenceGrade`, stamps metrics with diagnostic/simulated/read-only-live/promotion-economics grades, keeps old environment labels as storage provenance, and blocks `promotion_decision()` unless promotion-grade economics evidence is supplied.
- `src/strategy/__init__.py`: exports `EvidenceGrade`.
- `src/config.py`: removes environment selector authority from runtime state.
- `tests/conftest.py`: may set `ZEUS_MODE` only as inert test metadata so pytest collection can import config-backed modules without restoring production selectors.
- `tests/test_strategy_benchmark.py`: proves supporting evidence cannot promote alone and wrong economics evidence grade blocks promotion.
- `tests/test_k5_slice_l.py`: proves environment input cannot select alternate runtime state.
- `docs/reference/modules/strategy.md`: updates strategy module reference from replay/simulated/shadow promotion wording to evidence-grade plus promotion-grade economics wording.

Verification run:

- `python3 -m py_compile src/strategy/benchmark_suite.py src/strategy/__init__.py src/config.py tests/conftest.py` -> pass.
- `pytest -q tests/test_strategy_benchmark.py` -> 11 passed.
- `pytest -q tests/test_k5_slice_l.py` -> 8 passed.
- `pytest -q tests/test_strategy_benchmark.py tests/test_k5_slice_l.py::TestGetMode` -> 14 passed.
- `pytest -q tests/test_fdr.py tests/test_kelly.py tests/test_market_analysis.py tests/test_strategy_benchmark.py` -> 73 passed.
- `python scripts/semantic_linter.py --check src/strategy/benchmark_suite.py src/strategy/__init__.py tests/test_strategy_benchmark.py docs/reference/modules/strategy.md src/config.py tests/test_k5_slice_l.py tests/conftest.py` -> pass.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/strategy/benchmark_suite.py src/strategy/__init__.py tests/test_strategy_benchmark.py docs/reference/modules/strategy.md src/config.py tests/test_k5_slice_l.py tests/conftest.py --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files src/strategy/benchmark_suite.py src/strategy/__init__.py tests/test_strategy_benchmark.py docs/reference/modules/strategy.md src/config.py tests/test_k5_slice_l.py tests/conftest.py` -> pass.
- `python3 scripts/topology_doctor.py --tests --json` -> fail on pre-existing unclassified tests outside this phase's changed-file set: `tests/test_attribution_drift.py`, `tests/test_attribution_drift_weekly.py`, `tests/test_calibration_observation.py`, `tests/test_edge_observation.py`, `tests/test_edge_observation_weekly.py`, `tests/test_inv_prototype.py`, `tests/test_no_synthetic_provenance_marker.py`, `tests/test_settlements_physical_quantity_invariant.py`, `tests/test_ws_poll_reaction.py`, and `tests/test_ws_poll_reaction_weekly.py`.

Phase 0B recertification run:

- `python3 scripts/topology_doctor.py --navigation --task "DSA-12 get_mode ZEUS_MODE compatibility cleanup; environment input ignored by runtime state; no production DB mutation; no Paris config edit" ...`
  -> admitted to the Phase 0B selector-cleanup route.
- `pytest -q -p no:cacheprovider tests/test_k5_slice_l.py::TestGetMode tests/test_config.py::test_settings_missing_key_raises tests/test_config.py::test_settings_no_fallback_pattern tests/test_digest_profile_matching.py::test_dsa12_zeus_mode_selector_cleanup_routes_to_phase0b_profile tests/test_digest_profiles_equivalence.py`
  -> 10 passed.
- `python3 -m py_compile src/config.py tests/test_k5_slice_l.py tests/test_config.py tests/test_digest_profile_matching.py architecture/digest_profiles.py`
  -> pass.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `python3 scripts/semantic_linter.py --check ...` over the touched config,
  tests, topology, digest, and packet evidence files -> pass.
- Combined closeout after DSA-08/17 reviewer remediation and Phase 0B
  recertification:
  `pytest -q -p no:cacheprovider tests/test_fake_polymarket_venue.py tests/test_strategy_benchmark.py tests/test_k5_slice_l.py::TestGetMode tests/test_config.py::test_settings_missing_key_raises tests/test_config.py::test_settings_no_fallback_pattern tests/test_digest_profile_matching.py::test_dsa08_dsa17_evidence_grade_cleanup_routes_to_a1_profile tests/test_digest_profile_matching.py::test_r3_a1_strategy_benchmark_routes_to_a1_profile_not_heartbeat tests/test_digest_profile_matching.py::test_dsa12_zeus_mode_selector_cleanup_routes_to_phase0b_profile tests/test_digest_profiles_equivalence.py`
  -> 30 passed.
- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A1`
  -> GREEN.
- `python3 scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md`
  -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files config/AGENTS.md config/settings.json tests/AGENTS.md tests/test_execution_price.py tests/test_digest_profile_matching.py architecture/AGENTS.md architecture/topology.yaml architecture/digest_profiles.py architecture/test_topology.yaml docs/operations/AGENTS.md docs/operations/known_gaps.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md`
  -> pass. `docs/operations/AGENTS.md` is included as the required packet
  registry companion.
- `git diff --check -- ...` over the touched DSA-08/17 and DSA-12 files
  -> pass.

Phase 0C stale execution-price flag cleanup:

- `config/settings.json`: removes stale `feature_flags.EXECUTION_PRICE_SHADOW`
  only; existing unrelated `ensemble.n_mc` / `day0.n_mc` local changes were
  preserved.
- `tests/test_execution_price.py::TestEvaluatorWiring::test_settings_do_not_expose_execution_price_shadow_flag`
  locks the settings surface against reintroducing the deleted flag.
- `docs/operations/known_gaps.md`: updates D3 to state fee-adjusted execution
  price is unconditional and the rollback flag was removed.
- `tests/test_digest_profile_matching.py::test_dsa09_stale_execution_price_shadow_flag_routes_to_phase0c_profile`
  proves DSA-09 routes to the dedicated profile rather than live-readiness.
- `pytest -q -p no:cacheprovider tests/test_execution_price.py::TestEvaluatorWiring::test_shadow_flag_removed_from_evaluator tests/test_execution_price.py::TestEvaluatorWiring::test_settings_do_not_expose_execution_price_shadow_flag tests/test_execution_price.py::TestEvaluatorWiring::test_evaluator_always_uses_fee_adjusted_size tests/test_execution_price.py::TestEvaluatorWiring::test_shadow_off_path_raises_on_feature_flags_kwarg tests/test_digest_profile_matching.py::test_dsa09_stale_execution_price_shadow_flag_routes_to_phase0c_profile tests/test_digest_profiles_equivalence.py`
  -> 9 passed.
- `python3 - <<'PY' ... Settings()["feature_flags"] ...`
  -> settings ok; `EXECUTION_PRICE_SHADOW` absent.
- `python3 -m json.tool config/settings.json` -> pass.
- `python3 scripts/semantic_linter.py --check ...` over touched test,
  topology, digest, known-gaps, and packet evidence files -> pass.
- `python3 scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md`
  -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout ...`
  -> pass.
- `git diff --check -- ...` over DSA-09 files -> pass.
- `rg -n 'EXECUTION_PRICE_SHADOW' config/settings.json src/engine/evaluator.py`
  -> no matches.

Phase 1J replay snapshot-only fallback explicit opt-in:

- `src/engine/replay.py`: removes the `mode != "audit"` auto-enable branch for
  `allow_snapshot_only_reference`; replay modes now share strict market-events
  preflight unless the caller explicitly requests diagnostic fallback.
- `tests/test_run_replay_cli.py::test_counterfactual_replay_does_not_auto_enable_snapshot_only_reference`
  proves counterfactual replay fails closed on missing market-events without
  explicit fallback and succeeds only when `allow_snapshot_only_reference=True`.
- Existing replay provenance tests keep ensemble snapshot, forecast-row, and
  `shadow_signals` fallback available under explicit opt-in.
- Replay output still records `promotion_authority=False`; this slice does not
  rename or migrate `shadow_signals` and does not claim economics authority.
- `architecture/topology.yaml` now routes the exact Phase 1J closeout wording
  to the dedicated profile. The generic replay-fidelity profile vetoes
  `non-audit replay` / `snapshot-only fallback` so it cannot misread
  `non-audit replay` as an `audit replay` task.
- `tests/test_run_replay_cli.py` legacy calibration-pair fixtures were updated
  from regional cluster labels to city-level labels while the file was reused,
  satisfying the K3 cluster-collapse linter without changing replay behavior.
- `pytest -q -p no:cacheprovider tests/test_run_replay_cli.py::test_counterfactual_replay_does_not_auto_enable_snapshot_only_reference tests/test_run_replay_cli.py::test_run_replay_allows_snapshot_only_reference_opt_in tests/test_run_replay_cli.py::test_run_replay_snapshot_only_can_fallback_to_forecast_rows tests/test_replay_time_provenance.py::test_replay_context_snapshot_only_fallback_is_opt_in tests/test_replay_time_provenance.py::test_replay_context_can_fallback_to_shadow_signal_reference tests/test_digest_profile_matching.py::test_dsa10_dsa18_snapshot_only_fallback_routes_to_phase1j_profile tests/test_digest_profiles_equivalence.py`
  -> 10 passed.
- `python3 scripts/semantic_linter.py --check ...` over the touched replay,
  test, topology, digest, and packet evidence files -> pass.
- `python3 -m py_compile src/engine/replay.py tests/test_run_replay_cli.py tests/test_replay_time_provenance.py tests/test_digest_profile_matching.py architecture/digest_profiles.py`
  -> pass.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 1J DSA-10 DSA-18 replay snapshot-only fallback explicit opt-in; remove implicit snapshot-only fallback for non-audit replay modes; tests/docs only; no DB mutation; no live venue; no Paris source routing" --files ...`
  -> navigation ok; profile `phase 1j replay snapshot-only fallback explicit
  opt-in`; admission status `admitted`.
- `rg -n 'mode != "audit"|allow_snapshot_only_reference=\(allow_snapshot_only_reference or mode' src/engine/replay.py tests/test_run_replay_cli.py`
  -> no matches.
- `python3 scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md`
  -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files docs/operations/AGENTS.md src/AGENTS.md src/engine/AGENTS.md src/engine/replay.py tests/AGENTS.md tests/test_run_replay_cli.py tests/test_replay_time_provenance.py tests/test_digest_profile_matching.py architecture/AGENTS.md architecture/topology.yaml architecture/digest_profiles.py architecture/test_topology.yaml docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md`
  -> pass.
- `git diff --check -- ...` over Phase 1J files -> pass.
- Independent subagent review after closeout -> APPROVE. Reviewer confirmed
  non-audit modes no longer implicitly enable snapshot-only fallback, explicit
  opt-in still works, replay output remains non-promotion, exact topology
  navigation admits Phase 1J wording, and no DB/schema/source-routing/live-venue
  or economics-promotion scope creep was found.

Residual risk:

- Phase 1I removes `obsolete simulated-venue benchmark enum` and
  `BenchmarkEnvironment.SHADOW` public concepts. The underlying legacy storage
  string values remain only as DB provenance labels for compatibility, and
  `EvidenceGrade` is the promotion authority.
- `ECONOMICS` remains tombstoned; Phase 0 only prevents non-economics evidence from authorizing promotion. Phase 5 must still implement promotion-grade economics after market/venue/probability evidence exists.

## Phase 1A Closeout Evidence - 2026-04-29

Scope:

- Close F12 for the live evaluator entry path only: calibration Level 4 / missing Platt model is a hard no-trade before market edge construction, full-family FDR, Kelly sizing, or executable decision creation.
- Not in scope: source policy, LOW Day0/monitor semantics, Paris station source, Gamma child-market filtering, executable snapshot threading, replay parity, or calibration manager threshold definitions.

Design decision:

- `edge_threshold_multiplier(cal_level)` cannot safely be applied to the old effective live threshold because the live edge prefilter is `ci_lower > 0`; multiplying a zero floor is a no-op.
- Phase 1A therefore uses the stricter first-principles repair allowed by the F12 remediation text: Level 4 raw-probability entry is non-tradable until metric-aware Platt calibration exists or a future explicit nonzero edge floor is introduced.

Changed implementation surfaces:

- `src/engine/evaluator.py`: imports `edge_threshold_multiplier`, records `calibration_maturity_level_N` and `calibration_maturity_threshold_Xx` validations, and returns `CALIBRATION_IMMATURE` when `cal is None` or `cal_level >= 4` before VWMP market odds, `MarketAnalysis.find_edges()`, full-family FDR, strategy policy, or Kelly sizing.
- `tests/test_center_buy_repair.py`: converts the existing center-buy/opening-inertia fixture to mature calibration by default and adds `test_level4_raw_probability_entry_blocks_before_edge_selection` proving Level 4 returns no edge and `CALIBRATION_IMMATURE`.
- `tests/test_fdr.py`: keeps FDR-specific evaluator tests on the mature-calibration path so they continue testing FDR rather than calibration readiness.
- `tests/test_decision_evidence_runtime_invocation.py`: keeps the accept-path evidence fixture on mature calibration so it still reaches the `DecisionEvidence(evidence_type="entry")` construction site.
- `architecture/test_topology.yaml`: refreshes trusted-test metadata for touched tests and registers `tests/test_center_buy_repair.py` after adding the required lifecycle header.

Verification run:

- `python3 -m py_compile src/engine/evaluator.py tests/test_fdr.py tests/test_center_buy_repair.py tests/test_decision_evidence_runtime_invocation.py` -> pass.
- `pytest -q tests/test_fdr.py tests/test_center_buy_repair.py tests/test_execution_price.py` -> 46 passed, 1 xfailed.
- `pytest -q tests/test_decision_evidence_runtime_invocation.py tests/test_evaluator_metric_normalizer_failclosed.py tests/test_execution_price.py tests/test_center_buy_repair.py tests/test_fdr.py` -> 56 passed, 1 xfailed.
- `pytest -q tests/test_fdr.py tests/test_center_buy_repair.py tests/test_decision_evidence_runtime_invocation.py tests/test_execution_price.py tests/test_evaluator_metric_normalizer_failclosed.py tests/test_calibration_manager.py` -> 86 passed, 1 xfailed.
- `python3 scripts/semantic_linter.py --check src/engine/evaluator.py tests/test_fdr.py tests/test_center_buy_repair.py tests/test_decision_evidence_runtime_invocation.py architecture/test_topology.yaml` -> pass.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/engine/evaluator.py tests/test_fdr.py tests/test_center_buy_repair.py tests/test_decision_evidence_runtime_invocation.py architecture/test_topology.yaml --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files src/engine/evaluator.py tests/test_fdr.py tests/test_center_buy_repair.py tests/test_decision_evidence_runtime_invocation.py architecture/test_topology.yaml` -> pass.
- `git diff --check -- src/engine/evaluator.py tests/test_fdr.py tests/test_center_buy_repair.py tests/test_decision_evidence_runtime_invocation.py architecture/test_topology.yaml` -> pass.

Pre-existing / out-of-scope topology debt observed during verification:

- `pytest -q tests/test_topology_doctor.py` -> 227 passed, 16 deselected, 1 failed on `reference_replacement_missing_entry` for `docs/reference/zeus_calibration_weighting_authority.md`. This reference doc was not touched in Phase 1A.
- `python3 scripts/topology_doctor.py --tests --json` still fails on the pre-existing unclassified test files already observed after Phase 0: `tests/test_attribution_drift.py`, `tests/test_attribution_drift_weekly.py`, `tests/test_calibration_observation.py`, `tests/test_edge_observation.py`, `tests/test_edge_observation_weekly.py`, `tests/test_inv_prototype.py`, `tests/test_no_synthetic_provenance_marker.py`, `tests/test_settlements_physical_quantity_invariant.py`, `tests/test_ws_poll_reaction.py`, and `tests/test_ws_poll_reaction_weekly.py`.

Residual risk:

- Phase 1A closes live-entry Level 4 raw-probability execution. It does not yet implement a nonzero Level 2/3 edge floor, because no current live base edge threshold exists to multiply without inventing a new economics policy.
- `src/engine/replay.py` still lacks the calibration-maturity hard block, so replay/live parity for this rule is a later Phase 4/5 repair item before promotion-grade economics.

## Phase 1B Closeout Evidence - 2026-04-29

Scope:

- Close F08 for live evaluator snapshot persistence and downstream settlement-learning safety.
- Preserve true Open-Meteo provenance: do not fabricate `issue_time`; use `first_valid_time` only as `valid_time`; make missing issue-time snapshots auditable but not trainable.
- Extend test fixtures touched by Phase 1A so non-calibration tests explicitly run on mature calibration rather than relying on raw Level 4 probability.

Changed implementation surfaces:

- `src/engine/evaluator.py`: `_snapshot_valid_time_value()` falls back to `first_valid_time`; `_store_ens_snapshot()` uses NULL-safe issue/valid-time lookup and returns the inserted snapshot id for Open-Meteo-style snapshots; `evaluate_candidate()` now fails closed with `SIGNAL_QUALITY` before p_raw persistence/calibration/edge selection when no `decision_snapshot_id` is available.
- `src/execution/harvester.py`: `get_snapshot_context()` marks missing-issue snapshots as `snapshot_learning_ready=False`; `_snapshot_contexts_from_rows()` combines settlement-row readiness with snapshot provenance readiness instead of letting `decision_snapshot_id` alone authorize learning; `harvest_settlement()` returns 0 and logs when p_raw is present but `forecast_issue_time` is missing, preventing fabricated decision-group ids.
- `tests/test_runtime_guards.py`: proves Open-Meteo parser keeps `first_valid_time` without faking `issue_time`, snapshot storage links `valid_time` and p_raw in the attached world DB, and non-calibration runtime guard tests use mature calibration fixtures.
- `tests/test_center_buy_repair.py`: adds `test_empty_decision_snapshot_id_blocks_before_edge_selection` proving missing snapshot id blocks before edge construction.
- `tests/test_harvester_metric_identity.py`: adds missing-issue-time regressions proving direct harvest creates no training pairs and snapshot context becomes audit-only.
- `tests/test_pnl_flow_and_audit.py`: updates evaluator fixtures to mature calibration so harvester settlement-learning evidence can run under Phase 1A semantics.
- `architecture/test_topology.yaml`: refreshes trusted-test metadata for touched tests.

Critic / review evidence:

- Code-review subagent verdict on initial F08 evaluator-only patch: `REVISE`. It approved the evaluator direction but identified the downstream harvester learning leak where `issue_time=NULL` snapshots could become training data through `forecast_issue_time or available_at` fallback.
- Read-only explore subagent confirmed the call chain: `run_harvester()` filters `learning_snapshot_ready` contexts, `get_snapshot_context()` returned raw snapshot times without null-quality gate, `_snapshot_contexts_from_rows()` defaulted readiness from `decision_snapshot_id`, and `harvest_settlement()` fabricated missing issue time before writing `training_allowed=True` v2 pairs.
- Phase 1B repair explicitly implements the requested guard: Open-Meteo snapshot id persists and live decisions can remain auditable, but settlement learning does not train from missing `issue_time`.

Verification run:

- `python3 -m py_compile src/execution/harvester.py src/engine/evaluator.py tests/test_harvester_metric_identity.py tests/test_runtime_guards.py tests/test_center_buy_repair.py` -> pass.
- `pytest -q tests/test_harvester_metric_identity.py tests/test_runtime_guards.py tests/test_center_buy_repair.py` -> 131 passed.
- `pytest -q tests/test_center_buy_repair.py tests/test_runtime_guards.py tests/test_fdr.py tests/test_decision_evidence_runtime_invocation.py tests/test_execution_price.py tests/test_evaluator_metric_normalizer_failclosed.py tests/test_calibration_manager.py tests/test_harvester_metric_identity.py tests/test_lifecycle.py` -> 224 passed, 1 xfailed.
- `pytest -q tests/test_pnl_flow_and_audit.py` -> 56 passed, 5 skipped.
- `pytest -q tests/test_center_buy_repair.py tests/test_runtime_guards.py tests/test_fdr.py tests/test_decision_evidence_runtime_invocation.py tests/test_execution_price.py tests/test_evaluator_metric_normalizer_failclosed.py tests/test_calibration_manager.py tests/test_harvester_metric_identity.py tests/test_lifecycle.py tests/test_pnl_flow_and_audit.py` -> 280 passed, 5 skipped, 1 xfailed.
- `python3 scripts/semantic_linter.py --check src/execution/harvester.py src/engine/evaluator.py tests/test_harvester_metric_identity.py tests/test_runtime_guards.py tests/test_center_buy_repair.py tests/test_pnl_flow_and_audit.py tests/test_fdr.py tests/test_decision_evidence_runtime_invocation.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md` -> pass.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/engine/evaluator.py src/execution/harvester.py tests/test_center_buy_repair.py tests/test_runtime_guards.py tests/test_harvester_metric_identity.py tests/test_pnl_flow_and_audit.py tests/test_fdr.py tests/test_decision_evidence_runtime_invocation.py architecture/test_topology.yaml docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files ...` -> pass with advisory warnings that new packet docs still require top-level docs registry companions.
- `git diff --check` -> pass.

Pre-existing / out-of-scope topology debt observed during verification:

- `python3 scripts/topology_doctor.py --tests --json` still fails on unclassified tests outside this phase's implementation scope: `tests/test_attribution_drift.py`, `tests/test_attribution_drift_weekly.py`, `tests/test_calibration_observation.py`, `tests/test_calibration_observation_weekly.py`, `tests/test_edge_observation.py`, `tests/test_edge_observation_weekly.py`, `tests/test_inv_prototype.py`, `tests/test_no_synthetic_provenance_marker.py`, `tests/test_settlements_physical_quantity_invariant.py`, `tests/test_ws_poll_reaction.py`, and `tests/test_ws_poll_reaction_weekly.py`.

Residual risk:

- Phase 1B intentionally permits missing `issue_time` for audit-only live fallback snapshots. That is not canonical training evidence and not a claim that Open-Meteo is the desired primary source.
- The broader DSA-01 source-policy issue remains: Open-Meteo is still the default live ENS fetch path until a later phase introduces a single forecast-source policy and canonical/fallback selection contract.
- Replay/live parity for the new snapshot and learning guards remains a later economics/promotion phase item.

### Phase 1B Reviewer REVISE Remediation - 2026-04-29

Reviewer finding:

- Final code-review subagent returned `REVISE` because updated fixtures used unreachable semantic-linter guards such as `if False: _ = None.selected_method`, which can make AST checks pass without exercising runtime provenance.

Remediation:

- Removed dead-code provenance guards from all touched test files: `tests/test_decision_evidence_runtime_invocation.py`, `tests/test_fdr.py`, `tests/test_center_buy_repair.py`, `tests/test_runtime_guards.py`, and `tests/test_pnl_flow_and_audit.py`.
- Replaced them with executable fixture provenance: fake analyses carry/assert `selected_method`; p_raw fixtures carry `bias_correction`; monitor refresh fixtures carry/assert `entry_method` on positions before reading direction-dependent probabilities.
- Removed same-pattern dead-code guards from touched source files `src/engine/evaluator.py` and `src/execution/harvester.py`. Evaluator now constructs a real entry provenance context before probability evaluation; harvester reads position entry provenance inside settlement P&L flow before p_posterior settlement record construction.

Verification after remediation:

- `rg -n "if False: _ = None\.|None\.selected_method|None\.bias_correction" src/engine/evaluator.py src/execution/harvester.py tests/test_decision_evidence_runtime_invocation.py tests/test_fdr.py tests/test_center_buy_repair.py tests/test_runtime_guards.py tests/test_pnl_flow_and_audit.py` -> no matches.
- `python3 -m py_compile src/execution/harvester.py src/engine/evaluator.py tests/test_decision_evidence_runtime_invocation.py tests/test_fdr.py tests/test_center_buy_repair.py tests/test_runtime_guards.py tests/test_pnl_flow_and_audit.py` -> pass.
- `python3 scripts/semantic_linter.py --check src/execution/harvester.py src/engine/evaluator.py tests/test_harvester_metric_identity.py tests/test_runtime_guards.py tests/test_center_buy_repair.py tests/test_pnl_flow_and_audit.py tests/test_fdr.py tests/test_decision_evidence_runtime_invocation.py` -> pass.
- `pytest -q tests/test_center_buy_repair.py tests/test_runtime_guards.py tests/test_fdr.py tests/test_decision_evidence_runtime_invocation.py tests/test_execution_price.py tests/test_evaluator_metric_normalizer_failclosed.py tests/test_calibration_manager.py tests/test_harvester_metric_identity.py tests/test_lifecycle.py tests/test_pnl_flow_and_audit.py` -> 280 passed, 5 skipped, 1 xfailed.

## Phase 1C Closeout Evidence - 2026-04-29

Scope:

- Close F01 and F02 for the live holding-monitor and LOW Day0 exit-evaluation path.
- Preserve the phase boundary: no source-policy change, no TIGGE/Open-Meteo authority change, no Paris/source config edit, no DB mutation/backfill, no executable snapshot/envelope change, no fill/exposure ledger change, no FDR/NO-token reachability change, and no production live deploy.

Changed implementation surfaces:

- `src/engine/monitor_refresh.py`: `_refresh_ens_member_counting()` now resolves the position metric once, converts it to `MetricIdentity`, passes it into `EnsembleSignal`, and keeps LOW calibrator lookup on the same metric. `_refresh_day0_observation()` now injects `SettlementSemantics` rounding into `Day0SignalInputs` and uses remaining member minima for LOW Day0 bootstrap spread.
- `src/signal/day0_router.py`: the LOW branch now preserves observation source/time, current timestamp, temporal context, settlement round function, and precision instead of dropping those fields before LOW nowcast evaluation.
- `src/signal/day0_low_nowcast_signal.py`: LOW Day0 probability vector now handles open-low/open-high shoulders without `float(None)`, applies injected settlement rounding before binning, exposes forecast context, and uses temporal/observation closure when computing remaining forecast freedom.
- `tests/test_phase6_day0_split.py`: adds LOW open-shoulder, injected rounding, and router context regressions.
- `tests/test_phase9c_gate_f_prep.py`: adds monitor regressions proving HIGH/LOW metric continuity through `EnsembleSignal` and calibrator lookup, plus LOW Day0 monitor use of remaining minima and shoulder bins.
- `architecture/test_topology.yaml`: refreshes trusted-test metadata for the touched test files.
- `architecture/code_idioms.yaml`: removes files that no longer use the legacy unreachable semantic-provenance hook from that idiom registry and marks the hook as legacy-only; new/touched code should use executable same-scope provenance reads.
- `architecture/topology.yaml` and `architecture/docs_registry.yaml`: explicitly register the design-simplification packet as operations packet evidence so closeout map-maintenance can verify real companion movement rather than relying only on generic parent coverage.
- `docs/AGENTS.md`, `docs/README.md`, `docs/operations/AGENTS.md`, and `docs/operations/current_state.md`: register this packet as first-principles audit plus phased repair evidence while preserving the no-live-deploy/no-DB/no-source-routing/no-calibration-authority boundary.

Critic / review evidence:

- Explore subagent `Darwin` mapped F01 to `_refresh_ens_member_counting()` constructing `EnsembleSignal` without `temperature_metric` even though later calibrator lookup had parsed the position metric.
- Explore subagent `Nash` mapped F02 to the LOW `Day0Router` branch dropping settlement rounding and temporal context, while `Day0LowNowcastSignal.p_vector()` assumed finite numeric bin bounds.
- Critic subagent `Parfit` returned `REVISE` before implementation, requiring sharper tests for HIGH paired behavior, LOW monitor metric continuity, open shoulders, settlement rounding, context propagation, and LOW Day0 minima.
- Verifier subagent `Heisenberg` returned `REVISE` after the code patch because closeout still failed on map-maintenance companions and stale `SEMANTIC_PROVENANCE_GUARD` examples. The remediation updated docs packet registration, changed the packet from read-only-audit-only to phased-repair evidence wording, and reconciled the idiom registry with files that still contain the legacy hook.
- Verifier subagent `Heisenberg` returned a second `REVISE` because unchanged `architecture/topology.yaml` and `architecture/docs_registry.yaml` companions were insufficient under git-status-derived closeout. The remediation added explicit packet entries to both architecture registries and reran closeout with those files as actual diffs.

Verification run:

- `python3 -m py_compile src/engine/monitor_refresh.py src/signal/day0_router.py src/signal/day0_low_nowcast_signal.py tests/test_phase6_day0_split.py tests/test_phase9c_gate_f_prep.py` -> pass.
- `pytest -q tests/test_phase6_day0_split.py tests/test_phase9c_gate_f_prep.py tests/test_position_metric_resolver.py tests/test_ensemble_signal.py tests/test_runtime_guards.py::test_day0_observation_path_reaches_day0_signal tests/test_runtime_guards.py::test_day0_observation_path_rejects_missing_solar_context` -> 84 passed.
- `python3 scripts/semantic_linter.py --check src/engine/monitor_refresh.py src/signal/day0_router.py src/signal/day0_low_nowcast_signal.py tests/test_phase6_day0_split.py tests/test_phase9c_gate_f_prep.py architecture/test_topology.yaml architecture/code_idioms.yaml architecture/topology.yaml architecture/docs_registry.yaml docs/AGENTS.md docs/README.md docs/operations/AGENTS.md docs/operations/current_state.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> pass.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/engine/monitor_refresh.py src/signal/day0_router.py src/signal/day0_low_nowcast_signal.py tests/test_phase6_day0_split.py tests/test_phase9c_gate_f_prep.py architecture/test_topology.yaml architecture/code_idioms.yaml architecture/topology.yaml architecture/docs_registry.yaml docs/AGENTS.md docs/README.md docs/operations/AGENTS.md docs/operations/current_state.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/evidence.md --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files src/engine/monitor_refresh.py src/signal/day0_router.py src/signal/day0_low_nowcast_signal.py tests/test_phase6_day0_split.py tests/test_phase9c_gate_f_prep.py architecture/test_topology.yaml architecture/code_idioms.yaml architecture/topology.yaml architecture/docs_registry.yaml docs/AGENTS.md docs/README.md docs/operations/AGENTS.md docs/operations/current_state.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> pass.
- `python3 scripts/topology_doctor.py --idioms --json` -> pass with `ok=true` and no issues.
- `git diff --check -- src/engine/monitor_refresh.py src/signal/day0_router.py src/signal/day0_low_nowcast_signal.py tests/test_phase6_day0_split.py tests/test_phase9c_gate_f_prep.py architecture/test_topology.yaml architecture/code_idioms.yaml architecture/topology.yaml architecture/docs_registry.yaml docs/AGENTS.md docs/README.md docs/operations/AGENTS.md docs/operations/current_state.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> pass.

Pre-existing / out-of-scope topology debt observed during verification:

- `python3 scripts/topology_doctor.py --docs --json` still fails on broad pre-existing docs mesh debt outside this phase, including unregistered archived operations docs, `docs/methodology`, existing generic `plan.md` archive names, and missing current-state archive targets. No DSA packet paths appeared in the failure set after explicit registration.

Residual risk:

- F01/F02 are closed only for the scoped holding monitor and LOW Day0 signal path. The broader source-policy issue remains: live ENS still defaults to Open-Meteo until a later phase introduces one forecast-source policy and canonical/fallback selection contract.
- Phase 1C does not prove executable market identity, submit envelope correctness, partial-fill exposure truth, multi-bin NO reachability, or promotion-grade economics. Those remain Phase 2 through Phase 5 work.
- Legacy unreachable semantic-provenance hooks remain in untouched files that still register the idiom. This phase intentionally avoids sweeping those unrelated files; future touched files should replace the hook with executable provenance reads.

## Phase 2A Closeout Evidence - 2026-04-29

Scope:

- Implement the narrow entry-path executable market identity slice approved by the Phase 2 critic.
- Cover F03/F04/F05/F09 for entry only: scanner child tradability, scan-authority fail-closed, executable snapshot field threading, live missing-identity block, and snapshot-derived submit envelope binding.
- Explicitly out of scope: production Gamma+CLOB snapshot capture writer, DB backfill/migration, exit/cancel/redeem capability unification, fill ledger/partial-fill work, source-provider policy, and promotion-grade economics.

Changed implementation surfaces:

- `src/data/market_scanner.py`: keyword-search fallback now marks scan authority as `EMPTY_FALLBACK`; `_extract_outcomes()` skips child markets with explicit `closed=true`, `active=false`, `acceptingOrders=false`, or `enableOrderBook=false` while preserving legacy payloads with missing flags.
- `src/engine/cycle_runner.py`: runtime dependency surface now exposes `get_last_scan_authority` to `cycle_runtime`.
- `src/engine/cycle_runtime.py`: entry discovery records `market_scan_authority`, fails closed on `STALE`/`EMPTY_FALLBACK` before evaluator signal creation, records availability/no-trade evidence, extracts executable snapshot fields from decision tokens, blocks live entry intent creation when identity fields are missing, and passes snapshot id/tick/min-order/neg-risk into `create_execution_intent()`.
- `src/engine/evaluator.py`: outcome token payloads preserve executable snapshot id/tick/min-order/neg-risk aliases when scanner or future snapshot-capture code supplies them.
- `src/execution/executor.py`: `_live_order()` now builds the snapshot-derived pre-submit envelope once, persists it before SDK contact, and binds that envelope to the subsequent live submit call.
- `src/data/polymarket_client.py`: `bind_submission_envelope()` makes the next `place_limit_order()` submit through `PolymarketV2Adapter.submit(envelope)` instead of the compatibility `submit_limit_order()` envelope path; submit-shape mismatch rejects before adapter contact.
- `tests/test_k1_slice_d.py`, `tests/test_market_scanner_provenance.py`, `tests/test_runtime_guards.py`, `tests/test_v2_adapter.py`, and `tests/test_executor_command_split.py`: added regressions for child-market filtering, keyword fallback authority, stale scan fail-closed, missing live executable identity block, executable field threading, bound-envelope submit, mismatch rejection, and executor envelope binding before SDK submit.
- `architecture/test_topology.yaml`: refreshed trusted-test metadata and registered `tests/test_k1_slice_d.py` after adding its lifecycle header.

Critic / review evidence:

- Phase 2 critic returned `REVISE` on a broad Phase 2 and approved only the narrower Phase 2A entry-path slice.
- The critic required discovery/tradability repair before intent threading, candidate-to-intent executable identity checks before submit-envelope work, and called out the same-DB snapshot persistence stop condition.
- Phase 2A honors that stop condition: it does not pretend a snapshot id is enough when no production writer inserts `ExecutableMarketSnapshotV2` rows into the trade DB. Live entry now blocks before intent if executable identity is missing; if identity exists and the snapshot gate passes, the actual submit call uses the bound snapshot-derived envelope rather than compatibility placeholders.

Verification run:

- `python3 -m py_compile src/data/market_scanner.py src/engine/cycle_runner.py src/engine/cycle_runtime.py src/engine/evaluator.py src/data/polymarket_client.py src/execution/executor.py tests/test_k1_slice_d.py tests/test_market_scanner_provenance.py tests/test_runtime_guards.py tests/test_v2_adapter.py tests/test_executor_command_split.py` -> pass.
- `pytest -q tests/test_k1_slice_d.py tests/test_market_scanner_provenance.py tests/test_v2_adapter.py::test_polymarket_client_bound_envelope_bypasses_legacy_compat_submit tests/test_v2_adapter.py::test_polymarket_client_bound_envelope_rejects_submit_shape_mismatch tests/test_runtime_guards.py::test_discovery_phase_blocks_stale_market_scan_before_evaluator tests/test_runtime_guards.py::test_live_entry_requires_executable_market_identity_before_intent tests/test_runtime_guards.py::test_entry_intent_receives_executable_snapshot_fields tests/test_executor_command_split.py::TestLiveOrderCommandSplit::test_persist_precedes_submit` -> 25 passed.
- `pytest -q tests/test_v2_adapter.py tests/test_executor_command_split.py tests/test_executable_market_snapshot_v2.py tests/test_executor.py` -> 67 passed, 1 skipped.
- `pytest -q tests/test_runtime_guards.py::test_execute_discovery_phase_logs_rejected_live_entry_telemetry tests/test_runtime_guards.py::test_discovery_phase_blocks_stale_market_scan_before_evaluator tests/test_runtime_guards.py::test_live_entry_requires_executable_market_identity_before_intent tests/test_runtime_guards.py::test_entry_intent_receives_executable_snapshot_fields tests/test_market_scanner_provenance.py tests/test_k1_slice_d.py` -> 23 passed.
- `pytest -q tests/test_v2_adapter.py tests/test_executor_command_split.py tests/test_executable_market_snapshot_v2.py tests/test_executor.py tests/test_market_scanner_provenance.py tests/test_k1_slice_d.py` -> 86 passed, 1 skipped.
- `pytest -q tests/test_runtime_guards.py` -> 124 passed.

Reviewer REVISE remediation:

- Code-review subagent returned `REVISE` because `_boolish_market_field()` stopped on the first alias when that alias was `None` or unparsable, allowing later explicit non-tradable aliases such as `isClosed=true` or `accepting_orders=false` to be ignored.
- Remediation changed `_boolish_market_field()` to continue across null/unparsable aliases until it finds an explicit boolean value, while preserving missing-field compatibility.
- Added regression children for `closed=None/isClosed=true`, `active=None/isActive=false`, `acceptingOrders=None/accepting_orders=false`, and `enableOrderBook=None/orderbookEnabled=false`.
- Code-review subagent also found that keyword fallback authority degraded only when fallback returned events. Empty fallback attempts could leave the previous tag-scan status as `VERIFIED`.
- Remediation marks `EMPTY_FALLBACK` before every keyword fallback attempt, including empty results, and adds runtime coverage proving `EMPTY_FALLBACK` blocks before evaluator signal creation.

Reviewer remediation verification:

- `python3 -m py_compile src/data/market_scanner.py tests/test_k1_slice_d.py tests/test_market_scanner_provenance.py tests/test_runtime_guards.py` -> pass.
- `pytest -q tests/test_k1_slice_d.py::test_extract_outcomes_filters_untradable_gamma_children tests/test_market_scanner_provenance.py::TestB017MarketSnapshotProvenance::test_keyword_fallback_marks_scan_authority_degraded tests/test_market_scanner_provenance.py::TestB017MarketSnapshotProvenance::test_empty_keyword_fallback_marks_scan_authority_degraded tests/test_runtime_guards.py::test_discovery_phase_blocks_empty_fallback_market_scan_before_evaluator tests/test_runtime_guards.py::test_discovery_phase_blocks_stale_market_scan_before_evaluator` -> 5 passed.
- `pytest -q tests/test_v2_adapter.py tests/test_executor_command_split.py tests/test_executable_market_snapshot_v2.py tests/test_executor.py tests/test_market_scanner_provenance.py tests/test_k1_slice_d.py` -> 87 passed, 1 skipped.
- `pytest -q tests/test_runtime_guards.py` -> 125 passed.

Residual risk:

- Phase 2A blocks unsafe live entries without executable identity and closes the entry submit-envelope seam when identity exists, but it does not yet create production executable identity. `rg "insert_snapshot\\(|ExecutableMarketSnapshotV2\\(" src scripts -g '*.py'` still finds only `src/state/snapshot_repo.py`, so Phase 2B must add a forward snapshot capture/persistence path from Gamma+CLOB facts into the trade DB.
- `PolymarketClient.place_limit_order()` remains as a compatibility wrapper for call-surface stability. Entry live submit binds a snapshot-derived envelope before calling it; unbound calls still use the compatibility path and should be retired or confined in a later slice.
- Exit, cancel, redeem, and composed `ExecutionCapability` proof remain Phase 2B/2C or Phase 8 work.

## Phase 2B Closeout Evidence - 2026-04-29

Scope:

- Close the entry-only producer gap left by Phase 2A: forward capture of executable market identity from verified Gamma child facts plus fresh CLOB facts into the trade DB before live entry commands are allowed to submit.
- Not in scope: exit/cancel/redeem producers, U2 venue trade projections, production DB mutation, live cutover, source-provider policy, or composed capability proof.

Design decision:

- Snapshot capture is post-decision but pre-intent. This is intentional: before edge selection the selected YES/NO token is not known, and the current snapshot schema stores one selected orderbook. Capturing after selection prevents a `buy_no` order from being authorized by a YES-token orderbook hash.
- The producer does not infer executable facts. Missing tick size, min order size, neg-risk, fee-rate, CLOB market info, orderbook, top bid/ask, or tradability facts fail closed before intent creation.
- BUY entry limit prices are aligned down to the snapshot tick before command insertion. This avoids introducing overpaying price movement while satisfying `assert_snapshot_executable()` tick alignment.

Changed implementation surfaces:

- `src/data/market_scanner.py`: preserves Gamma child identity/tradability/raw payload fields in outcome rows and adds `capture_executable_market_snapshot()` plus CLOB/Gamma consistency checks.
- `src/data/polymarket_client.py`: adds raw `get_orderbook_snapshot()` and `get_clob_market_info()` fetchers, and updates `get_fee_rate()` to accept the current official `base_fee` response shape.
- `src/engine/cycle_runner.py`: exposes `capture_executable_market_snapshot` through the runtime dependency surface.
- `src/engine/cycle_runtime.py`: captures executable snapshot fields after edge selection and before intent creation; capture failure becomes structured no-trade; successful live entry commits the snapshot before calling `execute_intent()` without passing the cycle connection.
- `src/execution/executor.py`: aligns entry limit price to `executable_snapshot_min_tick_size` before constructing `ExecutionIntent`.
- `tests/test_executable_market_snapshot_v2.py`: adds producer happy path, `buy_no` selected-token orderbook path, documented CLOB token-map shapes, missing token-proof failure, non-VERIFIED authority failure, missing CLOB facts, and Gamma/CLOB inconsistency regressions.
- `tests/test_runtime_guards.py`: adds live discovery integration tests proving capture failure blocks before intent and happy-path capture commits a snapshot, threads fields into intent, and does not pass the cycle connection into executor.
- `tests/test_executor_command_split.py`: adds an own-connection live entry test proving command, envelope, and `SUBMIT_REQUESTED` rows are visible from a second DB connection before fake SDK submit is invoked.
- `tests/test_k1_slice_d.py`: asserts Gamma outcome extraction now preserves executable identity fields and raw payload hash.
- `tests/test_v2_adapter.py`: adds fee-rate parser regressions for current `base_fee` shape and malformed fee response.
- `architecture/test_topology.yaml`: refreshes trusted-test metadata for `tests/test_executable_market_snapshot_v2.py`.

Critic feedback handled:

- Topology/process: reran navigation after scope amendment; current Phase 2B navigation returns `navigation ok: True` under profile `r3 executable market snapshot v2 implementation`.
- Raw Gamma facts: `_extract_outcomes()` now carries condition/question ids, Gamma id, active/closed/accepting/orderbook flags, token map, raw payload hash, and raw child payload.
- CLOB fact contract: producer fetches market info, selected-token orderbook, fee-rate, tick size, min order size, neg-risk, top bid/ask, and rejects inconsistent condition/token facts.
- Durable boundary: runtime commits the captured snapshot before `execute_intent()`; the executor own-connection test proves command/envelope/`SUBMIT_REQUESTED` rows are committed before SDK contact.
- Tick alignment: entry intent limit price is aligned to the executable snapshot tick, and the integration test proves command-gate price alignment.

Verification run:

- `python3 -m py_compile src/data/market_scanner.py src/data/polymarket_client.py src/engine/cycle_runner.py src/engine/cycle_runtime.py src/execution/executor.py tests/test_executable_market_snapshot_v2.py tests/test_runtime_guards.py tests/test_k1_slice_d.py tests/test_v2_adapter.py` -> pass.
- Focused Phase 2B subset (`tests/test_executable_market_snapshot_v2.py`, targeted fee parser, Gamma extraction, runtime producer tests, and own-connection durability test) -> 34 passed.
- `pytest -q tests/test_executable_market_snapshot_v2.py tests/test_v2_adapter.py tests/test_k1_slice_d.py tests/test_market_scanner_provenance.py tests/test_runtime_guards.py` -> 205 passed.
- `pytest -q tests/test_executor_command_split.py tests/test_executor.py tests/test_live_execution.py tests/test_risk_allocator.py tests/test_executor_db_target.py tests/test_discovery_idempotency.py` -> 67 passed, 2 skipped, 1 xpassed.
- `python3 scripts/semantic_linter.py --check ...` on changed source/test files -> pass.
- `python3 scripts/digest_profiles_export.py --check` -> OK.
- `python3 scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> topology check ok.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout ...` including docs companion files -> topology check ok.
- `git diff --check -- ...` on tracked changed Phase 2B source/test/manifest files -> pass.

Official external reference checked:

- Polymarket fee-rate API currently documents `GET /fee-rate` returning `base_fee` in basis points. Source: https://docs.polymarket.com/api-reference/market-data/get-fee-rate.
- Polymarket public client docs describe CLOB market info/orderbook/fee-rate public methods as the current source for tick/order/fee market facts. Source: https://docs.polymarket.com/trading/clients/public.

Residual risk:

- This is entry-only. Exit/cancel/redeem still require their own persisted executable proof/capability path before those side effects can claim the same closure.
- The producer depends on current CLOB public response fields for tick size, min order size, and neg-risk. If Polymarket changes these field names again, the producer fails closed rather than guessing.
- Phase 5 economics remains tombstoned until forward market/venue/probability facts are populated and promotion-grade parity exists.
- `python3 scripts/topology_doctor.py --tests --json` -> known pre-existing failure on unclassified tests outside Phase 2B changed files: `tests/test_attribution_drift.py`, `tests/test_attribution_drift_weekly.py`, `tests/test_calibration_observation.py`, `tests/test_calibration_observation_weekly.py`, `tests/test_edge_observation.py`, `tests/test_edge_observation_weekly.py`, `tests/test_inv_prototype.py`, `tests/test_learning_loop_observation.py`, `tests/test_learning_loop_observation_weekly.py`, `tests/test_no_synthetic_provenance_marker.py`, `tests/test_settlements_physical_quantity_invariant.py`, `tests/test_ws_poll_reaction.py`, and `tests/test_ws_poll_reaction_weekly.py`.

Phase 2B verifier REVISE remediation:

- Added `tests/test_executable_market_snapshot_v2.py::test_capture_executable_snapshot_uses_market_fact_methods_only` to prove the Phase 2B producer calls only market-fact read methods (`get_clob_market_info`, `get_orderbook_snapshot`, `get_fee_rate`) and would fail if it touched cancel, redeem, live submit, or V2 preflight/cutover methods.
- Clarified boundary: the U2 `VenueSubmissionEnvelope` binding/persistence visible in the cumulative diff is Phase 2A entry-submit seam work for F04. Phase 2B does not add exit/cancel/redeem/U2 trade fact projections; it reuses the existing entry envelope/command gate only to prove the newly captured snapshot is durable before the executor performs live submit.
- Post-remediation focused producer/runtime group: `pytest -q tests/test_executable_market_snapshot_v2.py tests/test_v2_adapter.py tests/test_k1_slice_d.py tests/test_market_scanner_provenance.py tests/test_runtime_guards.py` -> 205 passed.
- Post-remediation adjacent executor/command/risk group: `pytest -q tests/test_executor_command_split.py tests/test_executor.py tests/test_live_execution.py tests/test_risk_allocator.py tests/test_executor_db_target.py tests/test_discovery_idempotency.py` -> 67 passed, 2 skipped, 1 xpassed.

Independent review:

- Code-reviewer follow-up on the Phase 2B remediation -> APPROVE, 0 concrete issues. It verified no cycle `conn` is passed to `execute_intent`, capture uses current UTC and commits before executor handoff, CLOB token proof is mandatory, and tests prove own-connection pre-submit durability.
- Verifier follow-up on the Phase 2B remediation -> APPROVE. Non-blocking residual risk: there is no mocked-clock equality assertion for the exact persisted capture timestamp; current evidence proves the runtime path uses current UTC and the integration test rejects reuse of the cycle `decision_time`.

## Phase 3 Closeout Evidence - 2026-04-29

Scope:

- Close F06/F11 and the fill-tracker slice of DSA-15 for the legacy pending-entry polling path.
- Not in scope: production DB mutation/backfill, schema migration, live venue side effects, M5 exchange-reconcile policy changes, exit/cancel/redeem capability unification, or full chain-to-settlement projection closure.

Changed implementation surfaces:

- `architecture/topology.yaml`: adds the dedicated `r3 fill finality ledger implementation` digest profile, realistic Phase 3 wording, and negative phrases to the raw U2 schema profile so fill-finality work does not misroute to schema/heartbeat surfaces.
- `architecture/digest_profiles.py`: regenerated from canonical topology YAML.
- `architecture/test_topology.yaml`: refreshed trusted-test metadata for tests touched/reused in this phase.
- `src/execution/fill_tracker.py`: removes `MATCHED` from effective final-fill status, adds optimistic/partial branches, writes linkable U2 order/trade/lot facts before local position mutation when payload identity permits, resolves executor runtime ids through `trade_decisions.runtime_trade_id` before lot projection even when the runtime id is numeric-looking, and preserves partial filled exposure across cancel-remainder timeout.
- `tests/test_live_safety_invariants.py`: adds regressions for `MATCHED` without filled size staying pending, `MATCHED` with live-shaped numeric-looking runtime id mapping to an optimistic lot plus no calibration truth, and partial timeout cancel preserving filled shares.
- `tests/test_command_recovery.py`: replaces a stale peer-worktree absolute path with the current repo path so the Phase 3 command-recovery gate can run in this workspace.
- `tests/test_digest_profile_matching.py`: adds Phase 3 routing regressions proving exact R3/U2 wording and realistic reviewer wording route to the new profile, not the raw schema or broader live-readiness profiles.

Verification run:

- `python3 -m py_compile src/execution/fill_tracker.py tests/test_live_safety_invariants.py tests/test_command_recovery.py tests/test_digest_profile_matching.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_live_safety_invariants.py::test_legacy_polling_matched_maps_numeric_live_runtime_id_to_optimistic_lot tests/test_live_safety_invariants.py::test_matched_without_filled_size_does_not_materialize_entry tests/test_live_safety_invariants.py::test_partial_remainder_cancel_preserves_filled_exposure` -> 3 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_u2_fill_finality_routes_to_finality_profile_not_schema tests/test_digest_profile_matching.py::test_phase3_fill_finality_realistic_wording_routes_to_finality_profile` -> 2 passed.
- Focused fill finality regressions plus existing fill-tracker safety checks -> 7 passed.
- `pytest -q -p no:cacheprovider tests/test_live_safety_invariants.py tests/test_runtime_guards.py` -> 186 passed, 3 skipped.
- `pytest -q -p no:cacheprovider tests/test_user_channel_ingest.py tests/test_provenance_5_projections.py tests/test_command_recovery.py tests/test_exchange_reconcile.py` -> 59 passed, 22 warnings.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py tests/test_digest_profile_matching.py::test_phase3_fill_finality_realistic_wording_routes_to_finality_profile` -> 36 passed.
- `python3 scripts/semantic_linter.py --check src/execution/fill_tracker.py tests/test_live_safety_invariants.py tests/test_digest_profile_matching.py architecture/topology.yaml architecture/test_topology.yaml` -> pass.
- `python3 scripts/topology_doctor.py --navigation --task "R3 U2 fill finality closure legacy fill polling MATCHED CONFIRMED partial fill materialization venue_trade_facts position_lots" --files src/execution/fill_tracker.py tests/test_live_safety_invariants.py tests/test_runtime_guards.py tests/test_user_channel_ingest.py tests/test_provenance_5_projections.py tests/test_command_recovery.py tests/test_exchange_reconcile.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> navigation ok under `r3 fill finality ledger implementation`.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 3 fill finality / exposure ledger slice legacy polling partial cancel command events lots" --files src/execution/fill_tracker.py tests/test_live_safety_invariants.py tests/test_digest_profile_matching.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> navigation ok under `r3 fill finality ledger implementation`.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files architecture/topology.yaml architecture/digest_profiles.py architecture/test_topology.yaml src/execution/fill_tracker.py tests/test_live_safety_invariants.py tests/test_command_recovery.py tests/test_digest_profile_matching.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files ...` including packet companion registry files -> pass.
- `git diff --check -- ...` on Phase 3 changed source/test/manifest/docs files -> pass.
- `python3 scripts/topology_doctor.py --tests --json` -> known pre-existing failure on unclassified tests outside Phase 3 changed files: `tests/test_attribution_drift.py`, `tests/test_attribution_drift_weekly.py`, `tests/test_calibration_observation.py`, `tests/test_calibration_observation_weekly.py`, `tests/test_edge_observation.py`, `tests/test_edge_observation_weekly.py`, `tests/test_inv_prototype.py`, `tests/test_learning_loop_observation.py`, `tests/test_learning_loop_observation_weekly.py`, `tests/test_no_synthetic_provenance_marker.py`, `tests/test_settlements_physical_quantity_invariant.py`, `tests/test_ws_poll_reaction.py`, and `tests/test_ws_poll_reaction_weekly.py`.

Residual risk:

- Legacy polling does not invent `venue_trade_facts` when a CLOB order-status payload lacks explicit trade id. It appends order facts when a venue command is linkable and leaves trade finality to WS user-channel, exchange reconciliation, or later chain evidence.
- `position_lots.position_id` remains integer-keyed. Legacy polling now resolves normal executor runtime ids, including numeric-looking UUID prefixes, through `trade_decisions.runtime_trade_id`; if that materialization row is absent when the venue fact arrives, the slice refuses to synthesize a lot id and leaves the fact for WS/exchange-reconcile identity closure.
- Exit-side `exit_lifecycle.py` still has its own fill-status semantics and is not changed in this entry-polling slice.

Independent review:

- Code-reviewer initial pass returned BLOCK on two issues: nonnumeric executor runtime ids did not project lots, and realistic Phase 3 wording routed to the broader live-readiness profile.
- First remediation added `trade_decisions.runtime_trade_id` mapping and realistic topology wording. Code-reviewer then found a second HIGH edge case: an executor UUID prefix can be all digits and must not bypass runtime-id mapping by numeric parse.
- Second remediation made `_position_id_from_command()` resolve command identity fields as runtime aliases before any numeric fallback, and accepts direct numeric compatibility only after checking the target `trade_decisions` row.
- Code-reviewer follow-up after the second remediation -> APPROVE. Its failed numeric probe now writes `position_lots.position_id=1` for `runtime_trade_id="123456789012"` and writes no lot when the materialization row is missing, matching the documented residual.
- Verifier follow-up -> APPROVE. It independently checked the focused finality tests, the numeric-looking runtime-id behavior, and the realistic Phase 3 routing test.

## Phase 4A Closeout Evidence - 2026-04-29

Scope:

- Close F13 using the fail-closed branch: full-family FDR may only test/select hypotheses that `MarketAnalysis.find_edges()` can materialize under the same executable side semantics.
- Reconfirm F12 live-entry closure from Phase 1A: Level 4 / missing Platt calibration hard-blocks before market edge construction, full-family FDR, policy, Kelly sizing, or executable decision creation.
- Not in scope: native multi-bin NO-token VWMP/orderbook producer, live venue side effects, Kelly formula changes, feature-flag deletion without canary receipts, replay/economics parity, or promotion-grade economics.

Design decision:

- Multi-bin weather events do have native NO tokens, but the current `MarketAnalysis` object receives YES-side market probability/VWMP only. Local `1 - YES` math is not a native NO-token executable price, so it must not enter the full-family FDR hypothesis set.
- Binary markets keep `buy_no` reachability because the local binary complement remains aligned with the available executable economics at this layer.
- This chooses the conservative fail-closed repair from F13 instead of widening Phase 4 into a CLOB/market-identity producer for native NO-token orderbook capture.

Changed implementation surfaces:

- `architecture/topology.yaml`: adds the dedicated `r3 strategy reachability selection parity implementation` profile so Phase 4 strategy/FDR/evaluator work does not route through the executable snapshot profile.
- `architecture/digest_profiles.py`: regenerated from canonical topology YAML.
- `src/strategy/market_analysis.py`: adds `supports_buy_no_edges()` and routes `find_edges()` through it, preserving binary NO while blocking synthetic multi-bin NO.
- `src/strategy/market_analysis_family_scan.py`: uses the same reachability predicate before creating `buy_no` `FullFamilyHypothesis` rows, so FDR cannot select a side that has no executable `BinEdge` path.
- `tests/test_fdr.py`: adds regressions proving multi-bin NO does not call `_bootstrap_bin_no()` or enter the family, binary NO still enters, and the evaluator materializes only executable hypotheses for a 4-bin Day0 family.
- `tests/test_digest_profile_matching.py`: adds Phase 4 routing regression for realistic strategy reachability wording.
- `docs/operations/task_2026-04-29_design_simplification_audit/findings.md` and `simplification_plan.md`: record F13 Phase 4A status and the residual Phase 4/5 boundaries.

Verification run:

- `python3 scripts/digest_profiles_export.py` -> regenerated 36 profiles.
- `python3 -m py_compile src/strategy/market_analysis.py src/strategy/market_analysis_family_scan.py tests/test_fdr.py tests/test_digest_profile_matching.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_phase4_strategy_reachability_routes_to_selection_parity_profile tests/test_fdr.py::TestSelectionFamilySubstrate::test_multi_bin_full_family_scan_excludes_unexecutable_buy_no` -> 2 passed.
- `pytest -q -p no:cacheprovider tests/test_fdr.py::TestSelectionFamilySubstrate::test_multi_bin_full_family_scan_excludes_unexecutable_buy_no tests/test_fdr.py::TestSelectionFamilySubstrate::test_binary_full_family_scan_keeps_executable_buy_no tests/test_fdr.py tests/test_center_buy_repair.py` -> 25 passed.
- `pytest -q -p no:cacheprovider tests/test_runtime_guards.py` -> 127 passed.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 4 strategy reachability selection sizing parity full-family FDR multi-bin buy_no executable BinEdge calibration maturity feature flags" --files src/strategy/market_analysis.py src/strategy/market_analysis_family_scan.py src/engine/evaluator.py tests/test_fdr.py tests/test_center_buy_repair.py tests/test_strategy_benchmark.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> navigation ok under `r3 strategy reachability selection parity implementation`.

Residual risk:

- Native multi-bin NO trading is still unavailable by design until a producer captures selected NO-token CLOB orderbook/VWMP, fee, tick, min-order, and token identity facts. This slice prevents false selection; it does not add that new trading capability.
- Feature-flagged settlement/exit semantics remain staged migration risk (DSA-11). This plan requires canary receipts before deleting alternate money-semantics paths.
- Promotion-grade economics remains tombstoned (DSA-19). Phase 4A closes selection reachability, but Phase 5 must still prove full market-price linkage, fill/slippage, fee/tick/neg-risk, confirmed venue facts, and live/replay parity before Zeus can claim remaining failure is only math/physics.

Phase 4A reviewer REVISE remediation:

- Code-reviewer found a topology false-negative for realistic wording: `Phase 4A strategy reachability full-family FDR parity close F13 fail-close multi-bin buy_no` routed away from the Phase 4 profile.
- Remediation added `Phase 4A`, `F13`, `full-family FDR parity`, `close F13`, and `fail-close multi-bin buy_no` phrases to `r3 strategy reachability selection parity implementation` and added `tests/test_digest_profile_matching.py::test_phase4a_f13_realistic_wording_routes_to_selection_parity_profile`.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 4A strategy reachability full-family FDR parity close F13 fail-close multi-bin buy_no" --files src/strategy/market_analysis.py src/strategy/market_analysis_family_scan.py tests/test_fdr.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> navigation ok under `r3 strategy reachability selection parity implementation`.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_phase4_strategy_reachability_routes_to_selection_parity_profile tests/test_digest_profile_matching.py::test_phase4a_f13_realistic_wording_routes_to_selection_parity_profile` -> 2 passed.

Independent review:

- Verifier follow-up -> APPROVE. It independently verified Phase 4 routing, the multi-bin NO exclusion regression, binary NO retention, executable-family selection fact counts, and docs boundaries that keep promotion-grade economics deferred to Phase 5.
- Code-reviewer initial pass -> REVISE on topology false-negative for realistic Phase 4A/F13 wording. No live-money code blocker was found in the strategy changes.
- Code-reviewer follow-up after remediation -> APPROVE. It verified the exact previously failing wording now routes to `r3 strategy reachability selection parity implementation`, the new digest regression admits strategy/FDR files, and docs do not claim native multi-bin NO trading or promotion-grade economics closure.

## Phase 5A Closeout Evidence - 2026-04-29

Scope:

- Convert the DSA-19 economics tombstone from a generic refusal into a structured, read-only readiness contract.
- Preserve the hard boundary: Phase 5A does not implement PnL, staged live, promotion-grade economics, capital scale-up, production DB mutation, live venue side effects, or strategy promotion.

Design decision:

- A database with table names and rows is not enough to prove economic alpha. Readiness therefore reports blockers by substrate dimension and always keeps `run_economics()` tombstoned in this slice.
- The readiness contract explicitly requires forward market/price/fill/probability/selection/outcome substrate: `market_events_v2`, `market_price_history`, `executable_market_snapshots`, `venue_trade_facts`, `position_lots`, `probability_trace_fact`, `trade_decisions`, `selection_family_fact`, `selection_hypothesis_fact`, `settlements_v2`, and `outcome_fact`.
- Even when a fixture supplies minimum rows for all those tables, readiness still reports `economics_engine_not_implemented`. This prevents table-count presence from authorizing promotion-grade economics.

Changed implementation surfaces:

- `architecture/topology.yaml`: adds `phase 5 promotion grade economics readiness implementation`, with explicit no-go boundaries for fake PnL, live side effects, production DB mutation, live promotion, schema migration, Kelly/FDR/calibration/lifecycle changes, and replay/simulated evidence promotion.
- `architecture/digest_profiles.py`: regenerated from canonical topology YAML.
- `src/backtest/economics.py`: adds `EconomicsReadiness`, `check_economics_readiness(conn)`, table/count/blocker reporting, confirmed-trade and confirmed-lot checks, executable snapshot fee/tick/orderbook fact checks, decision/probability snapshot-linkage checks, selected-FDR and outcome checks, and keeps `run_economics()` fail-closed.
- `tests/test_backtest_skill_economics.py`: adds regressions for missing connection, missing/empty substrate, non-CONFIRMED trade facts, and full fixture substrate still failing because the economics engine is not implemented.
- `tests/test_digest_profile_matching.py`: adds Phase 5 routing regression so DSA-19/economics wording admits backtest/strategy benchmark surfaces instead of routing to executable snapshot work.
- `docs/operations/task_2026-04-29_design_simplification_audit/findings.md` and `simplification_plan.md`: record that DSA-19 remains a blocker and Phase 5A is readiness only.

Verification run:

- `python3 scripts/digest_profiles_export.py` -> regenerated 37 profiles.
- `python3 -m py_compile src/backtest/economics.py tests/test_backtest_skill_economics.py tests/test_digest_profile_matching.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_backtest_skill_economics.py tests/test_backtest_purpose_contract.py tests/test_backtest_training_eligibility.py` -> 39 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_phase5_economics_readiness_routes_to_phase5_profile tests/test_strategy_benchmark.py` -> 12 passed.
- `pytest -q -p no:cacheprovider tests/test_backtest_purpose_contract.py tests/test_backtest_skill_economics.py tests/test_backtest_training_eligibility.py tests/test_backtest_trade_subject_identity.py tests/test_backtest_settlement_value_outcome.py tests/test_backtest_outcome_comparison.py tests/test_strategy_benchmark.py` -> 69 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_phase5_economics_readiness_routes_to_phase5_profile tests/test_digest_profiles_equivalence.py` -> 5 passed.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `python3 scripts/semantic_linter.py --check src/backtest/economics.py tests/test_backtest_skill_economics.py tests/test_digest_profile_matching.py architecture/topology.yaml` -> pass.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5 promotion-grade economics staged live DSA-19 economics tombstone parity market events price history probability trace confirmed trade facts" --files src/backtest/economics.py src/backtest/purpose.py src/backtest/decision_time_truth.py src/backtest/training_eligibility.py src/strategy/benchmark_suite.py tests/test_backtest_skill_economics.py tests/test_strategy_benchmark.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> navigation ok under `phase 5 promotion grade economics readiness implementation`.
- `python3 scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> topology check ok.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout ...` -> topology check ok.
- `git diff --check -- ...` on Phase 5A changed files and packet docs -> pass.

Critic feedback handled:

- Initial critic verdict -> REVISE: readiness must not infer promotion-grade readiness from table existence/counts alone; it must keep default not-ready, include executable market facts, fee/tick/min-order/neg-risk, confirmed trade/fill facts, position lots, probability traces, selection/FDR facts, and resolution/outcome substrate; no fake PnL or promotion-grade metrics from fixtures.
- Remediation: readiness now includes `executable_market_snapshots`, `selection_hypothesis_fact`, `settlements_v2`, and `outcome_fact`; checks confirmed trade facts, confirmed lots, executable snapshot fee/tick/min-order/orderbook fields, snapshot linkage, selected FDR facts, market outcomes, and outcome facts; and appends `economics_engine_not_implemented` unconditionally so this slice cannot return `ready=True`.

Residual risk:

- Phase 5A does not produce economics PnL or staged-live attribution. It only makes the blocker machine-readable and prevents accidental promotion from weak evidence.
- A future Phase 5B must implement the actual economics engine after real forward data exists, including decision-time market-price linkage, fill/slippage/adverse-selection modeling, resolution-source-matched outcomes, confirmed trade facts, and live/replay selection/sizing parity.

Phase 5A code-review REVISE remediation:

- Code-reviewer found two issues: Phase 5A review wording (`Phase 5A promotion-grade economics readiness for DSA-19`) could still route away from the Phase 5 profile, and executable snapshot readiness checked fee/tick/min-order/orderbook but omitted `neg_risk`.
- Remediation added Phase 5A / DSA-19 readiness / exact review wording phrases to the Phase 5 profile and added `tests/test_digest_profile_matching.py::test_phase5a_dsa19_review_wording_routes_to_phase5_profile`.
- Remediation updated `check_economics_readiness()` to require `neg_risk IS NOT NULL` in `executable_market_snapshots`, renamed the blocker to `no_fee_tick_min_order_neg_risk_orderbook_snapshot_facts`, and added `tests/test_backtest_skill_economics.py::test_economics_readiness_requires_neg_risk_snapshot_fact`.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5A promotion-grade economics readiness for DSA-19" --files src/backtest/economics.py tests/test_backtest_skill_economics.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> navigation ok under `phase 5 promotion grade economics readiness implementation`.
- `pytest -q -p no:cacheprovider tests/test_backtest_skill_economics.py tests/test_digest_profile_matching.py::test_phase5_economics_readiness_routes_to_phase5_profile tests/test_digest_profile_matching.py::test_phase5a_dsa19_review_wording_routes_to_phase5_profile` -> 14 passed.
- Post-remediation broader group `pytest -q -p no:cacheprovider tests/test_backtest_purpose_contract.py tests/test_backtest_skill_economics.py tests/test_backtest_training_eligibility.py tests/test_backtest_trade_subject_identity.py tests/test_backtest_settlement_value_outcome.py tests/test_backtest_outcome_comparison.py tests/test_strategy_benchmark.py` -> 70 passed.

Independent review after Phase 5A remediation:

- Verifier follow-up -> APPROVE. It independently verified the exact Phase 5A routing task maps to `phase 5 promotion grade economics readiness implementation`, the `neg_risk` regression passes, the full economics skill test file passes, and a direct runtime probe with `venue_trade_facts.state='MATCHED'` fails closed with `no_confirmed_venue_trade_facts` plus `economics_engine_not_implemented`.
- Code-reviewer follow-up -> APPROVE. It confirmed the two prior REVISE findings are remediated: Phase 5A / DSA-19 review wording now routes to the Phase 5 profile, and executable snapshot readiness now requires `neg_risk IS NOT NULL` before any future economics readiness can pass.
- Residual boundary is intentional: Phase 5A remains a readiness contract and DSA-19 tombstone. Actual economics PnL, staged-live attribution, capital scale-up, and any claim that remaining risk is only math/physics require a future Phase 5B implementation and forward-data evidence.

## Phase 5B Entry Probe - 2026-04-29

Scope:

- Test whether the next-stage wording for promotion-grade economics routes to the correct Phase 5 profile.
- Use the Phase 5A readiness contract against current local DB truth to decide whether PnL implementation can start.
- Preserve no-go boundaries: no production DB mutation, no live venue side effects, no fixture-derived PnL, and no strategy promotion.

Findings:

- Initial topology probe for `Phase 5B promotion-grade economics engine feasibility and forward substrate readiness for DSA-19; no production DB mutation, no live side effects` misrouted to `r3 executable market snapshot v2 implementation` and rejected `src/backtest/economics.py` as scope expansion.
- Remediation added Phase 5B / forward-substrate wording to the Phase 5 economics profile and added `tests/test_digest_profile_matching.py::test_phase5b_forward_substrate_wording_routes_to_phase5_profile`.
- After regeneration, the same topology probe routes to `phase 5 promotion grade economics readiness implementation` with `navigation ok: True`.
- Read-only runtime probe of `check_economics_readiness(conn)` against both `state/zeus_trades.db` and `state/zeus-world.db` returned `ready=False`.
- Both DBs have 0 rows in every required economics substrate table: `market_events_v2`, `market_price_history`, `executable_market_snapshots`, `venue_trade_facts`, `position_lots`, `probability_trace_fact`, `trade_decisions`, `selection_family_fact`, `selection_hypothesis_fact`, `settlements_v2`, and `outcome_fact`.
- Therefore Phase 5B actual PnL implementation is blocked before code work. The next live-money repair is upstream forward substrate production, not an economics engine over synthetic fixtures.

Verification run:

- `python3 scripts/digest_profiles_export.py` -> regenerated 37 profiles.
- `python3 -m py_compile tests/test_digest_profile_matching.py architecture/digest_profiles.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_phase5a_dsa19_review_wording_routes_to_phase5_profile tests/test_digest_profile_matching.py::test_phase5b_forward_substrate_wording_routes_to_phase5_profile tests/test_digest_profiles_equivalence.py` -> 6 passed.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5B promotion-grade economics engine feasibility and forward substrate readiness for DSA-19; no production DB mutation, no live side effects" --files src/backtest/economics.py tests/test_backtest_skill_economics.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> navigation ok under `phase 5 promotion grade economics readiness implementation`.

Phase 5B code-review REVISE remediation:

- Code-reviewer found that the first Phase 5B routing fix was too phrase-specific: exact long wording passed, but shorter realistic tasks such as `Phase 5B forward substrate DSA-19 wording` and `Phase 5B entry slice forward substrate / DSA-19 wording` could still route to non-economics profiles.
- Remediation added short strong phrases `Phase 5B forward substrate`, `Phase 5B forward substrate DSA-19`, and `Phase 5B entry slice forward substrate` to the Phase 5 profile.
- Added `tests/test_digest_profile_matching.py::test_phase5b_short_forward_substrate_wording_routes_to_phase5_profile` with both reviewer-provided short phrasings.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5B forward substrate DSA-19 wording" --files src/backtest/economics.py tests/test_backtest_skill_economics.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> navigation ok under `phase 5 promotion grade economics readiness implementation`.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5B entry slice forward substrate / DSA-19 wording" --files src/backtest/economics.py tests/test_backtest_skill_economics.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> navigation ok under `phase 5 promotion grade economics readiness implementation`.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py` -> 43 passed.

Independent review after Phase 5B entry remediation:

- Critic -> APPROVE for the narrow Phase 5B entry slice only. It explicitly blocked any PnL engine, fixture-derived economics, DB mutation, live side effects, or promotion claim while real forward substrate is empty.
- Code-reviewer initial pass -> REVISE because the first routing fix covered only the exact long wording and missed shorter realistic Phase 5B forward-substrate phrasings.
- Code-reviewer follow-up -> APPROVE after short strong phrases and parametrized digest regressions were added. It confirmed no no-go boundary drift in the Phase 5 profile.
- Verifier initial follow-up -> REVISE because its map-maintenance command omitted required docs companion registry/router files.
- Verifier final follow-up -> APPROVE after rerunning map-maintenance with `architecture/docs_registry.yaml`, `docs/AGENTS.md`, `docs/README.md`, `docs/operations/AGENTS.md`, and `docs/operations/current_state.md` included in `--changed-files`; command returned `topology check ok`.

## Phase 5C Forward-Substrate Producer Alignment - 2026-04-29

Scope:

- Re-align the next mainline after reading `AGENTS.md`, `zeus_current_architecture.md`, `zeus_current_delivery.md`, `current_state.md`, the domain model, and the design-simplification packet.
- Repair topology admission for future upstream forward-substrate producer work.
- Inventory existing substrate producers versus missing producers.
- Preserve no-go boundaries: no PnL engine, no production DB mutation, no live venue side effects, no CLOB cutover, no credentialed WS activation, and no live strategy promotion.

Mainline alignment:

- R3/G1 remains `LIVE NO-GO`; the active law does not authorize live deployment, production DB mutation, CLOB cutover, staged live smoke, or capital scale-up.
- Phase 5B proved `ECONOMICS` cannot be implemented because current local trade/world DBs contain zero rows in every required economics substrate table.
- The correct next axis is upstream forward substrate production, not `run_economics()`.

Producer inventory:

- Existing producer surfaces:
  - `executable_market_snapshots`: `src/data/market_scanner.py::capture_executable_market_snapshot()` writes through `src/state/snapshot_repo.py::insert_snapshot()` and is orchestrated from `src/engine/cycle_runtime.py` when a live selected edge needs executable identity.
  - `probability_trace_fact`: `src/state/db.py::log_probability_trace_fact()` is called from `src/engine/cycle_runtime.py` for decisions.
  - `selection_family_fact` and `selection_hypothesis_fact`: `src/state/db.py` loggers are called from `src/engine/evaluator.py` during family/FDR accounting.
  - `venue_trade_facts` and `position_lots`: `src/state/venue_command_repo.py` append helpers are used by legacy fill polling and user-channel/exchange reconciliation paths when payload identity permits.
  - `trade_decisions`: `src/state/db.py::log_trade_entry()` is the entry decision/position logging surface.
  - `outcome_fact`: `src/state/db.py::log_outcome_fact()` is reached through settlement logging helpers.
- Missing live producer surfaces:
  - `market_events_v2`: schema exists, but no runtime source insert was found outside tests.
  - `market_price_history`: local DB schema exists, but no runtime schema owner or source insert was found in `src/`; only tests and diagnostics mention it.
  - `settlements_v2`: schema exists, but harvester currently writes legacy settlement/outcome surfaces rather than this v2 table.

Topology repair:

- Initial `Phase 5C upstream forward substrate production for DSA-19 market_events_v2 market_price_history probability_trace_fact selection facts no production DB mutation no live side effects` navigation misrouted to `r3 collateral ledger implementation` and rejected data/engine/evaluator/test/doc surfaces as scope expansion.
- Added a dedicated `phase 5 forward substrate producer implementation` profile. It admits future producer work across the relevant data, engine, state, execution, ingest, and test surfaces while forbidding production DB mutation, live venue side effects, CLOB cutover, credentialed WS activation, schema migration, fixture-derived PnL, Kelly/FDR/calibration/lifecycle semantic changes, and strategy promotion.
- Added digest regressions for the exact long Phase 5C producer wording and shorter realistic producer-inventory wording.

Verification run:

- `python3 scripts/digest_profiles_export.py` -> regenerated 39 profiles.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_phase5c_forward_substrate_producer_wording_routes_to_producer_profile tests/test_digest_profile_matching.py::test_phase5c_short_producer_wording_routes_to_producer_profile tests/test_digest_profiles_equivalence.py` -> 7 passed.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5C upstream forward substrate production for DSA-19 market_events_v2 market_price_history probability_trace_fact selection facts no production DB mutation no live side effects" --files src/data/market_scanner.py src/engine/cycle_runtime.py src/engine/evaluator.py src/state/db.py src/state/venue_command_repo.py tests/test_market_scanner_provenance.py tests/test_decision_evidence_runtime_invocation.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> navigation ok under `phase 5 forward substrate producer implementation`.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5 forward substrate producer inventory" --files architecture/topology.yaml tests/test_digest_profile_matching.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md` -> navigation ok under `phase 5 forward substrate producer implementation`.

Independent feedback before this slice:

- Explorer mapping confirmed existing producers for executable snapshots, probability traces, selection facts, venue facts/lots, trade decisions, and outcome facts; it found no runtime producer for `market_events_v2`, `market_price_history`, or `settlements_v2`.
- Critic returned REVISE against immediate broad producer implementation under the old routing and required a separate producer profile plus inventory first. This slice implements that required profile/inventory repair only.

Phase 5C code-review REVISE remediation:

- Code-reviewer found the first producer profile was still too phrase-specific for this slice's own review wording. `Phase 5C forward-substrate producer profile/inventory slice`, `Phase 5C producer inventory`, and `forward-substrate producer profile inventory` could route away from the producer profile.
- Remediation added strong phrases `Phase 5C forward-substrate producer profile`, `Phase 5C producer inventory`, and `forward-substrate producer profile inventory`.
- Extended `tests/test_digest_profile_matching.py::test_phase5c_short_producer_wording_routes_to_producer_profile` with all three reviewer-provided phrasings.
- Direct topology probes for all three reviewer-provided phrasings now route to `phase 5 forward substrate producer implementation`.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py` -> 53 passed.

Independent review after Phase 5C producer-profile remediation:

- Code-reviewer initial pass -> REVISE because the first profile did not route this slice's own review wording (`Phase 5C forward-substrate producer profile/inventory slice`, `Phase 5C producer inventory`, `forward-substrate producer profile inventory`).
- Code-reviewer follow-up -> APPROVE after the missed strong phrases and parametrized regressions were added. It confirmed no-go boundaries remain explicit and docs do not claim runtime producer implementation.
- Verifier -> APPROVE. It independently verified the exact Phase 5C long producer wording, all three short review phrasings, digest export, full digest suite, planning-lock, map-maintenance, semantic linter, and diff-check.

## Phase 5C.1 Forward-Substrate Writer Seam - 2026-04-29

Scope:

- Implement the first code-only forward substrate producer seam for
  `market_events_v2` and `market_price_history`.
- Preserve no-go boundaries: no production DB mutation, no live venue side
  effects, no runtime cycle wiring, no schema migration, no `settlements_v2` or
  `outcome_fact` population, no PnL/economics engine, no strategy promotion.

Authority and topology:

- Phase start re-read: root `AGENTS.md`, `workspace_map.md`,
  `docs/runbooks/task_2026-04-19_ai_workflow_bridge.md`,
  `docs/reference/zeus_domain_model.md`,
  `docs/authority/zeus_current_architecture.md`,
  `docs/authority/zeus_current_delivery.md`,
  `docs/operations/current_state.md`, and scoped routers for `src/`,
  `src/state/`, `src/data/`, `tests/`, `architecture/`, and
  `docs/operations/`.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5 forward substrate producer implementation" --files src/state/db.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> navigation ok under `phase 5 forward substrate producer implementation`.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/state/db.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> topology check ok.

Implementation:

- `src/state/db.py` adds `log_forward_market_substrate(conn, ..., recorded_at,
  scan_authority)` and private helpers.
- The writer takes only a caller-supplied `sqlite3.Connection`; tests patch
  `get_connection()` to fail if the seam tries to open a default DB.
- The writer refuses `scan_authority` other than `VERIFIED`.
- The writer returns `skipped_missing_tables` or `skipped_invalid_schema`
  instead of creating/migrating tables in this slice.
- The writer requires explicit market slug, city, target date,
  `temperature_metric`, condition id, YES token, range label, at least one
  range bound, token ids, prices, and recorded timestamp; it does not infer
  missing identity/range/price facts.
- `market_events_v2.outcome` is always inserted as `NULL` for unresolved scanner
  rows; incoming resolved outcome facts are skipped, not copied.
- Existing rows are not overwritten. Repeated identical facts return unchanged;
  conflicting identity or token-time price facts are reported with conflict
  counters.
- `market_price_history` rows are explicitly scanner/Gamma price observations,
  not CLOB VWMP/orderbook truth.

Tests and verification:

- `tests/test_market_scanner_provenance.py` adds coverage for verified explicit
  writes, missing-table skip, degraded-authority refusal, missing-fact refusal,
  idempotence/conflict behavior, and `check_economics_readiness()` remaining
  blocked when only `market_events_v2` and `market_price_history` are present.
- `python3 -m py_compile src/data/market_scanner.py src/state/db.py tests/test_market_scanner_provenance.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_market_scanner_provenance.py` -> 15 passed.
- `pytest -q -p no:cacheprovider tests/test_backtest_skill_economics.py` -> 12 passed.
- `pytest -q -p no:cacheprovider tests/test_market_scanner_provenance.py tests/test_backtest_skill_economics.py` -> 27 passed.
- `git diff --check -- src/state/db.py tests/test_market_scanner_provenance.py` -> pass.

Critic gate:

- Critic returned REVISE before implementation and required the exact admitted
  topology wording, explicit connection only, no default DB opens, no
  DDL/schema migration, `scan_authority="VERIFIED"` only, no guessed market
  facts, unresolved `outcome=NULL`, scanner-price-only semantics,
  conflict-safe idempotence, and no runtime wiring.
- This implementation follows the narrower REVISE boundary. The schema-owner
  gap for `market_price_history` remains intentionally unresolved for a
  separate slice because this packet profile forbids schema migration.

Code-review REVISE remediation:

- Code-reviewer found two issues:
  - Exact `Phase 5C.1 forward-substrate writer seam
    log_forward_market_substrate` wording routed away from the producer profile.
  - Existing `market_events_v2` rows with non-null `outcome` were treated as
    unchanged, so the unresolved scanner seam could still append price-history
    rows for an already-resolved event.
- Remediation added strong phrases `Phase 5C.1 forward-substrate writer seam`,
  `forward-substrate writer seam`, and `log_forward_market_substrate` to the
  producer profile, regenerated `architecture/digest_profiles.py`, and added
  `tests/test_digest_profile_matching.py::test_phase5c_writer_seam_review_wording_routes_to_producer_profile`.
- Remediation changed `_insert_forward_market_event()` to return
  `resolved_existing` when the existing event row already has an outcome. The
  caller now increments `outcomes_skipped_with_outcome_fact` and does not write
  price-history rows for that condition.
- Added
  `tests/test_market_scanner_provenance.py::TestForwardMarketSubstrateProducer::test_forward_substrate_does_not_append_prices_for_resolved_events`.
- Code-reviewer follow-up found the same price-row orphan risk for ordinary
  market-event identity conflicts. Remediation now treats `conflict` as an
  outcome-level skip after incrementing `market_events_conflicted`, so rejected
  event identity cannot create new token price rows.
- Added
  `tests/test_market_scanner_provenance.py::TestForwardMarketSubstrateProducer::test_forward_substrate_does_not_append_prices_for_event_identity_conflicts`.
- `python3 scripts/digest_profiles_export.py` -> regenerated 39 profiles.
- `python3 scripts/digest_profiles_export.py --check` -> OK.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5C.1 forward-substrate writer seam log_forward_market_substrate" --files src/state/db.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> navigation ok under `phase 5 forward substrate producer implementation`.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py src/state/db.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> topology check ok.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py src/state/db.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md architecture/docs_registry.yaml docs/AGENTS.md docs/README.md docs/operations/AGENTS.md docs/operations/current_state.md` -> topology check ok.
- `python3 scripts/semantic_linter.py --check architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py src/state/db.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> pass.
- `python3 -m py_compile src/data/market_scanner.py src/state/db.py tests/test_market_scanner_provenance.py tests/test_digest_profile_matching.py architecture/digest_profiles.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_market_scanner_provenance.py tests/test_backtest_skill_economics.py` -> 29 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py` -> 57 passed.
- `git diff --check -- architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py src/state/db.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> pass.

Independent review after Phase 5C.1 remediation:

- Verifier -> APPROVE. It independently reran exact topology navigation,
  planning-lock, map-maintenance closeout, semantic linter, py_compile,
  writer/economics tests, digest profile tests, digest export check, diff-check,
  and a `ZEUS_MODE=live` explicit in-memory probe proving the seam writes only
  the supplied connection and `check_economics_readiness()` remains blocked.
- Code-reviewer initial pass -> REVISE for missing exact Phase 5C.1 topology
  wording and resolved rows still receiving price-history rows.
- Code-reviewer follow-up -> REVISE for ordinary market-event identity conflicts
  still receiving price-history rows.
- Code-reviewer final follow-up -> APPROVE after `conflict` became an
  outcome-level stop before the price loop and the conflicting-token regression
  proved no orphan price rows are appended.

Residual blockers:

- No live cycle writes call this seam yet.
- Current production/local DB row counts are not changed by this code-only
  slice.
- `market_price_history` still lacks a source-owned DDL/migration decision in
  this slice.
- `settlements_v2` producer work remains open.
- DSA-19 remains open until real forward substrate, confirmed fills/lots,
  outcome facts, full market-price linkage, and economics parity exist.

## Phase 5C.2 Market Price History Schema Owner - 2026-04-29

Scope:

- Add code-owned DDL for `market_price_history` so the explicit-connection
  forward-substrate writer has a source-owned schema surface.
- Preserve no-go boundaries: no production DB mutation, no live venue side
  effects, no runtime cycle wiring, no backfill, no CLOB VWMP/orderbook truth,
  no settlement/outcome fact population, no PnL/economics engine, no strategy
  promotion.

Authority and topology:

- Phase start re-read: root `AGENTS.md`, `workspace_map.md`,
  `docs/reference/zeus_domain_model.md`,
  `docs/authority/zeus_current_architecture.md`,
  `docs/authority/zeus_current_delivery.md`,
  `docs/operations/current_state.md`, and scoped routers for `src/`,
  `src/state/`, and `tests/`.
- Initial navigation for `Phase 5C.2 market_price_history schema owner DDL seam
  code-only no production DB mutation no live side effects no runtime wiring`
  misrouted to an R3 profile and rejected `src/state/schema/v2_schema.py`.
- Added separate topology profile
  `phase 5 forward substrate schema owner implementation`, preserving the
  existing producer profile's schema-migration no-go while admitting only
  code-owned `market_price_history` DDL.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5C.2 market_price_history schema owner DDL seam code-only no production DB mutation no live side effects no runtime wiring" --files src/state/schema/v2_schema.py tests/test_schema_v2_gate_a.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> navigation ok under `phase 5 forward substrate schema owner implementation`.
- Critic -> APPROVE for the two-step sequence with constraints: topology must
  admit the exact Phase 5C.2 schema-owner slice first; DDL must be
  `market_price_history` only; no production DB mutation, runtime wiring,
  CLOB cutover, PnL/economics readiness, `settlements_v2`, or `outcome_fact`;
  use existing local schema shape; add lookup indexes; verify only with
  in-memory/temp DB.

Implementation:

- `src/state/schema/v2_schema.py` adds `CREATE TABLE IF NOT EXISTS
  market_price_history` to `apply_v2_schema()`.
- Columns: `id`, `market_slug`, `token_id`, `price`, `recorded_at`,
  `hours_since_open`, and `hours_to_resolution`.
- Constraint: `UNIQUE(token_id, recorded_at)`.
- Domain check: `price >= 0.0 AND price <= 1.0`.
- Indexes: `idx_market_price_history_slug_recorded` on `(market_slug,
  recorded_at)` and `idx_market_price_history_token_recorded` on `(token_id,
  recorded_at)`.
- `tests/test_schema_v2_gate_a.py` received lifecycle headers and trusted-test
  registry metadata because this slice reuses it as current schema evidence.

Tests and verification:

- `tests/test_schema_v2_gate_a.py` now verifies table columns, unique
  token-time behavior, price-domain rejection, lookup indexes, idempotent
  re-application preserving rows and foreign-key PRAGMA state, writer
  compatibility after `apply_v2_schema(:memory:)`, and economics readiness
  remaining blocked.
- `tests/test_digest_profile_matching.py::test_phase5c2_market_price_history_schema_owner_wording_routes_to_schema_profile` pins the exact Phase 5C.2 wording to the schema-owner profile.
- Code-review initial pass -> REVISE because `market_price_history.price` was
  originally an unconstrained `REAL`, allowing impossible probability prices
  such as `-0.1` or `1.25` through direct SQL. Remediation added
  `CHECK (price >= 0.0 AND price <= 1.0)` plus direct-SQL `IntegrityError`
  regressions for both invalid prices.
- `pytest -q -p no:cacheprovider tests/test_schema_v2_gate_a.py tests/test_market_scanner_provenance.py tests/test_backtest_skill_economics.py` -> 38 passed, 9 subtests passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_phase5c2_market_price_history_schema_owner_wording_routes_to_schema_profile tests/test_digest_profiles_equivalence.py` -> 5 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py` -> 58 passed.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files architecture/topology.yaml architecture/digest_profiles.py architecture/test_topology.yaml tests/test_digest_profile_matching.py src/state/schema/v2_schema.py tests/test_schema_v2_gate_a.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> topology check ok.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files architecture/topology.yaml architecture/digest_profiles.py architecture/test_topology.yaml tests/test_digest_profile_matching.py src/state/schema/v2_schema.py tests/test_schema_v2_gate_a.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md architecture/docs_registry.yaml docs/AGENTS.md docs/README.md docs/operations/AGENTS.md docs/operations/current_state.md` -> topology check ok.
- `python3 scripts/semantic_linter.py --check architecture/topology.yaml architecture/digest_profiles.py architecture/test_topology.yaml tests/test_digest_profile_matching.py src/state/schema/v2_schema.py tests/test_schema_v2_gate_a.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> pass.
- `python3 -m py_compile src/state/schema/v2_schema.py tests/test_schema_v2_gate_a.py tests/test_market_scanner_provenance.py tests/test_digest_profile_matching.py architecture/digest_profiles.py` -> pass.
- `python3 scripts/digest_profiles_export.py --check` -> OK.
- `git diff --check -- architecture/topology.yaml architecture/digest_profiles.py architecture/test_topology.yaml tests/test_digest_profile_matching.py src/state/schema/v2_schema.py tests/test_schema_v2_gate_a.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> pass.
- Code-reviewer follow-up -> APPROVE after the price-domain CHECK and
  invalid-price regressions. Residual called out by reviewer: existing DBs
  with a pre-existing unconstrained `market_price_history` table are not
  migrated in this no-production-mutation/no-migration slice.
- Verifier follow-up -> APPROVE. It confirmed no runtime call site was added
  for `log_forward_market_substrate()`, no production/local DB rows were
  changed, topology admitted only the schema-owner wording, and docs did not
  claim DSA-19 or runtime readiness closure.

Residual blockers:

- No runtime cycle writes call `log_forward_market_substrate()` yet.
- No production/local DB row counts are changed by this code-only schema seam.
- Pre-existing DBs with an unconstrained `market_price_history` table are not
  migrated by this slice.
- `settlements_v2` producer work remains open.
- DSA-19 remains open until real forward substrate, confirmed fills/lots,
  outcome facts, full market-price linkage, and economics parity exist.

## Phase 5C.3 Runtime Wiring Admission Repair - 2026-04-29

Scope:

- Repair topology admission only so the next runtime-wiring slice can be
  reviewed under the Phase 5 forward-substrate producer profile instead of
  misrouting to the R3 collateral profile.
- No runtime code change, no production DB mutation, no live venue side
  effect, no CLOB cutover, no schema migration, and no economics readiness
  promotion in this admission-only slice.

Evidence:

- Initial navigation for `Phase 5C.3 runtime forward substrate wiring for
  DSA-19 log verified market scan substrate no production DB mutation no live
  venue side effects no CLOB cutover no economics readiness promotion`
  misrouted to `r3 collateral ledger implementation` and blocked
  `src/engine/cycle_runtime.py`.
- Critic -> APPROVE for an admission-only repair and required qualified
  strong phrases rather than bare `runtime wiring`, no runtime/source edits in
  this sub-slice, and side-effect wording regressions.
- `architecture/topology.yaml` now adds qualified producer phrases:
  `Phase 5C.3 runtime forward substrate wiring`,
  `log verified market scan substrate`, and
  `verified market scan substrate`.
- The same profile now treats non-negated `live venue submission`,
  `live venue cancel`, `live venue redeem`, and `live cutover` as negative
  phrases. Code-review follow-up also required qualified guards for realistic
  generic side-effect wording, so `with live venue side effects`,
  `requires live venue side effects`, `with CLOB cutover`, and
  `requires CLOB cutover` are negative phrases while the exact allowed wording
  keeps explicit `no live venue side effects` and `no CLOB cutover`
  disclaimers.
- `tests/test_digest_profile_matching.py` adds a Phase 5C.3 exact-wording
  regression and side-effect wording regressions.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5C.3 runtime forward substrate wiring for DSA-19 log verified market scan substrate no production DB mutation no live venue side effects no CLOB cutover no economics readiness promotion" --files src/engine/cycle_runtime.py src/state/db.py tests/test_runtime_guards.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> navigation ok under `phase 5 forward substrate producer implementation`.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_phase5c3_runtime_wiring_wording_routes_to_producer_profile tests/test_digest_profile_matching.py::test_phase5c3_runtime_wiring_side_effect_wording_is_not_admitted tests/test_digest_profile_matching.py::test_phase5c_forward_substrate_producer_wording_routes_to_producer_profile tests/test_digest_profile_matching.py::test_phase5c_short_producer_wording_routes_to_producer_profile tests/test_digest_profile_matching.py::test_phase5c_writer_seam_review_wording_routes_to_producer_profile tests/test_digest_profile_matching.py::test_phase5c2_market_price_history_schema_owner_wording_routes_to_schema_profile tests/test_digest_profiles_equivalence.py` -> 19 passed.
- Code-review initial pass -> REVISE because generic non-negated
  `with live venue side effects` and `with CLOB cutover` wording could still
  over-admit. Remediation added qualified negative phrases and regressions for
  both `with ...` and `requires ...` side-effect/cutover wording.
- Code-review second pass -> REVISE because equivalent phrasing without
  `with/requires` or using `includes ...` still over-admitted. Remediation
  added qualified negative phrases and regressions for
  `runtime forward substrate wiring live venue side effects`,
  `includes live venue side effects`,
  `runtime forward substrate wiring CLOB cutover`, and
  `includes CLOB cutover`.
- Code-review third pass -> REVISE on remaining `needs ...` and `perform ...`
  phrasings. The bounded profile-level repair adds regressions for those exact
  forms plus common non-negated verbs (`enable`, `implements`, `execute`,
  `activate`) without changing the topology kernel or adding bare negative
  phrases that would break `no ...` disclaimers.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_phase5c3_runtime_wiring_wording_routes_to_producer_profile tests/test_digest_profile_matching.py::test_phase5c3_runtime_wiring_side_effect_wording_is_not_admitted tests/test_digest_profiles_equivalence.py` -> 12 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_phase5c3_runtime_wiring_wording_routes_to_producer_profile tests/test_digest_profile_matching.py::test_phase5c3_runtime_wiring_side_effect_wording_is_not_admitted tests/test_digest_profiles_equivalence.py` -> 16 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_phase5c3_runtime_wiring_wording_routes_to_producer_profile tests/test_digest_profile_matching.py::test_phase5c3_runtime_wiring_side_effect_wording_is_not_admitted tests/test_digest_profiles_equivalence.py` -> 28 passed.
- `python3 scripts/digest_profiles_export.py --check` -> OK.

Residual blockers:

- At admission-only closeout, runtime code still did not call
  `log_forward_market_substrate()`.
- Admission being repaired does not itself prove production DB migration,
  runtime writer correctness, CLOB truth, venue execution, economics readiness,
  or strategy promotion.

## Phase 5C.3 Runtime Forward Substrate Wiring - 2026-04-29

Scope:

- Wire the existing explicit-connection `log_forward_market_substrate()` seam
  into the discovery runtime after market discovery filters and scan-authority
  capture.
- Preserve no-go boundaries: no live venue side effects, no CLOB cutover, no
  schema migration, no production DB mutation in this session, no economics
  readiness promotion, and no Kelly/FDR/calibration/lifecycle changes.

Implementation:

- `src/engine/cycle_runtime.py` now records mode-filtered scanner substrate
  exactly once per discovery phase, using `decision_time.isoformat()` as
  `recorded_at` and the current `scan_authority`.
- The helper stores compact cycle-summary status/counts under
  `forward_market_substrate_*` keys. It does not call `conn.commit()`, does
  not call CLOB/live venue methods, and does not block entry by itself.
- Existing scan-authority gates remain the entry safety gate: degraded Gamma
  authority still blocks before evaluator; executable snapshot gates still
  block live entry when executable market identity is missing.
- `written_with_conflicts`, `skipped_invalid_schema`, and unexpected exceptions
  mark `summary["degraded"] = True`; `skipped_missing_tables`,
  `refused_degraded_authority`, `skipped_no_connection`, and
  `skipped_no_valid_rows` are nonblocking in this slice.

Review-driven constraints:

- Critic -> REVISE before code, requiring mode-filtered logging only,
  nonblocking behavior for missing schema/degraded authority/no valid rows,
  degraded status for conflicts/invalid schema/exceptions, compact summary
  fields, no helper-level commit, and no claim of complete economics market
  coverage.
- The critic's stale precondition concern said `apply_v2_schema()` must create
  `market_price_history` for the success path; Phase 5C.2 already closed that
  DDL seam, and the runtime test uses `apply_v2_schema(conn)` to prove it.

Tests and verification:

- `tests/test_runtime_guards.py::test_discovery_phase_writes_verified_forward_market_substrate_before_evaluator` proves verified mode-filtered scanner rows are written to `market_events_v2` and `market_price_history` before evaluator execution, use cycle `decision_time`, remain invisible to a second connection before cycle commit, and leave `check_economics_readiness()` blocked.
- `tests/test_runtime_guards.py::test_discovery_phase_forward_market_substrate_missing_schema_is_nonblocking` proves missing tables report `skipped_missing_tables` without blocking candidate evaluation or marking degradation.
- `tests/test_runtime_guards.py::test_discovery_phase_forward_market_substrate_invalid_schema_degrades` proves invalid tables report `skipped_invalid_schema` and mark cycle degraded.
- `tests/test_runtime_guards.py::test_discovery_phase_blocks_unverified_market_scan_authority_before_evaluator` proves an explicit `NEVER_FETCHED` scan-authority value, or a missing authority getter, refuses substrate logging and blocks evaluator before candidate scoring.
- Existing stale and empty-fallback market-scan tests now also prove
  `forward_market_substrate_status="refused_degraded_authority"` while the
  evaluator remains blocked by the existing market-scan authority gate.
- `pytest -q -p no:cacheprovider tests/test_runtime_guards.py::test_discovery_phase_writes_verified_forward_market_substrate_before_evaluator tests/test_runtime_guards.py::test_discovery_phase_forward_market_substrate_missing_schema_is_nonblocking tests/test_runtime_guards.py::test_discovery_phase_forward_market_substrate_invalid_schema_degrades tests/test_runtime_guards.py::test_discovery_phase_blocks_stale_market_scan_before_evaluator tests/test_runtime_guards.py::test_discovery_phase_blocks_empty_fallback_market_scan_before_evaluator` -> 5 passed.
- `pytest -q -p no:cacheprovider tests/test_runtime_guards.py::test_discovery_phase_blocks_stale_market_scan_before_evaluator tests/test_runtime_guards.py::test_discovery_phase_blocks_empty_fallback_market_scan_before_evaluator tests/test_runtime_guards.py::test_discovery_phase_writes_verified_forward_market_substrate_before_evaluator tests/test_runtime_guards.py::test_discovery_phase_forward_market_substrate_missing_schema_is_nonblocking tests/test_runtime_guards.py::test_discovery_phase_forward_market_substrate_invalid_schema_degrades tests/test_runtime_guards.py::test_live_entry_requires_executable_market_identity_before_intent tests/test_runtime_guards.py::test_entry_intent_receives_executable_snapshot_fields tests/test_runtime_guards.py::test_live_entry_captures_and_commits_snapshot_before_executor tests/test_market_scanner_provenance.py tests/test_schema_v2_gate_a.py tests/test_backtest_skill_economics.py` -> 46 passed, 9 subtests passed.
- `pytest -q -p no:cacheprovider tests/test_runtime_guards.py` -> 132 passed after reviewer-block remediation.
- `pytest -q -p no:cacheprovider tests/test_market_scanner_provenance.py tests/test_schema_v2_gate_a.py tests/test_backtest_skill_economics.py tests/test_runtime_guards.py::test_discovery_phase_writes_verified_forward_market_substrate_before_evaluator tests/test_runtime_guards.py::test_discovery_phase_forward_market_substrate_missing_schema_is_nonblocking tests/test_runtime_guards.py::test_discovery_phase_forward_market_substrate_invalid_schema_degrades tests/test_runtime_guards.py::test_discovery_phase_blocks_unverified_market_scan_authority_before_evaluator tests/test_runtime_guards.py::test_discovery_phase_blocks_stale_market_scan_before_evaluator tests/test_runtime_guards.py::test_discovery_phase_blocks_empty_fallback_market_scan_before_evaluator` -> 45 passed, 9 subtests passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py` -> 82 passed.
- `python3 scripts/digest_profiles_export.py --check` -> OK.
- `python3 -m py_compile src/engine/cycle_runtime.py tests/test_runtime_guards.py` -> pass.
- `python3 scripts/semantic_linter.py --check src/engine/cycle_runtime.py tests/test_runtime_guards.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> pass.
- `git diff --check -- src/engine/cycle_runtime.py tests/test_runtime_guards.py docs/operations/task_2026-04-29_design_simplification_audit/evidence.md docs/operations/task_2026-04-29_design_simplification_audit/findings.md docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> pass.

Review remediation:

- Code-reviewer BLOCK found the first runtime wiring made missing
  `get_last_scan_authority()` default to `VERIFIED`, and treated
  `NEVER_FETCHED` as non-degraded. That was a real fail-open: the writer would
  refuse substrate, but evaluator could still run.
- Remediation makes a missing/non-callable authority getter return
  `NEVER_FETCHED`, maps `NEVER_FETCHED` to `DATA_UNAVAILABLE`, and adds the
  explicit regression above. Older runtime tests that intentionally exercise
  evaluator/telemetry paths now declare `get_last_scan_authority() ==
  "VERIFIED"` instead of relying on an implicit default.
- Code-reviewer follow-up -> APPROVE. It verified the previous fail-open is
  closed, `NEVER_FETCHED` blocks evaluator, VERIFIED plus missing substrate
  remains nonblocking, and docs keep runtime-substrate/no-promotion boundaries.
- Verifier follow-up -> APPROVE. It independently ran focused runtime tests,
  executable-snapshot runtime tests, full runtime guards, schema/scanner/
  economics tests, and checked that broader economics coverage remains out of
  scope.

Residual blockers:

- This is mode-filtered runtime substrate, not complete market-universe
  economics coverage.
- `settlements_v2` producer work remains open.
- DSA-19 remains open until confirmed fills/lots, outcome facts, full
  market-price linkage, and the economics engine exist and pass promotion
  parity.

## Phase 5C.4 Settlements V2 Producer - 2026-04-30

Scope:

- Add the harvester-side producer for `settlements_v2` from existing
  `SettlementSemantics`-gated settlement truth.
- Preserve no-go boundaries: no production DB mutation in this session, no
  live venue side effects, no schema migration, no source routing / Paris
  config change, no `market_events_v2.outcome` inference, no PnL/economics
  readiness promotion, and no LOW support claim beyond the existing HIGH-only
  harvester path.

Implementation:

- `src/state/db.py::log_settlement_v2()` writes `settlements_v2` through a
  caller-supplied SQLite connection only. It does not open default DBs, create
  or migrate tables, or commit.
- The helper fails closed / skips for absent `settlements_v2`, invalid schema,
  missing `city`, `target_date`, `temperature_metric`, `market_slug`, or invalid
  authority. It uses `ON CONFLICT(city, target_date, temperature_metric) DO
  UPDATE`, not `INSERT OR REPLACE`.
- `src/execution/harvester.py::_write_settlement_truth()` now calls the helper
  after the existing legacy `settlements` insert. The v2 provenance includes
  legacy table identity, bin bounds, unit, settlement source type, canonical
  `HIGH_LOCALDAY_MAX` identity fields, data version, source observation id, and
  quarantine reason when present.
- Missing `event_slug` still allows the legacy compatibility row but refuses the
  v2 row, preventing a NULL `market_slug` from entering the forward substrate.
- QUARANTINED rows mirror as `QUARANTINED`; this slice never promotes them to
  `VERIFIED`.
- `market_events_v2.outcome` is intentionally untouched because `_find_winning_bin()`
  returns only bin bounds and current harvester logic does not retain child
  `condition_id` / token identity. Updating child outcome from label text would
  infer market identity and violates the Phase 5C profile.

Tests and verification:

- `tests/test_harvester_metric_identity.py` adds regressions for verified v2
  mirror, missing-market-slug refusal, quarantine mirror, idempotent conflict
  update, missing-table no-DDL behavior, and economics readiness staying
  blocked after only `settlements_v2` is populated.
- `pytest -q -p no:cacheprovider tests/test_harvester_metric_identity.py` -> 11 passed.
- `pytest -q -p no:cacheprovider tests/test_harvester_metric_identity.py tests/test_market_scanner_provenance.py tests/test_schema_v2_gate_a.py tests/test_backtest_skill_economics.py` -> 65 passed, 9 subtests passed.
- `pytest -q -p no:cacheprovider tests/test_harvester_dr33_live_enablement.py tests/test_settlements_unique_migration.py` -> 38 passed.
- `pytest -q -p no:cacheprovider tests/test_db.py::test_log_outcome_fact_skips_missing_table_explicitly tests/test_db.py::test_log_settlement_event_preserves_prior_exit_time_in_outcome_fact tests/test_schema_v2_gate_a.py` -> 11 passed, 9 subtests passed.
- `python3 -m py_compile src/state/db.py src/execution/harvester.py tests/test_harvester_metric_identity.py tests/test_market_scanner_provenance.py tests/test_schema_v2_gate_a.py tests/test_digest_profile_matching.py architecture/digest_profiles.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_phase5c_short_producer_wording_routes_to_producer_profile` -> 11 passed.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5C.4 settlements_v2 producer forward substrate; write v2 settlement/outcome substrate from existing harvester settlement truth; no production DB mutation; no live venue side effects; no schema migration; no source routing; no Paris; no economics readiness promotion" ...` -> navigation ok under `phase 5 forward substrate producer implementation`.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> topology check ok.

Review remediation:

- Verifier -> REVISE for a topology evidence gap: the real workspace diff
  includes `docs/operations/AGENTS.md` as the required operations packet
  registry companion, but the first Phase 5C.4 navigation command omitted it
  and the profile did not admit it. Map-maintenance passed only when that file
  was supplied, proving the companion was required.
- Remediation adds `docs/operations/AGENTS.md` to the Phase 5 forward-substrate
  producer profile with explicit law that it may be touched only as the
  operations packet registry companion for this packet's evidence files.
- Full changed-file navigation after remediation, including
  `docs/operations/AGENTS.md`, routes to `phase 5 forward substrate producer
  implementation` and admits every changed file.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py` -> 109 passed.
- `python3 scripts/digest_profiles_export.py --check` -> OK.
- Code-reviewer -> two P2 findings, both remediated:
  `log_settlement_v2()` did not preflight the `UNIQUE(city, target_date,
  temperature_metric)` conflict target, so a malformed existing
  `settlements_v2` table could raise after the legacy settlement insert and
  interrupt downstream harvester processing; and Phase 5C negative phrases used
  bare `live venue submission/cancel/redeem`, so safe wording like `no live
  venue submission` could false-veto the producer profile.
- Remediation adds a `settlements_v2` unique-key preflight plus
  `OperationalError` schema-shape fallback to `skipped_invalid_schema`, and a
  regression proving malformed v2 schema does not abort the legacy settlement
  write. It also replaces the bare live-venue negative phrases with affirmative
  side-effect forms and adds a safe no-go wording route regression.
- `pytest -q -p no:cacheprovider tests/test_harvester_metric_identity.py tests/test_digest_profile_matching.py::test_phase5c4_safe_no_go_wording_routes_to_producer_profile tests/test_digest_profile_matching.py::test_phase5c3_runtime_wiring_side_effect_wording_is_not_admitted` -> 40 passed.
- `pytest -q -p no:cacheprovider tests/test_harvester_metric_identity.py tests/test_market_scanner_provenance.py tests/test_schema_v2_gate_a.py tests/test_backtest_skill_economics.py tests/test_harvester_dr33_live_enablement.py tests/test_settlements_unique_migration.py` -> 104 passed, 9 subtests passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py` -> 114 passed.
- Verifier follow-up -> PASS after checking both P2 remediations, full
  changed-file navigation including `docs/operations/AGENTS.md`, blocked
  affirmative live-side-effect navigation, planning-lock, map-maintenance,
  digest export, py_compile, and diff-check evidence.

Residual blockers:

- `market_events_v2.outcome` was intentionally deferred in Phase 5C.4 until
  harvester carried observed child market identity (`condition_id`, token id,
  market slug) through the resolved winning-bin path. Phase 5C.5 below closes
  that harvester-side outcome producer.
- DSA-19 remains open until full real forward substrate, confirmed fills/lots,
  outcome facts, full market-price linkage, and the economics engine exist and
  pass promotion parity.

## Phase 5C.5 Market Events V2 Outcome Producer - 2026-04-30

Scope:

- Add a harvester-side producer for `market_events_v2.outcome` from resolved
  Gamma child-market identity.
- Preserve no-go boundaries: no production DB mutation in this session, no
  live venue side effects, no schema migration, no source routing / Paris
  config change, no CLOB cutover, no LOW-support claim beyond the existing
  HIGH-only harvester path, and no PnL/economics readiness promotion.

Implementation:

- `src/execution/harvester.py` now extracts resolved Gamma child identity into
  `ResolvedMarketOutcome(condition_id, yes_token_id, range_label, range_low,
  range_high, yes_won)`.
- The resolved parser requires terminal `umaResolutionStatus="resolved"`,
  two binary outcome labels, two CLOB token ids, and a terminal 1/0 resolution
  vector. It follows the outcome labels to map the YES token instead of
  assuming positional order.
- `run_harvester()` now requires exactly one YES-resolved child before deriving
  the winning bin and writing settlement truth. A malformed payload with
  multiple YES winners is skipped rather than converted into settlement/outcome
  truth.
- `_write_settlement_truth()` calls `log_market_event_outcomes_v2()` only after
  the existing `SettlementSemantics` containment gate marks the settlement
  `VERIFIED`. QUARANTINED rows mirror to `settlements_v2` but do not set
  `market_events_v2.outcome`, because `market_events_v2` has no authority
  column to preserve quarantine status.
- `src/state/db.py::log_market_event_outcome_v2()` updates only an existing
  scanner-produced `market_events_v2` row matching exact `(market_slug,
  condition_id, token_id, city, target_date, temperature_metric)`. It never
  opens a default DB connection, creates schema, inserts missing market rows,
  commits, or overwrites a conflicting resolved outcome.
- `src/state/db.py::log_market_event_outcomes_v2()` prevalidates the full batch
  before mutation and writes inside a savepoint. Any missing child row,
  identity mismatch, invalid outcome, existing conflicting outcome, missing
  table, or invalid schema prevents partial outcome writes, avoiding the
  false-positive economics-readiness risk where one non-null outcome could
  unblock `no_market_event_outcomes`.
- The Phase 5C topology profile now admits realistic Phase 5C.5 wording,
  including remediation/re-review phrases, and admits the directly affected
  DR33 harvester test helper.

Tests and verification:

- `tests/test_harvester_metric_identity.py` adds regressions for resolved
  child identity preservation, reversed YES/NO label order, exactly-one winner
  enforcement, VERIFIED outcome updates by exact identity, missing-child
  no-insert behavior, all-or-nothing batch behavior, token mismatch refusal,
  missing-table no-DDL behavior, and QUARANTINED settlements not setting
  `market_events_v2.outcome`.
- `tests/test_harvester_dr33_live_enablement.py` fixtures now include realistic
  `conditionId` and `clobTokenIds`, preserving DR33 settlement-flow coverage
  under the stricter identity parser.
- `tests/test_digest_profile_matching.py` adds Phase 5C.5 route regressions for
  the narrow producer wording and realistic remediation/re-review wording.
- `pytest -q -p no:cacheprovider tests/test_harvester_metric_identity.py tests/test_harvester_dr33_live_enablement.py` -> 48 passed.
- `pytest -q -p no:cacheprovider tests/test_harvester_metric_identity.py tests/test_market_scanner_provenance.py tests/test_schema_v2_gate_a.py tests/test_backtest_skill_economics.py tests/test_harvester_dr33_live_enablement.py tests/test_settlements_unique_migration.py` -> 113 passed, 9 subtests passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py` -> 122 passed.
- `python3 scripts/digest_profiles_export.py --check` -> OK.
- `python3 -m py_compile src/execution/harvester.py src/state/db.py tests/test_harvester_metric_identity.py tests/test_harvester_dr33_live_enablement.py tests/test_digest_profile_matching.py architecture/digest_profiles.py` -> pass.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 5C.5 market_events_v2 outcome producer from harvester resolved child identity; preserve condition_id/token identity before updating market_events_v2.outcome; no production DB mutation; no live venue side effects; no schema migration; no source routing; no Paris; no economics readiness promotion" ...` -> navigation ok under `phase 5 forward substrate producer implementation`.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md` -> topology check ok.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files ...` -> topology check ok.
- `python3 scripts/semantic_linter.py --check ...` over Phase 5C.5 source,
  tests, topology, digest, and packet evidence files -> pass.
- `git diff --check -- ...` over Phase 5C.5 changed files -> pass.

Review remediation:

- First code-review pass found two issues: batch outcome updates could be
  partially applied, and malformed Gamma payloads with multiple YES winners
  were not explicitly rejected. Both were remediated with full-batch
  prevalidation/savepoint writes and exactly-one-winner enforcement.
- Code-review follow-up verified those two issues fixed and raised one route
  hygiene issue: realistic `Phase 5C.5 fixes/remediation/re-review` wording
  could misroute to a collateral profile. Remediation added dedicated Phase
  5C.5 route phrases and a regression.
- Verifier REVISE used an over-broad file set that included
  `docs/operations/current_state.md`, `docs/operations/known_gaps.md`,
  `src/state/schema/v2_schema.py`, and `architecture/test_topology.yaml`.
  Those files are not part of the Phase 5C.5 changed-file set. The precise
  Phase 5C.5 navigation/planning/map-maintenance closeout commands above pass.

Residual blockers:

- This closes only harvester-driven `market_events_v2.outcome` for resolved
  Gamma child identities already present in forward scanner substrate. It does
  not backfill historical outcomes, create missing market-event rows, populate
  production DBs, or resolve markets that never passed through the verified
  scanner producer.
- DSA-19 still requires complete forward venue/fill/probability/selection/
  outcome substrate, confirmed fills/lots, full market-price linkage, and an
  implemented economics engine before promotion-grade PnL attribution can be
  claimed.

## Phase 2C Execution Capability Proof Slice - 2026-04-29

Scope:

- Close the first command-side DSA-16 slice by making entry and exit submit
  events carry one composed capability proof payload.
- No production DB mutation, schema migration, source routing, Paris config
  edit, live venue side effect, CLOB cutover, cancel/redeem side-effect wiring,
  or live deployment authorization.

Implementation:

- Added a dedicated topology profile,
  `phase 2c execution capability proof implementation`, so DSA-16 wording
  routes away from the narrower heartbeat/risk profiles while forbidding
  state/control/riskguard/schema/live-side-effect expansion.
- `src/execution/executor.py` now builds an `execution_capability` payload for
  entry and exit `SUBMIT_REQUESTED` events after existing gates pass and before
  SDK contact. The payload includes `capability_id`, action, intent kind, mode,
  allowed flag, freshness time, command id, order type, token id, executable
  snapshot id, and component results.
- Entry components cover cutover, risk allocator, order-type selection,
  heartbeat, WS gap, collateral, and executable snapshot gate.
- Exit components cover cutover, reduce-only risk allocator, order-type
  selection, heartbeat, WS gap, collateral, replacement-sell guard, and
  executable snapshot gate.
- The slice reuses existing gate functions; it does not move a failed gate
  after command persistence or SDK contact and does not add a new schema field
  for the capability id.
- `tests/test_executor_command_split.py` adds entry/exit regressions that read
  the persisted `SUBMIT_REQUESTED` event payload and assert the proof is present
  with the expected component set.
- `tests/test_digest_profile_matching.py::test_phase2c_execution_capability_routes_to_dedicated_profile`
  pins exact Phase 2C wording to the dedicated profile.

Verification run:

- `pytest -q -p no:cacheprovider tests/test_executor_command_split.py::TestLiveOrderCommandSplit::test_entry_submit_requested_persists_execution_capability_proof tests/test_executor_command_split.py::TestExitOrderCommandSplit::test_exit_submit_requested_persists_execution_capability_proof tests/test_digest_profile_matching.py::test_phase2c_execution_capability_routes_to_dedicated_profile tests/test_digest_profiles_equivalence.py`
  -> 7 passed.
- `pytest -q -p no:cacheprovider tests/test_executor_command_split.py`
  -> 23 passed.
- `pytest -q -p no:cacheprovider tests/test_risk_allocator.py tests/test_heartbeat_supervisor.py tests/test_user_channel_ingest.py`
  -> 52 passed, 4 skipped, 22 warnings.
- `python3 -m py_compile src/execution/executor.py tests/test_executor_command_split.py tests/test_digest_profile_matching.py architecture/digest_profiles.py`
  -> pass.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `python3 scripts/semantic_linter.py --check ...` over touched executor,
  tests, topology, digest, and packet evidence files -> pass.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 2C DSA-16 composed execution capability proof for entry exit capability proof payload; no live venue side effects; no production DB mutation; no source routing; no Paris; no CLOB cutover" --files ...`
  -> navigation ok; profile `phase 2c execution capability proof
  implementation`; admission status `admitted`.

Independent planning review:

- Subagent read-only map approved the small first slice direction: use existing
  append/event payload capacity, avoid state/control/riskguard schema edits, and
  keep cancel/redeem live wiring out of the first package.
- It explicitly flagged a policy boundary: current executor blocks exit on the
  same WS gap submit guard even though the WS module doc says exit/reconcile may
  continue. Phase 2C records current behavior rather than changing that policy.
- Subagent code review after implementation -> APPROVE. Reviewer found no
  concrete defects and independently verified focused Phase 2C tests, full
  executor command-split tests, py_compile, dedicated topology navigation,
  digest export, semantic linter, and diff-check.

Residual blockers:

- Cancel and redeem capability proofs remain future DSA-16 slices.
- Capability id is persisted in the command event payload, not a dedicated
  command/envelope schema column.
- Source degradation, market authority, and unified time freshness are not yet
  first-class components of the proof payload.
- Status summary exposure was not closed by Phase 2C; it is closed separately
  by Phase 2D below.

## Phase 2D Execution Capability Status Matrix - 2026-04-29

Scope:

- Close the derived-operator-visibility portion of DSA-16 by exposing one
  status-summary matrix for entry, exit, cancel, and redeem capability state.
- No executor edit, state/control/riskguard/risk-allocator edit, schema
  migration, production DB mutation, source routing, Paris config edit, live
  venue side effect, CLOB cutover, or live deployment authorization.

Implementation:

- Added the dedicated topology profile
  `phase 2d execution capability status summary implementation` and a digest
  regression so Phase 2D wording no longer routes to the broader R3 live
  readiness profile.
- `src/observability/status_summary.py` now writes a top-level
  `execution_capability` object with `schema_version=1`,
  `authority=derived_operator_visibility`, `derived_only=True`, and
  `live_action_authorized=False`.
- The matrix composes only public summary/status surfaces:
  CutoverGuard, HeartbeatSupervisor, WS gap guard, RiskAllocator summary, and
  CollateralLedger snapshot summary. It does not import
  `src.execution.executor` and does not call live venue methods.
- Entry and exit report global submit blockers plus unresolved per-intent
  requirements: risk capacity, collateral notional/inventory, executable
  snapshot gate, and replacement-sell context for exit.
- Cancel reports global cutover cancel readiness plus unresolved command/order
  identity and cancelability facts. Redeem reports global cutover redemption
  readiness plus unresolved payout-asset/FX-classification command facts.
- The matrix intentionally distinguishes global readiness from action
  authorization; it is a derived status output, not a source of runtime
  permission.

Verification run:

- `pytest -q -p no:cacheprovider tests/test_phase10b_dt_seam_cleanup.py::TestPhase2DExecutionCapabilityStatus tests/test_digest_profile_matching.py::test_phase2d_execution_capability_status_routes_to_observability_profile`
  -> 4 passed.
- `pytest -q -p no:cacheprovider tests/test_phase10b_dt_seam_cleanup.py::TestRCPV2RowCountSensor tests/test_phase10b_dt_seam_cleanup.py::TestPhase2DExecutionCapabilityStatus tests/test_digest_profile_matching.py::test_phase2d_execution_capability_status_routes_to_observability_profile tests/test_digest_profiles_equivalence.py`
  -> 15 passed.
- `python3 -m py_compile src/observability/status_summary.py tests/test_phase10b_dt_seam_cleanup.py tests/test_digest_profile_matching.py architecture/digest_profiles.py`
  -> pass.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `python3 scripts/semantic_linter.py --check ...` over touched status,
  tests, topology, digest, and packet evidence files -> pass.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 2D DSA-16 execution capability status summary matrix for entry exit cancel redeem; derived operator visibility only; no live venue side effects; no production DB mutation; no schema migration; no source routing; no Paris; no CLOB cutover" --files ...`
  -> navigation ok; profile `phase 2d execution capability status summary
  implementation`; admission status `admitted`.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md`
  -> pass before implementation.

Residual blockers:

- Cancel and redeem still need command-side capability proof slices before
  claiming full DSA-16 closure.
- Source degradation, market authority, and unified time freshness are not yet
  first-class capability components.
- Capability id remains event-payload evidence only, not an envelope/schema
  field.
- Independent reviewer initially returned REVISE because cancel could report
  `ready` from global cutover alone. Remediation added cancel
  `required_intent_components` for command identity and venue-order
  cancelability, so cancel now remains `requires_intent` even when
  `global_allow_cancel=True`.
- Independent re-review after remediation -> APPROVE. Reviewer confirmed the
  original cancel false-positive is closed, found no new authority drift or JSON
  shape issues, and independently verified focused Phase 2D tests, broader
  focused gates, py_compile, digest export, semantic linter, topology
  navigation, and the JSON shape check showing `cancel_status=requires_intent`
  with `live_action_authorized=false`.

## Phase 2E DSA-16 Cancel/Redeem Command-Side Capability Proof - 2026-04-30

Scope:

- Close the direct cancel/redeem command-side proof slice by putting capability
  evidence on the pre-side-effect event rows.
- No executor edit, venue adapter edit, state schema change, production DB
  mutation, source routing, Paris config edit, CLOB cutover, live venue side
  effect, or live deployment authorization.

Implementation:

- Added the dedicated topology profile
  `phase 2e cancel redeem capability proof implementation` plus a digest
  regression so Phase 2E wording routes to the narrow execution-proof slice.
- `src/execution/exit_safety.py` now builds a deterministic cancel
  `execution_capability` proof with `schema_version=1`, `action=CANCEL`,
  `intent_kind=CANCEL`, `mode=cancel`, `capability_id`, command id, venue
  order id, command state, freshness time, and components for CutoverGuard,
  command/order identity, and venue-order cancelability.
- `request_cancel_for_command()` appends the proof-bearing
  `CANCEL_REQUESTED` event before invoking the injected `cancel_order()`
  callable.
- A legacy or externally created `CANCEL_PENDING` row whose latest
  `CANCEL_REQUESTED` lacks a valid cancel proof now fails closed to
  `CANCEL_REPLACE_BLOCKED` with `missing_cancel_capability_proof` and does not
  invoke the cancel callable.
- Missing `venue_order_id` remains a no-side-effect unknown path; when it
  writes `CANCEL_REQUESTED`, the capability proof is blocked rather than
  allowed.
- `src/execution/settlement_commands.py` now builds a deterministic redeem
  `execution_capability` proof with `schema_version=1`, `action=REDEEM`,
  `intent_kind=REDEEM`, `mode=redeem`, `capability_id`, command id,
  condition id, market id, payout asset, freshness time, and components for
  submittable state, pUSD FX classification, and CutoverGuard redemption.
- `submit_redeem()` appends the proof-bearing `REDEEM_SUBMITTED` event before
  invoking `adapter.redeem()`. As before, caller-owned connections get a
  savepoint-visible event before adapter contact; own-connection mode commits
  before adapter contact.

Verification run:

- `python3 -m py_compile src/execution/exit_safety.py src/execution/settlement_commands.py tests/test_exit_safety.py tests/test_settlement_commands.py`
  -> pass.
- `pytest -q -p no:cacheprovider tests/test_exit_safety.py::test_cancel_requested_persists_execution_capability_before_cancel_callable tests/test_exit_safety.py::test_cancel_guard_blocks_before_cancel_callable_and_command_transition tests/test_exit_safety.py::test_cancel_network_timeout_creates_CANCEL_UNKNOWN tests/test_exit_safety.py::test_cancel_pending_without_capability_fails_closed_without_duplicate_request tests/test_settlement_commands.py::test_redeem_submitted_persists_execution_capability_before_adapter_contact tests/test_settlement_commands.py::test_redeem_submit_blocks_before_adapter_when_cutover_disallows tests/test_settlement_commands.py::test_redeem_blocked_until_q_fx_1_classified`
  -> 7 passed.
- `pytest -q -p no:cacheprovider tests/test_exit_safety.py tests/test_settlement_commands.py`
  -> 22 passed.
- `pytest -q -p no:cacheprovider tests/test_exit_safety.py tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_phase2e_cancel_redeem_capability_routes_to_dedicated_profile tests/test_digest_profiles_equivalence.py`
  -> 27 passed.
- `python3 scripts/digest_profiles_export.py --check`
  -> OK.
- `python3 scripts/semantic_linter.py --check ...` over Phase 2E source,
  tests, topology, digest, and packet evidence files -> pass.
- `git diff --check -- ...` over Phase 2E source, tests, topology, digest,
  operations registry, and packet evidence files -> pass.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 2E DSA-16 cancel redeem command-side capability proof payload; docs operations registry companion; no live venue side effects; no production DB mutation; no schema migration; no source routing; no Paris; no CLOB cutover" --files ...`
  -> navigation ok; profile `phase 2e cancel redeem capability proof
  implementation`; admission status `admitted`.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md`
  -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files ...`
  -> pass after registering the packet evidence files in
  `docs/operations/AGENTS.md`.
- Independent final reviewer initially returned one MEDIUM routing finding:
  realistic wording such as `CANCEL_REQUESTED in request_cancel_for_command`
  and `REDEEM_SUBMITTED in submit_redeem` could still select the broader Phase
  2C entry/exit submit proof profile. Remediation added those concrete seam
  phrases as Phase 2E strong phrases, added matching Phase 2C negative
  phrases, regenerated `architecture/digest_profiles.py`, and added
  `tests/test_digest_profile_matching.py::test_phase2e_realistic_seam_wording_routes_to_dedicated_profile`.
- Post-remediation verifier verdict -> PASS. Verifier confirmed
  `architecture/topology.yaml` and regenerated `architecture/digest_profiles.py`
  contain the concrete Phase 2E seam phrases, Phase 2C excludes that wording,
  and the realistic-wording regression routes to
  `phase 2e cancel redeem capability proof implementation`.
- Final local post-remediation gates:
  `python3 -m py_compile ...` over Phase 2E source/tests/digest -> pass;
  `pytest -q -p no:cacheprovider tests/test_exit_safety.py tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_phase2e_cancel_redeem_capability_routes_to_dedicated_profile tests/test_digest_profile_matching.py::test_phase2e_realistic_seam_wording_routes_to_dedicated_profile tests/test_digest_profiles_equivalence.py`
  -> 28 passed; `python3 scripts/digest_profiles_export.py --check` -> OK;
  `python3 scripts/semantic_linter.py --check ...` -> pass;
  `git diff --check -- ...` -> pass; topology navigation/planning-lock/map
  closeout for the Phase 2E changed-file set -> pass.

Residual blockers:

- This does not claim every historical or side-effect-free `CANCEL_REQUESTED`
  writer now emits a proof; the Phase 2E live side-effect seam is
  `request_cancel_for_command()`.
- Source degradation, market authority, and unified time freshness are not yet
  first-class capability components.
- Capability id remains event-payload evidence only, not a schema/envelope
  column.
- RED-force side-effect-free cancel-request event normalization remains a
  later cleanup slice if the project wants all derived events to share the same
  payload shape.
- Independent sidecar reviewer flagged these exact boundaries and found no
  need to widen into executor/venue/state/schema/source work.

## Paris Settlement Source Boundary Evidence - 2026-04-29

Scope:

- Read-only source-truth confirmation for F07. No `config/cities.json` edit,
  no production DB mutation, no observation/calibration rebuild, and no Paris
  live-candidate unquarantine in this slice.

Authority reads:

- `config/AGENTS.md` says current Polymarket market description wins over
  config, WU mirrors, and code constants for `cities.json` source routing.
- `architecture/city_truth_contract.yaml` requires volatile city/source facts
  to carry freshness, evidence references, date ranges, and current-fact
  surfaces.
- `architecture/fatal_misreads.yaml` warns that endpoint liveness or airport
  station availability is not proof of settlement source correctness.

Gamma evidence:

- Local read-only Gamma sweep with browser-like user agent across
  `daily-temperature`, `temperature`, and `weather` tag ids found `77` Paris
  temperature events.
- Station split from the sweep: `LFPG` on `63` events, first observed
  2026-02-11 and latest observed 2026-04-18; `LFPB` on `14` events, first
  observed 2026-04-19 and latest observed 2026-05-01.
- Direct slug sweep for `highest-temperature-in-paris-on-april-15-2026` through
  `highest-temperature-in-paris-on-may-1-2026` found HIGH Apr 15-18 resolving
  on `LFPG`, then HIGH Apr 19-May 1 resolving on `LFPB`.
- Direct LOW slug sweep for Apr 15-22 found no matching Paris LOW slugs; LOW
  Apr 23-May 1 resolved on `LFPB`.
- Independent subagent confirmation reached the same boundary: latest observed
  `LFPG` was Paris HIGH on 2026-04-18, earliest observed `LFPB` was Paris HIGH
  on 2026-04-19, and LOW cannot be proven before its first observable Paris LOW
  event on 2026-04-23.

Independent subagent dated evidence captured on 2026-04-30 UTC:

| Market date | Family | resolutionSource | Event / slug | Fetched at UTC | Gamma endpoint |
| --- | --- | --- | --- | --- | --- |
| 2026-04-18 | HIGH | `https://www.wunderground.com/history/daily/fr/paris/LFPG` | `384013` / `highest-temperature-in-paris-on-april-18-2026` | 2026-04-30T00:23:36Z | `https://gamma-api.polymarket.com/events?slug=highest-temperature-in-paris-on-april-18-2026&closed=true&limit=100` |
| 2026-04-19 | HIGH | `https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB` | `387419` / `highest-temperature-in-paris-on-april-19-2026` | 2026-04-30T00:23:36Z | `https://gamma-api.polymarket.com/events?slug=highest-temperature-in-paris-on-april-19-2026&closed=true&limit=100` |
| 2026-04-23 | LOW | `https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB` | `404339` / `lowest-temperature-in-paris-on-april-23-2026` | 2026-04-30T00:24:00Z | `https://gamma-api.polymarket.com/events?slug=lowest-temperature-in-paris-on-april-23-2026&closed=true&limit=100` |
| 2026-04-24 | LOW | `https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB` | `405161` / `lowest-temperature-in-paris-on-april-24-2026` | 2026-04-30T00:24:00Z | `https://gamma-api.polymarket.com/events?slug=lowest-temperature-in-paris-on-april-24-2026&closed=true&limit=100` |
| 2026-04-25 | LOW | `https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB` | `408549` / `lowest-temperature-in-paris-on-april-25-2026` | 2026-04-30T00:24:00Z | `https://gamma-api.polymarket.com/events?slug=lowest-temperature-in-paris-on-april-25-2026&closed=true&limit=100` |
| 2026-04-26 | LOW | `https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB` | `411948` / `lowest-temperature-in-paris-on-april-26-2026` | 2026-04-30T00:24:00Z | `https://gamma-api.polymarket.com/events?slug=lowest-temperature-in-paris-on-april-26-2026&closed=true&limit=100` |
| 2026-04-27 | LOW | `https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB` | `415403` / `lowest-temperature-in-paris-on-april-27-2026` | 2026-04-30T00:24:00Z | `https://gamma-api.polymarket.com/events?slug=lowest-temperature-in-paris-on-april-27-2026&closed=true&limit=100` |
| 2026-04-29 | HIGH / LOW | `https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB` | HIGH `422449` / `highest-temperature-in-paris-on-april-29-2026`; LOW `422416` / `lowest-temperature-in-paris-on-april-29-2026` | 2026-04-30T00:23:36Z | `https://gamma-api.polymarket.com/events?slug=highest-temperature-in-paris-on-april-29-2026&closed=false&limit=100`; `https://gamma-api.polymarket.com/events?slug=lowest-temperature-in-paris-on-april-29-2026&closed=false&limit=100` |
| 2026-04-30 | HIGH / LOW | `https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB` | HIGH `426227` / `highest-temperature-in-paris-on-april-30-2026`; LOW `426177` / `lowest-temperature-in-paris-on-april-30-2026` | 2026-04-30T00:23:36Z | `https://gamma-api.polymarket.com/events?slug=highest-temperature-in-paris-on-april-30-2026&closed=false&limit=100`; `https://gamma-api.polymarket.com/events?slug=lowest-temperature-in-paris-on-april-30-2026&closed=false&limit=100` |
| 2026-05-01 | HIGH / LOW | `https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB` | HIGH `429698` / `highest-temperature-in-paris-on-may-1-2026`; LOW `429671` / `lowest-temperature-in-paris-on-may-1-2026` | 2026-04-30T00:23:36Z | `https://gamma-api.polymarket.com/events?slug=highest-temperature-in-paris-on-may-1-2026&closed=false&limit=100`; `https://gamma-api.polymarket.com/events?slug=lowest-temperature-in-paris-on-may-1-2026&closed=false&limit=100` |

Evidence classification:

- Direct evidence: the Gamma API responses listed above.
- Corroborating evidence: this packet's broad Gamma sweep, direct slug sweep,
  `docs/operations/known_gaps.md`, and legacy source-provenance report notes.
- Inference: the observable HIGH switch occurred between 2026-04-18 and
  2026-04-19. LOW before 2026-04-23 remains an unknown gap because the current
  Gamma surface did not expose Paris LOW slugs for 2026-04-15 through
  2026-04-22.

Conclusion:

- Evidence is sufficient to open a dated source-routing repair packet and keep
  Paris live candidates quarantined unless market resolutionSource matches the
  configured/dated route.
- Evidence is not sufficient for a blind global Paris config flip because
  historical HIGH contracts used `LFPG` through 2026-04-18 while current/future
  HIGH uses `LFPB` from 2026-04-19 onward, and LOW has no observed Paris events
  before 2026-04-23 in this probe.
- Safer repair path is date/family-aware source routing plus quarantine and
  rebuild of affected Paris observations/calibration rows.

## F4 Status Summary V2 Row-Count Repair - 2026-04-29

Scope:

- Fix derived observability only: `status_summary.py::_get_v2_row_counts()` must
  not report empty trade shadow v2 tables when the attached `world` DB contains
  the data-authority table.
- No canonical truth writes, no production DB mutation, no backfill, no source
  routing change, no economics readiness promotion, and no live venue side
  effects.

Implementation:

- Added `observability status summary v2 world truth implementation` topology
  profile so F4 routes to `src/observability/status_summary.py` and
  `tests/test_phase10b_dt_seam_cleanup.py` instead of the executable snapshot
  profile.
- `_get_v2_row_counts()` now uses explicit schema qualification. Current v2
  data tables prefer `world` when attached and the table exists, then fall back
  to `main` only if the world table is absent.
- The row signal is bounded for cycle-path latency: rowid tables use
  `MAX(rowid)` instead of full-table `COUNT(*)`, and rowid-less fallback uses
  a non-empty sentinel. This is derived status telemetry, not promotion-grade
  data cardinality truth.
- Missing tables remain nonblocking and return `0`, preserving status-summary
  write resilience.
- The fix is table-role aware through `_V2_ROW_COUNT_SCHEMA_PREFERENCE`; it is
  not a blanket unqualified query and not a promotion of status JSON into
  canonical truth.

Tests and verification:

- `tests/test_phase10b_dt_seam_cleanup.py::TestRCPV2RowCountSensor` now covers
  the exact false-alarm shape for all five `_V2_TABLES`: empty main/trade v2
  tables plus populated attached `world` tables report the world counts.
- Pair-negative coverage proves that if world v2 tables exist but are empty,
  they remain the reported authority instead of falling back to populated trade
  shadow rows.
- Latency guard coverage traces the executed SQL and fails if
  `_get_v2_row_counts()` uses `COUNT(*)`, preventing per-cycle full scans of
  large v2 tables.
- `pytest -q -p no:cacheprovider tests/test_phase10b_dt_seam_cleanup.py::TestRCPV2RowCountSensor` -> 7 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_f4_status_summary_world_v2_row_counts_routes_to_observability_profile tests/test_digest_profile_matching.py::test_f4_status_summary_plus_paris_boundary_evidence_routes_to_observability_profile tests/test_digest_profile_matching.py::test_paris_source_boundary_evidence_routes_to_docs_only_profile tests/test_digest_profiles_equivalence.py` -> 15 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py` -> 85 passed.
- `python3 scripts/digest_profiles_export.py --check` -> OK.
- `python3 -m py_compile src/observability/status_summary.py tests/test_phase10b_dt_seam_cleanup.py` -> pass.
- Read-only local DB probe using `ZEUS_MODE=live`, read-only `state/zeus_trades.db`
  connection, and read-only attached `state/zeus-world.db` returned
  `ensemble_snapshots_v2=684624` and `calibration_pairs_v2=40418974` instead
  of the prior empty trade-shadow count. `historical_forecasts_v2`,
  `platt_models_v2`, and `settlements_v2` remain `0`.
- Reviewer REVISE on first repair found exact `COUNT(*)` over
  `world.calibration_pairs_v2` took ~23s on the current local DB. Remediation
  changed the sensor to bounded rowid high-water reads. The same read-only DB
  probe now returns the same nonzero world values in `0.001249s`.
- Reviewer REVISE also found the combined wording `F4 status_summary v2
  row-count fix and Paris source-boundary evidence recording` misrouted. The
  topology now has explicit combined F4 wording plus a docs-only
  `source boundary evidence recording` profile for Paris evidence that forbids
  `config/cities.json`, `current_source_validity.md`, production DB mutation,
  backfill, and source-routing implementation.

Residual blockers:

- This closes the F10/F4 derived-status false alarm only.
- It does not resolve the broader DSA-13 legacy/v2 snapshot authority split in
  evaluator live writes and replay/learning consumers.

## Phase 1D Forecast Source Policy Gate - 2026-04-29

Scope:

- Close the first DSA-01 safety slice: Open-Meteo live ENS must not be an
  implicit normal entry-primary source.
- No TIGGE activation, no external TIGGE HTTP/GRIB fetch, no production DB
  mutation, no Paris/source-routing change, no calibration retrain, no live
  venue side effect, and no degraded-entry capital policy in this slice.

Current upstream documentation check:

- Open-Meteo official Ensemble API docs list ECMWF IFS 0.25 ensemble as a 51
  member ensemble with 25 km resolution and six-hour update frequency, with
  data interpolated to one-hourly time steps
  (`https://open-meteo.com/en/docs/ensemble-api`).
- Open-Meteo official ECMWF docs say Open-Meteo dynamically interpolates ECMWF
  data to one-hourly time-series and distinguish IFS HRES/open-data timing and
  delay behavior. This confirms provider distribution/latency semantics are
  not identical to direct TIGGE/ECMWF payload authority
  (`https://open-meteo.com/en/docs/ecmwf-api`).

Implementation:

- `ForecastSourceSpec` now carries `allowed_roles` and `degradation_level`.
- `openmeteo_ensemble_ecmwf_ifs025` and `openmeteo_ensemble_gfs025` are
  explicitly `DEGRADED_FORECAST_FALLBACK`, allowed for `monitor_fallback` and
  `diagnostic`, not `entry_primary`; the ECMWF live ensemble row is no longer
  `tier="primary"`.
- `tigge` remains operator-gated and disabled by default, but is the registered
  `entry_primary` source role when a future packet supplies operator evidence
  and env authorization. This slice does not activate it.
- `fetch_ensemble()` accepts a role parameter defaulting to `entry_primary`.
  Open-Meteo is therefore impossible to use by omitted model/role arguments; a
  caller must explicitly request `monitor_fallback` or `diagnostic`.
- The ensemble cache key includes role, preventing a monitor fallback result
  from being reused as diagnostic or entry-primary evidence.
- Open-Meteo results stamp `degradation_level` and `forecast_source_role`; TIGGE
  registered-ingest results stamp the requested role without activating TIGGE.
- `evaluate_candidate()` requests `role="entry_primary"` and fail-closes on
  `SourceNotEnabled` before any probability-vector construction. It also keeps
  the degraded-payload defense-in-depth gate for any fallback payload that
  reaches evaluator unexpectedly, recording `forecast_source_policy` as an
  applied validation and `DATA_STALE` as availability.
- Monitor refresh explicitly requests `role="monitor_fallback"` for held
  positions and Day0 refresh; GFS crosscheck explicitly requests
  `role="diagnostic"`.
- Successful monitor refresh applied validations now include
  `forecast_source_id:*`, `forecast_source_role:*`, and
  `forecast_degradation:*` so exit review can distinguish degraded fallback
  evidence from primary-quality refresh evidence.

Tests and verification:

- `tests/test_forecast_source_registry.py` proves Open-Meteo live ENS is
  monitor fallback, not entry primary, and `gate_source_role()` refuses
  `entry_primary` for it.
- `tests/test_ensemble_client.py` proves normal Open-Meteo fetch results carry
  `DEGRADED_FORECAST_FALLBACK` and `monitor_fallback`, and requesting
  `entry_primary` role fails before network equivalence is assumed. It also
  proves cache keys are role-specific.
- `tests/test_runtime_guards.py::test_openmeteo_degraded_forecast_fallback_blocks_entry_before_vector`
  proves evaluator entry rejects degraded Open-Meteo fallback before p_raw,
  preserving pre-vector traceability.
- `tests/test_runtime_guards.py::test_entry_primary_source_policy_exception_blocks_entry_before_vector`
  proves evaluator entry records `forecast_source_policy` when the source-role
  gate blocks `entry_primary`.
- `tests/test_runtime_guards.py::test_monitor_ens_refresh_records_forecast_fallback_provenance`
  and `tests/test_runtime_guards.py::test_day0_monitor_refresh_records_forecast_fallback_provenance`
  prove both monitor refresh lanes expose fallback source provenance.
- `tests/test_digest_profile_matching.py::test_phase1d_forecast_source_policy_routes_to_source_policy_profile`
  proves the Phase 1D wording routes to the dedicated source-policy profile
  rather than executable-snapshot work.
- The Phase 1D topology profile now requires the focused evaluator/monitor
  runtime guard tests as closeout gates.
- `pytest -q -p no:cacheprovider tests/test_forecast_source_registry.py tests/test_ensemble_client.py tests/test_runtime_guards.py::test_openmeteo_degraded_forecast_fallback_blocks_entry_before_vector tests/test_runtime_guards.py::test_entry_primary_source_policy_exception_blocks_entry_before_vector tests/test_runtime_guards.py::test_monitor_ens_refresh_records_forecast_fallback_provenance tests/test_runtime_guards.py::test_day0_monitor_refresh_records_forecast_fallback_provenance tests/test_runtime_guards.py::test_ens_validation_failure_is_pre_vector_traceable tests/test_digest_profile_matching.py::test_phase1d_forecast_source_policy_routes_to_source_policy_profile tests/test_digest_profiles_equivalence.py` -> 26 passed.
- `python3 -m py_compile src/data/forecast_source_registry.py src/data/ensemble_client.py src/engine/evaluator.py src/engine/monitor_refresh.py tests/test_forecast_source_registry.py tests/test_ensemble_client.py tests/test_runtime_guards.py tests/test_digest_profile_matching.py architecture/digest_profiles.py` -> pass.

Residual blockers:

- There is still no activated direct TIGGE/ECMWF primary live ingest in this
  slice, so live entries remain blocked by source policy unless a future
  operator-approved primary source is provided.
- The broader DSA-02/DSA-03 work remains open: settings-driven source selection,
  provider/model-family separation in bias keys, and crosscheck policy are not
  fully normalized yet.

## Phase 1E Forecast Source Selection And Provider Identity - 2026-04-29

Scope:

- Close DSA-02/DSA-03 for the live evaluator and monitor probability paths:
  ensemble `primary`/`crosscheck` settings must control runtime model
  selection, and provider-specific forecast source identity must not collapse
  into broad model-family identity before bias lookup.
- No TIGGE activation, no direct ECMWF/TIGGE network fetch, no production DB
  mutation, no Paris/source-routing change, no calibration retrain, no source
  equivalence map, no live venue side effect, and no degraded-entry capital
  policy in this slice.

Implementation:

- `src/config.py` now exposes strict `ensemble_primary_model()` and
  `ensemble_crosscheck_model()` accessors. Missing keys still raise through the
  strict settings contract, and empty values raise `ValueError`.
- `src/engine/evaluator.py` requests entry ENS with
  `model=ensemble_primary_model()` and `role="entry_primary"`.
- `src/engine/evaluator.py` requests crosscheck ENS with
  `model=ensemble_crosscheck_model()` and `role="diagnostic"`; diagnostics and
  rejection reasons name the configured crosscheck model rather than assuming
  GFS.
- `src/engine/monitor_refresh.py` requests held-position ENS refresh and Day0
  ENS refresh with `model=ensemble_primary_model()` and
  `role="monitor_fallback"`, preserving the Phase 1D fallback provenance
  evidence.
- `src/engine/evaluator.py` splits broad `model_family` from provider-specific
  `forecast_source_id`. Bias lookup and `MarketAnalysis.forecast_source` now
  use `ens_result["source_id"]` when present, while `model_family` remains in
  forecast context for audit and diagnostics.
- The topology source-policy profile now covers Phase 1E wording, admits
  `src/config.py`, forbids direct `config/settings.json` edits for this packet,
  and requires the Phase 1E runtime guard tests.

Tests and verification:

- `tests/test_runtime_guards.py::test_evaluator_uses_configured_primary_and_crosscheck_models`
  mutates `settings["ensemble"]["primary"]` to `tigge` and
  `settings["ensemble"]["crosscheck"]` to `gfs025`, then proves evaluator fetch
  calls use `tigge` for `entry_primary` and `gfs025` for `diagnostic`. The same
  regression forces a crosscheck conflict and proves the rejection reason is
  `tigge/gfs025 CONFLICT`, not the old hardcoded `ECMWF/GFS CONFLICT`.
- `tests/test_runtime_guards.py::test_monitor_ens_refresh_records_forecast_fallback_provenance`
  and `tests/test_runtime_guards.py::test_day0_monitor_refresh_records_forecast_fallback_provenance`
  mutate `settings["ensemble"]["primary"]` and prove both monitor lanes pass the
  configured model while still recording fallback source/role/degradation
  evidence.
- `tests/test_runtime_guards.py::test_forecast_provider_identity_uses_source_id_not_model_family`
  inserts conflicting `model_bias` rows for `ecmwf` and
  `openmeteo_ensemble_ecmwf_ifs025`, then proves the provider-specific
  `source_id` row is selected.
- `tests/test_digest_profile_matching.py::test_phase1e_forecast_source_selection_routes_to_source_policy_profile`
  proves Phase 1E wording routes to the source-policy profile rather than an
  unrelated live-readiness profile.
- `pytest -q -p no:cacheprovider tests/test_forecast_source_registry.py tests/test_ensemble_client.py tests/test_runtime_guards.py::test_openmeteo_degraded_forecast_fallback_blocks_entry_before_vector tests/test_runtime_guards.py::test_entry_primary_source_policy_exception_blocks_entry_before_vector tests/test_runtime_guards.py::test_monitor_ens_refresh_records_forecast_fallback_provenance tests/test_runtime_guards.py::test_day0_monitor_refresh_records_forecast_fallback_provenance tests/test_runtime_guards.py::test_evaluator_uses_configured_primary_and_crosscheck_models tests/test_runtime_guards.py::test_forecast_provider_identity_uses_source_id_not_model_family tests/test_runtime_guards.py::test_ens_validation_failure_is_pre_vector_traceable tests/test_digest_profile_matching.py::test_phase1d_forecast_source_policy_routes_to_source_policy_profile tests/test_digest_profile_matching.py::test_phase1e_forecast_source_selection_routes_to_source_policy_profile tests/test_digest_profiles_equivalence.py`
  -> 29 passed.
- `python3 -m py_compile src/config.py src/data/forecast_source_registry.py src/data/ensemble_client.py src/engine/evaluator.py src/engine/monitor_refresh.py tests/test_forecast_source_registry.py tests/test_ensemble_client.py tests/test_runtime_guards.py tests/test_digest_profile_matching.py architecture/digest_profiles.py`
  -> pass.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `python3 scripts/semantic_linter.py --check ...` over the touched source,
  tests, and packet docs -> pass.
- `python3 scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md`
  -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout ...`
  -> pass when run with the required docs companion files in the checked scope,
  including `docs/operations/AGENTS.md`, `docs/README.md`, and
  `architecture/docs_registry.yaml`.
- `git diff --check -- ...` over the touched source, tests, topology, and
  packet docs -> pass.

Scope note:

- `config/settings.json` is explicitly forbidden by the Phase 1 source-policy
  topology profile and was not part of the Phase 1E admissible closeout scope.
  The current dirty worktree contains an unrelated `n_mc`/`day0.n_mc` runtime
  precision change in that file; this Phase 1E evidence neither validates nor
  reverts that separate change.

Residual blockers:

- Direct TIGGE/ECMWF primary live ingest remains operator-gated and inactive, so
  Phase 1E does not authorize live entry by itself.
- Phase 1E separates provider identity for live evaluator bias lookup, but the
  broader source/time-causality program still must persist full source timing,
  payload hash, issue-time status, and source degradation/capability facts
  through every downstream evidence surface.

## Phase 1F ECMWF Open Data Scheduled Collector Policy - 2026-04-29

Scope:

- Close DSA-04 by making the live-scheduled ECMWF Open Data collector explicit
  source-policy inventory, without promoting it to live primary forecast
  authority.
- No TIGGE activation, no direct TIGGE implementation, no production DB
  mutation, no Paris/source-routing change, no calibration retrain, no live
  venue side effect, and no selection of ECMWF Open Data as canonical primary
  provider in this slice.

Implementation:

- `src/data/forecast_source_registry.py` now registers `ecmwf_open_data` as
  `kind="scheduled_collector"`, `allowed_roles=("diagnostic",)`, and
  `degradation_level="DIAGNOSTIC_NON_EXECUTABLE"`.
- `src/data/ecmwf_open_data.py` gates `collect_open_ens_cycle()` through
  `gate_source("ecmwf_open_data")` plus `gate_source_role(..., "diagnostic")`
  before download/extract execution.
- `src/data/ecmwf_open_data.py` writes mirrored legacy `ensemble_snapshots`
  rows with `authority="UNVERIFIED"` so scheduled diagnostic rows cannot
  masquerade as verified executable live signal evidence through the legacy
  table default.
- `src/main.py` still schedules the collector, but the scheduled job is now
  policy-owned and diagnostic/non-executable by registry contract.
- The Phase 1 topology profile now covers Phase 1F wording, admits
  `src/main.py` and `src/data/ecmwf_open_data.py`, and requires the Phase 1F
  runtime guard tests.

Tests and verification:

- `tests/test_runtime_guards.py::test_ecmwf_open_data_collector_marks_rows_unverified_non_executable`
  proves the collector result carries `source_id="ecmwf_open_data"`,
  `forecast_source_role="diagnostic"`,
  `degradation_level="DIAGNOSTIC_NON_EXECUTABLE"`, writes rows with
  `authority="UNVERIFIED"`, and rejects `entry_primary` for that registry row.
- `tests/test_runtime_guards.py::test_main_registers_only_policy_owned_ecmwf_open_data_jobs`
  proves the daemon still registers ECMWF Open Data jobs only after asserting
  the source registry row is diagnostic and non-executable.
- `tests/test_digest_profile_matching.py::test_phase1f_ecmwf_open_data_routes_to_source_policy_profile`
  proves Phase 1F wording routes to the source-policy topology profile.

Residual blockers:

- This is a conservative diagnostic/non-executable closure. It does not decide
  whether ECMWF Open Data should become a future canonical primary provider.
- The broader DSA-13/DSA-18 snapshot/time-causality work remains open: legacy
  `ensemble_snapshots` is still a compatibility surface, not the final
  canonical live decision snapshot contract.

## Phase 1G Forecast-History Provenance Eligibility - 2026-04-29

Scope:

- Close the narrow DSA-06 leak where migrated legacy `forecasts` tables could
  admit `openmeteo_previous_runs` rows with `availability_provenance IS NULL`
  into replay/skill ETL through the compatibility predicate.
- No production DB mutation, no source-routing change, no live previous-runs
  collection change, no replay-purpose redesign, no economics promotion, and no
  live venue side effect in this slice.

Implementation:

- `src/engine/replay.py` still falls back to the no-provenance legacy query
  when the `availability_provenance` column is absent, preserving true pre-F11
  diagnostic compatibility.
- When the `availability_provenance` column exists, `src/engine/replay.py`
  now accepts either `SKILL_ELIGIBLE_SQL` or a NULL-provenance row whose
  `source != 'openmeteo_previous_runs'`.
- `scripts/etl_historical_forecasts.py` and
  `scripts/etl_forecast_skill_from_forecasts.py` apply the same migrated-schema
  predicate so skill ETL cannot materialize NULL-provenance Open-Meteo
  previous-runs rows.

Tests and verification:

- `tests/test_replay_skill_eligibility_filter.py` now seeds both an
  Open-Meteo NULL-provenance row and a non-Open-Meteo NULL-provenance row. It
  proves Open-Meteo is excluded for both RECONSTRUCTED and NULL provenance,
  while the non-Open-Meteo NULL legacy row remains tolerated.
- `tests/test_etl_skill_eligibility_filter.py` mirrors the same predicate in
  the historical-forecasts ETL and forecast-skill ETL SELECT shapes.
- `pytest -q -p no:cacheprovider tests/test_replay_skill_eligibility_filter.py tests/test_etl_skill_eligibility_filter.py`
  -> 6 passed.

Residual blockers:

- This is not a full replay-purpose rewrite. Non-audit replay still has broader
  snapshot-only diagnostic behavior that belongs to later DSA-18/DSA-19 work.
- Existing production DB rows are not mutated; the repair makes weak
  Open-Meteo rows inert at read time for the scoped replay/skill ETL consumers.

## Phase 1H Non-Live Execution Residue Cleanup - 2026-04-29

Scope:

- Close DSA-07 by removing the production non-live execution branch from
  `src/engine/monitor_refresh.py`.
- No live venue side effects, no production DB mutation, no executor/venue
  adapter edits, no source routing change, no Paris config edit, no schema
  migration, and no broad benchmark evidence-class rename in this slice.

Implementation:

- `src/engine/monitor_refresh.py` no longer imports
  `get_current_yes_price`.
- Held-position monitor pricing now always selects the executable side token
  (`token_id` for `buy_yes`, `no_token_id` for `buy_no`) and calls
  `clob.get_best_bid_ask()`.
- `day0_window` positions continue to use realizable best bid; non-Day0
  positions continue to use VWMP.
- Missing token or quote failure leaves `last_monitor_market_price_is_fresh`
  false, preserving fail-closed exit-authority behavior.
- Monitor-refresh tests now use live-shaped fake CLOB quotes rather than Gamma
  current-price patches. The buy-NO bootstrap case uses a native NO-token quote
  instead of reconstructing a price from a retired shortcut.
- `tests/test_runtime_guards.py` scans production `src/engine` and `src/execution`
  Python files for non-live execution branch tokens.

Verification:

- `pytest -q -p no:cacheprovider` over the monitor-refresh branch scanner,
  bootstrap symmetry, Day0 monitor, PnL monitor, pre-live integration,
  K1 monitor ENS, digest profile matching, and digest equivalence tests
  -> 18 passed.
- `python3 -m py_compile src/engine/monitor_refresh.py tests/test_runtime_guards.py tests/test_bootstrap_symmetry.py tests/test_live_safety_invariants.py tests/test_pnl_flow_and_audit.py tests/test_pre_live_integration.py tests/test_k1_review_fixes.py tests/test_digest_profile_matching.py architecture/digest_profiles.py`
  -> pass.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- Production scan for non-live execution branch tokens and `get_current_yes_price`
  under `src/engine` and `src/execution` -> no matches.

Residual blockers:

- Broader simulated/shadow naming in strategy benchmark and skipped legacy tests is
  still evidence-class cleanup, not production monitor branching.
- This slice does not edit live adapter/executor semantics or authorize live
  cutover.

## Phase 1I Strategy Benchmark Evidence-Grade Naming Cleanup - 2026-04-29

Scope:

- Close the public-concept portion of DSA-08/DSA-17 by removing simulated/shadow
  runtime-mode names from `StrategyBenchmarkSuite` promotion concepts.
- No production DB mutation, no existing benchmark table migration, no live
  venue side effects, no CLOB cutover, and no live strategy promotion in this
  slice.

Implementation:

- `obsolete simulated-venue benchmark enum`, `BenchmarkEnvironment.SHADOW`, and
  `BenchmarkEnvironment.LIVE` were renamed to `SIMULATED_VENUE`,
  `READ_ONLY_LIVE`, and `PROMOTION_GRADE_ECONOMICS`.
- `obsolete simulated-venue wrapper` and `evaluate_live_shadow()` compatibility wrappers were
  removed; callers must use `evaluate_simulated_venue()` and
  `evaluate_read_only_live()`.
- `StrategyBenchmarkSuite.__init__()` now accepts `read_only_live_corpora`, not
  `shadow_corpora`.
- `promotion_decision()` validates `EvidenceGrade` contracts rather than
  requiring legacy environment labels. This prevents runtime-mode labels from
  being promotion authority.
- Existing `strategy_benchmark_runs.environment` storage values are not migrated
  in this slice; the old string labels remain compatibility provenance only.
- Reviewer remediation: `ensure_schema()` no longer silently `ALTER TABLE`s an
  existing legacy `strategy_benchmark_runs` table missing `evidence_grade`.
  It now fails closed and requires a separate explicit migration packet.

Verification:

- `pytest -q -p no:cacheprovider tests/test_fake_polymarket_venue.py tests/test_strategy_benchmark.py tests/test_digest_profile_matching.py::test_dsa08_dsa17_evidence_grade_cleanup_routes_to_a1_profile tests/test_digest_profile_matching.py::test_r3_a1_strategy_benchmark_routes_to_a1_profile_not_heartbeat tests/test_digest_profiles_equivalence.py`
  -> 24 passed.
- `tests/test_strategy_benchmark.py::test_strategy_benchmark_runs_missing_evidence_grade_fails_closed_without_migration`
  proves legacy tables are not silently migrated by this slice.
- `python3 -m py_compile src/strategy/benchmark_suite.py src/strategy/data_lake.py src/strategy/candidates/__init__.py tests/test_strategy_benchmark.py tests/test_digest_profile_matching.py architecture/digest_profiles.py`
  -> pass.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `python3 scripts/semantic_linter.py --check ...` over the touched source,
  tests, topology, digest, and packet evidence files -> pass.
- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A1`
  -> GREEN.
- `python3 scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md`
  -> pass.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout ...`
  -> pass.
- Historical exact symbol scan redacted in this active archive copy; it covered obsolete simulated-venue benchmark enums, shadow benchmark enums, replay/simulated evidence combinations, and compatibility wrappers in `src/strategy/benchmark_suite.py`, `tests/test_strategy_benchmark.py`, and `docs/reference/modules/strategy.md`.
  -> no matches.

Residual blockers:

- `shadow_signals` remains a legacy diagnostic/replay table name and belongs to
  DSA-10/DSA-18, not this benchmark-concept cleanup.
- Existing benchmark DB rows are not migrated; if a future operator wants clean
  storage values instead of compatibility labels, that requires a separate DB
  migration packet.

## Phase 1K Live Decision Snapshot Causality Gate - 2026-04-30

Scope:

- Close the executable-entry side of DSA-05/DSA-13/DSA-18 where an ENS result
  could proceed toward snapshot, calibration, edge, and sizing without explicit
  source/timing/payload/degradation evidence.
- No TIGGE activation, no Open-Meteo promotion, no source routing change, no
  Paris config edit, no production DB mutation, no schema migration, and no
  live venue side effect.

Implementation:

- `src/engine/evaluator.py` now validates executable-entry forecast evidence
  immediately after ENS fetch validation and degraded-source policy checks, but
  before `EnsembleSignal`, snapshot persistence, calibration, FDR, and sizing.
- The entry evidence gate requires explicit `source_id`, `model`,
  `raw_payload_hash`, `authority_tier`, `degradation_level`,
  `forecast_source_role`, `issue_time`, `valid_time` or `first_valid_time`,
  `fetch_time`, and effective `available_at` (falling back to fetch time when
  the upstream result does not expose a separate available-at field).
- The gate requires `forecast_source_role="entry_primary"`,
  `degradation_level="OK"`, and `authority_tier="FORECAST"` for executable
  entries. Monitor/diagnostic fallback may remain weaker, but cannot pass this
  entry path.
- Accepted decisions now persist the source evidence in
  `epistemic_context_json.forecast_context`: forecast source id, model family,
  issue/valid/fetch/available times, raw payload hash, degradation level, role,
  authority tier, and decision-time status.
- The Phase 1 topology profile now admits realistic DSA-05/DSA-13/DSA-18 live
  decision snapshot causality wording so the evaluator/source-policy package no
  longer misroutes to Phase 2C execution capability.

Verification:

- `pytest -q -p no:cacheprovider tests/test_center_buy_repair.py tests/test_ensemble_client.py tests/test_forecast_source_registry.py tests/test_digest_profile_matching.py::test_phase1k_live_decision_snapshot_causality_routes_to_source_policy_profile`
  -> 22 passed.
- `pytest -q -p no:cacheprovider tests/test_runtime_guards.py` -> 139 passed.
- `pytest -q -p no:cacheprovider tests/test_fdr.py::TestSelectionFamilySubstrate::test_evaluate_candidate_materializes_selection_facts tests/test_execution_price.py::TestEvaluatorWiring::test_evaluator_always_uses_fee_adjusted_size tests/test_center_buy_repair.py tests/test_decision_evidence_runtime_invocation.py`
  -> 8 passed.
- `python3 -m py_compile src/engine/evaluator.py tests/test_center_buy_repair.py tests/test_runtime_guards.py tests/test_decision_evidence_runtime_invocation.py tests/test_digest_profile_matching.py architecture/digest_profiles.py`
  -> pass.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `python3 scripts/topology_doctor.py --navigation --task "Phase 1K live decision snapshot causality DSA-05 DSA-13 DSA-18 live decision snapshot issue valid fetch available payload hash Open-Meteo fallback auditable snapshot id no source routing no TIGGE activation no production DB mutation no live venue side effects" ...`
  -> navigation ok under `phase 1 forecast source policy implementation`.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md`
  -> topology check ok.

Residual blockers:

- This is an entry-side gate and persisted decision-context repair, not the full
  DSA-13 canonical snapshot-table decision. The live writer still uses the
  existing legacy `ensemble_snapshots` compatibility table until a later
  canonical live snapshot design is approved.
- This is not a full DSA-18 `CausalTimestampSet` for observations, market
  snapshots, commands, fills, settlements, replay, and learning. It closes the
  forecast-entry portion and leaves the broader cross-path timestamp contract
  for a separate phase.
- Direct TIGGE/ECMWF primary activation remains operator-gated and inactive;
  Open-Meteo remains monitor/diagnostic fallback and cannot satisfy executable
  entry authority through this gate.

Phase 1K closeout addendum:

- After adding the affected evaluator consumer fixtures to the Phase 1 topology
  profile, exact-scope navigation for Phase 1K admitted
  `docs/operations/AGENTS.md`, `src/engine/evaluator.py`, the focused test
  files, packet evidence files, and topology artifacts under
  `phase 1 forecast source policy implementation`.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files ...`
  -> topology check ok.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md`
  -> topology check ok.
- Combined closeout test command over source-policy, runtime guard, FDR fixture,
  execution-price, decision-evidence, and digest routing regressions -> 164
  passed.
- `python3 scripts/semantic_linter.py --check ...` over exact Phase 1K source,
  test, topology, digest, and packet docs -> pass.
- `git diff --check -- ...` over exact Phase 1K files -> pass.
