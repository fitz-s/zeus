# Findings

Severity scale follows the review convention: P1 blocks or can materially break live money correctness; P2 is significant semantic/reliability risk; P3 is complexity debt or operator-confusion risk that should be removed after higher risks are closed.

## Thread-Visible Prior Findings To Preserve

These were provided in-thread before this packet. This packet preserves them so the design-simplification audit has a single index. The detailed evidence for each remains in the earlier review text unless repeated in `evidence.md`.

| ID | Priority | Title | Repair Flow |
|----|----------|-------|-------------|
| F01 | P1 | LOW holding monitor defaults to HIGH probability chain | Thread `temperature_metric` into `EnsembleSignal` in `monitor_refresh`; add LOW active-position regression with LOW bins and LOW calibrator; verify entry and monitor use the same `MetricIdentity`. |
| F02 | P1 | LOW Day0 cannot handle real open-shoulder bins | Replace `float(None)` assumptions with `Bin` shoulder helpers; pass settlement rounding and temporal context into LOW Day0; add open-low/open-high regression for Polymarket bins. |
| F03 | P1 | Entry intent does not carry executable snapshot fields | Populate `executable_snapshot_id`, `min_tick_size`, `min_order_size`, and `neg_risk` at intent creation; add executor gate regression proving live entry no longer fail-closes after safe gate removal. |
| F04 | P1 | V2 submit still uses compatibility envelope | Make V2 submit consume U1-certified executable snapshot facts end-to-end; remove compatibility placeholders; test envelope identity fields against snapshot gate. |
| F05 | P2 | Market scan authority not enforced before entry discovery | Preserve `MarketSnapshot.authority` through `find_weather_markets`; fail closed on fallback/provenance downgrade before entry signal emission. |
| F06 | P2 | Legacy fill polling may treat MATCHED as final | Prove whether the polling payload is order status or trade lifecycle; require filled size/order size and confirmed-finality boundary before materializing exposure. |
| F07 | P1 | Paris settlement source config points at LFPG while current markets resolve on LFPB | Update Paris current source facts and city config only after fresh source-validity packet evidence; rebuild impacted observations/calibration rows; quarantine mismatched Paris decisions. |
| F08 | P1 | Live Open-Meteo ENS snapshots cannot be stored with missing issue/valid time | Do not insert NULL issue/valid time into legacy NOT NULL columns; either derive legally stamped issue/valid fields with provenance or route live Open-Meteo snapshots to a schema that permits missing issue time; fail closed if no auditable snapshot id. |
| F09 | P1 | Closed/non-accepting Gamma child markets enter outcome vector | Filter Gamma child markets on active/open/orderbook acceptance before outcome extraction; add fixture with mixed closed/open children. |
| F10 | P2 | v2 row-count status reads empty trade shadow tables before world truth | Qualify v2 row-count reads against attached `world.` DB; add status summary regression using trade shadow table plus populated world table. |
| F11 | P1 | PARTIAL remainder cancel voids filled exposure | Materialize partial fills before cancelling remainder; split filled exposure from unfilled remainder; prevent `_mark_entry_voided` from removing real filled shares. |
| F12 | P1 | Calibration maturity threshold is not enforced | Apply documented `edge_threshold_multiplier(cal_level)` before executable edge selection; add Level 4 bucket regression proving raw low-maturity buckets cannot pass on CI/FDR alone. |
| F13 | P1 | Multi-bin buy_no hypotheses cannot execute | Build executable NO-token `BinEdge` for multi-bin families or fail closed before FDR selection can choose non-executable NO hypotheses. |

Phase 1A status for F12 (2026-04-29): live evaluator Level 4 is now a hard
`CALIBRATION_IMMATURE` no-trade before edge/FDR selection. This deliberately
chooses the hard-block branch because no current nonzero base edge floor exists
for `edge_threshold_multiplier(cal_level)` to multiply without becoming a
no-op. Replay parity and any future Level 2/3 nonzero edge floor remain later
phase work.

Phase 4A status for F13 (2026-04-29): full-family FDR now uses the same
YES/NO reachability predicate as executable `BinEdge` generation. Multi-bin
weather families no longer create `buy_no` hypotheses from synthetic `1-p`
YES-side math because native NO-token VWMP/orderbook facts are not present at
`MarketAnalysis`; binary markets still retain executable `buy_no` hypotheses.
Native multi-bin NO execution remains a future producer/economics expansion,
not an inferred price shortcut.

Phase 1B status for F08 (2026-04-29): live evaluator snapshot persistence now
allows Open-Meteo-style `issue_time=NULL` without fabricating an issue cycle,
stores `first_valid_time` as `valid_time`, performs NULL-safe snapshot lookup,
and fails closed before calibration/edge selection if no snapshot id is
returned. Settlement learning was also hardened: a persisted snapshot with
missing `issue_time` is audit-only (`learning_snapshot_ready=False`) and
`harvest_settlement` refuses to generate calibration pairs from p_raw when
`forecast_issue_time` is missing. Open-Meteo can therefore support live audit
fallback, but it is not promoted into training evidence without true issue-time
provenance.

Phase 1C status for F01/F02 (2026-04-29): LOW monitor and LOW Day0 semantic
closure is implemented for the scoped live holding/exit path. Monitor refresh
now threads the position `temperature_metric` into `EnsembleSignal`, uses the
same metric for LOW calibrator lookup, and computes LOW Day0 bootstrap spread
from remaining member minima rather than maxima. LOW Day0 now accepts real
open-shoulder `Bin` bounds, applies injected `SettlementSemantics` rounding,
and preserves observation/temporal context through `Day0Router`. This closes
the deterministic HIGH/LOW semantic break for the scoped monitor/Day0 path.
It does not close source-policy, executable market identity, fill/exposure
ledger, replay/economics parity, or live deployment readiness.

Phase 1D status for DSA-01 (2026-04-29): Open-Meteo live ENS is now an
explicit degraded fallback instead of an implicit entry-primary source.
`fetch_ensemble()` defaults to `entry_primary`, Open-Meteo source specs allow
only `monitor_fallback` and `diagnostic`, the live ensemble row is not
`tier="primary"`, evaluator entry fail-closes on
`SourceNotEnabled` or any `DEGRADED_FORECAST_FALLBACK` payload before p_raw,
monitor refresh explicitly requests monitor fallback, and GFS crosscheck
explicitly requests diagnostic fallback. Review remediation also makes monitor
success validations carry forecast `source_id`, role, and degradation level, so
held-position exit review can see degraded fallback evidence. This closes the
first DSA-01 safety slice but not the broader source-policy program:
TIGGE/direct ECMWF primary ingest is still operator-gated and inactive.

Phase 1E status for DSA-02/DSA-03 (2026-04-29): live evaluator and monitor
source selection now use strict ensemble settings accessors instead of hardcoded
model defaults. Entry uses `settings["ensemble"]["primary"]`, monitor fallback
uses the same configured primary model, and evaluator crosscheck uses
`settings["ensemble"]["crosscheck"]`. Forecast identity is split into
provider-specific `forecast_source_id` and broad `model_family`; provider bias
lookup now keys on `source_id` when present, so Open-Meteo ECMWF and direct
TIGGE/ECMWF cannot silently share the same provider bucket through the broad
`ecmwf` family key. Remaining source-program work is direct primary-provider
activation, causal timestamp/payload-hash completeness, and downstream
capability/evidence propagation.

Phase 1F status for DSA-04 (2026-04-29): ECMWF Open Data scheduled collection
is now explicitly policy-owned as a diagnostic, non-executable source. The
source registry contains `ecmwf_open_data` with `allowed_roles=("diagnostic",)`
and `degradation_level="DIAGNOSTIC_NON_EXECUTABLE"`. The live-scheduled
collector gates itself through that registry role before running and writes
legacy `ensemble_snapshots` rows as `authority="UNVERIFIED"` rather than
letting the table default imply VERIFIED/canonical executable evidence. The
daemon may still schedule the diagnostic collector, but the collector is no
longer an unregistered ECMWF-like source plane outside the source policy.
Remaining source-program work is a full canonical live decision snapshot/time
contract, not selecting ECMWF Open Data as a live primary provider.

Phase 1G status for DSA-06 (2026-04-29): forecast-history Open-Meteo
previous-runs rows with `availability_provenance IS NULL` are no longer
admitted by replay/skill ETL when the provenance column exists. The repair is
read-time only: no production DB mutation, no live previous-runs collection
change, and no source-routing change. True pre-F11 schemas with no
`availability_provenance` column still fall back to legacy diagnostic behavior,
but migrated schemas cannot leak NULL-provenance Open-Meteo rows through the
`availability_provenance IS NULL OR SKILL_ELIGIBLE_SQL` compatibility clause.
Non-Open-Meteo NULL legacy rows remain tolerated in this narrow slice; broader
purpose-gated replay remains future DSA-18/DSA-19 work.

