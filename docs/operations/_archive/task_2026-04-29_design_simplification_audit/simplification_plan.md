# First-Principles Simplification Plan

This is a proposed repair sequence, not implementation authority. It is intentionally ordered to reduce risk and false positives before deleting complexity.

## Principle 1: One Source Policy, Not Implicit Defaults

Target design:

- Forecast signal source selection is a typed policy, not a default argument in `fetch_ensemble()`.
- Every source has explicit roles: `entry_allowed`, `monitor_allowed`, `learning_allowed`, `settlement_allowed`, `diagnostic_allowed`.
- Open-Meteo can be allowed as `monitor_fallback` or `forecast_fallback`, but fallback status must propagate into decision authority and sizing.
- A provider switch must be visible in persisted decision facts and model-bias lookup keys.

Implementation sequence:

1. Add read-only source inventory tests first: enumerate all live scheduled forecast jobs, all `fetch_ensemble()` call sites, and all provider source ids.
2. Introduce a `ForecastSourcePolicy` object and thread it into evaluator/monitor without changing behavior.
3. Add tests proving config/policy controls primary/crosscheck model selection.
4. Move Open-Meteo to explicit fallback state only after a direct TIGGE/ECMWF ingest can satisfy issue-time/payload/hash requirements.
5. Block executable entries when the chosen source is fallback unless an explicit risk policy allows degraded entries with caps.

Acceptance gates:

- No live entry decision can be created without `forecast_source_id`, `model_family`, `issue_time_status`, `payload_hash`, and `degradation_level`.
- Open-Meteo default use is impossible by omitted model argument.
- Direct TIGGE and Open-Meteo ECMWF cannot share a provider-bias bucket unless an explicit equivalence map says so.

## Principle 2: Canonical Data Writes First, Compatibility Projections Second

Target design:

- Live decision snapshots write one canonical table.
- Legacy tables are read-only projections or migration compatibility, never the first live write.
- Status, replay, learning, and evaluator all agree on the canonical table.

Implementation sequence:

1. Inventory `ensemble_snapshots`, `ensemble_snapshots_v2`, `probability_trace_fact`, and `shadow_signals` consumers.
2. Decide canonical table per purpose.
3. Make evaluator write canonical live decision snapshots and fail closed on missing snapshot id.
4. Convert legacy table writes into optional projections after canonical commit.
5. Remove status unqualified reads.

Acceptance gates:

- A synthetic Open-Meteo ENS response with missing issue time cannot continue to executable entry without an auditable snapshot record.
- A status summary over trade DB plus attached world DB reports world v2 row counts, not empty trade shadows.
- Replay cannot silently promote diagnostic-only snapshot references.

## Principle 3: Runtime Modes Are Not Evidence Classes

Target design:

- Zeus runtime mode is live only.
- Evidence classes are named as evidence classes: `simulated_venue_evidence`, `read_only_live_evidence`, `diagnostic_replay`, not `paper`/`shadow` runtime modes.
- Test fakes live under `tests/fakes` and implement the live adapter protocol without production branches.

Implementation sequence:

1. Move/rename benchmark `PAPER` and `SHADOW` concepts to evidence-class names.
2. Remove production `paper_mode` branch from monitor refresh.
3. Keep fake venue parity tests, but require fakes to stay test-only.
4. Add source scanner preventing `paper_mode` in production engine/execution code.

Acceptance gates:

- `rg "paper_mode" src/engine src/execution` returns no production branch.
- Missing `ZEUS_MODE` does not block production or utility code; `get_mode()`
  ignores the retired environment switch and returns the single live runtime.
- Fake venue imports cannot import credentials or live network clients.

## Principle 4: Feature Flags May Disable Authority, Not Change Money Semantics Forever

Target design:

- Kill switches can reduce authority or stop entries.
- Accounting, settlement, and exit semantics converge to exactly one live path after canary evidence.
- Dead flags are removed from config and schema tests reject unknown flags.

Implementation sequence:

1. Classify every `feature_flags` key as active migration, kill switch, or stale.
2. Delete stale `EXECUTION_PRICE_SHADOW` from config after updating schema tests.
3. Close canonical exit canary, then delete `CANONICAL_EXIT_PATH` alternate path.
4. Close hold-value exit-cost canary, then delete `HOLD_VALUE_EXIT_COSTS` alternate path.

Acceptance gates:

- `settings.json` contains no key that has no runtime reader.
- A live position's settlement close and exit EV behavior cannot differ by hidden config after canary completion.

## Principle 5: Fallbacks Must Carry Degradation To Sizing Or Block Entry

Target design:

- Fallback is not just a data value. It is an authority state.
- Authority state reaches edge threshold, Kelly sizing, and executor gate.
- Monitoring can use weaker fallback evidence to avoid blindness, but entry must either block or size down by explicit policy.

Implementation sequence:

1. Classify fallback sources by lane: observation fallback, forecast fallback, market-data fallback, replay fallback, portfolio truth fallback.
2. For each lane, define whether fallback may support entry, monitor, exit, learning, or only diagnostics.
3. Add a `degradation_level` to decision context and command intent.
4. Add tests that Open-Meteo Day0 observation fallback does not become settlement/training truth and that Open-Meteo ENS fallback cannot execute as normal primary evidence.

Acceptance gates:

- Every fallback branch either fails closed or stamps a non-OK degradation state consumed downstream.
- No branch returns ordinary `OK` authority after a provider fallback unless policy explicitly allows it.

## Principle 6: One Market Identity Contract From Discovery To Submit

Target design:

- Gamma event identity, child market identity, CLOB condition id, question id, yes/no token ids, outcome label, tradability, min tick/order size, neg-risk, orderbook, and payload hashes live in one executable identity object.
- Discovery may produce non-executable candidates, but execution may only consume executable identity.
- Live SDK submit envelopes are derived from that identity object; compatibility placeholders are test-only or archived.

Implementation sequence:

1. Promote `ExecutableMarketSnapshotV2` or a peer `MarketIdentity` into the required candidate-to-intent payload.
2. Change `create_execution_intent()` to require the snapshot object instead of optional scalar snapshot fields.
3. Make market scanner preserve authority/tradability and reject closed/non-accepting child markets before p_market construction.
4. Replace the V2 adapter compatibility envelope with a snapshot-derived live envelope.
5. Add fixture tests from Gamma payload to executable command envelope.

Acceptance gates:

- A live entry cannot reach `insert_command()` unless a fresh executable identity object authorizes it.
- Token id, condition id, outcome label, min tick/order size, and neg-risk are identical across candidate, intent, command row, pre-submit envelope, and SDK request.
- Closed or non-accepting child markets cannot contribute to p_market or executable bins.

## Principle 7: One Exposure Ledger From Command To Chain To Settlement

Target design:

- Venue command states describe submit lifecycle.
- Venue trade facts describe exchange/chain trade lifecycle.
- Position lots describe economic exposure.
- Portfolio/current-state, risk caps, exits, settlement, and calibration are deterministic projections from the ledger by declared purpose.

Implementation sequence:

1. Make every fill observation append `venue_trade_facts` before mutating position state.
2. Project `position_lots` from trade facts for optimistic and confirmed exposure.
3. Convert legacy fill polling to ledger emission plus projection.
4. Handle partial timeout cancel as filled exposure plus canceled remainder.
5. Make chain reconciliation correct the ledger through append-only facts, not direct portfolio truth invention.

Acceptance gates:

- `MATCHED` can create optimistic exposure but cannot feed calibration or final PnL as confirmed truth.
- Canceling a partially filled order never voids filled shares.
- Risk caps and portfolio read models agree on confirmed plus optimistic exposure from the same source rows.

## Principle 8: One Capability Gate For Submit, Reduce, Cancel, And Redeem

Target design:

- Each action has one persisted capability proof: entry, exit, cancel, redeem.
- The proof composes cutover state, risk level, portfolio governor, collateral, heartbeat, WS gap, unknown side effects, reconcile findings, source degradation, executable snapshot, market authority, and time freshness.
- Individual modules still own their local checks, but execution consumes the composed result.

Implementation sequence:

1. Define `ExecutionCapability` with `action`, `allowed`, `mode`, component reason codes, and freshness time.
2. Compose current gates into this object without changing behavior.
3. Persist capability id on command intent and pre-submit envelope.
4. Route all submit/cancel/redeem paths through the same proof object.
5. Add operator status that reports the composed state and component blockers.

Acceptance gates:

- No live side effect can occur with an absent or stale capability proof.
- A single status view explains why entry is blocked or why reduce-only/cancel/redeem remain allowed.
- Source degradation and snapshot freshness are visible in the same decision as risk/collateral gates.

## Principle 9: One Evidence Grade Grammar For Replay, Learning, And Promotion

Target design:

- Evidence is classified by grade and purpose, not by runtime-mode names.
- Diagnostic replay, skill scoring, economics, read-only live evidence, simulated venue evidence, and promotion-grade evidence are typed.
- Strategy promotion cannot consume evidence whose grade is below its declared purpose.

Implementation sequence:

1. Rename benchmark `PAPER`/`SHADOW` concepts to evidence-grade names.
2. Make replay outputs carry typed evidence grade instead of limitations text only.
3. Make strategy promotion docs and tests depend on evidence-grade contracts.
4. Keep economics tombstoned until market-event, price-history, sizing, and selection parity contracts pass.
5. Add a scanner preventing runtime-mode names from being used as promotion authority.

Acceptance gates:

- Diagnostic replay and snapshot-only fallbacks cannot satisfy promotion or economics gates.
- No docs or code claim "paper mode" is a live readiness requirement after paper runtime decommission.
- Promotion decisions cite evidence grade and missing parity dimensions explicitly.

## Principle 10: One Time-Causality Contract

Target design:

- Forecast, observation, market snapshot, command, fill, settlement, replay, and learning rows use one causal timestamp vocabulary.
- Required fields are explicit: issue time, valid time, fetch time, available-at time, observation time, decision time, venue timestamp, ingest time, and settlement time where applicable.
- Missing time facts degrade authority or block entry; they do not disappear into empty snapshot ids or synthetic replay references.

Implementation sequence:

1. Define a `CausalTimestampSet` or equivalent schema helper.
2. Thread it through ENS snapshots, observation facts, market snapshots, command envelopes, trade facts, and replay references.
3. Fail closed or stamp degraded authority when issue/available/decision ordering is incomplete.
4. Move replay synthetic timing into diagnostic-only provenance by type.
5. Add latency regression fixtures for Open-Meteo ENS, TIGGE/ECMWF direct ingest, WU/IEM observations, CLOB snapshots, and trade facts.

Acceptance gates:

- Entry decisions cannot execute without proving the source was knowable before the decision.
- Replay and learning cannot use reconstructed or synthetic timing for economics/promotion.
- Day0 observation delay and provider latency are reflected in authority and sizing decisions.

## Principle 11: One Ex-Ante Economic Alpha Proof

Target design:

- Zeus distinguishes "the system can flow live money correctly" from "the strategy has promotion-grade economic alpha evidence".
- The only promotion-authoritative economics path uses real decision-time market facts, real venue facts, and live-parity selection/sizing.
- If Zeus loses money after this proof and staged-live checks pass, the remaining failure boundary can be narrowed to alpha decay, forecast/model limits, weather physics, or market competition rather than known system/statistical/execution design errors.

Implementation sequence:

1. Keep `ECONOMICS` tombstoned until the data substrate is real: `market_events_v2`, `market_price_history`, `venue_trade_facts`, `position_lots`, `probability_trace_fact`, and decision snapshots must be populated with point-in-time provenance.
2. Capture forward-only Polymarket market facts for weather markets: Gamma event/child-market identity, CLOB condition ids, token ids, bid/ask/orderbook snapshots, min tick, min order size, neg-risk, fee facts, accepting-orders status, and resolution source.
3. Link every decision to one causal evidence chain: forecast source, observation source, p_raw, calibration level, p_cal, market fusion, p_posterior, CI/FDR, Kelly sizing, execution capability, executable market snapshot, command envelope, venue response, fill facts, and settlement outcome.
4. Implement `run_economics()` only with full live parity: same eligibility gates, same BH-FDR family, same calibration maturity rule, same Kelly/bootstrap sizing, same fee/tick/slippage constraints, and the same executable market identity contract as live entry.
5. Separate three promotion inputs by type: forecast skill evidence, diagnostic divergence evidence, and economics evidence. Only economics evidence can authorize capital scale-up; the other two can block promotion but cannot prove it.
6. Add realized execution model checks: fill probability, partial-fill handling, cancel remainder, adverse selection, spread/slippage, capital lock, and confirmed-vs-optimistic exposure reconciliation.
7. Require out-of-sample and frozen-evidence gates before model or strategy promotion, using only CONFIRMED trade facts for learning and resolution-source-matched markets for economics.

Acceptance gates:

- `ECONOMICS` cannot run when market-price linkage is partial, timing is reconstructed, trade facts are not CONFIRMED for learning, or selection/sizing parity differs from live.
- A strategy cannot be marked promotion-ready from replay/paper/shadow names or simulated venue evidence alone.
- A staged-live run records every dollar of expected and realized PnL into alpha, spread, fee, slippage, failed-settlement, and capital-lock components.
- A post-loss attribution report can separate model/physics error from execution slippage, source degradation, stale market identity, fill finality, calibration immaturity, and selection/sizing drift.

## Final Live-Money Repair Path

This is the proposed implementation order after the audit. It is narrow enough to start repair, but each phase must still run topology navigation and scoped tests before code changes.

### Phase 0: Freeze Authority And Evidence Vocabulary

Purpose:

- Prevent more false readiness claims while implementation proceeds.

Finding coverage:

- DSA-08, DSA-12, DSA-17, DSA-19.

Work:

1. Replace paper/shadow promotion wording with typed evidence grades.
2. Remove remaining assumptions that `ZEUS_MODE` is runtime authority; keep only
   narrow antibodies proving the retired switch cannot bypass live-only guards.
3. Keep economics as the only promotion-authoritative PnL proof.
4. Add scanners/tests that diagnostic replay, simulated venue evidence, and read-only live evidence cannot authorize promotion alone.

Exit criteria:

- Every readiness or promotion decision cites evidence grade and missing parity dimensions.
- No production code path can infer live mode from missing environment.

### Phase 1: Source And Time Causality Closure

Purpose:

- Ensure weather data was knowable, authorized, and correctly degraded before any executable entry.

Finding coverage:

- F01, F02, F07, F08, F12, DSA-01, DSA-02, DSA-03, DSA-04, DSA-05, DSA-06, DSA-13, DSA-18.

Work:

1. Define one forecast-source policy and make Open-Meteo explicit fallback, not implicit primary.
2. Split `forecast_source_id` from `model_family` and persist both.
3. Add one causal timestamp contract across forecasts, observations, market snapshots, commands, fills, replay, and learning.
4. Fix LOW metric threading, LOW Day0 shoulders, Paris current settlement-source mismatch handling, and Open-Meteo missing issue-time persistence/fail-closed behavior.
5. Enforce calibration maturity at executable edge selection.

Exit criteria:

- No entry executes without source id, model family, issue/valid/fetch/available/decision timing, payload hash, degradation state, and non-empty decision snapshot id.
- LOW and HIGH use separate metric identity and calibration semantics end-to-end.

Phase 1E implemented second source-policy slice on 2026-04-29:

- Strict `src.config` accessors now make `settings["ensemble"]["primary"]` and
  `settings["ensemble"]["crosscheck"]` the runtime source/model selection
  authority for evaluator entry, evaluator crosscheck, held-position monitor
  refresh, and Day0 monitor refresh.
- Evaluator bias lookup now prefers provider-specific `source_id` for
  `forecast_source` while preserving broad `model_family` separately in
  forecast context.
- This closes DSA-02/DSA-03 for the scoped live evaluator/monitor probability
  paths. It does not activate direct TIGGE/ECMWF, create a provider equivalence
  map, or complete the full causal timestamp/payload-hash persistence contract.

Phase 1F implemented third source-policy slice on 2026-04-29:

- ECMWF Open Data scheduled collection is now represented in
  `ForecastSourceSpec` as a diagnostic, non-executable source with
  `degradation_level="DIAGNOSTIC_NON_EXECUTABLE"`.
- The scheduled collector gates itself through that diagnostic role before
  download/extract and writes mirrored legacy `ensemble_snapshots` rows as
  `UNVERIFIED`.
- This closes DSA-04's "scheduled but unowned by source policy" problem without
  promoting ECMWF Open Data to canonical live primary or changing production DB
  rows.

Phase 1G implemented fourth source-policy/replay slice on 2026-04-29:

- Migrated `forecasts` schemas with `availability_provenance` no longer admit
  NULL-provenance `openmeteo_previous_runs` rows into replay forecast fallback
  or skill ETL.
- True pre-F11 schemas without the provenance column still use the legacy
  diagnostic fallback query.
- This closes DSA-06's Open-Meteo NULL-provenance leak without mutating DB rows,
  changing live collection/source routing, or redesigning replay purposes.

Phase 1H implemented the first runtime-mode residue slice on 2026-04-29:

- Production `src/engine/monitor_refresh.py` no longer has a `paper_mode`
  branch or Gamma current-price fallback.
- Monitor pricing now uses only live-shaped CLOB quotes: YES/NO token selection
  plus `get_best_bid_ask()`, best bid for Day0, VWMP otherwise.
- `tests/test_runtime_guards.py` now scans production `src/engine` and
  `src/execution` Python files to prevent `paper_mode` reintroduction.
- This closes the monitor-refresh production branch portion of DSA-07 without
  changing executor/venue behavior, mutating DB rows, or renaming benchmark
  evidence classes.

Phase 1I implemented the strategy benchmark evidence-grade naming slice on
2026-04-29:

- Strategy benchmark public concepts no longer expose `PAPER`, `SHADOW`, or
  `LIVE` environment names as promotion concepts.