Phase 1K status for DSA-05/DSA-13/DSA-18 (2026-04-30): executable entry now
has a forecast-evidence gate after ENS fetch validation and degraded-source
policy checks but before signal construction, snapshot persistence,
calibration, FDR, or sizing. The gate requires explicit source id, model,
raw payload hash, authority tier, degradation level, source role, issue time,
valid/first-valid time, fetch time, explicit available-at time, and a
parseable decision time. The gate rejects issue/fetch/available-at
timestamps that were not knowable by decision time and rejects
`available_at < issue_time`. Accepted decisions persist
those fields into `epistemic_context_json.forecast_context`. Review remediation
also split Phase 1K into a dedicated narrow topology profile, so this scoped
snapshot-causality repair no longer admits source-registry/config/replay/script
files through the broad source-policy profile. This closes the scoped
entry-side "missing or future source/timing/hash evidence can continue" path.
It does not choose the canonical live snapshot table, activate TIGGE/direct
ECMWF, promote Open-Meteo, or implement the full cross-path
`CausalTimestampSet`.

Phase 1H status for DSA-07 (2026-04-29): production monitor refresh no longer
contains a `paper_mode` / Gamma price branch. Held-position market refresh now
uses the live venue protocol shape only: select YES `token_id` or NO
`no_token_id`, call `clob.get_best_bid_ask()`, use best bid in `day0_window`,
and VWMP otherwise. Test fixtures that exercised monitor refresh were converted
to live-shaped fake CLOB quotes, and
`tests/test_runtime_guards.py::test_monitor_refresh_has_no_production_paper_mode_branch`
locks `src/engine` and `src/execution` production Python files against future
`paper_mode` reintroduction. Paper/shadow benchmark naming was closed
separately by Phase 1I and was never a monitor-refresh production branch.

Phase 1I status for DSA-08/DSA-17 (2026-04-29): strategy benchmark promotion is
now evidence-grade driven instead of runtime-mode-name driven. The public code
concepts formerly named `PAPER`, `SHADOW`, and `LIVE` were renamed to
`SIMULATED_VENUE`, `READ_ONLY_LIVE`, and `PROMOTION_GRADE_ECONOMICS`;
`evaluate_paper()` and `evaluate_live_shadow()` compatibility wrappers were
removed; and `StrategyBenchmarkSuite.promotion_decision()` checks evidence
grades rather than environment labels. Legacy storage string values remain only
as DB provenance labels for existing `strategy_benchmark_runs` compatibility.

Phase 0B recertification status for DSA-12 (2026-04-30): `get_mode()` now
ignores `ZEUS_MODE` and returns the single live runtime. Missing or invalid
`ZEUS_MODE` cannot block production or utility code, and tests use
`ZEUS_MODE=paper` only as an antibody proving the retired switch cannot bypass
live-only guards. `tests/test_k5_slice_l.py::TestGetMode` locks explicit-live,
missing-env, and retired-non-live-env behavior. This closes the old env-authority
finding for
production and utility callers without introducing a new runtime mode or
touching `config/settings.json`.

Phase 0C status for DSA-09 (2026-04-29): the stale
`EXECUTION_PRICE_SHADOW` operator flag was removed from `config/settings.json`
after verifying the evaluator shadow-off branch is already deleted and
fee-adjusted execution price is unconditional at the Kelly boundary.
`tests/test_execution_price.py::TestEvaluatorWiring::test_settings_do_not_expose_execution_price_shadow_flag`
locks the settings surface, while the existing evaluator tests keep the
runtime branch removed. This did not change evaluator behavior, live venue
behavior, production DB rows, or Paris/source routing.

Phase 1J status for DSA-10/DSA-18 (2026-04-29/2026-04-30): replay
snapshot-only fallback is now explicit opt-in for every replay mode.
`run_replay()` no longer treats `mode != "audit"` as permission to set
`allow_snapshot_only_reference=True`; counterfactual and walk-forward lanes
must pass the same market-events preflight unless the caller deliberately
requests diagnostic fallback. The 2026-04-30 follow-up also stops exporting the
physical `shadow_signals` table name as a replay decision-reference source:
storage-backed fallback now reports `legacy_shadow_signal_diagnostic`, keeps
`storage_source=shadow_signals` for traceability, and stamps
`authority_scope=diagnostic_non_promotion` in replay validations. This slice
does not rename or migrate the physical `shadow_signals` table; making
`probability_trace_fact` the sole live probability-lineage table remains future
DSA-10 work.

Paris source-boundary status for F07 (2026-04-29): read-only Gamma sweep plus
independent subagent confirmation found the observable Paris HIGH source switch
between 2026-04-18 and 2026-04-19. HIGH Apr 15-18 resolved on `LFPG`; HIGH
Apr 19-May 1 resolved on `LFPB`. Paris LOW slugs were not found for Apr 15-22
in the probe; the first observable Paris LOW event was Apr 23 and resolved on
`LFPB`. This strengthens the finding but narrows the safe repair path:
do not blindly flip Paris globally. Open a source-validity/source-routing packet
for dated, family-aware Paris routing, quarantine affected Paris live candidates
until resolutionSource matches the dated route, and rebuild affected
observations/calibration rows after the routing decision.

Phase 2A status for F03/F04/F05/F09 (2026-04-29): entry-path executable
market identity is partially closed as a safe first slice. Gamma child markets
with explicit `closed=true`, `active=false`, `acceptingOrders=false`, or
`enableOrderBook=false` are filtered before outcome-vector construction,
including null-first alias payloads where a later Gamma alias carries the
explicit non-tradable value. Keyword fallback discovery is stamped
`EMPTY_FALLBACK` whenever fallback is attempted, including empty fallback
results, and entry discovery fails closed on `STALE`/`EMPTY_FALLBACK` scan
authority before evaluator signal creation. Evaluator token maps now preserve
executable snapshot id, tick size, min order size, and neg-risk when present;
live runtime refuses to create an entry intent if any executable identity field
is missing; and successful entry intents pass those fields into
`create_execution_intent`. Executor live submit now binds the persisted
snapshot-derived `VenueSubmissionEnvelope` into
`PolymarketClient.place_limit_order()`, so the real adapter submit path uses
snapshot condition/question/token/tick/order-size/neg-risk facts instead of
creating a compatibility `legacy:<token_id>` envelope. This does not yet close
the full DSA-14/DSA-16 contract: production still lacks a forward snapshot
capture/persistence writer from Gamma+CLOB facts into the trade DB, and exit,
cancel, redeem, plus composed capability proof remain later slices.

Phase 2B status for F03/F04/F05/F09 / DSA-14 entry path (2026-04-29):
entry-only forward executable snapshot capture is implemented for the live
discovery path. After evaluator selection, live runtime captures the selected
YES/NO token's verified Gamma child facts plus fresh CLOB market info,
selected-token orderbook, fee-rate, tick size, min order size, and neg-risk
facts into append-only `ExecutableMarketSnapshotV2` rows in the same trade DB
used by `venue_commands`. Runtime commits the captured snapshot before calling
`execute_intent`, then lets the executor open its own live command connection so
the pre-submit command/envelope rows are durably committed before SDK contact.
Runtime threads snapshot id, tick size, min order size, and neg-risk into
`ExecutionIntent`. Capture fails closed before intent creation on
`STALE`/`EMPTY_FALLBACK` scan authority, missing CLOB market/orderbook/fee
facts, Gamma/CLOB condition or token mismatch, disabled orderbook, closed,
inactive, or non-accepting child markets. Entry limit prices are tick-aligned
from the executable snapshot tick before command insertion. Remaining out of
scope: exit/cancel/redeem snapshot producers, composed capability proof, U2
trade fact projection, production DB mutation, and live cutover authorization.

Phase 3 status for F06/F11/DSA-15 (2026-04-29): the scoped legacy fill polling
path now respects optimistic vs confirmed finality. `MATCHED` is no longer an
effective final-fill status, even through the stale cycle-runner constant, and
`MATCHED`/partial statuses require explicit filled/matched size before local
exposure changes. Linkable polling observations append U2 order facts before
portfolio mutation; if the payload includes real trade identity, the same path
also appends `venue_trade_facts`, resolves executor runtime ids through
`trade_decisions.runtime_trade_id` before projecting optimistic/confirmed
`position_lots`, and records legal command events without synthesizing trade
ids. Partial observations keep the order pending while recording the filled
quantity; after timeout-cancel of the remainder, the filled shares become active
instead of the whole position being voided. Residual: payloads without real
trade ids cannot be converted into trade facts by polling alone; full
chain/settlement closure remains later phase work.

Phase 3 follow-up status for DSA-15 (2026-04-30): legacy polling now projects
`PARTIAL`, `PARTIALLY_MATCHED`, and `PARTIALLY_FILLED` payloads that carry a
real venue trade id into an optimistic `venue_trade_facts.state='MATCHED'` row
and `position_lots.state='OPTIMISTIC_EXPOSURE'` row. This closes the residual
gap where a partial order fact could be linkable to a real venue trade id but
still fail to materialize an optimistic lot because the payload omitted a
separate `trade_status` field. Payloads with no real trade id still do not
synthesize trade truth; confirmed PnL/learning still require CONFIRMED trade
facts.

## New Design-Simplification Findings

### DSA-01 [P1] Live ENS signal path uses Open-Meteo as primary, not final fallback

Classification: live semantic blocker.

Evidence:

- `src/data/forecast_source_registry.py:72-77` maps default model `ecmwf_ifs025` to `openmeteo_ensemble_ecmwf_ifs025`.
- `src/data/forecast_source_registry.py:115-126` marks Open-Meteo ECMWF live ensemble as primary and GFS as secondary.
- `src/data/forecast_source_registry.py:127-137` marks TIGGE as experimental, disabled by default, and operator-gated.
- `src/data/ensemble_client.py:62-81` defaults `fetch_ensemble` to `model="ecmwf_ifs025"`, then gates the Open-Meteo source id.
- `src/engine/evaluator.py:870`, `src/engine/evaluator.py:1174`, `src/engine/monitor_refresh.py:118`, and `src/engine/monitor_refresh.py:307` call the Open-Meteo-backed defaults for live entry/crosscheck/monitor.
- `docs/reference/zeus_math_spec.md:106-108` says Open-Meteo ECMWF is a temporary live fallback, not canonical primary.

Impact:

- The live alpha path is provider-primary on Open-Meteo distribution semantics, availability, interpolation, issue-time behavior, and quota/rate limits.
- The current code cannot support the claim that Open-Meteo is only final fallback for forecast signal generation.
- Provider latency or payload transformations become unmodeled alpha inputs rather than explicit fallback risk.

False-positive guard:

- This is not about Day0 observations. The Day0 observation chain correctly tries WU/IEM before Open-Meteo and is addressed separately below.
- This is not claiming Open-Meteo data is unusable. It is claiming the implemented authority order contradicts the stated source hierarchy.

Repair flow:

1. Define one forecast-source policy object: `canonical`, `fallback_order`, `allowed_for_entry`, `allowed_for_monitor`, `allowed_for_learning`, and `degradation_level`.
2. Make evaluator and monitor consume the policy, not bare `fetch_ensemble()` defaults.
3. Promote a real direct TIGGE/ECMWF live ingest before setting it as canonical; until then, mark Open-Meteo live ENS as explicit `DEGRADED_FORECAST_FALLBACK` and block or cap entries according to policy.
4. Require persisted source identity, issue-time provenance, payload hash, and latency metadata before a decision can be executable.
5. Add a test that sets primary to TIGGE/Open-Meteo and proves runtime source selection follows policy, not function defaults.

Phase 1D repair status: first safety slice implemented. Open-Meteo no longer
has an omitted-argument path into entry-primary evidence; it must be requested
as monitor fallback or diagnostic, and entry fails closed until a primary source
is operator-authorized. The remaining repair-flow items are the fuller policy
object, active primary-source selection, and source metadata/capability
propagation through all downstream evidence.

### DSA-02 [P2] Ensemble `primary` and `crosscheck` config keys do not control model selection

Classification: semantic risk and operator-surface risk.

Evidence:

- `config/settings.json:29-34` declares `ensemble.primary="ecmwf_ifs025"` and `ensemble.crosscheck="gfs025"`.
- `src/engine/evaluator.py:870` hardcodes the primary fetch by omitting `model`.
- `src/engine/evaluator.py:1174` hardcodes crosscheck as `model="gfs025"`.
- `src/engine/monitor_refresh.py:118` and `src/engine/monitor_refresh.py:307` omit `model`.
- Search found settings ensemble keys used for member counts and MC parameters, not runtime primary/crosscheck source selection.

Impact:

- An operator changing `settings.json` could believe the live source changed when runtime continues using Open-Meteo defaults.
- A future TIGGE switch could pass config review but fail to affect trading.

False-positive guard:

- Member count config is used. The finding is only about source/model identity selection.

Repair flow:

1. Add strict settings accessors for `ensemble.primary_model` and `ensemble.crosscheck_model` or remove the keys if policy becomes code-owned.
2. Thread selected models into evaluator, monitor, and tests.
3. Add a regression that mutates settings to `tigge` and proves `fetch_ensemble(..., model="tigge")` is attempted and gate behavior is surfaced.

Phase 1E repair status: implemented for the live evaluator/monitor source
selection paths. The settings keys now control entry-primary, held-position
monitor fallback, Day0 monitor fallback, and diagnostic crosscheck fetches.

### DSA-03 [P2] Forecast provider identity collapses to model-family identity before bias/fusion

Classification: semantic risk.

Evidence:

- `src/data/ensemble_client.py:298-309` preserves provider-specific `source_id` in parsed ENS results.
- `src/engine/evaluator.py:347-357` maps model names to broad keys like `ecmwf`, `gfs`, or `openmeteo`.
- `src/engine/evaluator.py:1312-1339` loads model bias and constructs `MarketAnalysis` with that broad key from `ens_result.get("model")`, not `ens_result.get("source_id")`.

Impact:

- Open-Meteo ECMWF, direct TIGGE ECMWF, ECMWF Open Data, and future ECMWF sources can share the same `ecmwf` bias bucket despite different availability, interpolation, issue-time, grid, and payload semantics.
- Calibration, attribution, and model-fusion diagnostics can look stable while the provider changed.

False-positive guard:

- If `model_bias` intentionally models model family only, the current key is coherent. But then provider identity must be separately recorded and gated before trading. Current executable edge context does not appear to do that.

Repair flow:

1. Split `model_family` from `forecast_source_id` everywhere.
2. Index provider-specific bias by `forecast_source_id` or make provider mismatch an explicit fallback/degradation term.
3. Persist both fields with each decision and snapshot.
4. Add tests proving Open-Meteo ECMWF and TIGGE ECMWF do not silently share the same provider bucket unless an explicit equivalence map says so.

Phase 1E repair status: implemented for live evaluator bias lookup and forecast
context. `source_id` is preferred for `forecast_source`/bias identity and
`model_family` is retained separately in the forecast context. Snapshot and
downstream evidence propagation remain part of the later source/time-causality
closure.

### DSA-04 [P2] ECMWF Open Data collector is scheduled but not integrated into the live signal selector

Classification: complexity debt with source-authority risk.

Evidence:

- `src/main.py:236-241` defines `_ecmwf_open_data_cycle()`.
- `src/main.py:693-698` schedules it from `discovery.ecmwf_open_data_times_utc`.
- `src/data/ecmwf_open_data.py:1-23` writes `open_ens_v1` / `ecmwf_open_data` rows.
- `src/data/ecmwf_open_data.py:130-150` writes directly to legacy `ensemble_snapshots`.
- Live evaluator/monitor still fetch Open-Meteo via `fetch_ensemble()` rather than selecting this collector's data.

Impact:

- Zeus has two ECMWF-like surfaces: one scheduled local collector and one live HTTP Open-Meteo fetch path. They do not form a single source-selection state machine.
- Operators may believe ECMWF Open Data jobs strengthen live signal authority, but the live path does not consume them.

False-positive guard:

- This path may be intended as archive/support data. The issue is not that it exists; the issue is that it is scheduled by the live daemon and writes source-like snapshots outside the live selector.

Repair flow:

1. Decide whether ECMWF Open Data is a candidate canonical live provider, a backfill-only feed, or deprecated.
2. If live candidate, wrap it behind `ForecastSourceSpec` and one selector interface.
3. If backfill-only, move scheduling out of live daemon or mark outputs non-executable/diagnostic.
4. Add a source inventory test that every scheduled forecast job is either consumed by policy or explicitly non-executable.

Phase 1F repair status: implemented the conservative diagnostic/non-executable
branch. This does not promote ECMWF Open Data to canonical live source; it makes
the live-scheduled collector policy-owned and prevents its legacy snapshot rows
from masquerading as verified executable forecast evidence.

### DSA-05 [P2] Live Open-Meteo ENS snapshots lack issue-time semantics but decisions may continue without a snapshot id

Classification: live audit blocker.

Evidence:

- `src/data/ensemble_client.py:298-309` returns `issue_time=None` for Open-Meteo parsed ENS responses and `first_valid_time` separately.
- `src/engine/evaluator.py:1822-1837` returns `None` for missing issue and valid time.
- `src/engine/evaluator.py:1884-1921` inserts those fields into legacy `ensemble_snapshots` columns that local schema reports as NOT NULL.
- `src/engine/evaluator.py:1934-1936` catches the insert failure and returns an empty snapshot id.
- This duplicates prior F08 because DSA-01 makes clear this is the default live forecast path, not an obscure fallback.

Impact:

- The live default provider cannot reliably produce auditable decision snapshots in the current legacy writer path.
- Downstream learning/replay linkage can break exactly on the provider that currently powers live entry.

False-positive guard:

- Direct TIGGE registered ingest can carry `bundle.run_init_utc` and would not necessarily hit the same missing-issue-time failure. The finding is specific to current Open-Meteo default live path.

Repair flow:

1. Do not allow executable decisions without a non-empty snapshot id unless a named emergency mode explicitly blocks entries.
2. Persist Open-Meteo `first_valid_time`, `fetch_time`, provider `source_id`, and payload hash into a schema that allows missing upstream issue time but tags provenance as derived/unknown.
3. If issue time is inferred from provider update schedule, store inference method and confidence, not a silent synthetic value.