- Promotion decisions now validate `EvidenceGrade` rather than legacy
  environment labels.
- Legacy benchmark storage string values remain compatibility provenance only;
  no production DB migration or live promotion was performed.

Phase 0C implemented stale execution-price shadow flag cleanup on 2026-04-29:

- `EXECUTION_PRICE_SHADOW` was removed from `config/settings.json` because the
  evaluator shadow-off branch was already deleted and fee-adjusted execution
  price is unconditional at the Kelly boundary.
- The cleanup preserved unrelated local `settings.json` Monte Carlo precision
  changes and did not modify evaluator behavior, live venue behavior,
  production DB rows, or Paris/source routing.
- D3 in `docs/operations/known_gaps.md` now describes the unconditional typed
  execution-price path instead of a stale rollback flag.

Phase 1J implemented the first DSA-10/DSA-18 replay-causality slice on
2026-04-29, with a diagnostic source-label follow-up on 2026-04-30:

- `run_replay()` no longer auto-enables snapshot-only diagnostic fallback for
  non-`audit` modes. Counterfactual and walk-forward replay must satisfy the
  same strict market-events preflight unless the caller explicitly passes
  `allow_snapshot_only_reference=True`.
- `shadow_signals`, ensemble snapshot, and forecast-row fallback remain
  diagnostic non-promotion evidence behind explicit opt-in.
- The `shadow_signals` storage fallback no longer exports the physical table
  name as the decision-reference source. Replay now reports
  `legacy_shadow_signal_diagnostic`, keeps `storage_source=shadow_signals` for
  audit traceability, and attaches `authority_scope:diagnostic_non_promotion`
  to diagnostic fallback validations.
- The Phase 1J topology profile now admits the exact closeout task wording and
  prevents the generic replay-fidelity profile from treating `non-audit replay`
  as an `audit replay` match.
- No state/schema migration, `shadow_signals` table rename, production DB
  mutation, source routing change, live venue side effect, or promotion-grade
  economics activation was performed.

#### Phase 1C implementation slice: LOW monitor and LOW Day0 deterministic closure

Purpose:

- Close the deterministic LOW semantic breaks before broader source-policy work.

In scope:

1. Thread resolved LOW `MetricIdentity` into held-position ENS monitor refresh so raw probability uses local-day minima for LOW and maxima for HIGH.
2. Make LOW Day0 accept the same rounding, observation timing, and temporal context fields already supplied to HIGH Day0.
3. Make LOW Day0 bin containment shoulder-aware for `Bin(low=None, ...)` and `Bin(..., high=None)`.
4. Fix LOW Day0 monitor spread/bootstrap context to use remaining minima rather than `extrema.maxes`.
5. Add focused regressions for LOW/HIGH monitor metric routing, LOW Day0 open shoulders, injected settlement rounding, rich-context propagation, and LOW Day0 monitor refresh.

Out of scope:

- Source-policy changes, TIGGE/Open-Meteo authority changes, Paris/current source edits, DB mutation/backfill, settlement-row rewrites, executable market snapshot/envelope work, fill/exposure ledger work, strategy reachability/FDR changes, or production live deploy.

Acceptance gates:

- `python3 -m py_compile src/engine/monitor_refresh.py src/signal/day0_router.py src/signal/day0_low_nowcast_signal.py`
- Focused pytest for touched LOW/Day0 monitor tests.
- Semantic linter on touched source/tests/docs.
- Planning-lock with this plan as evidence.
- Critic/verifier review before phase closeout.

### Phase 2: Market Identity And Execution Envelope Closure

Purpose:

- Make the executable market a single object from discovery through SDK submit.

Finding coverage:

- F03, F04, F05, F09, DSA-14, DSA-16.

Work:

1. Promote executable market snapshot or a peer `MarketIdentity` as the required candidate-to-intent payload.
2. Filter closed or non-accepting Gamma child markets before outcome vector construction.
3. Thread snapshot id, tick size, min order size, neg-risk, authority, and tradability through `ExecutionIntent`, command rows, and submit envelopes.
4. Compose one `ExecutionCapability` proof for entry, exit, cancel, and redeem.

Phase 2A implemented first slice on 2026-04-29:

1. Gamma child-market filtering is implemented for explicit closed, inactive, non-accepting, and disabled-orderbook child markets, including null-first alias payloads where a later Gamma alias carries the explicit non-tradable value.
2. Keyword fallback scan authority is degraded to `EMPTY_FALLBACK` whenever fallback is attempted, including empty fallback results, and entry discovery fails closed on `STALE`/`EMPTY_FALLBACK` before evaluator signal creation.
3. Candidate/evaluator token payloads preserve executable snapshot id, tick size, min order size, and neg-risk when present.
4. Live entry refuses to create an `ExecutionIntent` when executable market identity fields are absent.
5. Entry submit binds the persisted snapshot-derived `VenueSubmissionEnvelope` into the real V2 adapter submit path, avoiding the compatibility `legacy:<token_id>` envelope for entries with a persisted executable snapshot.

Phase 2A remaining stop condition:

- Production still has no non-test writer that captures Gamma+CLOB facts and inserts `ExecutableMarketSnapshotV2` rows into the trade DB. Therefore Phase 2A prevents unsafe live entry without executable identity and closes the entry submit-envelope seam when identity exists, but it does not yet create executable identity from discovery. The next Phase 2B must implement forward snapshot capture/persistence before claiming the full Gamma fixture to SDK request exit criterion.

Phase 2B scope amendment on 2026-04-29:

1. Critic review blocked implementation under the original U1 topology because the producer necessarily touches `src/data` and `src/engine`, while the old U1 profile admitted only snapshot/gate consumer surfaces.
2. The U1 profile is explicitly expanded only for entry-only forward producer work: verified Gamma child facts plus fresh CLOB facts may be captured into `ExecutableMarketSnapshotV2` rows in the same trade DB used by `venue_commands`.
3. The expansion does not authorize source-provider policy changes, exit/cancel/redeem capability work, command grammar changes, U2 venue fact projections, production DB mutation, live cutover, or any guessed CLOB facts.
4. Phase 2B must fail closed when Gamma scan authority is `STALE`/`EMPTY_FALLBACK`, when required CLOB facts are missing, or when any snapshot field would need to be inferred from defaults.

Phase 2B implemented on 2026-04-29:

1. `market_scanner._extract_outcomes()` now preserves Gamma child identity,
   tradability flags, question/condition ids, token-map provenance, timestamps,
   raw child payload hash, and raw child payload for executable capture.
2. `market_scanner.capture_executable_market_snapshot()` is the entry-only
   producer: it requires VERIFIED scan authority, selected direction/token,
   Gamma child tradability, CLOB market identity, selected-token orderbook,
   fee-rate, tick size, min order size, neg-risk, top bid/ask, and payload
   hashes before appending `ExecutableMarketSnapshotV2`.
3. `cycle_runtime.execute_discovery_phase()` calls the producer only after an
   edge is selected and before `create_execution_intent`; capture failure
   becomes structured no-trade, not a live command.
4. `cycle_runtime` commits the captured snapshot before calling
   `execute_intent()`. The executor then opens its own live command connection,
   sees the durable snapshot, and preserves the pre-submit command/envelope
   commit before SDK contact.
5. `create_execution_intent()` aligns BUY entry limits down to the executable
   snapshot tick to avoid downstream snapshot-gate rejection from floating
   unaligned prices.

Phase 2B residual stop line:

- This phase intentionally does not create exit/cancel/redeem producers,
  capability objects, U2 order/trade projections, or any live deployment
  authorization. Those remain later phases.
- The entry `VenueSubmissionEnvelope` path visible in the cumulative diff is
  Phase 2A F04 work. Phase 2B only reuses that pre-existing entry command gate
  as the consumer that proves the newly captured executable snapshot is visible
  through the trade DB before any live submit side effect.

Phase 2C implemented the first DSA-16 command-side capability proof slice on
2026-04-29:

1. Entry and exit `SUBMIT_REQUESTED` command events now include one
   `execution_capability` payload with deterministic `capability_id`, action,
   intent kind, order type, token id, executable snapshot id, freshness time,
   and passed component gates.
2. The payload is composed from the existing pre-submit gates; it does not
   bypass or reimplement CutoverGuard, HeartbeatSupervisor, WS gap,
   RiskAllocator, CollateralLedger, executable snapshot, idempotency, or
   pre-submit envelope checks.
3. This is not full DSA-16 closure: cancel/redeem capability proofs,
   status-summary matrix, source-degradation/time-freshness components, and an
   envelope/schema-level capability id remained later slices at Phase 2C close;
   the status-summary matrix is closed by Phase 2D below.
4. No production DB mutation, schema migration, source routing change,
   Paris/source edit, live venue side effect, CLOB cutover, or live deployment
   authorization was performed.

Phase 2D implemented the DSA-16 derived status-summary visibility slice on
2026-04-29:

1. `status_summary.json` now includes one `execution_capability` matrix for
   entry, exit, cancel, and redeem.
2. The matrix is explicitly derived-only operator visibility:
   `authority=derived_operator_visibility`, `derived_only=True`, and
   `live_action_authorized=False`.
3. It composes public summaries for CutoverGuard, HeartbeatSupervisor, WS gap,
   RiskAllocator, and CollateralLedger without importing executor internals or
   writing control/state truth.
4. It reports global blockers while leaving per-intent facts unresolved:
   executable snapshot freshness, risk capacity, collateral notional/inventory,
   replacement-sell context, cancel command/order identity, cancelability, and
   payout-asset/FX classification.
5. This does not close cancel/redeem command-side proof, source-degradation
   and unified freshness components, envelope/schema-level capability id, or
   any live deployment authorization.

Phase 2E implemented the DSA-16 cancel/redeem command-side proof slice on
2026-04-30:

1. `request_cancel_for_command()` now persists an `execution_capability`
   payload on `CANCEL_REQUESTED` before the injected `cancel_order()` callable
   can contact a venue.
2. The cancel proof records deterministic `capability_id`, action/intent,
   command id, venue order id, freshness time, CutoverGuard cancel decision,
   command/order identity, and command-state cancelability.
3. Already-`CANCEL_PENDING` rows without a proof-bearing `CANCEL_REQUESTED`
   fail closed to M5 review without duplicate request events and without
   invoking the cancel callable.
4. `submit_redeem()` now persists an `execution_capability` payload on
   `REDEEM_SUBMITTED` before `adapter.redeem()` can contact chain/venue
   infrastructure.
5. The redeem proof records deterministic `capability_id`, action/intent,
   command id, condition/market identity, payout asset, submittable state,
   pUSD FX classification, and CutoverGuard redemption decision.
6. This does not add schema/envelope capability columns, source-degradation or
   unified freshness components, executor/venue rewiring, production DB
   mutation, CLOB cutover, live side effects, RED-force side-effect-free event
   normalization, or Paris/source routing.

Phase 2F implemented the DSA-16 decision-source integrity slice on 2026-04-30:

1. `ExecutionIntent` now carries a frozen `DecisionSourceContext` distilled
   from evaluator/cycle-runtime `forecast_context`; executor does not re-query
   DBs, recompute source policy, or infer missing evidence.
2. `cycle_runtime` threads accepted decision `forecast_context` into the entry
   intent before `execute_intent()`.
3. Entry `_live_order()` now fail-closes before command persistence and SDK
   contact when source evidence is missing, degraded, non-entry-primary,
   non-FORECAST, hash-invalid, time-invalid, or not knowable before the
   decision.
4. Entry `SUBMIT_REQUESTED.execution_capability` includes a
   `decision_source_integrity` component on the happy path.
5. Exit capability proof keeps reduce-only behavior available and records
   `decision_source_integrity` as `not_applicable_reduce_only`.
6. This proves source-degradation and evidence-time causality survival across
   evaluator -> runtime -> executor. It does not define a full age/SLO
   freshness law, add schema/envelope capability columns, change source
   routing, mutate production DBs, authorize live side effects, or edit Paris
   config.

Exit criteria:

- A Gamma fixture can be traced to an SDK request with identical condition id, token ids, outcome labels, tradability, tick/order size, neg-risk, and payload hash.
- No live side effect can occur without a fresh capability proof and executable market identity.

### Phase 3: Exposure Ledger And Fill Finality Closure

Purpose:

- Make economic exposure deterministic from venue facts and position lots.

Finding coverage:

- F06, F11, DSA-15.

Work:

1. Make every fill observation append `venue_trade_facts` first.
2. Project optimistic and confirmed `position_lots` from trade facts.
3. Convert legacy fill polling to ledger emission/projection.
4. Preserve filled exposure when canceling a partial-fill remainder.
5. Restrict learning/calibration to CONFIRMED trade facts.

Exit criteria:

- `MATCHED` can create optimistic exposure but cannot become confirmed PnL or learning truth.
- Canceling a partially filled order never voids filled shares.

Phase 3 implemented on 2026-04-29:

1. Added a dedicated topology profile, `r3 fill finality ledger implementation`, so finality/partial-fill work no longer misroutes to the raw U2 schema profile or heartbeat profiles.
2. Removed `MATCHED` from the effective legacy polling final-fill path, including protection against the stale `cycle_runner.PENDING_FILL_STATUSES` constant.
   Follow-up on 2026-04-30 tightened the same seam so `FILLED` is also treated
   as an order/venue observation, while `CONFIRMED` is the only success
   terminality signal.
3. Required `MATCHED`/partial polling observations to carry explicit filled/matched size before local exposure can materialize.
4. Made linkable legacy polling append U2 order facts first; when the payload carries explicit trade identity, it also appends `venue_trade_facts`, resolves executor runtime ids through `trade_decisions.runtime_trade_id`, projects optimistic/confirmed `position_lots`, and records legal command events without inventing trade ids. Follow-up on 2026-04-30 maps `PARTIAL`, `PARTIALLY_MATCHED`, and `PARTIALLY_FILLED` payloads with a real trade id but no separate `trade_status` to optimistic `MATCHED` trade facts and `OPTIMISTIC_EXPOSURE` lots.
5. Preserved partial filled exposure across timeout cancel: a prior partial observation records the filled quantity while the order remains pending; after canceling the remainder, only the filled exposure becomes active instead of voiding the whole position.
6. Repaired `tests/test_command_recovery.py` to read the current worktree instead of a stale absolute peer-worktree path, unblocking the Phase 3 gate.

Phase 3 residual stop line:

- Legacy polling cannot append `venue_trade_facts` when the venue payload lacks a real trade id. In that case Phase 3 records only the linkable order fact and does not synthesize trade truth. Full command-to-chain settlement closure still depends on WS user-channel and exchange reconciliation delivering real trade ids plus later chain/settlement projection phases.
- If a venue trade fact arrives before `trade_decisions.runtime_trade_id` exists, the polling slice will not synthesize a `position_lots.position_id`; that race remains for the WS/exchange-reconcile identity-spine phase.

### Phase 4: Strategy Reachability And Selection/Sizing Parity

Purpose:

- Ensure selected statistical hypotheses are executable and sized under the same economics as live.

Finding coverage:

- F12, F13, DSA-11, DSA-17, DSA-19.

Work:

1. Make full-family FDR selection and executable `BinEdge` generation agree on YES/NO reachability.
2. Add multi-bin NO token execution or fail closed before FDR can select non-executable NO hypotheses.
3. Apply calibration maturity threshold before executable selection or make immature calibration a hard no-trade state.
4. Remove or close feature-flag branches that change money semantics after canary proof.

Current status (2026-04-29):

- Items 1-2 are closed by Phase 4A using the fail-closed branch: multi-bin
  `buy_no` hypotheses are not tested/selected until native NO-token economics
  are available; binary `buy_no` remains reachable.
- Item 3 is closed for the live evaluator by Phase 1A; replay/economics parity
  remains part of Phase 5 before promotion-grade claims.
- Item 4 is intentionally not closed without canary receipts; removing those
  flags before evidence would violate this plan's own stop condition.

Exit criteria:

- Every selected hypothesis has a native executable side/token route.
- Kelly sizing consumes fee-adjusted, tick-quantized, slippage-aware cost under the same parity contract used by economics.

### Phase 5: Promotion-Grade Economics And Staged Live

Purpose:

- Move from "system can flow correctly" to "system has economic alpha evidence".

Finding coverage:

- DSA-19 and all upstream phases.

Work:

1. Populate forward-only market events, quote history, decision probability traces, command/fill facts, and position lots.
2. Implement `ECONOMICS` only when full parity is available.
3. Produce out-of-sample economics reports with PnL decomposition, confidence intervals, drawdown, FDR-adjusted alpha, and execution-quality attribution.
4. Run staged live with hard capital caps and compare realized slippage/fill/finality against the model.
5. Scale capital only when economics, source causality, market identity, execution ledger, and risk capability gates pass together.

Current status (2026-04-29):

- Phase 5A adds a read-only economics readiness contract and keeps
  `ECONOMICS` tombstoned. Missing/empty/partial substrate is now reported as
  structured blockers instead of a generic refusal.
- Even a fixture with all minimum substrate rows remains blocked by
  `economics_engine_not_implemented`; this prevents table-count presence from
  being mistaken for promotion-grade economics.
- Actual economics PnL computation, staged-live reporting, and capital scale-up
  remain out of scope until real forward market/venue/probability/settlement
  evidence exists and the economics engine is implemented under a separate
  gate.
- Phase 5B entry probe shows the next-stage wording now routes to the Phase 5
  profile, but both `state/zeus_trades.db` and `state/zeus-world.db` have zero
  rows in every required economics substrate table. Phase 5B implementation is
  therefore blocked before PnL work; the correct next repair is upstream
  forward substrate production, not synthetic economics from fixtures.
- Phase 5C entry alignment adds a separate forward-substrate producer profile.
  The readiness profile remains for tombstone/readiness work; producer work now
  routes to data/engine/state append surfaces only under explicit no-go
  boundaries. Inventory found existing producers for probability traces,
  selection facts, executable snapshots, trade facts, lots, trade decisions,
  and outcome facts; missing live producers remain `market_events_v2`,
  `market_price_history`, and `settlements_v2`.