Phase 1K repair status: executable entry now refuses to proceed when the ENS
result lacks source id, model, raw payload hash, authority tier, degradation
level, entry role, issue time, valid/first-valid time, fetch time, or effective
available-at time, or when issue/fetch/available-at timestamps are not parseable
and ordered as `issue_time <= available_at <= fetch_time <= decision_time`.
Accepted decisions persist
that evidence in `epistemic_context_json.forecast_context`. Open-Meteo
monitor/diagnostic fallback still cannot satisfy entry authority; no issue time
is fabricated.

### DSA-06 [P2] Forecast-history Open-Meteo previous-runs lane is live-scheduled and replay-eligible in legacy tolerance cases

Classification: replay/learning semantic risk.

Pre-Phase 1G evidence:

- `src/data/forecasts_append.py:1-8` names the forecast-history lane as Open-Meteo Previous Runs API.
- `src/main.py:219-231` runs forecast catch-up at startup.
- Before Phase 1G, the migrated-schema replay predicate allowed rows when
  `availability_provenance IS NULL OR SKILL_ELIGIBLE_SQL`.
- Before Phase 1G, the replay skill-eligibility regression accepted one
  Open-Meteo NULL-provenance legacy row while excluding reconstructed
  Open-Meteo rows.
- Local DB sample showed reconstructed Open-Meteo previous-runs rows and NULL-provenance legacy rows.

Current Phase 1G repair evidence:

- `src/engine/replay.py` now keeps the `availability_provenance IS NULL`
  tolerance only for non-Open-Meteo rows in migrated schemas.
- `scripts/etl_historical_forecasts.py` and
  `scripts/etl_forecast_skill_from_forecasts.py` use the same narrowed
  Open-Meteo NULL-provenance exclusion.
- `tests/test_replay_skill_eligibility_filter.py` now asserts
  `openmeteo_rows == []` while preserving the explicitly scoped tolerance for
  non-Open-Meteo NULL legacy rows.

Impact:

- The system has another Open-Meteo forecast surface besides live ENS and Day0 fallback. It is not live entry authority, but it can enter diagnostic replay in legacy cases.
- If future code promotes diagnostic replay outputs without respecting `diagnostic_non_promotion`, Open-Meteo fallback data can leak into evaluation conclusions.

False-positive guard:

- Current ECONOMICS eligibility excludes reconstructed rows, and replay identifies diagnostic references. This is not a current proof of live order placement from forecast-history rows.

Repair flow:

1. Require explicit replay purpose (`diagnostic`, `skill`, `economics`, `promotion`) at entrypoint.
2. For promotion/economics, forbid NULL-provenance forecast rows.
3. Rename Open-Meteo Previous Runs source classes as `forecast_history_diagnostic` unless promoted by policy.
4. Add a regression that Open-Meteo NULL-provenance legacy rows cannot enter economics/promotion outputs.

Phase 1G repair status: implemented the narrow read-time guard for migrated
schemas. Open-Meteo previous-runs rows with NULL provenance no longer enter
replay forecast fallback or skill ETL. The broader explicit replay-purpose
entrypoint and full promotion/economics grammar remain out of scope.

### DSA-07 [P3] Production monitor contained a `paper_mode` branch

Classification: complexity debt, low current live reachability.

Pre-Phase 1H evidence:

- `src/main.py:602-609` consumes the live-only `get_mode()` helper; daemon
  launch is no longer gated by `ZEUS_MODE`.
- `src/engine/cycle_runner.py:476` constructs `PolymarketClient()`.
- `src/data/polymarket_client.py:60-94` initializes live CLOB V2 and exposes no paper constructor.
- Before Phase 1H, `src/engine/monitor_refresh.py` branched on
  `getattr(clob, "paper_mode", False)` and used Gamma price in that branch.
- `src/venue/AGENTS.md:23-24` says fake behavior belongs in test-only fakes, not production paper/live split paths.

Current Phase 1H repair evidence:

- `src/engine/monitor_refresh.py:689-718` now uses only
  `clob.get_best_bid_ask()` with YES/NO token selection; Day0 uses best bid,
  non-Day0 uses VWMP.
- `src/engine/monitor_refresh.py:29` no longer imports
  `get_current_yes_price`.
- `tests/test_runtime_guards.py::test_monitor_refresh_has_no_production_paper_mode_branch`
  scans production `src/engine` and `src/execution` Python files and fails on
  any `paper_mode` token.

Impact:

- Current main-daemon reachability appears low, but the branch keeps a paper/live split in production exit monitoring code.
- Test or injected clients can exercise behavior that the live daemon should not carry.

False-positive guard:

- This does not mean production currently runs paper mode. It means production code still contains a paper-specific alternative pricing path.

Repair flow:

1. Move paper/fake price behavior to tests/fakes or benchmark-only code.
2. Make production monitor require the live venue protocol and fail closed on missing CLOB price authority.
3. Add a source scan test forbidding `paper_mode` in `src/engine` and `src/execution` except test-only adapters.

Phase 1H repair status: implemented for the production monitor-refresh branch.
Broader evidence-class naming cleanup for strategy benchmark `PAPER`/`SHADOW`
and other skipped legacy paper tests remains separate debt.

### DSA-08 [P3] Strategy benchmark modeled replay/paper/shadow promotion after paper decommission

Classification: complexity debt and naming risk.

Pre-Phase 1I evidence:

- `docs/reference/zeus_domain_model.md:156` says paper mode was decommissioned.
- Earlier `src/strategy/benchmark_suite.py` revisions defined `PAPER`,
  `SHADOW`, and `LIVE` environment concepts and exposed paper/live-shadow
  compatibility wrappers.
- Earlier strategy docs said promotion required replay + paper + shadow
  evidence.

Current Phase 1I repair evidence:

- `src/strategy/benchmark_suite.py` now names supporting venue evidence as
  `SIMULATED_VENUE` and `READ_ONLY_LIVE`, and names economics as
  `PROMOTION_GRADE_ECONOMICS`.
- `StrategyBenchmarkSuite.promotion_decision()` now validates evidence grades,
  not legacy environment labels.
- `tests/test_strategy_benchmark.py::test_benchmark_api_uses_evidence_class_names_not_runtime_paper_shadow`
  locks the public enum/API names against returning to `PAPER`, `SHADOW`, or
  `LIVE` mode concepts.

Impact:

- Before Phase 1I, a decommissioned runtime mode remained as a promotion
  concept, which made live-readiness language harder to reason about.
- Phase 1I removed that public-concept ambiguity from the benchmark API and
  strategy module reference; existing benchmark storage values remain legacy
  provenance only.

False-positive guard:

- The benchmark suite explicitly does not place orders. This is not a live execution bug.

Repair flow:

1. Rename `PAPER` to `SIMULATED_VENUE_EVIDENCE` or equivalent.
2. Rename `SHADOW` to `READ_ONLY_LIVE_EVIDENCE`.
3. Keep fake venue parity tests under `tests/fakes`, not production runtime mode language.
4. Update promotion docs to use evidence classes rather than runtime modes.

Phase 1I repair status: implemented for StrategyBenchmarkSuite public concepts
and promotion-decision logic. Legacy storage labels remain as DB provenance
values only; no DB migration or production data mutation was performed.

### DSA-09 [P3] `EXECUTION_PRICE_SHADOW` remained in config after evaluator shadow-off path removal

Classification: stale operator surface.

Pre-Phase 0C evidence:

- `config/settings.json:145-149` still has `EXECUTION_PRICE_SHADOW`.
- `tests/test_execution_price.py:177-187` asserts that `EXECUTION_PRICE_SHADOW` must not appear in `src/engine/evaluator.py` because the shadow-off branch was deleted.

Current Phase 0C repair evidence:

- `config/settings.json` no longer contains `EXECUTION_PRICE_SHADOW`.
- `tests/test_execution_price.py::TestEvaluatorWiring::test_settings_do_not_expose_execution_price_shadow_flag`
  asserts the operator settings surface does not expose the stale flag.
- `tests/test_execution_price.py::TestEvaluatorWiring::test_shadow_flag_removed_from_evaluator`
  continues to assert the evaluator has no `EXECUTION_PRICE_SHADOW` branch.
- `docs/operations/known_gaps.md` now states fee-adjusted sizing is
  unconditional and that the stale rollback flag was removed.

Impact:

- Before Phase 0C, operators and future agents could infer a live toggle
  existed even though the relevant code path had been intentionally removed.
- Phase 0C closes that operator-surface ambiguity.

False-positive guard:

- The stale flag does not currently appear to alter evaluator behavior.

Repair flow:

1. Remove the stale config key or move it to archived migration notes — implemented by removing the key from `config/settings.json`.
2. Add a config-schema test that forbids unknown/dead feature flags —
   implemented for this specific stale flag in `tests/test_execution_price.py`.
3. Keep the existing evaluator test to ensure the branch does not return —
   retained.

### DSA-10 [P2] `shadow_signals` is live decision telemetry but named and reused as shadow replay fallback

Classification: semantic risk and audit complexity.

Evidence:

- `src/state/db.py:637-648` names `shadow_signals` as pre-trading validation.
- `src/engine/cycle_runtime.py:1167-1194` writes it during live candidate evaluation.
- `src/state/db.py:650-655` also defines `probability_trace_fact` for durable probability lineage.
- `src/engine/replay.py:42-46` and `src/engine/replay.py:532-588` use `shadow_signals` as a diagnostic replay reference source.
- Before Phase 1J, `src/engine/replay.py` also auto-enabled snapshot-only
  fallback for every non-`audit` replay mode via `mode != "audit"`.

Current Phase 1J repair evidence:

- `src/engine/replay.py` now passes only the caller-provided
  `allow_snapshot_only_reference` into `ReplayContext`.
- `tests/test_run_replay_cli.py::test_counterfactual_replay_does_not_auto_enable_snapshot_only_reference`
  proves `mode="counterfactual"` fails strict market-events preflight unless
  explicit snapshot-only fallback is requested.
- `tests/test_replay_time_provenance.py::test_replay_context_can_fallback_to_shadow_signal_reference`
  keeps `shadow_signals` fallback available only under explicit
  `allow_snapshot_only_reference=True`.
- 2026-04-30 follow-up: `src/engine/replay.py` now reports the
  `shadow_signals` table-backed fallback as
  `legacy_shadow_signal_diagnostic`, while preserving
  `storage_source=shadow_signals` and
  `authority_scope=diagnostic_non_promotion`.
- `tests/test_run_replay_cli.py::test_run_replay_shadow_signal_fallback_uses_legacy_diagnostic_source`
  locks the summary source count and per-decision validation markers for that
  legacy diagnostic path.

Impact:

- One physical table still carries pre-trading shadow/live telemetry storage,
  but replay no longer exposes the table name as the semantic source label.
- Phase 1J reduces the authority risk by preventing replay mode names from
  implicitly enabling diagnostic fallback and by making legacy table-backed
  replay fallback visibly diagnostic/non-promotion.

False-positive guard:

- Local DB sample showed zero `shadow_signals` rows at audit time, so this is a design/naming risk rather than a current data contamination finding.

Repair flow:

1. Make `probability_trace_fact` the sole live probability-lineage table.
2. Freeze `shadow_signals` as legacy diagnostic input or migrate it to a clearly named `legacy_shadow_signal_diagnostic` table/view — partially implemented by source-label remapping without schema migration.
3. Require replay fallback outputs to carry `diagnostic_non_promotion` authority and prevent promotion/economics consumers from reading them — partially implemented by explicit fallback opt-in, `promotion_authority=False`, and per-decision diagnostic authority validations.

### DSA-11 [P2] Live money path still has feature-flagged dual settlement/exit semantics

Classification: semantic risk after canary phase; accepted migration risk before canary completion.

Evidence:

- `config/settings.json:147-149` defaults `CANONICAL_EXIT_PATH=false` and `HOLD_VALUE_EXIT_COSTS=false`.
- `src/execution/harvester.py:58-73` reads the canonical-exit flag.
- `src/execution/harvester.py:1217-1222` chooses `mark_settled` versus `compute_settlement_close` by flag.
- `src/config.py:463-470` and `src/state/portfolio.py:619-630` keep hold-value exit costs behind a flag.

Impact:

- A single live-money lifecycle has multiple possible semantics depending on config state.
- This is acceptable during a deliberate migration, but not compatible with a claim that only math/physics uncertainty remains after known bugs are fixed.

False-positive guard:

- The flags appear deliberate and tested. The issue is not that they are necessarily wrong today; the issue is that they are not a final simplified architecture.

Repair flow:

1. Complete canary evidence for canonical exit and hold-value exit costs.
2. Promote one path to mandatory canonical behavior.
3. Delete the alternate path and flag.
4. Keep only emergency kill switches that reduce authority, not switches that change accounting semantics.

### DSA-12 [P2] `get_mode()` defaulted to live outside `main()`

Classification: operator-safety risk in non-daemon code paths.

Pre-Phase 0B evidence from earlier revisions:

- `src/config.py` returned `os.environ.get("ZEUS_MODE", "live")` and accepted
  live.
- `tests/test_k5_slice_l.py` locked the missing-env default to live.
- `src/main.py` had a daemon-local environment guard, leaving non-main callers
  dependent on the looser helper behavior.
- Many non-main modules called `get_mode()` for DB environment tags and query
  filters.

Current Phase 0B repair evidence:

- `src/config.py:48-57` returns the constant live runtime and documents that
  `ZEUS_MODE` is no longer authority.
- `tests/test_k5_slice_l.py::TestGetMode` proves explicit live, missing
  `ZEUS_MODE`, and retired non-live env values all resolve to the same live
  runtime contract.
- `tests/test_config.py::test_get_mode_is_live_constant_not_env_authority`
  proves `ZEUS_MODE=paper` cannot change the runtime.

Impact:

- Before Phase 0B, the daemon was protected but scripts/tests/imported tools
  could call `get_mode()` without `main()` and silently tag/query as live.
- Phase 0B closes the env-authority path by making `get_mode()` a live constant;
  replay/backtest behavior must use replay/backtest APIs directly.

False-positive guard:

- This does not bypass `src/main.py` startup guard. The risk is non-main surfaces and future utilities.

Repair flow:

1. Make `ZEUS_MODE` non-authoritative by returning the single live runtime from
   `get_mode()` — implemented.
2. Keep replay/backtest on their explicit APIs instead of using runtime env
   switches — no extra helper needed in current code.
3. Update tests that previously locked live default behavior — implemented in
   `tests/test_k5_slice_l.py::TestGetMode`.
4. Require scripts to pass explicit mode or use read-only DB paths — enforced
   by explicit `read_mode_truth_json(..., mode=...)` calls and the live-only
   `get_mode()` contract for mode state paths.

### DSA-13 [P2] Legacy/v2 snapshot split hides current world truth from live snapshot writes and status

Classification: schema authority complexity.

Evidence:

- `src/engine/evaluator.py:1840-1847` selects `world.ensemble_snapshots` if attached, not `ensemble_snapshots_v2`.
- `src/engine/evaluator.py:1884-1921` writes live snapshots into the legacy table shape.
- Local DB sample showed `ensemble_snapshots_v2` populated with TIGGE high/low rows, while the simple legacy grouped count returned no rows.
- Prior F10 found status row counts read unqualified empty trade shadow tables before world truth.

Impact:

- The active audited world-truth table and live writer/status surfaces are not obviously the same authority surface.
- Future fixes can appear to work against one table while monitoring another.

False-positive guard:

- This finding is about authority split, not necessarily asserting the legacy table must be empty in every environment.

Repair flow:

1. Declare one canonical snapshot table per purpose: live decision snapshots, training/rebuild snapshots, and legacy compatibility if needed.
2. Make evaluator write the canonical live decision table or make legacy insert a compatibility projection after canonical commit.
3. Qualify all cross-DB status reads.
4. Add a DB topology test that fails if live writer, replay reader, and status summary disagree on canonical snapshot source.

Phase 1K partial repair status: live decisions now carry explicit
source/timing/hash/degradation evidence in their persisted epistemic context and
cannot reach executable entry with missing or future forecast evidence. This
reduces the legacy/v2 split's audit blind spot, but DSA-13 remains open for the
canonical live snapshot table decision.

Phase 1L status (2026-04-30): the evaluator live decision snapshot writer now
uses `ensemble_snapshots_v2` as the canonical write target when the v2 table is
present, preferring attached `world.ensemble_snapshots_v2` over trade/main
shadow tables. Legacy `ensemble_snapshots` is written only as a same-ID
compatibility projection after the v2 insert; `p_raw_json` updates are mirrored
to both surfaces. The v2 row carries metric identity, canonical data_version,
members unit, source/degradation provenance, training eligibility, and causal
status. Open-Meteo-style missing issue time can be auditable but
`training_allowed=0`/`causality_status=UNKNOWN`; degraded or non-entry-primary
fallback becomes `UNVERIFIED`/`RUNTIME_ONLY_FALLBACK`. This closes the live
writer/table-authority portion of DSA-13 after critic remediation: if a v2
snapshot id collides with an unrelated legacy row, if v2 insertion conflicts
without an exact canonical row, or if canonical `p_raw_json` cannot be updated,
the path fails closed instead of corrupting legacy projection state or falling
back to legacy authority. Remaining DSA-13 work is reader cleanup where
replay/harvester consumers still use legacy compatibility surfaces; those paths
must stay diagnostic/compatibility unless a later phase promotes them with
economics/learning gates.

Phase 1L reader follow-up status (2026-04-30): replay and harvester snapshot
readers now prefer `ensemble_snapshots_v2` when a canonical row exists and fall
back to legacy `ensemble_snapshots` only as compatibility/diagnostic evidence.
Replay preserves the `available_at <= decision_time` point-in-time guard,
requires v2 metric identity match, and stamps v2 snapshots
`authority_scope=canonical_snapshot_v2` while snapshot-only fallback remains
`diagnostic_non_promotion` and requires stored `p_raw_json`. Harvester context
lookup now reads v2 first only when the snapshot id also matches expected
city/date/metric identity, preserves legacy fallback when no matching v2 row
exists, and treats v2 `training_allowed=0` as not learning-ready even if
`issue_time` is present.
This closes the reader side of DSA-13 for replay/harvester diagnostics and
learning-context gates; it still does not promote diagnostic replay to
economics authority or mutate production DB rows.