- Phase 5C.1 adds the first code-only producer seam for `market_events_v2` and
  `market_price_history`: `log_forward_market_substrate()` writes only through
  an explicit SQLite connection, requires `scan_authority="VERIFIED"`, refuses
  missing facts, preserves unresolved `outcome=NULL`, and reports conflicts
  without overwriting. It does not add schema DDL, live runtime wiring,
  settlement/outcome population, CLOB VWMP truth, or production DB mutation.
  The remaining producer work is a separately authorized guarded runtime call
  path and a separate schema-owner decision for `market_price_history`.
  Follow-up remediation also pins exact `Phase 5C.1` writer-seam topology
  wording and blocks price-history appends for already-resolved market-event
  rows or rejected market-event identity conflicts.
- Phase 5C.2 adds the code-owned `market_price_history` DDL seam to
  `apply_v2_schema()`. This closes the schema-owner gap only: it does not create
  live rows, wire the runtime cycle, mutate production DBs, backfill history,
  create CLOB VWMP/orderbook truth, or weaken the economics tombstone.
- Phase 5C.3 admission repair makes realistic runtime-wiring wording route to
  the Phase 5 forward-substrate producer profile instead of the R3 collateral
  profile. This is admission-only: no runtime writer, production DB mutation,
  live venue side effect, CLOB cutover, economics readiness, or strategy
  promotion is implemented by the repair itself.
- Phase 5C.3 runtime wiring then connects `log_forward_market_substrate()` to
  the discovery phase for mode-filtered, VERIFIED scanner markets. The helper
  writes compact forward-substrate status/counts, does not commit inside the
  phase, does not call CLOB/live venue methods, does not block entry by itself,
  and leaves degraded Gamma authority plus executable snapshot gates as the
  entry-safety gates. A missing authority getter or explicit `NEVER_FETCHED`
  status is now fail-closed before evaluator; tests that need evaluator
  behavior must explicitly provide VERIFIED authority. This is not complete
  economics coverage and does not close DSA-19.
- Phase 5C.4 adds the harvester-side `settlements_v2` producer. The new
  `log_settlement_v2()` helper writes only through an explicit SQLite
  connection, requires market identity (`market_slug`) plus high/low metric
  identity, performs no DDL and no commit, and is called from
  `_write_settlement_truth()` after the existing `SettlementSemantics` legacy
  settlement write. It mirrors VERIFIED and QUARANTINED harvester settlement
  facts without promoting quarantine to verified and keeps missing child-market
  outcome resolution out of scope because current harvester code does not carry
  child `condition_id` / token identity through `_find_winning_bin()`. This is
  real forward settlement substrate only; it does not mutate production DBs,
  update `market_events_v2.outcome`, compute PnL, or weaken the economics
  tombstone. Review remediation makes malformed `settlements_v2` unique-key
  shape a nonblocking `skipped_invalid_schema` result and fixes topology
  negatives so explicit `no live venue ...` wording remains admitted while
  affirmative live-side-effect wording remains blocked.
- Phase 5C.5 adds the harvester-side `market_events_v2.outcome` producer from
  resolved Gamma child identity. The harvester now preserves
  `condition_id`/YES-token identity, requires exactly one YES-resolved child,
  and writes outcomes only after `SettlementSemantics` marks the settlement
  `VERIFIED`. The DB helper updates only exact existing scanner-substrate rows
  matching `(market_slug, condition_id, token_id, city, target_date,
  temperature_metric)`, prevalidates the full batch before mutation, and uses a
  savepoint so missing/conflicting children cannot leave a partially resolved
  market family or falsely clear `no_market_event_outcomes`. This does not
  backfill production rows, create missing market-event rows, authorize live
  side effects, or weaken the economics tombstone.
- Phase 5D tightens the economics readiness contract for full market-price
  linkage. Existing `market_price_history` scanner/Gamma `price` rows no longer
  count as promotion-grade market-price evidence by themselves; readiness now
  requires explicit `market_price_linkage="full"`, CLOB source, best bid/ask,
  and raw orderbook hash columns plus at least one valid row. This is a
  read-only tombstone/readiness guard only: it does not change schema, mutate
  DB rows, wire WebSocket capture, compute PnL, or promote strategy evidence.
- Phase 5E adds the first full-linkage row-shape producer using the executable
  snapshot facts Zeus already captures for live entry. `market_price_history`
  now has code-owned full-linkage columns, and an explicit-connection helper
  writes `market_price_linkage="full"` rows from
  `ExecutableMarketSnapshotV2` top-of-book/orderbook-hash evidence after
  snapshot capture. Validation did not touch production DBs and the change does
  not backfill history, wire WebSocket market capture, compute PnL, or weaken
  the economics tombstone. When deployed, the runtime call is a live-path DB
  substrate write and remains under the existing G1 live no-go / operator
  deploy gates.
- Paris source-boundary evidence is now precise enough for a source-routing
  packet but not for a blind config flip. Observed HIGH events resolve on
  `LFPG` through 2026-04-18 and on `LFPB` from 2026-04-19 onward; observed LOW
  Paris events start on 2026-04-23 and resolve on `LFPB`.
- F4 status-summary observability is repaired: v2 row counts now prefer
  attached `world` data-authority tables over empty trade shadow tables and
  preserve missing-table nonblocking behavior. This is derived operator
  visibility only, not economics readiness or canonical truth.
- Phase 1D source-policy gate is repaired for the Open-Meteo safety boundary:
  omitted `fetch_ensemble()` role/model arguments now mean `entry_primary`,
  Open-Meteo live ENS is allowed only as `monitor_fallback` or `diagnostic`,
  the live ensemble row is no longer `tier="primary"`, evaluator entry fails
  closed on blocked/degraded forecast authority before p_raw, monitor refresh
  explicitly requests fallback and records forecast source/role/degradation in
  successful monitor validations, and GFS crosscheck explicitly requests
  diagnostic. This does not activate TIGGE/direct ECMWF, close provider-bias
  separation, or authorize live entries.
- Phase 1E source-selection/identity gate is repaired for the scoped live
  evaluator/monitor probability paths: primary/crosscheck model selection now
  reads strict settings accessors, monitor fallback uses the configured primary
  model, crosscheck uses the configured crosscheck model, and provider-specific
  source id drives model-bias lookup separately from model family. This does not
  activate TIGGE/direct ECMWF or close the full source timing/payload hash
  evidence contract.
- Phase 1F ECMWF Open Data scheduled-collector policy is repaired for the
  conservative diagnostic path: the live-scheduled collector is registered as
  diagnostic/non-executable, gates itself through source policy, and stamps
  legacy mirrored snapshot rows `UNVERIFIED`. This does not promote ECMWF Open
  Data to canonical live primary.
- Phase 1G forecast-history provenance eligibility is repaired for migrated
  schemas: Open-Meteo previous-runs rows with NULL provenance no longer enter
  replay/skill ETL through compatibility tolerance. This does not mutate
  production DB rows or redesign replay purpose grammar.
- Phase 1K live decision snapshot causality gate is repaired for the scoped
  executable-entry forecast path: evaluator entry now fail-closes before signal,
  snapshot, calibration, FDR, and sizing when source id, model, payload hash,
  authority tier, degradation level, source role, issue/valid/fetch/available
  timing, decision time, entry-primary authority, or knowability-before-decision
  evidence is missing. Accepted decisions persist those fields in
  `epistemic_context_json.forecast_context`. Phase 1K now has a dedicated narrow
  topology profile so this repair does not admit source-registry/config/replay
  files through the broader source-policy route. This does not choose the
  canonical live snapshot table, activate TIGGE/direct ECMWF, promote
  Open-Meteo, or implement the full cross-path timestamp contract.
- Phase 1K review follow-up is repaired for executable metadata/source/oracle
  causality: v2 metadata gates now require the exact `decision_snapshot_id`
  returned by the current candidate's snapshot persistence; Day0 WU-settlement
  executable entries reject non-`wu_api` fallback observations before the signal
  path; HKO/NOAA/CWA Day0 executable entries fail closed until a
  settlement-type-specific executable observation policy exists; and oracle
  evidence rejects missing, stale, invalid, or future-dated city/metric rows
  before sizing. Historical tests that exercise unrelated gates now provide
  point-in-time oracle evidence fixtures instead of reading current JSON as
  future knowledge. LOW executable entries remain intentionally blocked until
  LOW-specific oracle evidence exists. This does not mutate production DB rows,
  change source routing, activate TIGGE/direct ECMWF, promote Open-Meteo, or
  authorize live entries.
- Phase 1L canonical snapshot authority is repaired for the live evaluator
  writer: `ensemble_snapshots_v2` is the canonical write target when present
  and attached `world` v2 is preferred over trade/main shadow tables. Legacy
  `ensemble_snapshots` is now a same-ID compatibility projection after the v2
  insert, and `p_raw_json` updates mirror into both surfaces. The v2 row carries
  metric identity, members unit, source/degradation provenance,
  `training_allowed`, and `causality_status`, so Open-Meteo-style missing
  issue time remains auditable but not training evidence. Critic remediation
  prevents v2/legacy ID collision corruption, prevents v2 conflict fallback to
  legacy authority, and makes canonical p_raw persistence fail closed before
  executable edge selection. This does not promote replay/harvester legacy
  readers to authority, mutate production DB rows, change source routing,
  activate TIGGE/direct ECMWF, or decide Paris.
- Phase 1L reader follow-up is repaired for replay and harvester: both now
  prefer `ensemble_snapshots_v2` for snapshot rows and keep legacy
  `ensemble_snapshots` as compatibility/diagnostic fallback. Replay preserves
  the `available_at <= decision_time` guard, requires v2 metric identity, and
  keeps snapshot-only fallback diagnostic/non-promotion with non-null p_raw
  evidence. Harvester now requires expected city/date/metric identity before a
  v2 row can outrank legacy fallback and respects v2 `training_allowed=0` in
  learning context, so degraded/runtime-only snapshots can be audited without
  entering calibration training. This does not mutate production DB rows or
  promote replay/economics authority.

Exit criteria:

- If staged live loses money, the attribution report can show whether loss came from model/weather physics, market alpha decay, source degradation, execution slippage, fill finality, calibration immaturity, or system parity drift.
- Only after this phase can Zeus credibly claim no known statistical/system/execution design blocker remains.

## Phase-Boot Probes

These are not blockers to starting repair. Run the relevant probe at the start of the matching implementation phase to refresh evidence and prevent stale assumptions:

1. Build a call graph of all `fetch_ensemble()` call sites and all direct Open-Meteo HTTP calls.
2. Build a call graph of all `shadow_signals`, `probability_trace_fact`, `ensemble_snapshots`, and `ensemble_snapshots_v2` consumers.
3. Build a source inventory from local DB by table and source field, classifying `entry`, `monitor`, `learning`, `diagnostic`, and `status` consumers.
4. Build a production-code scanner for mode residue: `paper`, `shadow`, `fake`, `simulated`, `fallback`, `default live`, and compare against an allowlist.
5. Build a market-identity flow test from Gamma payload to CLOB envelope.
6. Build an exposure-ledger reconciliation test across partial fill, cancel remainder, chain view, and settlement projection.
7. Build a composed capability matrix for entry/exit/cancel/redeem under source degradation, risk levels, collateral, heartbeat, and cutover states.
8. Re-run official provider documentation checks before changing forecast-source policy because provider docs and endpoint behavior are time-sensitive.

Stop conditions before code changes:

- Do not change source authority without a fresh current-fact packet.
- Do not promote TIGGE live ingest until issue time, payload hash, member count, units, local-day aggregation, and latency are proved with real provider payloads.
- Do not delete benchmark evidence classes until promotion docs are updated to evidence-class naming.
- Do not remove feature flags until canary receipts prove the canonical path is safe.