### DSA-14 [P1] Market identity is not yet one closed contract from discovery to SDK submit

Classification: architecture blocker behind F03/F04/F05/F09.

Evidence:

- `src/contracts/executable_market_snapshot_v2.py:42-78` defines the intended executable identity facts: Gamma ids, CLOB condition/question ids, yes/no token ids, tradability flags, min tick/order size, neg-risk, payload hashes, authority tier, and freshness window.
- `src/contracts/executable_market_snapshot_v2.py:164-220` fails closed when snapshot facts do not authorize the submitted token/price/size shape.
- `src/execution/executor.py:449-525` exposes executable snapshot fields on `ExecutionIntent`.
- Pre-repair `src/engine/cycle_runtime.py:1301-1318` created entry intents
  without passing those snapshot facts. Current `cycle_runtime` now threads the
  captured executable snapshot id, min tick, min order size, and neg-risk into
  `create_execution_intent()` before `execute_intent()`.
- Prior F04 shows the V2 adapter still has a compatibility envelope with placeholder identity.
- Prior F05 and F09 show market-discovery authority and child-market tradability are not enforced early enough.

Impact:

- Fixing only the missing parameters in one caller closes the immediate fail-closed submit blocker, but does not prove the Gamma event, child market, CLOB condition, token map, orderbook, and SDK envelope are one identity object.
- Any future path that reintroduces a market id, token id, or tradability flag outside the snapshot contract can bypass or duplicate the authority seam.

False-positive guard:

- The executable snapshot contract itself is strong. The issue is incomplete end-to-end adoption, not absence of a designed seam.

Repair flow:

1. Define `MarketIdentity` or promote `ExecutableMarketSnapshotV2` as the only executable identity object after candidate discovery.
2. Make scanner output candidates carry a snapshot authority result, including child-market tradability and provenance.
3. Make `ExecutionIntent` construction require the snapshot object, not optional scalar fields.
4. Make V2 adapter submit accept the same snapshot-derived envelope and reject compatibility placeholders in live mode.
5. Add a golden test from Gamma event fixture to command envelope proving condition id, yes/no token ids, outcome label, tick/order size, neg-risk, and tradability stay identical.

Post-merge verification status (2026-04-30): the entry-path identity seam is
machine-verified. The `r3 executable market snapshot v2 implementation`
profile admits the entry-only files, and the focused snapshot/scanner/executor
/ adapter-bound-envelope regression set passes. `PolymarketClient` with a bound
snapshot-derived `VenueSubmissionEnvelope` bypasses the legacy compatibility
submit path, and the executor tests prove the command row cites the executable
snapshot before submit. The remaining DSA-14 work is not a small entry-path
fix: topology marks `src/venue/polymarket_v2_adapter.py` as scope expansion for
this profile, so replacing/removing the adapter compatibility shim or making
exit/cancel/redeem consume one full identity contract requires a new
adapter/live-envelope design slice.

### DSA-15 [P1] Exposure truth has a newer ledger but legacy fill paths still define economic reality

Classification: architecture blocker behind F06/F11.

Evidence:

- `src/execution/command_bus.py:44-68` defines the command-state grammar.
- `src/state/db.py:222-242` defines append-only `venue_trade_facts` with `MATCHED`, `MINED`, `CONFIRMED`, `RETRYING`, and `FAILED`.
- `src/state/db.py:258-280` defines append-only `position_lots` with optimistic, confirmed, economically closed, settled, and quarantined exposure states.
- `src/state/venue_command_repo.py:939-1115` appends trade facts and position lots.
- `src/state/venue_command_repo.py:1138-1144` restricts calibration training to `CONFIRMED` trade facts.
- Pre-repair `src/execution/fill_tracker.py:24` treated `MATCHED` as filled
  on the legacy polling path. The 2026-04-30 follow-up now treats
  `MATCHED`/`FILLED` as non-final observations and keeps `CONFIRMED` as the
  only success terminality signal.
- `src/execution/fill_tracker.py:337-358` can cancel a timed-out pending entry and void the whole position without materializing a partial fill.
- `src/execution/exchange_reconcile.py:342-408` and `src/execution/command_recovery.py:153-199` already contain richer partial/full fill reconciliation logic.

Impact:

- The system has two conceptual owners of exposure: the append-only trade-fact/lot spine and the legacy pending-entry fill tracker.
- Partial fills, optimistic exposure, confirmed exposure, cancel remainders, chain rescue, exit, settlement, and calibration can diverge if any legacy path mutates portfolio truth directly instead of projecting from the same ledger.

False-positive guard:

- This is not saying the new ledger is wrong. It says live closure requires all remaining legacy fill/materialization paths to become projections of that ledger.

Repair flow:

1. Make every venue fill observation append a `venue_trade_facts` row first.
2. Project `position_lots` from trade facts as the only exposure ledger.
3. Make `fill_tracker` read or emit ledger events instead of deciding economic finality from status strings alone.
4. Handle timeout cancel as "cancel remainder" plus preserved filled exposure, never whole-position void unless filled size is zero.
5. Make portfolio/current-state, risk allocator, chain reconciliation, settlement, and calibration consume confirmed/optimistic lot states by declared purpose.

### DSA-16 [P2] Entry/exit/cancel capability is composed across multiple gates instead of one proof object

Classification: safety-surface complexity.

Evidence:

- `src/control/cutover_guard.py:4-18` says cutover decisions happen before venue command rows or SDK side effects.
- `src/control/cutover_guard.py:71-86` exposes submit/cancel/redemption booleans.
- `src/riskguard/risk_level.py:14-20` maps risk levels to operational actions.
- `src/risk_allocator/governor.py:159-194` checks allocation caps, unknown side effects, reduce-only mode, and existing exposure.
- `src/risk_allocator/governor.py:196-243` derives maker/taker/no-trade and kill-switch reasons from heartbeat, WS gaps, reconcile findings, and drawdown.
- `src/risk_allocator/governor.py:363-404` fails closed for global submit and order-type selection.
- `src/risk_allocator/governor.py:440-470` refreshes allocator state from position lots, unknown side effects, reconcile findings, heartbeat, and WS status.
- Source degradation and executable snapshot validity are currently separate from this governor summary.

Impact:

- Strong gates exist, but an operator or future developer must reason across cutover state, risk level, portfolio governor, heartbeat, WS gap, collateral, source quality, and snapshot validity separately.
- That increases false-positive risk in audits and false-negative risk in live readiness: one lane can say "allowed" while another lane fails later.

False-positive guard:

- The existing gates are mostly fail-closed. This finding is about composition and observability, not a discovered bypass of every gate.

Repair flow:

1. Introduce one `ExecutionCapability` or equivalent proof object for each attempted action: entry, exit, cancel, redeem.
2. Compose cutover, risk level, governor, collateral, source degradation, executable snapshot, market authority, and time freshness into that object.
3. Persist the capability proof id on the command intent/envelope.
4. Make all submit paths require an `allow_*` capability with reason codes, not raw booleans from separate modules.
5. Add a status summary that reports one top-level capability state and component reasons.

Phase 2C status (2026-04-29): first command-side proof slice implemented for
entry and exit submit. `SUBMIT_REQUESTED` events now carry an
`execution_capability` payload with deterministic `capability_id`, action,
intent kind, order type, token, executable snapshot id, freshness time, and the
passed component gates for cutover, risk allocator, order-type selection,
heartbeat, WS gap, collateral, and executable snapshot; exit also records the
replacement-sell guard. This does not change gate semantics, add schema fields,
or authorize live side effects. Cancel/redeem proof, status-summary matrix,
source-degradation/time-freshness components, and an envelope/schema-level
capability id remained future DSA-16 slices at Phase 2C close; the
status-summary matrix is closed by Phase 2D below.

Phase 2D status (2026-04-29): the derived `status_summary.json` now exposes
one `execution_capability` matrix for entry, exit, cancel, and redeem operator
visibility. It composes existing public cutover, heartbeat, WS gap,
risk-allocator, and collateral summaries without importing executor internals
or moving any gate. The matrix is explicitly `derived_only`, keeps
`live_action_authorized=False`, reports global blockers, and leaves
per-intent/per-command facts such as executable snapshot freshness, order
notional, token inventory, replacement-sell context, and payout-asset
classification unresolved rather than pretending they are globally known.
After independent review, cancel also remains `requires_intent` on command
identity and venue-order cancelability even when global cutover allows cancel.
This closes the status-summary visibility portion of DSA-16 but not the
remaining cancel/redeem command-side proof or schema/envelope capability id
slices.

Phase 2E status (2026-04-30): cancel and redeem command-side proof payloads
are now added at the direct pre-side-effect seams. `CANCEL_REQUESTED` events
created by `request_cancel_for_command()` carry an `execution_capability`
payload with deterministic `capability_id`, command id, venue order id,
cutover, command identity, and cancelability components before `cancel_order()`
is invoked. Existing `CANCEL_PENDING` rows without such proof now fail closed
into `CANCEL_REPLACE_BLOCKED` without duplicate `CANCEL_REQUESTED` or venue
cancel contact. `REDEEM_SUBMITTED` events carry an `execution_capability`
payload with command id, condition/market identity, payout asset, submittable
state, pUSD FX classification, and cutover-redemption components before
`adapter.redeem()` is invoked. This closes the Phase 2E command-side
cancel/redeem proof slice only; it does not add schema/envelope capability ids,
source-degradation/freshness components, RED-force side-effect-free event
normalization, CLOB cutover, live side effects, or Paris/source routing.

### DSA-17 [P2] Evidence grade vocabulary was stronger in code than in strategy docs and benchmark names

Classification: promotion-authority complexity.

Evidence:

- `src/engine/replay.py:1-13` says replay is approximate audit only and diagnostic non-promotion authority.
- `src/engine/replay.py:532-588` can use `shadow_signals` as a snapshot-only replay fallback when allowed.
- `src/engine/replay.py:1790-1804`, `src/engine/replay.py:2030-2038`, and `src/engine/replay.py:2354-2359` stamp replay outputs with `promotion_authority=False`.
- `src/backtest/decision_time_truth.py:22-79` defines provenance and purpose gates for diagnostic, skill, and economics use.
- `src/backtest/economics.py:1-24` tombstones economics until market-event, price-history, and parity contracts exist.
- Pre-Phase 1I strategy docs and benchmark names still used replay/paper/shadow
  wording even though replay/economics code had stronger evidence-grade and
  purpose gates.

Impact:

- Before Phase 1I, code had moved toward purpose/provenance gates while docs
  and benchmark terminology still used runtime-mode language.
- Phase 1I makes the strategy benchmark promotion decision depend on typed
  evidence grades. `shadow_signals` diagnostic replay naming remains separate
  DSA-10/DSA-18 debt, not a benchmark promotion concept.

False-positive guard:

- Replay currently marks non-promotion and economics is tombstoned, so this is not an immediate promotion bypass finding.

Repair flow:

1. Replace replay/paper/shadow promotion wording with evidence grades: diagnostic, skill, economics, read-only live, simulated venue, promotion-grade.
2. Make `StrategyBenchmarkSuite.promotion_decision()` depend on evidence-grade contracts rather than environment enum names.
3. Keep all diagnostic replay and shadow fallback rows non-promotion by type, not only by limitations text.
4. Update strategy docs and tests so runtime modes cannot be confused with evidence classes.

Phase 1I repair status: implemented for the strategy benchmark and strategy
module reference. `shadow_signals` replay fallback remains a separate DSA-10 /
DSA-18 diagnostic naming and causality package.

### DSA-18 [P2] Time causality is lane-local rather than one live-money contract

Classification: causality and latency risk.

Evidence:

- `src/backtest/decision_time_truth.py:1-12` documents ECMWF ENS availability timing and provenance.
- `src/backtest/decision_time_truth.py:35-45` stores `available_at` and provenance.
- Before Phase 1J, `src/engine/replay.py` could relax replay decision-reference
  causality for every non-audit replay mode by automatically enabling
  snapshot-only references.

Current Phase 1J repair evidence:

- `src/engine/replay.py` now requires explicit `allow_snapshot_only_reference`
  before replay can use `shadow_signals`, ensemble snapshots, or forecast rows
  as fallback decision references.
- `tests/test_run_replay_cli.py::test_counterfactual_replay_does_not_auto_enable_snapshot_only_reference`
  locks counterfactual replay to the same strict preflight unless explicitly
  diagnostic.
- 2026-04-30 follow-up: `shadow_signals` storage fallback is still explicit
  opt-in, but its replay source label is now
  `legacy_shadow_signal_diagnostic` and its validations carry
  `authority_scope:diagnostic_non_promotion`.
- Prior F08 shows live Open-Meteo ENS snapshots can continue without persisted issue/valid time.
- `src/data/observation_client.py:188-218` implements WU/IEM/Open-Meteo Day0 observation fallbacks with different authority and latency.
- `src/engine/replay.py:42-46` allows diagnostic references from snapshot availability and synthetic forecast rows.

Impact:

- Weather alpha depends on whether data was knowable before the decision, not just whether the value is eventually correct.
- Issue time, valid time, fetch time, available-at time, observation time, decision time, venue timestamp, and settlement time are currently enforced by separate lane-specific contracts.
- That makes data-delay mistakes easy to miss, especially around Open-Meteo fallback, Day0 nowcast, replay, and learning.

False-positive guard:

- Several lane-specific time guards already exist. The finding is that they are not yet one cross-path contract.

Repair flow:

1. Define one `CausalTimestampSet` for forecast, observation, market, command, fill, settlement, and replay rows.
2. Require every decision snapshot to persist issue/valid/fetch/available/decision times or explicit missing-time degradation.
3. Make entry require promotion-grade causality; allow weaker causality only for monitor/diagnostic paths by policy.
4. Make replay and learning consume the same timestamp contract rather than synthetic or lane-local fallbacks.
5. Add latency regression tests for Open-Meteo ENS, WU/IEM Day0 observations, CLOB snapshots, and trade facts.

Phase 1K partial repair status: the forecast-entry portion now requires
issue/valid/fetch/available timing, a parseable decision time, and knowability
before decision for issue/fetch/available evidence. Accepted decisions record
decision-time status in decision context. The broader
observation/market/command/fill/settlement/replay `CausalTimestampSet` is still
a separate open design slice.

### DSA-19 [P1] Promotion-grade economic alpha proof is tombstoned

Classification: final live-readiness blocker.

Phase 5A status (2026-04-29): DSA-19 remains a blocker, but the tombstone is
now structured. `src/backtest/economics.py` exposes read-only
`check_economics_readiness(conn)` to report missing/empty substrate and parity
blockers for market events, market price history, executable market snapshots,
confirmed venue trade facts, position lots, probability traces, selection/FDR
facts, settlements, and outcome facts. `run_economics()` still raises
`PurposeContractViolation` even when a fixture supplies all minimum rows,
because the actual economics engine is not implemented in this slice. No
replay/paper/shadow/read-only-live evidence can authorize promotion-grade
economics from this readiness contract.

Phase 5B entry status (2026-04-29): next-stage economics wording now routes to
the Phase 5 profile, but actual local substrate is absent. A read-only probe of
both `state/zeus_trades.db` and `state/zeus-world.db` found 0 rows in
`market_events_v2`, `market_price_history`, `executable_market_snapshots`,
`venue_trade_facts`, `position_lots`, `probability_trace_fact`,
`trade_decisions`, `selection_family_fact`, `selection_hypothesis_fact`,
`settlements_v2`, and `outcome_fact`. Phase 5B PnL implementation remains
blocked until upstream forward substrate writers produce real rows.

Phase 5C entry alignment (2026-04-29): upstream forward-substrate producer work
now has a dedicated topology profile separate from economics readiness. Producer
inventory shows live/scoped writers already exist for `probability_trace_fact`,
`selection_family_fact`, `selection_hypothesis_fact`,
`executable_market_snapshots`, `venue_trade_facts`, `position_lots`,
`trade_decisions`, and `outcome_fact`. Missing live producers remain
`market_events_v2`, `market_price_history`, and `settlements_v2`; those are the
next forward-substrate repair targets before any PnL implementation.

Phase 5C.1 implementation status (2026-04-29): `src/state/db.py` now exposes
`log_forward_market_substrate(conn, ..., scan_authority="VERIFIED")` as a
code-only, explicit-connection producer seam for `market_events_v2` and
`market_price_history`. It refuses degraded scan authority, skips missing or
invalid tables, refuses missing market identity/range/price facts, leaves
`market_events_v2.outcome` unset, and reports idempotent unchanged rows or
conflicts without overwriting existing facts. This slice intentionally does not
add schema DDL, runtime cycle wiring, CLOB VWMP/orderbook truth, settlements,
outcome facts, economics PnL, or live DB mutation. Therefore DSA-19 remains
open: the seam is ready for a future guarded runtime writer, but no live
substrate rows are populated by this change. Follow-up review remediation added
exact `Phase 5C.1` / `log_forward_market_substrate` topology wording and a
regression ensuring already-resolved `market_events_v2` rows do not receive new
price-history rows through the unresolved scanner seam. A second follow-up
regression also prevents ordinary market-event identity conflicts from creating
orphan price-history rows.

Phase 5C.2 schema-owner status (2026-04-29): `src/state/schema/v2_schema.py`
now owns `market_price_history` DDL through `apply_v2_schema()`. The table uses
the existing local shape (`id`, `market_slug`, `token_id`, `price`,
`recorded_at`, `hours_since_open`, `hours_to_resolution`) with
`CHECK (price >= 0.0 AND price <= 1.0)`, `UNIQUE(token_id, recorded_at)`, plus
lookup indexes on `(market_slug, recorded_at)` and `(token_id, recorded_at)`.
Tests prove fresh in-memory schema
creation, idempotent re-application, writer compatibility after
`apply_v2_schema(:memory:)`, and `check_economics_readiness()` remaining
blocked. This is code-owned DDL only: no production DB mutation, backfill,
runtime wiring, CLOB truth, settlement/outcome fact population, PnL, or strategy
promotion is authorized.

Phase 5C.3 admission status (2026-04-29): realistic runtime-wiring wording for
`log_forward_market_substrate()` now routes to the Phase 5 forward-substrate
producer profile and admits `src/engine/cycle_runtime.py` for the next guarded
slice. This admission repair is not runtime wiring; it does not create rows,
touch production DBs, authorize live venue side effects, perform CLOB cutover,
or weaken the economics tombstone.

Phase 5C.3 runtime status (2026-04-29): `src/engine/cycle_runtime.py` now calls
`log_forward_market_substrate()` once per discovery phase after mode filters and
scan-authority capture. It records mode-filtered VERIFIED scanner market/event
and price substrate using the cycle decision time, stores compact summary
status/counts, avoids helper-level commits and CLOB/live venue calls, and keeps
entry safety on existing scan-authority and executable snapshot gates. This is
runtime substrate only; it is not full market-universe economics coverage and
does not close DSA-19.

Phase 5C.3 review remediation (2026-04-29): the runtime authority gate is now
strictly positive. Missing/non-callable `get_last_scan_authority()` is treated
as `NEVER_FETCHED`, and `NEVER_FETCHED` maps to `DATA_UNAVAILABLE` before
evaluator execution. The forward-substrate writer still refuses degraded
authority, but evaluator cannot proceed unless scan authority is exactly
`VERIFIED`. Runtime tests that intentionally exercise evaluator behavior now
declare VERIFIED authority explicitly rather than inheriting a permissive
default.

Phase 5C.4 settlement-substrate status (2026-04-30):
`src/state/db.py::log_settlement_v2()` now provides an explicit-connection
producer for `settlements_v2`, and `src/execution/harvester.py::_write_settlement_truth()`
calls it after the existing `SettlementSemantics`-gated legacy settlement
write. The helper refuses absent tables, invalid schema, missing market identity,
invalid authority, and missing high/low metric identity; it uses
`ON CONFLICT(city, target_date, temperature_metric) DO UPDATE`, not
`INSERT OR REPLACE`; it does not open default DB connections, create schema,
or commit. VERIFIED and QUARANTINED harvester settlement facts are mirrored
without promoting quarantine to verified. `market_events_v2.outcome` was
intentionally deferred because the Phase 5C.4 harvester winning-bin path did
not retain child `condition_id` / token identity; marking a child outcome from
label text would have reintroduced inferred market identity.
Review remediation added a `settlements_v2` unique-key preflight so malformed
v2 schema returns `skipped_invalid_schema` instead of interrupting downstream
harvester processing after the legacy settlement insert. It also repaired Phase
5C topology negatives so safe `no live venue submission/cancel/redeem` wording
does not false-veto this producer profile, while affirmative side-effect wording
remains out of scope.

Phase 5C.5 market-event-outcome status (2026-04-30):
`src/execution/harvester.py` now carries resolved Gamma child identity
(`condition_id`, YES token, range label/bounds, YES-won flag) through the
resolved winning-bin path, requires exactly one YES-resolved child, and passes
the full resolved family into `_write_settlement_truth()`. The state helper
updates `market_events_v2.outcome` only for exact existing scanner-substrate
rows matching `(market_slug, condition_id, token_id, city, target_date,
temperature_metric)` and only after the settlement is `VERIFIED`. Full-batch
prevalidation plus savepoint writes prevent partial resolved families from
creating false `no_market_event_outcomes` readiness. QUARANTINED settlements,
missing child rows, identity mismatches, invalid schema, and existing
conflicting outcomes do not write outcome rows. This closes the harvester-side
outcome producer without production DB mutation, historical backfill, missing
row insertion, live side effects, schema migration, source routing, or
economics-tombstone weakening.

Phase 5D readiness guard status (2026-04-30):
`check_economics_readiness()` now treats `market_price_history` rows as
insufficient unless they satisfy an explicit full-linkage contract:
`market_price_linkage="full"`, CLOB source, valid best bid/ask, and non-empty
raw orderbook hash. This prevents scanner/Gamma `price` observations from
being mistaken for the decision-time CLOB market-price vector required by
promotion-grade economics. The change is read-only readiness logic; it performs
no schema migration, DB mutation, WebSocket capture, PnL computation, or
economics tombstone weakening.

F4/F10 observability status (2026-04-29): the derived status row-count false
alarm is fixed for current v2 data-authority tables. `_get_v2_row_counts()`
now qualifies reads by schema and prefers attached `world` tables over empty
trade shadow tables, while missing tables still return `0` and do not block
status writes. The row signal uses bounded rowid high-water reads instead of
per-cycle `COUNT(*)` full scans, after reviewer performance evidence showed
the exact count path could take ~23s on the local `calibration_pairs_v2` table.
Regression tests cover populated `world` rows for every current `_V2_TABLES`
member masked by empty main/trade shadow rows, the pair-negative where existing
empty world tables remain authoritative, and a trace guard against `COUNT(*)`.
This closes only the status-summary false alarm; it does not settle the broader
DSA-13 live snapshot writer vs v2 world-truth authority split.

Evidence:

- `src/backtest/purpose.py:84-88` defines `ECONOMICS` parity as Kelly bootstrap sizing, BH-FDR selection, and full market-price linkage.
- `src/backtest/purpose.py:113-118` makes only the `ECONOMICS` purpose promotion-authoritative.
- `src/backtest/economics.py:17-24` refuses to run economics until `market_events_v2`, `market_price_history`, and parity contracts exist.
- `src/engine/replay.py:1-13` says replay can score forecast skill or diagnostic divergence, not dollars, when market-price linkage is missing.
- `src/engine/replay.py:2351-2359` stores replay summaries with `promotion_authority=False` and missing parity dimensions.
- `src/state/db.py` now contains `log_forward_market_substrate()`, which can
  write verified forward market/price substrate through a caller-supplied
  SQLite connection only.
- `tests/test_market_scanner_provenance.py` now covers verified writes,
  missing-table skip, degraded-authority refusal, missing-fact refusal,
  idempotence/conflict handling, and the fact that these two tables alone do not
  unblock `check_economics_readiness()`.
- `src/state/schema/v2_schema.py` now creates `market_price_history`.
- `tests/test_schema_v2_gate_a.py` now verifies the schema, idempotence,
  explicit writer compatibility, and continued economics tombstone behavior.
- `src/engine/cycle_runtime.py` now invokes the forward-substrate writer during
  discovery and records compact `forward_market_substrate_*` summary fields.
- `tests/test_runtime_guards.py` proves verified writes occur before evaluator
  without helper-level commit, missing schema is nonblocking, invalid schema
  degrades, missing/never-fetched scan authority fails closed, and degraded
  scan authority still blocks before evaluator.
- `docs/operations/task_2026-04-27_backtest_first_principles_review/01_backtest_upgrade_design.md:46-61` identifies decision-time market price vector, Polymarket fee/tick/neg-risk facts, realized fill/slippage, active Kelly sizing, and BH-FDR as required economics inputs.
- `docs/operations/task_2026-04-27_backtest_first_principles_review/03_data_layer_issues.md:286-292` says `ECONOMICS` is structurally blocked until market events, price history, resolution-source-match cohorts, and full parity exist.
- Local read-only DB probe on 2026-04-29 returned zero rows for `market_events_v2`, `market_price_history`, `venue_trade_facts`, `position_lots`, `probability_trace_fact`, `shadow_signals`, `trade_decisions`, and `position_events` in both trade and world DBs.

Impact:

- Even after all live-flow plumbing bugs are fixed, Zeus cannot yet prove that a no-profit outcome is attributable only to weather physics or model math.
- A no-profit outcome can still come from unproved execution economics, absent point-in-time quote capture, unmodeled fill probability, slippage, adverse selection, fee/tick/neg-risk mismatch, or selection/sizing parity drift.
- Replay/paper/shadow-like evidence cannot substitute for promotion-grade economic proof because the repository explicitly marks current replay as non-promotion and economics as tombstoned.

False-positive guard:

- This finding does not claim the alpha hypothesis is false.
- This finding does not block read-only shadow evidence, skill scoring, or diagnostic replay.
- This finding blocks only the stronger claim that, after implementation, remaining failure must be mathematical/physical rather than statistical/system/execution design.

Repair flow:

1. Treat economics proof as a first-class live-money axis, not as an optional backtest improvement.
2. Populate forward-only market-event and quote capture for all tradeable weather markets, including decision-time bid/ask/orderbook snapshots, min tick, min order size, neg-risk, fees, and resolution source.
3. Persist every live decision's probability trace, source evidence, market snapshot, capability proof, command envelope, venue response, fill events, and confirmed settlement facts into one causal evidence chain.
4. Implement the `ECONOMICS` lane only after captured data satisfies full parity: Kelly bootstrap sizing, BH-FDR selection, decision-time price vector, fees, ticks, neg-risk, fill/slippage, and resolution-source match.
5. Require strategy promotion and capital scale-up to consume only promotion-grade economics evidence plus out-of-sample/frozen replay checks; simulated venue and read-only live evidence remain supporting evidence only.
6. Add acceptance tests that economics refuses reconstructed timing, missing market-price linkage, non-CONFIRMED trade facts for learning, and any selection/sizing mismatch with live evaluator semantics.
