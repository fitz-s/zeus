# Known Gaps — Venus Evolution Worklist

每个 gap 是一个 belief-reality mismatch。每个 gap 的终态：变成 antibody（test/type/code）→ FIXED。
如果一个 gap 包含 "proposed antibody"，下一步就是实现它。

**Active surface**: this file lists OPEN, MITIGATED, PARTIALLY FIXED, and
STALE-UNVERIFIED gaps that still demand attention.

**Antibody archive** (closed FIXED/CLOSED entries — immune-system record of
what we made impossible): `docs/operations/known_gaps_archive.md`. Reference
when a similar pattern resurfaces; do not re-open without proof the antibody
failed.

---

## CRITICAL: Full-flow live-alpha audit (2026-04-28)

**Status:** OPEN; read-only audit record.
**Audit scope:** weather contract semantics -> source truth -> forecast signal
-> calibration -> edge -> execution -> holding/monitoring -> exit -> settlement
-> learning/observability.
**Audit posture:** This section records current findings only. It does not
authorize live deployment, production DB mutation, CLOB cutover, calibration
promotion, or external data-fetch side effects.
**Runtime reality at audit time:**
- `scripts/live_readiness_check.py --json` returns `live_deploy_authorized=false`
  with `16/17` gates passing; `G1-02` fails because Q1 Zeus-egress evidence is
  absent.
- `state/status_summary.json` reports live mode but entries paused, wallet
  balance `0`, `entry_block_reason=entry_bankroll_non_positive`, and no open
  positions.
- `architecture/runtime_posture.yaml` default posture is `NO_NEW_ENTRIES`, and
  the current branch is not explicitly listed as `NORMAL`; runtime posture
  therefore independently blocks entries even if earlier gates are cleared.
- `state/zeus-world.db` and `state/zeus_trades.db` both have zero
  `executable_market_snapshots`, `venue_commands`, `venue_order_facts`, and
  `venue_trade_facts`.
- Calibration is not live-alpha complete: `platt_models_v2`,
  `calibration_pairs_v2`, legacy `platt_models`, and legacy
  `calibration_pairs` are all empty in current world/trade DBs.
- `scripts/verify_truth_surfaces.py --mode training-readiness --json` returns
  `status=NOT_READY`: `historical_forecasts_v2`, `calibration_pairs_v2`,
  `platt_models_v2`, `market_events_v2`, `market_price_history`, and
  `settlements_v2` are empty; legacy `settlements` rows are evidence-only until
  canonical v2 market identity/finalization policy is present.
- Weather observation freshness is not current for same-day source truth:
  `observations.source='wu_icao_history' AND authority='VERIFIED'` currently
  ends at 2026-04-19, while the audit date is 2026-04-28.

### Money-path coverage verdict

| Money path segment | Current verdict | Primary blockers |
|---|---|---|
| Contract semantics | PARTIAL | Paris station mismatch; LOW shoulder/Day0 semantics incomplete |
| Source truth | PARTIAL | current source validity must be refreshed before live claims; DB observations lag current markets; Day0 WU geocode path can select wrong station; Day0 observation coverage/freshness is soft, not a trade gate |
| Forecast signal | BLOCKED | Open-Meteo live ENS lacks persisted issue/valid time; live snapshot id can be empty; local-day ENS NaNs can pass validation and collapse p_raw to an invalid zero vector; WU epoch observation timestamps are treated as stale while still producing tradeable Day0 p_raw |
| Calibration | BLOCKED | current calibration model/pair tables are empty, so live path falls back to raw probabilities; maturity edge-threshold multiplier is not wired into evaluator selection |
| Edge construction | PARTIAL | Day0 discovery mode can produce an empty candidate set due contradictory resolution-hour filters; current CLOB fee-rate response shape is not parsed, causing live sizing to reject candidates; closed/non-accepting child markets can enter outcome vector; authority degradation not fail-closed before entry; weather multi-bin `buy_no`/shoulder-sell hypotheses can be selected but never become executable edges |
| Execution intent | BLOCKED | entry intent does not carry executable snapshot facts; no production executable-snapshot producer/refresher was found; VWMP-derived limit prices are clamped but not tick-quantized before snapshot gate; `max_slippage` budget is typed but not enforced; executable snapshot table is empty |
| Venue submission | BLOCKED | V2 compatibility envelope is not U1-certified market identity; command row is bound to a pre-submit envelope while the signed/raw-response SDK envelope is not persisted back into canonical provenance |
| Risk/control | BLOCKED | RED force-exit sweep records proxy intent but does not actually cancel/sell; fail-closed RED causes do not necessarily trigger sweep; ORANGE favorable-exit behavior is not explicit |
| Fill/holding | PARTIAL | CONFIRMED-only finality is now enforced in legacy polling, M5 exchange reconciliation, and command recovery; residuals are optimistic-vs-confirmed drift journal split, partial-fill ledger completeness, and filled-command idempotency recovery without an order id |
| Monitoring/exit | PARTIAL | LOW monitor can recompute HIGH raw probability; LOW Day0 open shoulders crash; exit partial fills do not reduce local exposure; whale-toxicity gate has no live detector feeding it |
| Settlement/learning | BLOCKED | harvester live-write path default-off; LOW settlement writes are HIGH-only; settlement obs lookup ignores authority/station/metric; pending-exit exposure can skip settlement; empty decision snapshot id breaks learning traceability; harvester can rebrand live/Open-Meteo p_raw as TIGGE training rows |
| Observability | PARTIAL | v2 status row counts can read empty trade shadow tables instead of world truth |

### [OPEN P1] Day0 observation path can bypass settlement-source routing

**Location:** `src/data/observation_client.py::get_current_observation` and
`_fetch_wu_observation`; `config/cities.json` Hong Kong and Paris rows.
**Problem:** Day0 observation fetch tries WU geocode timeseries for every city
before checking settlement source type. A read-only probe on 2026-04-28 showed
Hong Kong, whose config is `settlement_source_type="hko"` with `wu_station=null`,
returning WU `obs_id=VHHH`. The same probe showed Paris returning WU
`obs_id=LFPG`, while current Polymarket Paris markets resolve on `LFPB`.
**Impact:** Day0 high/low observation can be anchored to the wrong physical
station even when settlement semantics correctly say HKO or the live market
source says LFPB. This directly affects Day0 p_raw, shoulder capture, monitor
refresh, and exit decisions.
**False-positive boundary:** London probe returned `obs_id=EGLC`, matching its
WU config. The issue is source-routed cities and any city where geocode-nearest
station differs from the contract station.
**Proposed remediation:**
1. Route Day0 observation by `settlement_source_type`, not by generic provider
   priority.
2. For WU cities, require returned `obs_id` to match the contract station or a
   dated approved station map.
3. For HKO, skip WU/IEM entirely and use an HKO-native current observation path;
   if HKO current data is unavailable, fail closed for Day0 HK entries.
4. Persist `obs_id`/source station in `Day0ObservationContext` and
   probability-trace facts.
**Acceptance evidence:** HK Day0 never returns VHHH/WU as settlement observation;
Paris Day0 only returns LFPB after the Paris source decision; mismatched obs_id
produces structured no-trade.

### [OPEN P1] LOW non-Day0 monitor can use HIGH probability chain

**Location:** `src/engine/monitor_refresh.py` around `_refresh_ens_member_counting()`;
`src/signal/ensemble_signal.py::EnsembleSignal.__init__`.
**Problem:** Monitor refresh resolves `position.temperature_metric` and passes it
to the LOW calibrator, but constructs `EnsembleSignal` without
`temperature_metric`. `EnsembleSignal` defaults to `HIGH_LOCALDAY_MAX`. A LOW
position can therefore recompute `p_raw_vector` from daily maximums and then
apply LOW bins/calibration logic.
**Impact:** Holding and exit decisions for LOW positions can be based on the
wrong physical quantity. This is a semantic chain break from holding -> exit.
**False-positive boundary:** This does not affect HIGH positions. It requires a
LOW position on the non-Day0 monitor path; current runtime has zero positions.
**Proposed remediation:**
1. Pass the resolved LOW/HIGH `MetricIdentity` into monitor
   `EnsembleSignal(...)`.
2. Add a regression where a LOW position's monitor path feeds member minima, not
   maxima.
3. Make missing/malformed `temperature_metric` on active LOW-like positions
   fail closed or emit an operator-visible quarantine, not silently default HIGH.
**Acceptance evidence:** LOW monitor p_raw fixture differs from HIGH max fixture;
exit trigger receives LOW-consistent `EdgeContext`; test covers active LOW
position with sibling bins.

### [OPEN P1] LOW Day0 cannot handle real open-shoulder bins and loses rich semantics

**Location:** `src/signal/day0_low_nowcast_signal.py`,
`src/signal/day0_router.py`, `src/engine/monitor_refresh.py`.
**Problem:** LOW Day0 `p_vector()` converts `None` shoulders with `float(None)`.
Real Polymarket bins and Zeus `Bin` explicitly use `None` for open shoulders.
The LOW router also does not carry the rich Day0 fields used by HIGH
(`round_fn`, observation timing, temporal context) into the LOW signal object.
**Impact:** LOW Day0 can crash on real shoulder markets and does not fully
encode settlement rounding or observation-latency semantics. This affects
entry-time Day0 and held-position Day0 refresh.
**False-positive boundary:** Center bins with finite low/high do not crash.
This is specifically about open shoulders and rich Day0 semantics.
**Proposed remediation:**
1. Implement LOW shoulder probability with unbounded predicates:
   `(-inf, hi]`, `[lo, +inf)`, and finite `[lo, hi]`.
2. Thread `round_fn`, observation time/source, and temporal context through
   LOW Day0 the same way HIGH receives them.
3. Add HKO/WU-specific rounding tests and observation-staleness tests for LOW
   Day0.
4. Add a live Gamma fixture containing LOW shoulder bins.
**Acceptance evidence:** LOW Day0 p_vector returns normalized probabilities for
open shoulders, does not raise `TypeError`, and records applied validations for
rounding/freshness.

### [OPEN P1] Entry intent does not carry executable snapshot facts

**Location:** `src/engine/cycle_runtime.py::deps.create_execution_intent(...)`,
`src/execution/executor.py::create_execution_intent`,
`src/state/venue_command_repo.py::_assert_executable_snapshot_gate`.
**Problem:** `create_execution_intent()` supports `executable_snapshot_id`,
`min_tick_size`, `min_order_size`, and `neg_risk`, and
`venue_command_repo` requires a non-empty snapshot id before inserting a venue
command. The runtime callsite does not pass those fields.
**Impact:** Even if strategy gates and bankroll gates are cleared, live entry
cannot form the certified snapshot -> command path. It should fail closed at
the snapshot gate rather than silently trading, but it is not a complete
order-entry path.
**False-positive boundary:** This is not a paper-mode fill issue. It concerns
the live command insertion path after executable snapshot gating is enforced.
**Proposed remediation:**
1. Build executable market snapshots during market discovery or immediately
   before command insertion from fresh Gamma/CLOB facts.
2. Carry `snapshot_id`, min tick, min order size, and neg-risk from decision to
   `ExecutionIntent`.
3. Make missing snapshot id an evaluator/entry rejection before opportunity is
   marked tradeable.
4. Add an integration test from candidate -> decision -> intent -> command repo
   proving the snapshot gate passes with fresh facts and fails stale facts.
**Acceptance evidence:** A live-mode dry-run can insert a command only with a
fresh executable snapshot; stale/missing snapshot produces structured no-trade.

### [OPEN P1] No production executable snapshot producer/refresher was found

**Location:** `src/state/snapshot_repo.py`, `src/engine/cycle_runtime.py`,
`src/execution/exit_lifecycle.py::_latest_exit_snapshot_context`.
**Problem:** The executable snapshot gate is present, but repository search found
`ExecutableMarketSnapshotV2(...)` construction and `insert_snapshot(...)` calls
only in tests and the snapshot repository module, not in a live runtime producer.
Exit lifecycle also expects the latest fresh `executable_market_snapshots` row
by token and returns an empty context when none exists, deliberately letting the
executor fail closed. Current audit DBs had zero `executable_market_snapshots`.
**Impact:** Threading `snapshot_id` through `ExecutionIntent` is not sufficient.
Both entry and exit can remain blocked because no live component proves and
refreshes the executable CLOB facts required by the U1 gate.
**False-positive boundary:** This is a static/runtime-inventory finding. A
producer outside `src/` or outside the current branch would invalidate it only if
it writes the canonical `executable_market_snapshots` table with fresh Gamma/CLOB
facts before entry and exit decisions.
**Proposed remediation:**
1. Add or identify the single production owner for executable snapshot creation.
2. Build snapshots from fresh market metadata, token ids, orderbook state, min
   tick, min order, fee/neg-risk facts, and freshness deadline.
3. Refresh snapshots for both candidate entry tokens and held-position exit
   tokens before intent creation.
4. Make missing snapshot a structured no-trade/no-exit-side-effect state with
   operator-visible reason, not a hidden downstream executor rejection.
5. Add an integration test proving a real market scan creates a usable snapshot
   and a stale/missing snapshot blocks both entry and exit command insertion.
**Acceptance evidence:** A live dry-run shows non-empty fresh
`executable_market_snapshots`, entry/exit intents cite those ids, and no command
can use a stale or test-only snapshot.

### [OPEN P1] V2 submit path still uses compatibility envelope

**Location:** `src/venue/polymarket_v2_adapter.py::place_limit_order` and
`_create_compat_submission_envelope`.
**Problem:** The V2 adapter explicitly says this path exists until U1 wires
executable market snapshots into the executor. It creates placeholder market
identity such as `condition_id="legacy:{token_id}"`, `question_id="legacy-compat"`,
and identical YES/NO token ids.
**Impact:** The final SDK submit envelope is not closed over U1-certified
market facts. This is an execution identity gap even after entry snapshot
threading is fixed.
**False-positive boundary:** The compatibility helper may be acceptable for
local smoke or pre-submit rejection surfaces. It is not acceptable evidence for
certified live-money venue submission.
**Proposed remediation:**
1. Replace compatibility envelope construction with an envelope created from
   executable snapshot facts.
2. Require condition id, question id, YES/NO token ids, min tick, min order,
   neg-risk, fee fields, and source timestamps to be present and hash-bound.
3. Reject any live submit path that carries `legacy:` identity.
4. Add a test that fails if YES/NO token ids collapse to the selected token.
**Acceptance evidence:** Submit envelope canonical hash includes real snapshot
identity; live path has no `legacy-compat` marker.

### [OPEN P2] Entry max-slippage budget is typed but not enforced

**Location:** `src/contracts/execution_intent.py`,
`src/execution/executor.py::create_execution_intent`, and `_live_order`.
**Problem:** `ExecutionIntent.max_slippage` is a typed `SlippageBps`, but the
contract comment explicitly says enforcement is a later packet and repository
search shows no live reader that rejects or clamps entry orders by that budget.
The dynamic limit branch can jump to `best_ask` when the gap is within 5% of
ask, independent of the 200 bps adverse-slippage budget. Read-only reproduction
with base limit `0.48`, `best_ask=0.50`, and `max_slippage=200 bps` produced
`limit_price=0.50`, an adverse jump of about `416.7 bps`.
**Impact:** A live order can intentionally improve price for fill probability
while exceeding the configured slippage budget. Limit orders still cap the venue
price, but the configured risk budget is advisory rather than executable.
**False-positive boundary:** This is not a market-order overfill claim. For GTC
limit orders the submitted limit still bounds execution. The gap is that the
named `max_slippage` control does not alter the submitted limit or reject the
intent.
**Proposed remediation:**
1. Define the slippage reference price: base computed limit, VWMP, best ask, or
   all-in fee-adjusted entry price.
2. Enforce `intent.max_slippage` before command persistence by rejecting or
   clamping dynamic-limit jumps beyond budget.
3. Record applied slippage bps and reference price in `ExecutionIntent` and
   command payload evidence.
4. Add tests where dynamic best ask is within the 5% jump window but above the
   max-slippage budget.
**Acceptance evidence:** An entry fixture with a 416 bps adverse dynamic jump and
200 bps budget cannot submit at the higher price unless an explicit operator
override is recorded.

### [OPEN P2] Market scan authority is dropped before entry discovery

**Location:** `src/data/market_scanner.py::MarketSnapshot` and
`find_weather_markets`; `src/engine/cycle_runtime.py` market discovery loop.
**Problem:** The scanner has an authority type (`VERIFIED`, `STALE`,
`EMPTY_FALLBACK`, `NEVER_FETCHED`) but `find_weather_markets()` returns bare
market dicts. Runtime consumes the bare list and can continue from keyword
fallback or stale degraded provenance without a live-entry fail-closed branch.
**Impact:** Entry decisions can be formed from data whose authority was known
at the scanner boundary but lost before runtime gating.
**False-positive boundary:** Cycle start clears cache, reducing stale-cache
risk. This remains a provenance plumbing gap, not proof of an observed stale
live trade.
**Proposed remediation:**
1. Return a provenance-tagged scan result to runtime, not only a list.
2. Fail closed for new entries on `STALE`, `EMPTY_FALLBACK`, or keyword fallback
   unless an explicit operator-read-only mode is active.
3. Preserve authority in no-trade/availability facts.
4. Add tests for fresh fetch success, network failure with cache, and empty
   fallback.
**Acceptance evidence:** Entry lane refuses non-`VERIFIED` market scan authority
while monitor/exit lanes remain read-only.

### [OPEN P1] RED force-exit sweep is proxy-only, not venue cancel/sell

**Location:** `src/engine/cycle_runner.py::_execute_force_exit_sweep`,
`src/execution/command_recovery.py`, `tests/test_riskguard_red_durable_cmd.py`.
**Problem:** Architecture law says RED must cancel pending orders and exit all
positions immediately. The cycle sweep marks `exit_reason="red_force_exit"` and,
when enough context exists, inserts durable `CANCEL` proxy commands. Its own
docstring states it does not post sell orders in-cycle and remains
side-effect-free. Command recovery later observes `CANCEL_PENDING` by polling
venue state, but does not call `cancel_order()` for still-active orders; it
waits for an already-missing or terminal order to appear as cancelled.
**Impact:** A live RED state can look compliant in local summaries while pending
orders and active exposure remain at the venue until normal monitor/exit
machinery happens to act. That is a control-plane design gap, not a modeling
error.
**False-positive boundary:** If a separate currently active runtime consumes
these proxy commands and performs the venue cancel/sell side effects, this
finding must be narrowed to that consumer's SLA. No such production consumer was
identified in this audit slice.
**Proposed remediation:**
1. Define the RED action contract as an executable command flow, not only a
   lifecycle mark.
2. On RED, immediately cancel live pending entry/exit orders with venue
   `cancel_order()` or a proven command worker that does so within a bounded SLA.
3. Submit exit/sweep sell orders for active filled exposure through the certified
   executable snapshot path, with explicit fallback when no safe bid exists.
4. Persist separate facts for cancel requested, cancel acked, sell submitted,
   sell filled, and residual exposure.
5. Add a fail-closed test with a fake venue proving RED invokes cancel/sell side
   effects or records an actionable `RED_SWEEP_BLOCKED` state.
**Acceptance evidence:** In a RED dry-run with pending and active positions,
venue cancel/sell methods or their certified command-worker equivalents are
called exactly once per eligible exposure, and residual exposure is visible until
confirmed closed.

### [OPEN P1] Fail-closed RED causes do not trigger force-exit sweep

**Location:** `src/riskguard/riskguard.py::get_current_level`,
`get_force_exit_review`, and `src/engine/cycle_runner.py` risk gating.
**Problem:** `get_current_level()` returns RED fail-closed when risk state is
missing, stale, or unreadable. The cycle only calls the sweep when
`get_force_exit_review()` is true. That flag is persisted only when
`daily_loss_level == RED`, and `get_force_exit_review()` returns false when no
row exists. The result is an entry block for some RED causes, not the documented
RED cancel/sweep behavior.
**Impact:** The most infrastructure-sensitive RED states, such as stale
RiskGuard or missing risk DB rows, can stop entries while leaving existing venue
orders/exposure unmanaged. RED action semantics depend on the cause of RED even
though the documented risk level contract does not.
**False-positive boundary:** Daily-loss RED does set `force_exit_review=1`.
This finding concerns RED from staleness, missing rows, DB-read errors, or other
component levels that raise the overall risk level without setting that flag.
**Proposed remediation:**
1. Derive force-exit behavior from effective `RiskLevel.RED`, not only the
   daily-loss flag.
2. Preserve reason codes so operators can distinguish daily-loss RED from
   infrastructure fail-closed RED.
3. For infrastructure RED, decide whether immediate venue sweep or
   authority-limited safe cancel is required; encode that policy explicitly.
4. Make no-row/stale-row behavior conservative for both entry block and existing
   exposure handling.
5. Add tests for daily-loss RED, stale RiskGuard RED, no-row RED, and DB-error
   RED.
**Acceptance evidence:** Every effective RED scenario produces either executed
cancel/sweep actions or an explicit, alerting `RED_ACTION_BLOCKED` state with no
silent entry-block-only mode.

### [OPEN P2] ORANGE risk currently behaves like entry-block-only YELLOW

**Location:** `src/riskguard/risk_level.py::LEVEL_ACTIONS`,
`src/engine/cycle_runner.py` entry gating, `tests/test_runtime_guards.py`.
**Problem:** The risk law says ORANGE means no new entries and exit positions at
favorable prices. Runtime gating treats YELLOW, ORANGE, RED, and
DATA_DEGRADED uniformly for entry blocking, while monitoring continues normally.
No separate ORANGE path was identified that actively scans held exposure for
favorable exit opportunities beyond ordinary exit triggers.
**Impact:** ORANGE does not appear to have an enforceable runtime behavior
distinct from YELLOW. That can leave expected de-risking unrealized during
elevated but non-RED risk.
**False-positive boundary:** Existing monitor/exit logic may independently exit
positions when normal economics trigger. The gap is that ORANGE itself does not
appear to lower or override exit thresholds as documented.
**Proposed remediation:**
1. Define "favorable price" in executable terms: minimum bid, max slippage,
   expected value floor, or break-even threshold.
2. Thread ORANGE state into exit evaluation so held positions are offered for
   sale when the favorable-price rule is met.
3. Keep YELLOW and ORANGE distinct in summary reason codes and tests.
4. Add fixtures proving ORANGE exits a favorable held position while YELLOW only
   blocks entries and monitors.
**Acceptance evidence:** ORANGE produces deterministic favorable-exit intents
for qualifying held positions and no longer has identical behavior to YELLOW.

### [OPEN P2] Whale-toxicity exit gate has no live detector feeding it

**Location:** `src/engine/monitor_refresh.py::refresh_position`,
`src/state/portfolio.py::Position.evaluate_exit`, and
`src/execution/exit_triggers.py::evaluate_exit_triggers`.
**Problem:** Exit logic has a `WHALE_TOXICITY` immediate-exit branch and the
execution module advertises adjacent-bin sweep detection, but monitor refresh
initializes `pos.last_monitor_whale_toxicity = None` and no production path was
identified that computes and sets it to `True`. The portfolio exit path records
`whale_toxicity_unavailable` when the field is `None`.
**Impact:** A documented microstructure safety gate cannot trigger in live
monitoring, so adjacent-bin sweep toxicity is advisory/spec-only unless another
component supplies the field.
**False-positive boundary:** Tests can pass `is_whale_sweep=True` directly to
the lower-level trigger, proving the branch works when supplied. The gap is the
runtime detector and data feed, not the branch's local behavior.
**Proposed remediation:**
1. Define the adjacent-bin sweep signal from orderbook/trade facts, including
   lookback window, affected sibling bins, size threshold, and confidence.
2. Compute it during monitor refresh from fresh CLOB/user-channel facts and set
   `last_monitor_whale_toxicity` to a concrete bool with provenance.
3. Fail to conservative hold/exit policy explicitly when the detector is
   unavailable, rather than silently treating it as absent.
4. Add an integration fixture where sibling-bin sweep facts cause a held
   position to produce `WHALE_TOXICITY`.
**Acceptance evidence:** Runtime monitoring can produce both
`whale_toxicity_available` and `WHALE_TOXICITY` exit decisions from real or
fixture-backed market data.

### [MITIGATED 2026-04-30] Legacy fill polling no longer treats `MATCHED` as a fill terminal

**Location:** `src/execution/fill_tracker.py`, `src/execution/exit_lifecycle.py`.
**Original problem:** Legacy polling set `FILL_STATUSES = {"FILLED", "MATCHED"}`.
Polymarket trade lifecycle treats `MATCHED` as non-terminal; `CONFIRMED` is the
successful terminal.
**Antibody deployed:** Entry and exit polling now use `CONFIRMED` as the only
success terminal. `MATCHED`/`FILLED` entry payloads record venue facts and
optimistic exposure only; they do not set `entry_fill_verified`, `entered_at`, or
canonical entry-fill truth. Exit polling leaves `MATCHED`/`FILLED` pending.
Stale deps cannot remove `CONFIRMED` from the fill-status set.
**Evidence:** `src/execution/fill_tracker.py::FILL_STATUSES`,
`_fill_statuses()`, `_record_optimistic_entry_observed()`;
`src/execution/exit_lifecycle.py::FILL_STATUSES`;
`tests/test_live_safety_invariants.py::test_confirmed_fill_survives_stale_deps_fill_statuses`,
`test_legacy_polling_matched_maps_numeric_live_runtime_id_to_optimistic_lot`,
and `test_pending_exit_filled_status_does_not_economically_close`.
**Residual:** If a future adapter proves an order-level status is irreversible
fill finality before trade `CONFIRMED`, it needs a typed order-finality contract
instead of reusing `MATCHED`/`FILLED` as generic success terminals.

### [MITIGATED 2026-04-30; RESIDUAL P2] Entry partial fills preserve filled exposure after remainder cancel

**Location:** `src/execution/fill_tracker.py::_check_entry_fill`,
`_record_partial_entry_observed`, `_mark_entry_filled`.
**Original problem:** A `PARTIAL` entry could remain `pending_tracked`; after the
remainder timed out and cancellation succeeded, the entire local position could
be voided as `UNFILLED_ORDER`, losing already-filled shares.
**Antibody deployed:** `PARTIAL` now records filled shares, fill price, and cost
basis without marking the entry fully active. If the remainder is cancelled or
expires after observed fill, the filled quantity is materialized instead of
voiding the position.
**Evidence:** `tests/test_live_safety_invariants.py::test_partial_remainder_cancel_preserves_filled_exposure`.
**Residual:** Rich command-fact semantics for partial->failed/retrying and a
confirmed optimistic-vs-final partial ledger remain a separate packet. The
specific void-after-cancel exposure-loss bug is no longer an active open gap.

### [OPEN P1] Exit partial fills do not reduce local position exposure

**Location:** `src/execution/exit_lifecycle.py::check_pending_exits`.
**Problem:** Exit lifecycle now closes only on `CONFIRMED` full-fill finality and
`CANCELLED`/`EXPIRED`/`REJECTED` remain retry states. A sell order status of
`PARTIAL` is still neither materialized nor used to reduce shares. Read-only dynamic
reproduction with a 25-share pending exit and a venue payload
`{"status":"PARTIAL","filledSize":"4.0","avgPrice":"0.55"}` returned
`unchanged=1` and left `shares=25`. A subsequent `CANCELLED` status moved the
position to `retry_pending`, still with `shares=25`.
**Impact:** Zeus can overstate remaining exposure after a partial exit fill,
retry selling shares that were already sold, miscompute P&L/cost basis, and make
chain reconciliation repair a state that should have been handled in the exit
lifecycle itself.
**False-positive boundary:** Full `CONFIRMED` status closes the position
economically. `MATCHED`/`FILLED` no longer close exits; the remaining defect is
partial sell fill plus remaining-share retry/cancel handling.
**Proposed remediation:**
1. Add explicit partial-exit semantics: realized shares, realized price,
   remaining shares, and remaining cost basis.
2. On partial sell fill, reduce local `shares` and cost basis immediately while
   emitting a realized-fill fact.
3. When the remainder is cancelled, retry only the remaining unsold shares.
4. Prevent duplicate sell attempts for already-realized shares by tying retries
   to command/fill facts.
5. Add tests for partial->partial, partial->cancel remainder,
   partial->full-remainder, and partial->venue-missing flows.
**Acceptance evidence:** A 25-share exit with 4 shares filled and the remainder
cancelled leaves 21 shares pending/active, realizes 4 shares of P&L, and never
resubmits a 25-share sell.

### [OPEN P1] Harvester live settlement write is HIGH-only for LOW markets

**Location:** `src/execution/harvester.py::_lookup_settlement_obs`,
`run_harvester`, `_write_settlement_truth`, and `harvest_settlement`.
**Problem:** `run_harvester()` fetches all closed temperature events, including
LOW/`mn2t6` markets, but `_lookup_settlement_obs()` selects only
`observations.high_temp`. `_write_settlement_truth()` applies
`SettlementSemantics` to `obs_row["high_temp"]` and always writes
`HIGH_LOCALDAY_MAX` identity fields (`temperature_metric="high"`,
`observation_field="high_temp"`). The later calibration call tries to infer
LOW from `source_model_version`, but the canonical settlement truth row has
already been written as HIGH.
**Impact:** If harvester live mode is enabled, LOW settled markets can be
written, quarantined, trained, or audited against the daily maximum instead of
the daily minimum. That is a physical-quantity identity break in settlement ->
learning.
**False-positive boundary:** `ZEUS_HARVESTER_LIVE_ENABLED` currently defaults
off, so this does not mutate current DB state unless the flag is enabled. HIGH
markets are consistent with the current high-only helper.
**Proposed remediation:**
1. Infer market temperature metric from Gamma event slug/title/series and carry
   it through `run_harvester`.
2. Make settlement observation lookup metric-aware: HIGH uses `high_temp`, LOW
   uses `low_temp`.
3. Write `LOW_LOCALDAY_MIN` identity for LOW settlement rows, including
   `temperature_metric`, `physical_quantity`, `observation_field`, and
   data-version provenance.
4. Pass the settlement value into `harvest_settlement()` so calibration pairs
   contain the metric-correct realized value.
5. Add HIGH and LOW settled-event fixtures with identical city/date but distinct
   winning bins.
**Acceptance evidence:** A LOW Gamma fixture writes a `settlements` row with
`temperature_metric='low'`, `observation_field='low_temp'`, and a LOW-derived
settlement value; HIGH and LOW rows for the same city/date can coexist without
overwriting each other.

### [OPEN P1] Settlement observation lookup ignores authority, station, and metric identity

**Location:** `src/execution/harvester.py::_lookup_settlement_obs`.
**Problem:** `_lookup_settlement_obs()` queries observations by
`city/target_date/high_temp IS NOT NULL`, then returns the first source-family
match. It does not require `authority='VERIFIED'`, does not check `station_id`
against the contract station, does not select the field required by
temperature metric, and does not order by freshness or provenance. A read-only
in-memory reproduction returned a `QUARANTINED` WU row with station `WRONG` as
the settlement observation because those columns are not selected.
**Impact:** A quarantined, stale, wrong-station, or wrong-metric observation can
be promoted into a `VERIFIED` settlement row if its rounded value happens to fit
the winning bin. This is a source-truth authority leak at the final learning
boundary.
**False-positive boundary:** The daily observation append path intends to write
`authority='VERIFIED'` rows for accepted collectors. The gap is that harvester
does not enforce that contract at read time, so any legacy or quarantined row
with the same source family is eligible.
**Proposed remediation:**
1. Select and require observation `authority='VERIFIED'`.
2. Verify `station_id` or source-specific station metadata against the contract
   station/source table used for that market.
3. Require metric-specific field presence (`high_temp` for HIGH, `low_temp` for
   LOW) and matching unit/source data version.
4. Order deterministically by verified freshness or reject duplicates requiring
   manual authority review.
5. Persist the accepted observation id/station/authority in settlement
   provenance and calibration pair lineage.
**Acceptance evidence:** A quarantined or wrong-station observation is rejected
even when it matches the winning bin; only a metric-correct VERIFIED source row
can create a VERIFIED settlement.

### [OPEN P1] Settled pending-exit exposure can be skipped indefinitely

**Location:** `src/execution/harvester.py::_settle_positions`,
`src/execution/exit_lifecycle.py`.
**Problem:** `_settle_positions()` skips positions in `pending_exit` unless
`exit_state == "backoff_exhausted"`, and also skips `exit_intent`,
`sell_placed`, `sell_pending`, and `retry_pending`. A read-only in-memory
reproduction with a 20-share `pending_exit/sell_pending` position on the settled
market returned `settled count 0`, left the position unchanged, and emitted no
settlement event.
**Impact:** Once the market resolves, settlement truth is authoritative for any
remaining exposure. A resting or retrying exit order that did not fill should not
block settlement terminalization forever. Skipping can leave resolved exposure in
runtime truth, delay P&L/learning, and continue retry/sell logic after the market
has become a settlement event.
**False-positive boundary:** If the sell order actually filled before
settlement, the position should be `economically_closed` and can later settle
normally. The gap is pending or retrying sell state with unresolved residual
exposure at settlement time.
**Proposed remediation:**
1. At settlement, reconcile/cancel any in-flight exit order and materialize
   confirmed filled quantity first.
2. Apply settlement close to the remaining exposure regardless of prior
   `pending_exit` state.
3. Mark stale exit commands as resolution-superseded rather than leaving them in
   retry state.
4. Use partial-exit fill facts so settlement closes only unsold residual shares.
5. Add tests for `sell_pending`, `retry_pending`, `backoff_exhausted`, and
   economically closed positions at settlement.
**Acceptance evidence:** A settled market terminalizes every non-terminal
residual exposure exactly once, including positions that were in exit retry, and
does not resubmit sell orders after settlement.

### [OPEN P1] Paris config uses LFPG while current markets resolve on LFPB

**Location:** `config/cities.json` Paris `wu_station` and
`settlement_source`.
**Problem:** Fresh Gamma daily-temperature probe for 2026-04-28..2026-04-30
showed Paris HIGH/LOW resolutionSource as WU Bonneuil-en-France `LFPB`, while
production config still points Paris at `LFPG` / Charles de Gaulle. A broader
read-only active-event probe found `146` active daily-temperature events and
`6` station mismatches; all `6` were Paris HIGH/LOW Apr 28-30 with
`LFPB` vs `LFPG`. Existing LOW backfill evidence also records `LFPB`. A later
read-only source-boundary sweep found observed Paris HIGH contracts resolving
on `LFPG` through 2026-04-18 and on `LFPB` from 2026-04-19 onward. Paris LOW
slugs were not observable for 2026-04-15..2026-04-22 in that sweep; the first
observable Paris LOW event was 2026-04-23 and resolved on `LFPB`.
**Impact:** Paris observation, model calibration, signal generation, and
settlement rebuild can use a different station than the market contract. This
is a contract/source truth mismatch, not a modeling error.
**False-positive boundary:** Both WU pages currently respond. The issue is not
endpoint liveness; it is which WU station the active Polymarket contract names.
The same active-event probe did not find non-Paris station mismatches among
recognized configured cities.
**Proposed remediation:**
1. Run a fresh source audit for all active weather cities and both HIGH/LOW
   families.
2. Decide whether Paris should be globally remapped to `LFPB` for future
   contracts or routed by date/family, preserving the observed HIGH boundary
   between 2026-04-18 (`LFPG`) and 2026-04-19 (`LFPB`) plus the LOW unknown
   window before 2026-04-23.
3. Quarantine affected Paris training/settlement rows until station identity is
   reconciled.
4. Add a source-contract test that compares current Gamma resolutionSource
   station id against the configured settlement source for tradable markets.
**Acceptance evidence:** Paris live candidates only proceed when configured
station id matches event resolutionSource or an explicit dated routing table.

### [OPEN P1] Live Open-Meteo ENS snapshots can fail to persist and return empty snapshot id

**Location:** `src/data/ensemble_client.py`, `src/engine/evaluator.py::_store_ens_snapshot`.
**Problem:** Open-Meteo parsing returns `issue_time=None` and
`first_valid_time`, while `_store_ens_snapshot()` reads `issue_time` and
`valid_time`. The legacy `ensemble_snapshots` table requires non-null
`issue_time` and `valid_time`. The exception is caught and the function returns
`""`, allowing decision flow to continue without `decision_snapshot_id` or
`p_raw_json` persistence.
**Impact:** Decision audit, settlement learning, harvester joins, and replay
traceability lose their primary snapshot key. This breaks forecast signal ->
learning lineage.
**False-positive boundary:** A registered ingest source such as TIGGE can carry
`run_init_utc`. The current default live Open-Meteo path is the affected path.
**Proposed remediation:**
1. Normalize Open-Meteo payloads to a non-null issue/valid-time contract, using
   a documented availability semantics rather than silent `None`.
2. Use `first_valid_time` or explicit derived valid time where appropriate, and
   persist availability provenance.
3. Treat failed snapshot persistence as a signal-quality rejection, not as an
   empty snapshot id continuation.
4. Add an in-memory DB regression with NOT NULL `ensemble_snapshots` schema and
   a live Open-Meteo-shaped payload.
**Acceptance evidence:** Every tradeable decision has non-empty
`decision_snapshot_id`; `_store_ens_snapshot` failure prevents entry.

### [OPEN P1] Calibration maturity edge-threshold multiplier is dead on the live path

**Location:** `src/calibration/manager.py::maturity_level` and
`edge_threshold_multiplier`; `src/engine/evaluator.py` edge selection and
sizing.
**Problem:** The calibration manager states that maturity Level 4 (`n < 15`)
means no Platt model and an edge threshold multiplier of `3x`. The helper
`edge_threshold_multiplier(level)` encodes `1x/1.5x/2x/3x`, but repository
search shows the live evaluator and replay paths do not call it. `cal_level`
is passed to `compute_alpha()`, while the evaluator only applies
strategy/control `threshold_multiplier` by reducing Kelly size after an edge
has already passed CI/FDR selection.
**Impact:** If outer readiness gates are bypassed or a bucket lacks a
calibrator, Level 4 raw-probability decisions can still become tradeable based
on positive CI/FDR and low alpha alone, without the documented `3x` edge
threshold. That weakens the probability-chain contract exactly when calibration
evidence is absent.
**False-positive boundary:** Current runtime is already blocked by readiness,
zero bankroll, no executable snapshots, and empty calibration tables. This
finding is about the evaluator's mathematical guard if those outer gates are
cleared or if only a specific city/season/metric bucket is immature.
**Proposed remediation:**
1. Decide whether calibration Level 4 is a hard no-trade state or a tradable
   raw-probability state with an explicit stronger edge threshold.
2. If tradable, apply `edge_threshold_multiplier(cal_level)` before FDR/entry
   selection or as an explicit minimum forward-edge gate, not only as a Kelly
   size haircut.
3. If not tradable, return a structured `CALIBRATION_IMMATURE` no-trade reason
   whenever `cal is None` or `cal_level == 4`.
4. Record `calibration_level`, applied multiplier, and calibrated-vs-raw
   probability mode in decision evidence.
5. Add tests for Level 1, Level 3, and Level 4 buckets proving the maturity
   rule affects executable decisions.
**Acceptance evidence:** A Level 4 fixture cannot produce the same tradeable
decision as a Level 1 fixture with the same raw edge unless it clears the
documented stricter maturity rule, and decision evidence records that rule.

### [OPEN P1] Closed/non-accepting Gamma child markets enter outcome vector

**Location:** `src/data/market_scanner.py::_extract_outcomes`,
`src/engine/evaluator.py` market price loop.
**Problem:** Current Gamma responses can contain mixed child markets under an
open event. For Paris 2026-04-28, some child markets were `closed=true` while
the event was still returned by active discovery. `_extract_outcomes()` parses
all child markets with tokens/prices and never checks `closed`, `active`,
`acceptingOrders`, or `enableOrderBook`.
**Impact:** Closed bins can skew market probability, add stale token mapping,
or cause the evaluator to reject the whole candidate when an untradeable bin has
an empty orderbook.
**False-positive boundary:** Fully open future events are not affected. The
observed risk occurs near resolution or during partial child-market closure.
**Proposed remediation:**
1. Filter child markets to tradable status before outcome extraction.
2. Preserve skipped closed/non-accepting labels as availability facts rather
   than silently ignoring market topology.
3. Decide whether a partially closed family should be a no-trade for the whole
   event or a reduced-family evaluation; default should be fail-closed until
   the statistical treatment is proven.
4. Add Gamma fixture tests with mixed `closed/acceptingOrders` child markets.
**Acceptance evidence:** Closed child markets never contribute token ids/prices
to p_market; partial family closure produces explicit no-trade or verified
reduced-family logic.

### [OPEN P2] v2 row-count observability reads trade shadow tables before world truth

**Location:** `src/observability/status_summary.py::_get_v2_row_counts`,
`src/state/db.py::get_trade_connection_with_world`.
**Problem:** Status summary opens a trade connection with world attached, but
queries unqualified v2 table names. Current `zeus_trades.db` has empty v2
shadow tables while `world.ensemble_snapshots_v2` has 684,624 rows, so status
can report `ensemble_snapshots_v2=0` and emit `v2_empty_despite_closure_claim`.
**Impact:** Operator readiness dashboards can report false data-readiness
failure or hide the real source of missing calibration. This is an observability
false alarm, not proof that world v2 data is absent.
**False-positive boundary:** Tables that intentionally live in trade DB should
stay trade-qualified. The fix must be table-role aware, not blanket
`world.` for everything.
**Proposed remediation:**
1. Classify each v2 table as world or trade authority.
2. Query row counts with explicit schema qualification.
3. Add status tests with both main/trade and attached world tables populated
   differently.
4. Separate "world data empty" from "trade shadow empty" in discrepancy flags.
**Acceptance evidence:** Status reports `world.ensemble_snapshots_v2=684624`
on the current DB and does not emit the wrong closure-claim flag for that table.

### [OPEN P1] Weather multi-bin `buy_no`/shoulder-sell hypotheses are not executable

**Location:** `src/strategy/market_analysis.py::find_edges`,
`src/strategy/market_analysis_family_scan.py::scan_full_hypothesis_family`,
`src/engine/evaluator.py` FDR filtering and token routing.
**Problem:** `scan_full_hypothesis_family()` tests every bin in both
`buy_yes` and `buy_no` directions, but `MarketAnalysis.find_edges()` only
creates `buy_no` edges when `len(self.bins) <= 2`. Normal Polymarket weather
families are multi-bin events, while the durable strategy catalog explicitly
includes shoulder-bin sell / native NO-token trades. The evaluator later
intersects FDR-selected hypotheses with the already-created edge list, so a
selected multi-bin `buy_no` hypothesis has no `BinEdge` object and never reaches
the NO-token route.
**Read-only reproduction:** An in-memory three-bin weather family with a
positive lower-shoulder `buy_no` edge produced a full-family selected
hypothesis for `67 or lower / buy_no`, while `find_edges()` returned only the
upper-shoulder `buy_yes` edge. No source files or DB rows were mutated.
**Impact:** The Shoulder Bin Sell alpha family can be structurally unreachable
for standard multi-bin weather markets. Zeus may record that the full-family
hypothesis existed while never producing a tradeable NO-side decision, causing
strategy attribution, opportunity accounting, and live-alpha expectations to
diverge from reality.
**False-positive boundary:** The `len(self.bins) <= 2` guard may have been
introduced to avoid synthetic `1-p` math on multi-bin families. If so, the
restriction is a valid safety choice, but then shoulder-bin sell must be marked
unsupported until a native NO-token VWMP and family-consistent probability
contract is implemented.
**Proposed remediation:**
1. Decide the product contract: either explicitly disable weather multi-bin
   `buy_no` and mark Shoulder Bin Sell as not live-supported, or implement a
   native NO-token path for each child market.
2. If enabled, compute `buy_no` edges from native NO-token VWMP and
   direction-native posterior `P(NO for this child market)`, not from an
   undocumented synthetic market-price complement.
3. Make FDR selection and executable edge construction operate over the same
   hypothesis universe so selected hypotheses cannot disappear at execution
   time.
4. Add tests for a multi-bin shoulder-sell opportunity, a rejected synthetic
   complement case, and exact NO-token routing into `ExecutionIntent`.
**Acceptance evidence:** A real weather multi-bin fixture with an overpriced
shoulder YES market either yields a validated `buy_no` edge routed to the
native NO token, or produces an explicit `strategy_unsupported_buy_no_multibin`
no-trade reason.

### [MITIGATED 2026-04-30; RESIDUAL P2] M5 exchange reconciliation no longer promotes non-final trades to filled commands

**Location:** `src/execution/exchange_reconcile.py::run_reconcile_sweep`,
`src/execution/exchange_reconcile.py::_append_linkable_trade_fact_if_missing`,
`src/execution/exchange_reconcile.py::_fill_event_for_command`,
`src/execution/exchange_reconcile.py::_journal_positions_by_token`.
**Original problem:** REST/M5 reconciliation recorded `MATCHED`, `MINED`, and
`CONFIRMED` as linkable trade facts, then emitted `FILL_CONFIRMED` when
`filled_size >= command.size` even if the trade state was only `MATCHED`/`MINED`.
**Antibody deployed:** `_fill_event_for_command()` now returns
`PARTIAL_FILL_OBSERVED` for every non-`CONFIRMED` trade state; only
`CONFIRMED` plus filled-size coverage can emit `FILL_CONFIRMED`.
**Evidence:** `src/execution/exchange_reconcile.py::_fill_event_for_command`,
`tests/test_command_recovery.py` finality coverage, and the first-principles
finality relationship tests in `tests/test_cross_module_relationships.py`.
**Residual:** `_journal_positions_by_token()` still counts `MATCHED`, `MINED`,
and `CONFIRMED` in the position journal used for drift comparison. That residual
is a separate optimistic-vs-confirmed drift-view packet; it is not a command
finality blocker because non-`CONFIRMED` facts no longer emit `FILL_CONFIRMED`.
**Acceptance evidence:** A full-size REST/M5 `MATCHED` fact no longer moves a
command to `FILLED`; only `CONFIRMED` does. Future drift evidence should name
whether it compared optimistic or confirmed exposure.

### [MITIGATED 2026-04-30] Exit lifecycle no longer economically closes on non-final `MATCHED`/`FILLED`

**Location:** `src/execution/exit_lifecycle.py::FILL_STATUSES`,
`src/execution/exit_lifecycle.py::_check_order_fill`,
`src/execution/exit_lifecycle.py::check_pending_exits`,
`src/execution/exit_lifecycle.py::_execute_live_exit`.
**Original problem:** Exit lifecycle defined `FILL_STATUSES = {'MATCHED',
'FILLED'}` and used that set in both immediate post-submit fill checks and
later `check_pending_exits()`. A `MATCHED` order/trade status could call
`compute_economic_close()` before trade `CONFIRMED`.
**Antibody deployed:** Exit lifecycle now defines `FILL_STATUSES =
frozenset({"CONFIRMED"})`; `MATCHED` and `FILLED` are explicit non-final
observations and leave the exit pending.
**Evidence:** `src/execution/exit_lifecycle.py::FILL_STATUSES`,
`tests/test_live_safety_invariants.py::test_pending_exit_filled_status_does_not_economically_close`,
`test_pending_exit_matched_status_does_not_economically_close`, and
`test_deferred_confirmed_fill_logs_last_monitor_best_bid`.
**Residual:** If a future adapter proves a non-`CONFIRMED` order string is
irreversible fill finality, add a typed order-finality source instead of
widening this raw status set.

### [OPEN P2] Collateral preflight accepts arbitrarily stale snapshots

**Location:** `src/state/collateral_ledger.py::CollateralLedger.snapshot`,
`src/state/collateral_ledger.py::buy_preflight`,
`src/state/collateral_ledger.py::sell_preflight`,
`src/engine/cycle_runtime.py::entry_bankroll_for_cycle`,
`src/execution/executor.py::_assert_collateral_allows_buy`,
`src/execution/executor.py::_assert_collateral_allows_sell`.
**Problem:** `CollateralSnapshot` stores `captured_at`, but `buy_preflight()`
and `sell_preflight()` check only authority tier, balances, allowances, and
reservations. They do not reject stale snapshots. Cycle startup and
entry-bankroll refresh normally update the global ledger, but monitoring/exit
lanes can continue after a wallet refresh failure and executor preflight can
reuse an older process-global snapshot.
**Read-only reproduction:** A `CollateralLedger` loaded with a
`CHAIN` snapshot captured at `2000-01-01T00:00:00+00:00` returned `True` for
both `buy_preflight()` and `sell_preflight()` when balances/allowances were
numerically sufficient.
**Impact:** Live submit can pass Zeus' preflight against stale pUSD or CTF
inventory. The venue may still reject insufficient collateral, but Zeus would
have crossed local command persistence and possibly submit-side-effect
boundaries using stale account truth.
**False-positive boundary:** The main entry path does refresh wallet balance
before discovery, so this is not proof every entry uses stale collateral. The
gap is the absence of a preflight freshness invariant at the executor boundary,
especially for exit/recovery paths and failed wallet-refresh cycles.
**Proposed remediation:**
1. Add a collateral freshness deadline or max-age policy to snapshots.
2. Make buy/sell preflight fail closed on stale, missing, or degraded
   collateral truth.
3. Refresh collateral on the same path, or immediately before, command
   persistence when the snapshot is stale.
4. Add tests for stale buy and stale sell snapshots, plus the
   entry-bankroll-failure/exit-submit path.
**Acceptance evidence:** A stale `CHAIN` snapshot fails preflight with a
specific `collateral_snapshot_stale` reason, and executor tests prove stale
collateral cannot reach command persistence or SDK contact.

### [OPEN P2] Filled-command idempotency collision can rematerialize without order id

**Location:** `src/execution/executor.py::_orderresult_from_existing`,
`src/engine/cycle_runtime.py::materialize_position`,
`src/execution/fill_tracker.py::check_pending_entries`.
**Problem:** When an idempotency retry finds an existing `FILLED` command,
`_orderresult_from_existing()` returns `OrderResult(status='pending',
command_state='FILLED')` and sets `external_order_id`, but not `order_id`.
`cycle_runtime` treats `command_state='FILLED'` as durable, but derives the
new position state from `result.status`, so it materializes
`pending_tracked` rather than `entered`. `materialize_position()` stores
`order_id=result.order_id or ''`, ignoring `external_order_id`.
**Impact:** A crash/retry after command finalization can create a pending local
position with no order id even though the command is already filled. The next
fill-tracker pass then cannot query the venue order and moves the position
toward `quarantine_no_order_id` instead of active exposure. This is a recovery
path, but recovery correctness is required for live-money continuity.
**False-positive boundary:** The happy path where the first submit returns
ACKED and a later normal fill check runs is not affected. The bug appears when
the command journal is ahead of the portfolio projection, such as after a
restart, retry, or duplicate decision-id collision.
**Proposed remediation:**
1. Make `OrderResult` collision mapping preserve `order_id` as well as
   `external_order_id`.
2. When `command_state='FILLED'`, return a semantic result that materializes
   as `entered`, or make `cycle_runtime` prioritize command-state finality over
   `result.status`.
3. Add a regression that an existing FILLED command collision creates an active
   position with an order id and does not enter the no-order-id quarantine path.
4. Ensure duplicate tracking does not double-count strategy entries when the
   repair materializes active state.
**Acceptance evidence:** Retrying an already FILLED command after simulated
projection loss reconstructs an active/entered position with the venue order id
and does not require a new CLOB submit.

### [OPEN P1] ENS local-day NaNs can pass validation and create false posterior edges

**Location:** `src/data/ensemble_client.py::validate_ensemble`,
`src/signal/ensemble_signal.py::member_maxes_for_target_date`,
`src/signal/ensemble_signal.py::p_raw_vector_from_maxes`,
`src/signal/model_agreement.py::model_agreement`,
`src/strategy/market_fusion.py::compute_posterior`,
`src/strategy/market_analysis.py::MarketAnalysis.find_edges`.
**Problem:** `validate_ensemble()` rejects only when more than half of the
entire hourly matrix is NaN. That can pass a forecast where every member has a
NaN inside the selected local target-day slice. `member_maxes_for_target_date()`
then uses plain `.max()` / `.min()`, so one NaN in the local-day slice makes
that member's daily extremum NaN. `p_raw_vector_from_maxes()` bins the rounded
NaN values into no bin and returns an all-zero probability vector when total
mass is zero. `model_agreement()` receives the zero vector, `jensenshannon()`
returns NaN, and the comparison chain classifies the result as
`SOFT_DISAGREE` rather than failing closed. In complete markets with sub-1.0
raw price totals, `compute_posterior()` can then normalize market prices and
create positive YES edges even though `p_model` is `0.0`.
**Read-only reproduction:** A 51x24 ENS matrix with one NaN per member in the
target local day passed `validate_ensemble=True`, produced
`member_extrema_nan_count=51 of 51`, and returned
`p_raw=[0.0, 0.0, 0.0]`. A `MarketAnalysis` constructed with that zero
`p_cal`, `p_market=[0.30,0.30,0.30]`, and NaN member extrema produced
positive tail `buy_yes` edges with `p_model=0.0`, `edge=0.075`,
`ci_lower=0.075`, and `p_value=0.0`.
**Impact:** A provider data-quality defect can cross from weather ingestion into
edge construction without a deterministic no-trade. This is not just a missing
audit row: it can produce false alpha from market-vig normalization and tail
alpha scaling while the actual model probability vector is invalid.
**False-positive boundary:** This requires NaNs in the selected local-day slice,
not arbitrary isolated NaNs outside the traded day. If Open-Meteo never emits
such partial-hour NaNs in production, the live trigger probability is lower, but
the code contract is still wrong because the validator is global-matrix based
while the trading quantity is local-day extrema.
**Proposed remediation:**
1. Validate finite values after selecting the exact local target-day slice and
   before computing per-member extrema.
2. Use an explicit missing-data policy: either reject any member with NaN inside
   the local-day slice, or drop members only if the remaining member count still
   meets the configured minimum.
3. Add a probability-simplex gate after every p_raw/p_cal computation:
   finite, non-negative, and sum within tolerance of 1.0 for complete bin
   families. Failure must produce a structured no-trade.
4. Make `model_agreement()` reject non-finite or non-normalized vectors instead
   of classifying NaN JSD as `SOFT_DISAGREE`.
5. Make `MarketAnalysis` refuse non-finite member extrema and invalid p_raw/p_cal
   before posterior/CI construction.
**Acceptance evidence:** A local-day NaN fixture fails closed before alpha,
posterior, or bootstrap; a complete finite fixture still produces a normalized
p_raw vector; `model_agreement(np.zeros(...), valid_gfs)` raises or returns an
explicit invalid-signal no-trade, never `SOFT_DISAGREE`.

### [OPEN P1] Harvester can rebrand live decision p_raw as TIGGE training data

**Location:** `src/execution/harvester.py::run_harvester`,
`src/execution/harvester.py::get_snapshot_context`,
`src/execution/harvester.py::harvest_settlement`,
`src/calibration/store.py::add_calibration_pair_v2`.
**Problem:** Snapshot contexts carry the source model string from
`ensemble_snapshots.data_version` / `model_version` into
`harvest_settlement(source_model_version=...)`. However, `harvest_settlement()`
uses `source_model_version` only for `decision_group_id`; it calls
`add_calibration_pair_v2()` with `data_version=metric_identity.data_version`
(`tigge_mx2t6_local_calendar_day_max_v1` or
`tigge_mn2t6_local_calendar_day_min_v1`) and omits `source`. Because
`add_calibration_pair_v2()` treats empty `source` as "skip explicit source
check" and whitelists by `data_version` prefix, live decision p_raw can be
stored as `training_allowed=1` TIGGE calibration rows even when the p_raw came
from `live_v1` / Open-Meteo. The same harvester path also has no parameter for
`decision_snapshot_id`, so `calibration_pairs_v2.snapshot_id` remains `NULL`
even when `_snapshot_contexts_for_market()` resolved a concrete decision
snapshot.
**Read-only reproduction:** Calling `harvest_settlement()` in an in-memory DB
with `source_model_version='live_v1'`, p_raw `[0.6, 0.4]`, and a HIGH metric
wrote two `calibration_pairs_v2` rows with
`data_version='tigge_mx2t6_local_calendar_day_max_v1'` and
`training_allowed=1`. No TIGGE forecast source was present in the input.
**Impact:** The learning loop can train future Platt models on source-mislabeled
examples. That is not model imperfection; it changes the empirical distribution
being fitted and destroys the ability to audit whether live edge came from the
same forecast source, issue time, and p_raw construction as the training row.
**False-positive boundary:** Stage-2 learning currently requires the harvester
live flag and DB-shape preflight, so this is not proof the current DB has been
mutated by the path. It is a live-enable blocker: once the flag and preflight are
cleared, the source rebranding happens deterministically.
**Proposed remediation:**
1. Carry `decision_snapshot_id`, source id, provider model, and snapshot
   `data_version` through `get_snapshot_context()` and `harvest_settlement()`.
2. Store calibration pair `data_version` from the actual snapshot source, not
   from `MetricIdentity.data_version`. Keep metric identity as separate
   `temperature_metric` / `physical_quantity` / `observation_field` fields.
3. Pass explicit `source` into `add_calibration_pair_v2()` and make empty
   `source` non-training unless a migration-specific override is present.
4. Populate `calibration_pairs_v2.snapshot_id` from the resolved decision
   snapshot, and reject learning rows when snapshot lookup is missing or
   degraded.
5. Add tests for `live_v1`, Open-Meteo, TIGGE, and LOW snapshot contexts proving
   source labels and `training_allowed` match the real forecast source.
**Acceptance evidence:** A `source_model_version='live_v1'` harvester fixture no
longer writes `data_version='tigge_*'` or `training_allowed=1` unless the source
is explicitly promoted by policy; every training-allowed pair has a non-null
snapshot id and an auditable source/provider lineage.

### [MITIGATED 2026-04-30] Command recovery no longer turns non-final `MINED`/`FILLED` into `FILL_CONFIRMED`

**Location:** `src/execution/command_recovery.py::_reconcile_row`,
`src/state/venue_command_repo.py::append_event`,
`docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md`.
**Original problem:** The command recovery loop treated a recovered
`SUBMIT_UNKNOWN_SIDE_EFFECT` order response with venue status in
`{"FILLED", "MINED", "CONFIRMED"}` as `FILL_CONFIRMED`, collapsing non-final
venue observations into confirmed command truth.
**Antibody deployed:** Recovery now emits `FILL_CONFIRMED` only for
`CONFIRMED`; `FILLED`, `MATCHED`, `MINED`, `PARTIAL`, `PARTIALLY_MATCHED`, and
`PARTIALLY_FILLED` emit `PARTIAL_FILL_OBSERVED`.
**Evidence:** `src/execution/command_recovery.py::_reconcile_row`,
`tests/test_command_recovery.py::test_unknown_side_effect_nonconfirmed_status_stays_partial_not_fill_finality`,
and `test_unknown_side_effect_confirmed_reaches_fill_finality`.
**Residual:** A future adapter may add typed order-finality proof, but raw
`MINED`/`FILLED` recovery responses no longer advance to `FILLED`.

### [OPEN P1] Day0 stale/epoch observations can still produce tradeable p_raw

**Location:** `src/data/observation_client.py::_fetch_wu_observation`,
`src/data/observation_client.py::_select_local_day_samples`,
`src/signal/forecast_uncertainty.py::day0_nowcast_context`,
`src/signal/day0_signal.py::Day0Signal`, `src/engine/evaluator.py` Day0 path.
**Problem:** WU, the priority Day0 settlement-source path for WU cities, stores
`valid_time_gmt` as the raw `Day0ObservationContext.observation_time` epoch.
`build_day0_temporal_context()` can parse that epoch for solar/remaining-hour
context, but `day0_nowcast_context()` only parses ISO strings and catches only
`ValueError`; therefore a fresh WU epoch observation gets
`age_hours=None`, `freshness_factor=0.0`, and `fresh_observation=False`.
Separately, provider sample selection requires only "target local day and not in
the future"; there is no minimum sample count, coverage-from-local-midnight
threshold, maximum observation age gate, or source-lag fail-closed check before
Day0 p_raw is accepted. Staleness only expands sigma and reduces blending; it
does not block entry.
**Read-only reproduction:** With `current_utc_timestamp` equal to the WU epoch's
actual time, `day0_nowcast_context(observation_source='wu_api',
observation_time=<epoch>)` returned `age_hours=None`, `freshness_factor=0.0`,
`fresh_observation=False`, while the same timestamp as ISO returned
`age_hours=0.0`, `freshness_factor=1.0`, `fresh_observation=True`. A
`Day0Signal` using the WU epoch still returned normalized `p_raw=[0.0,1.0,0.0]`
with `sum=1.0`.
**Impact:** The same-day observation edge can be built from a primary provider
timestamp that the freshness model declares stale or from a provider response with
insufficient coverage. That turns weather-data delay into a soft model parameter
instead of a live-money authority gate, so Zeus can trade Day0 when the observed
high/low-so-far is not proven current enough to anchor the contract.
**False-positive boundary:** This does not prove every WU API response is delayed
or sparse. It proves the live path has no hard freshness/coverage invariant and
that the currently returned WU epoch timestamp format is misinterpreted by the
freshness function.
**Proposed remediation:**
1. Normalize `Day0ObservationContext.observation_time` to an aware UTC
   `datetime`/ISO string at provider boundaries, while retaining raw provider
   timestamp and `obs_id` as separate audit fields.
2. Add a Day0 observation authority gate before `Day0Signal`: max age by source,
   minimum sample count, minimum coverage since local midnight or an explicit
   provider daily-summary fact, and matching station/source identity.
3. Make stale/unknown-age observations produce structured no-trade for new
   entries; monitoring may degrade to read-only with explicit stale-observation
   provenance instead of generating fresh exit alpha.
4. Thread the same freshness/coverage verdict into LOW Day0 and monitor-refresh
   paths, not just HIGH entry.
5. Add fixtures for fresh WU epoch, stale ISO, sparse sample set, delayed provider
   response, and Open-Meteo fallback to prove only authority-fresh observations can
   produce tradeable Day0 p_raw.
**Acceptance evidence:** A fresh WU epoch observation is parsed as fresh; stale or
coverage-insufficient Day0 observations reject entry before p_raw/calibration;
monitor artifacts explicitly show stale-observation read-only degradation.

### [OPEN P1] Day0 capture mode has contradictory resolution-hour filters

**Location:** `src/engine/cycle_runner.py::MODE_PARAMS`,
`src/engine/cycle_runtime.py::execute_discovery_phase`,
`src/data/market_scanner.py::find_weather_markets` / `_parse_event`,
`architecture/runtime_modes.yaml`.
**Problem:** Project law defines `day0_capture` as markets less than 6 hours to
settlement. Runtime implements that second-stage filter with
`max_hours_to_resolution=6`, but the call into `find_weather_markets()` does not
override the scanner default `min_hours_to_resolution=6`. `_parse_event()` first
drops markets whose `hours_to_resolution < 6`; then runtime keeps only markets
whose `hours_to_resolution < 6`. The practical intersection is empty.
**Read-only reproduction:** A synthetic New York temperature event 5 hours from
resolution returned `scanner_default_returns=False` while
`scanner_no_min_returns=True`; a 7-hour event survived the scanner default but
`runtime_day0_keeps_after_default=False`. `MODE_PARAMS[DAY0_CAPTURE]` contains
only `{'max_hours_to_resolution': 6}`, so runtime relies on the scanner default
for the missing min filter.
**Impact:** The strategy family that is supposed to exploit same-day observation
speed can discover zero entry candidates before the evaluator sees observation,
calibration, or edge logic. This means fixing Day0 signal math, station routing,
and observation freshness would still not prove live Day0 capture works unless
the discovery-mode filter contract is repaired.
**False-positive boundary:** This affects the `day0_capture` entry discovery
lane. It does not block monitoring of already-held positions, and it does not
block non-Day0 opening/update modes.
**Proposed remediation:**
1. Make mode params explicit: `day0_capture` should pass
   `min_hours_to_resolution=0` or the scanner should accept separate min/max
   bounds instead of a global lower-bound default.
2. Move mode-specific time-window filtering into one owner so scanner and
   runtime cannot enforce contradictory halves of the same contract.
3. Add a regression with 5h, 6h boundary, and 7h synthetic events proving
   `day0_capture` keeps the intended <6h set and other modes keep their intended
   windows.
4. Record the applied discovery window in opportunity/no-trade facts.
**Acceptance evidence:** A `day0_capture` dry-run with a fresh <6h market reaches
`MarketCandidate(... discovery_mode='day0_capture')`; a >6h market is rejected
with a structured discovery-window reason, not silently removed by two filters.

### [OPEN P1] Live fee-rate API shape is not parsed before Kelly sizing

**Location:** `src/data/polymarket_client.py::get_fee_rate`,
`src/engine/evaluator.py::_fee_rate_for_token`,
`src/engine/evaluator.py::_size_at_execution_price_boundary`.
**Problem:** The live evaluator asks the CLOB client for a token-specific fee
rate before Kelly sizing. `PolymarketClient.get_fee_rate()` calls
`https://clob.polymarket.com/fee-rate?token_id=...` but only accepts fields such
as `feeRate`, `fee_rate`, `takerFeeRate`, or `taker_fee_rate`. Current official
Polymarket documentation and a live read-only weather-token request show the
endpoint returning `{"base_fee": <integer>}`. Because `base_fee` is not parsed,
the client raises `RuntimeError`, `_fee_rate_for_token()` converts that into
`FeeRateUnavailableError`, and evaluator rejects the candidate at
`EXECUTION_PRICE_UNAVAILABLE`.
**Read-only reproduction:** On 2026-04-29, querying a current Paris weather YES
token returned HTTP 200 with `{"base_fee":1000}` from `/fee-rate`. Calling
`PolymarketClient().get_fee_rate(token)` on the same token produced
`RuntimeError: Fee-rate response missing feeSchedule.feeRate ...`.
**Impact:** Even after market discovery, signal, calibration, and executable
snapshot gates are repaired, live entry can still fail before position sizing
because the current CLOB fee-rate response does not match the parser. This is an
API-contract compatibility blocker, not an alpha/model limitation.
**False-positive boundary:** If runtime injects a test/dummy `clob` without
`get_fee_rate`, evaluator falls back to `FEE_RATE_WEATHER`. The live path uses
the real client method, so the fallback is not reached when the parser raises.
**Proposed remediation:**
1. Parse `base_fee` from `/fee-rate` and convert it to the exact fee-rate unit
   expected by `polymarket_fee()`, with an explicit test against official docs and
   live fixture JSON.
2. Decide whether sizing should use category reality-contract fallback only when
   fee-rate endpoint is unavailable, or fail closed when endpoint returns an
   unknown shape.
3. Store raw fee response, converted fee rate, and conversion basis in decision
   evidence/executable snapshot facts.
4. Add tests for `{"base_fee": 1000}`, legacy `feeRate`, disabled-fee shape, and
   malformed response.
**Acceptance evidence:** A current weather token's `/fee-rate` response parses
without exception, the converted value matches the intended Polymarket fee
formula units, and evaluator no longer rejects otherwise-valid candidates at
`EXECUTION_PRICE_UNAVAILABLE` solely because the response field is `base_fee`.

### [OPEN P1] VWMP-derived entry limits are not quantized to tick size

**Location:** `src/contracts/semantic_types.py::compute_native_limit_price`,
`src/contracts/tick_size.py::TickSize.clamp_to_valid_range`,
`src/execution/executor.py::create_execution_intent`,
`src/contracts/executable_market_snapshot_v2.py::assert_snapshot_executable`.
**Problem:** Entry limit price is derived from `min(p_posterior, edge.vwmp) -
limit_offset`. `edge.vwmp` is a volume-weighted bid/ask/size price and can be an
arbitrary decimal even when bid and ask themselves are valid tick prices.
`compute_native_limit_price()` only clamps to `[0.01,0.99]`; it does not round or
floor to the market tick. The U1 snapshot gate later requires
`submitted_price % snapshot.min_tick_size == 0`, so a normal VWMP such as
`0.333333...` yields a limit like `0.313333...` and fails the executable snapshot
gate before command persistence.
**Read-only reproduction:** `compute_native_limit_price(p_posterior=0.60,
native_price=0.3333333333333333, limit_offset=0.02)` returned
`0.3133333333333333`; `Decimal(str(price)) % Decimal('0.01')` returned
`0.0033333333333333`, proving it is not aligned to a one-cent tick.
**Impact:** Even with a valid edge, fresh executable snapshot, and compatible
submit envelope, live entry can fail closed because the planner produces a price
shape the snapshot contract correctly rejects. This is not market alpha
uncertainty; it is a deterministic execution-shape mismatch.
**False-positive boundary:** If `edge.vwmp` happens to be tick-aligned after the
offset, the command can pass this particular gate. The bug is that tick alignment
is accidental rather than guaranteed by the price-planning contract.
**Proposed remediation:**
1. Add a typed tick-quantization operation, distinct from range clamp, with side-
   aware rounding: buy limits should not round upward past the configured budget
   unless explicitly intended; sell limits should not round downward past the
   exit budget unless explicitly intended.
2. Quantize entry and exit limit prices before idempotency-key generation,
   provenance-envelope persistence, collateral reservation, and snapshot gate.
3. Use the snapshot's actual `min_tick_size` when available rather than the
   default weather tick.
4. Add tests for VWMP fractional prices, `0.001` tick snapshots, dynamic
   best-ask jumps, and idempotency-key stability after quantization.
**Acceptance evidence:** A fractional VWMP fixture produces a snapshot-aligned
limit price before `insert_command()`, and U1 gate passes/fails only on real
tradability facts, not on avoidable planner rounding residue.

### [OPEN P1] Final SDK submission envelope is not persisted after CLOB submit

**Location:** `src/execution/executor.py::_persist_pre_submit_envelope` and
`_live_order`; `src/data/polymarket_client.py::_legacy_order_result_from_submit`;
`src/state/venue_command_repo.py::insert_submission_envelope`.
**Problem:** `_live_order()` persists `venue_submission_envelopes` before SDK
contact with `sdk_version="pre-submit"`, `signed_order_hash=None`,
`raw_response_json=None`, and `order_id=None`, then binds `venue_commands.envelope_id`
to that pre-submit row. The actual V2 adapter does produce an enriched
`SubmitResult.envelope` containing SDK version, signed-order hash for the
two-step path, raw response JSON, and order id, and the compatibility wrapper
returns it under `_venue_submission_envelope`. The executor only extracts
`orderID` and appends a small `SUBMIT_ACKED` payload; no source path inserts or
links the final SDK envelope, and the envelope table is append-only so the
pre-submit row cannot be updated later.
**Impact:** Even after executable snapshot and compatibility-envelope fixes,
the canonical command row cannot prove the exact signed request/raw CLOB response
that produced the live order. That weakens replay, idempotency investigation,
post-trade audit, and learning lineage because the durable DB truth stops at
"intent persisted" plus a minimal ack event rather than the final SDK
side-effect evidence.
**False-positive boundary:** This does not claim the order cannot be posted; the
adapter can return an accepted order id. The gap is durable provenance: the
accepted SDK envelope remains in the transient Python return value rather than a
canonical append-only evidence table linked to the command.
**Proposed remediation:**
1. Add a post-submit append-only evidence row, or a dedicated
   `venue_submission_attempts` table, for the SDK-produced envelope/result.
2. Link the final envelope/attempt id to the command through a command event or
   immutable relation instead of mutating the pre-submit row.
3. Require `SUBMIT_ACKED` to carry a reference to the persisted final envelope
   when SDK returns one; require typed degraded evidence when the submit result
   lacks raw response or signed-order hash.
4. Add entry and exit tests proving the pre-submit envelope, signed-order hash,
   raw response JSON, order id, and command event chain all reconcile.
**Acceptance evidence:** A two-step V2 submit leaves durable rows for both the
pre-side-effect intent envelope and the post-side-effect SDK envelope; the
command's ack event links the latter, and audit replay can recover signed hash,
raw response, order id, and snapshot identity from canonical DB only.

### Repair sequencing proposal

Do not fix these as isolated one-line patches. The safe sequence is:

1. **No-go guard preservation:** Keep live deployment blocked until readiness,
   bankroll, egress, executable snapshot, and calibration evidence are all
   present. Any repair that removes a fail-closed gate must include a stronger
   replacement gate in the same packet.
2. **Contract/source audit first:** Refresh Gamma resolutionSource for all
   active HIGH/LOW weather markets, resolve Paris `LFPB/LFPG`, close the
   HK Day0 HKO-vs-VHHH route, and update source routing or quarantine policy
   before touching calibration or trading.
3. **Discovery-mode window closure:** Repair `day0_capture` time-window ownership
   before validating Day0 alpha, so <6h markets can actually reach the evaluator
   and >6h markets are rejected once with explicit provenance.
4. **Day0 observation authority:** Normalize provider timestamps, require
   station/source identity, max-age, minimum sample coverage, and explicit
   stale-observation no-trade/read-only behavior before any Day0 p_raw can be
   tradeable.
5. **Forecast signal validity:** Fix Open-Meteo issue/valid-time persistence,
   make snapshot write failure block tradeable decisions, and add local-day
   finite-extrema plus probability-simplex gates for p_raw/p_cal. This must
   land before learning/harvester or live-alpha claims.
6. **Market discovery authority:** Preserve scan authority and filter
   closed/non-accepting child markets before evaluator sees outcomes.
7. **Executable identity closure:** Generate executable snapshots from fresh
   market facts, thread them through `ExecutionIntent`, and eliminate
   compatibility placeholders from live V2 submit.
8. **Execution economics closure:** Repair live CLOB fee-rate parsing, unit
   conversion, and fee evidence before claiming Kelly sizing reflects current
   Polymarket costs.
9. **Execution price-shape closure:** Quantize all entry/exit limit prices to
   snapshot tick size before idempotency, envelope, collateral, and command
   persistence; then enforce `max_slippage` and any dynamic-limit jumps before
   command persistence. Every configured execution budget must be behavior-
   changing or explicitly removed.
10. **Venue submission provenance closure:** Persist the final SDK submit
   envelope/result as append-only canonical evidence and link it to the command
   ack, so pre-submit intent evidence and post-submit side-effect evidence are
   both durable.
11. **LOW semantic closure:** Fix LOW Day0 shoulders, LOW monitor metric
   threading, LOW rounding/freshness context, and add regression fixtures for
   shoulder markets.
12. **Calibration maturity semantics:** Before calibration promotion, decide and
   implement the Level 4 contract: hard no-trade or documented stricter edge
   threshold wired into evaluator selection.
13. **Strategy direction reachability:** Resolve the multi-bin `buy_no`
   contract before claiming Shoulder Bin Sell alpha. Either implement native
   NO-token edge construction or remove/disable the unsupported live strategy
   surface.
14. **Calibration readiness:** Populate and validate metric-aware calibration
   pairs/models only after source/snapshot lineage is clean. Until then,
   `p_cal=p_raw` must remain an explicit no-go or degraded strategy state, not
   a silent "calibrated" surface.
15. **Risk-action closure:** Make RED and ORANGE risk states executable, not
   advisory: RED must cancel/sweep or emit `RED_ACTION_BLOCKED`; ORANGE must
   create favorable-exit intents under an explicit price rule.
16. **Partial-fill lifecycle and fill finality:** Implement entry and exit
   partial-fill materialization before hardening finality semantics. The fix
   must preserve filled shares when entry remainder is cancelled, reduce
   remaining shares when exit is partially filled, prevent REST/M5 and exit
   lifecycle/command-recovery from promoting non-final `MATCHED`/`MINED`, and
   use WS/M5 `CONFIRMED` as the reference for confirmed exposure.
17. **Collateral freshness closure:** Enforce a collateral snapshot freshness
   invariant at buy/sell executor preflight and prove stale pUSD/CTF inventory
   cannot cross command persistence or SDK contact.
18. **Filled-command recovery closure:** Make idempotency recovery for existing
   `FILLED` commands reconstruct active positions with order ids, not pending
   no-order-id projections.
19. **Monitor microstructure closure:** Either implement the live
   whale-toxicity detector or remove it from claimed behavior; if implemented,
   feed it into monitor-to-exit provenance.
20. **Settlement/learning closure:** Make harvester metric-aware for HIGH/LOW,
   enforce verified source/station reads, preserve actual forecast-source
   lineage into calibration pairs, populate pair snapshot ids, and ensure
   settlement terminalizes all residual exposure even when an exit order is
   pending or retrying.
21. **Observability repair:** Qualify v2 row-count queries and make status
   distinguish data absence from projection/schema ambiguity.
22. **End-to-end proof:** Run a staged live-smoke/dry-run that exercises
    market scan -> snapshot -> decision -> command insert -> V2 envelope ->
    user-channel or polling finality -> position projection -> monitor exit
    without venue side effects unless operator gates explicitly authorize them.

### Required acceptance coverage before live-alpha claim

- Unit tests for every patched seam above.
- Integration test for candidate -> decision -> executable snapshot -> command
  insertion.
- Fixture-backed Gamma tests for open shoulders, mixed closed child markets,
  Paris station identity, and HK HKO Day0 station routing.
- Discovery-mode window tests proving `day0_capture` reaches <6h markets, rejects
  >6h markets once, and does not inherit the scanner's non-Day0 minimum-hour
  default.
- Day0 observation authority tests proving WU epoch timestamps parse as fresh
  when current, stale timestamps block entry, sparse/coverage-insufficient
  samples fail closed, and monitor paths degrade read-only with provenance.
- DB migration/projection tests proving non-empty `decision_snapshot_id` and
  p_raw persistence.
- ENS data-quality tests proving local-day NaNs, all-zero p_raw, non-finite
  p_cal, and non-normalized model-agreement inputs fail closed before posterior
  or bootstrap edge construction.
- Strategy reachability tests proving every advertised live strategy family has
  at least one executable decision path, including weather multi-bin
  shoulder-sell / `buy_no`.
- Partial-fill lifecycle tests proving `PARTIAL -> CANCELLED remainder` leaves
  an active position for the filled shares and does not create a chain-unknown
  quarantine for the same token.
- Command-recovery finality tests proving `MINED` and `MATCHED` do not emit
  `FILL_CONFIRMED` unless followed by `CONFIRMED` or a typed order-finality
  source.
- Exit partial-fill lifecycle tests proving realized partial sells reduce
  remaining shares and retries sell only the unsold remainder.
- Risk-action tests proving RED causes execute or block with alerting cancel/sweep
  semantics, and ORANGE produces favorable-exit intents under its documented rule.
- Production executable-snapshot producer tests proving fresh snapshots are
  created/refreshed before both entry and exit commands.
- Execution-budget tests proving dynamic limit price improvement cannot exceed
  the configured slippage budget without explicit override evidence.
- Fee-rate API compatibility tests proving current `base_fee` responses parse
  into the correct fee formula units and malformed fee responses fail closed with
  explicit provenance.
- Tick-quantization tests proving VWMP-derived fractional prices are aligned to
  the executable snapshot's `min_tick_size` before command persistence and before
  idempotency-key hashing.
- Venue-submission provenance tests proving the final SDK envelope/result is
  durably appended and linked to `SUBMIT_ACKED`, not only returned transiently
  from `PolymarketClient.place_limit_order()`.
- Monitor microstructure tests proving whale-toxicity is either fed by live data
  and behavior-changing or explicitly unsupported.
- Harvester settlement tests proving HIGH/LOW metric identity, VERIFIED
  source/station enforcement, and settlement terminalization of pending-exit
  residual exposure.
- Calibration-learning lineage tests proving live/Open-Meteo p_raw cannot be
  stored as TIGGE `training_allowed=1` rows and that every training row carries a
  non-null decision snapshot id.
- Status-summary tests with attached world/trade DB name collisions.
- Live-readiness check at `17/17`, Q1 egress evidence present, staged smoke
  evidence present, and explicit `live-money-deploy-go`.
- Current data evidence showing non-empty metric-aware Platt models/pairs or a
  deliberate strategy gate that blocks uncalibrated live entries.
- Calibration-maturity tests proving Level 4 either blocks entry or applies the
  documented stricter edge threshold before executable decision creation.

### Websearch policy for this audit family

Use websearch only for current external facts that can change outside the repo:
Gamma active markets/resolutionSource, Polymarket CLOB/WS semantics, fee/order
rules, WU/HKO endpoint behavior, and current provider availability. Do not use
websearch to override canonical DB truth, local architecture law, or historical
packet evidence without recording the conflict and treating it as a new audit
finding.

---

## ANTI-RABBIT-HOLE: upstream-Polymarket scope limits (READ FIRST)

### [STRUCTURAL — NOT A BUG] Polymarket LOW market series starts 2026-04-15
**Status:** documented; do not chase.
**Audit date:** 2026-04-28 (gamma-api.polymarket.com live probe).
**Fact:** Polymarket did NOT offer LOW (mn2t6 / "lowest temperature") weather markets before 2026-04-15. First closed LOW event resolved 2026-04-15. Coverage is 8 cities only: London, Seoul, NYC, Tokyo, Shanghai, Paris, Miami, Hong Kong.
**Reality numbers:** 48 closed LOW events / 18 active. Date range 2026-04-15..2026-04-29. HIGH (max temp) market series predates LOW by ~2 years.
**Implication:**
- `state/zeus-world.db::settlements` LOW rows will never exceed ~50 historical + ~8/day going forward
- LOW Platt training MUST use `observations.low_temp` (42,749 rows / 51 cities / 2023-12-27..2026-04-19) as canonical ground truth — NOT `settlements` LOW
- Absence of LOW settlement rows for (city, date) tuples outside the 8-city × post-2026-04-15 scope is structural, not a backfill miss
**Do NOT:**
- Write retro-scrapers for pre-2026-04-15 dates
- Open quarantine reactivation tickets for cities outside the 8-city set
- Search archives expecting historical LOW market truth to exist
- Block on this gap when training LOW calibration; use observations.low_temp
**Antibody:** `architecture/fatal_misreads.yaml::polymarket_low_market_history_starts_2026_04_15` (severity=critical)
**Proof artifacts:**
- `docs/operations/task_2026-04-28_settlements_low_backfill/plan.md`
- `docs/operations/task_2026-04-28_settlements_low_backfill/evidence/pm_settlement_truth_low.json`
**Invalidation:** only a fresh gamma-api probe with HTTP-evidence showing LOW events with endDate < 2026-04-15 OR coverage beyond 8 cities may relax this.

---

## CRITICAL: DST / Timezone

### [OPEN — NOT LIVE-CERTIFIED] Historical diurnal aggregates still need DST-safe rebuild cleanup
**Certification status:** This gap blocks live math certification. The DST historical rebuild has NOT been executed and historical data derived from pre-fix aggregates is NOT certified for promotion. See `architecture/data_rebuild_topology.yaml` → `dst_historical_rebuild`.
**Location:** `scripts/etl_hourly_observations.py`, `scripts/etl_diurnal_curves.py`, `src/signal/diurnal.py`
**Problem:** The old London 2025-03-30 hour=1 evidence is stale. ETL/runtime is now partially DST-aware, but historical `diurnal_curves` materializations may still need to be rebuilt from true zone-aware local timestamps.
**Runtime mismatch:** `get_current_local_hour()` in `diurnal.py` already uses `ZoneInfo` and is DST-aware. The remaining risk is stale pre-fix aggregates/backfill, not the runtime clock itself.
**Impact:** Day0 `diurnal_peak_confidence` can still drift if old hourly/diurnal tables remain in circulation. NYC (EDT/EST), Chicago (CDT/CST), London (BST/GMT), Paris (CEST/CET) should be revalidated after rebuild; Tokyo, Seoul, Shanghai remain safe (no DST).
**Proposed antibody:**
1. Verify every ETL/backfill path derives `obs_hour` from zone-aware local timestamps.
2. Rebuild historical `hourly_observations` / `diurnal_curves` materializations from the corrected path.
3. Keep `test_diurnal_curves_hour_is_dst_aware` (or equivalent) to guard spring-forward/fall-back behavior.
**Cities affected:** DST cities only until the historical rebuild is proven clean.

---

## CRITICAL: Instrument Model

All entries antibody-closed (Bin.unit / SettlementSemantics.for_city / Platt
bin-width-aware / astype(int) → SettlementSemantics.round_values, etc.). See
`known_gaps_archive.md` → "CRITICAL: Instrument Model".

---

## CRITICAL: Exit/Entry Epistemic Asymmetry

Instrument-level antibodies all closed (MC count parity / CI-aware exit /
hours_since_open / MODEL_DIVERGENCE_PANIC threshold). See
`known_gaps_archive.md` → "CRITICAL: Exit/Entry Epistemic Asymmetry".

The structural relationship gap remains OPEN as **D4** under "MEDIUM-CRITICAL:
Cross-Layer Epistemic Fragmentation" below.

---

## CRITICAL: Day0 Signal Quality

All entries antibody-closed (continuous observation_weight / continuous
post-peak sigma decay). See `known_gaps_archive.md` → "CRITICAL: Day0 Signal
Quality".

---

## MEDIUM: Data Confidence

### [STALE-UNVERIFIED] Open-Meteo quota contention is workspace-wide, not Zeus-only
**Location:** Zeus + `51 source data` + Rainstorm-era ingestion loops
**Problem (filed 2026-04-03):** Workspace has shared data agents that can cause `429 Too Many Requests` on Open-Meteo, causing Zeus to misdiagnose quota issues.
**Status (2026-04-06):** All recent Open-Meteo API calls in the log show `HTTP/1.1 200 OK` with no 429 errors. Harvester ran successfully (`settlements_found=141`) but created 0 pairs — the failure mode appears to be Stage-2 bootstrap, not quota exhaustion. This gap may be less active than initially feared.
**Proposed antibody:** 建立 workspace-wide quota coordination：至少要有共享计数 / cooldown / update watermark，或者明确调度隔离，让 Zeus 的交易路径优先于后台数据 agent。

(2 FIXED entries on persistence_anomaly + 2 CLOSED 2026-04-15 entries on
alpha_overrides / harvester bias correction archived to
`known_gaps_archive.md` → "MEDIUM: Data Confidence".)

---

## CRITICAL: Settlement Source Mismatch (2026-04-16 smoke test)

### [OPEN] HK: SettlementSemantics uses WMO half-up, but PM resolution uses floor (bin containment)
**Location:** `src/contracts/settlement_semantics.py` → `for_city()` → non-WU path
**Problem:** PM HK description says: "resolve to the temperature range that **contains** the highest temperature... temperatures in Celsius to **one decimal place**." HKO Daily Extract returns 0.1°C precision (e.g., 27.8°C). PM maps 27.8 into "27°C" bin via floor containment: 27 ≤ 27.8 < 28. Our `SettlementSemantics` uses `precision=1.0` + `rounding_rule="wmo_half_up"`, giving `floor(27.8+0.5)=28` — wrong bin.
**Evidence:** Floor fixes 3/3 HKO-period mismatches (03-18, 03-24, 03-29) with 0 regressions against 16 total HK PM markets. All 11 existing matches preserved under floor.
**Impact:** HK is the only city with decimal-precision raw values (all WU cities return integers where floor=WMO). This is an architecture-level change: modifying `SettlementSemantics.for_city()` for HKO rounding affects the probability chain (ENS → noise → settlement rounding → bin assignment).
**Fix scope:** Change `rounding_rule` to `"floor"` for `settlement_source_type == "hko"` in `SettlementSemantics.for_city()`. Requires system constitution review since WMO half-up is stated as universal law in AGENTS.md line 49 and line 117.
**Blocked by:** System constitution review — AGENTS.md says "Settlement: WMO asymmetric half-up rounding" as universal. HKO is an exception where PM uses containment semantics instead.

### [OPEN] HK 03-13, 03-14: unresolved HKO source/audit mismatch; no WU ICAO route
**Problem:** Earlier packet language claimed a WU/VHHH Airport route. Operator correction 2026-04-28 supersedes that: Hong Kong has no WU ICAO route in Zeus. We have HKO Observatory data and the two early dates remain unresolved source/audit mismatches until fresh operator-approved primary-source evidence proves the settlement source.
**Impact:** 2 mismatches. Do not resolve by adding HK WU/VHHH/`wu_icao` aliases; keep quarantined/fail-closed pending HKO-specific audit evidence.

### [OPEN] WU cities (SZ/Seoul/SP/KL/etc.): API max(hourly) ≠ website daily summary high
**Problem:** PM resolves from WU website daily summary page (e.g., `wunderground.com/history/daily/cn/shenzhen/ZGSZ`). We compute `max(hourly_temp_C)` from WU v1 API. These are different values. Tested on 10 SZ mismatch dates: neither floor(F→C) nor WMO(F→C) from API hourly data explains PM values (1/10 and 3/10 respectively). Additionally, the WU API returns obs from "Lau Fau Shan" (HK station) for ZGSZ, while PM reads the Bao'an Airport page.
**Impact:** ~19 mismatches across SZ(10), Seoul(5), SP(2), KL(1), Chengdu(1).
**Fix:** Need to either scrape the WU website daily summary or find the XHR API endpoint that the WU Angular SPA uses to load daily summary data.

### [OPEN] Taipei: PM switched resolution source 3 times
**Problem:** PM used CWA (03-16~03-22) → NOAA Taiwan Taoyuan Intl Airport (03-23~04-04) → WU/RCSS Taipei Songshan Airport (04-05+). We only have WU/RCSS data for all dates. Gaps of 1-5°C on 16 mismatch dates confirm wrong source.
**Impact:** 16 mismatches. Need per-date source routing or historical data from CWA and NOAA for the affected periods.

---

## Polymarket Bin Structure (verified from zeus.db, 2026-03-31)

**这是 ground truth，来自实际市场数据，不是 spec：**

### °F 城市（Atlanta 示例）
```
40-41°F, 42-43°F, 44-45°F, 46-47°F, 48-49°F, 50-51°F, 52-53°F, 54-55°F, 56-57°F
+ shoulder: X°F or below, X°F or higher
```
每个 center bin = 2°F range，覆盖 2 个 integer settlement 值。
每个 market 约 9 个 center bins + 2 shoulder bins。

### °C 城市（London 示例）
```
9°C, 10°C, 11°C, 12°C, 13°C, 14°C, 15°C
+ shoulder: X°C or below, X°C or higher
```
每个 center bin = 1°C point bin，覆盖 1 个 integer settlement 值。
每个 market 约 7-10 个 center bins + 2 shoulder bins。

### Settlement Chain
```
Atmosphere → NWP model → ASOS sensor (0.1°C precision) → METAR report →
WU display (integer °F for US, integer °C for international) → Polymarket settlement
```

---

## Module Relationship Map（从这个 session 的 deep reading 中提取）

### Entry Path
```
market_scanner → evaluator → EnsembleSignal.p_raw_vector(bins, n_mc=5000)
                           → Platt calibrate → MarketAnalysis.find_edges()
                           → FDR filter → Kelly sizing → risk limits
                           → executor → Position(env=mode, unit=city.unit)
```

### Monitor Path
```
cycle_runner._execute_monitoring_phase()
  → monitor_refresh.refresh_position(conn, clob, pos)
    → _refresh_ens_member_counting() OR _refresh_day0_observation()
      → EnsembleSignal.p_raw_vector(single_bin, n_mc=5000)  [was 1000, fixed]
      → Platt calibrate → compute alpha → p_posterior
      → EdgeContext(forward_edge, p_market, confidence_band_*)
  → exit_triggers.evaluate_exit_triggers(pos, edge_ctx)
    → EDGE_REVERSAL / BUY_NO_EDGE_EXIT / SETTLEMENT_IMMINENT / etc.
  → exit_lifecycle.execute_exit(portfolio, pos, reason, price, paper_mode, clob)
    → paper: close_position() directly
    → live: place_sell_order() → check fill → retry/backoff
```

### Key Cross-Module Relationships
1. **Entry 和 monitor 必须用相同的 MC count** — FIXED (both 5000)
2. **Entry 和 monitor 必须用相同的 SettlementSemantics** — FIXED (for_city)
3. **Entry uses bootstrap CI, monitor now emits coherent conservative bounds for exit logic** — PARTIALLY CLOSED
4. **Entry and monitor both use real hours_since_open semantics** — FIXED
5. **Evaluator 传 Bin.unit，monitor_refresh 传 Bin.unit** — FIXED (both use position.unit)
6. **Harvester 和 evaluator 的 bias correction 设置不同步** — OPEN gap
7. **Canonical settlement payload path is authoritative** — FIXED (canonical path landed; no stale OPEN claim remains)
8. **`status_summary` runtime truth is lane-specific and enum-normalized** — FIXED (no mixed `ChainState.UNKNOWN` vs `unknown` truth)

---

## Tooling / Operator Health

### [STALE-UNVERIFIED] CycleRunner fails on malformed `solar_daily` schema rootpage
**Location:** `zeus/state/zeus.db` / the day0 capture path that reads `solar_daily`
**Problem (filed 2026-04-02):** The paper cycle failed with `malformed database schema (solar_daily) - invalid rootpage`. The monitor path was reading a broken SQLite object and the cycle aborted instead of degrading cleanly.
**Status (2026-04-06):** The latest `opening_hunt` cycles completed without this error appearing in the log. Not confirmed fixed — may have been intermittent or masked by a different cycle mode. Requires a deliberate `day0_capture` run to verify.
**Proposed antibody:** Add an explicit schema/integrity check before day0 capture and fail closed with a structured error (plus a repair/migration path) instead of letting SQLite rootpage corruption surface mid-cycle.

### [OPEN] strategy_tracker can report profit that is not reconstructible from durable DB truth
**Location:** `src/state/strategy_tracker.py`, `zeus/state/strategy_tracker-paper.json`, `zeus/state/positions-paper.json`, `zeus/state/zeus.db`
**Problem:** `strategy_tracker-paper.json` currently reports `opening_inertia` cumulative PnL of `+247.83`, but the authoritative current-regime cash ledger in `positions-paper.json` only reflects `opening_inertia` realized PnL of `-2.21`. Several large positive `opening_inertia` trades in the tracker (for example `f4e0d2a6-b8a`, `b2086cca-a1a`, `836270b8-2cc`, `8d9071fa-fab`, `eebdb911-99e`, `16a62cac-696`) are not reconstructible from `trade_decisions` or `position_events` in the current DB snapshot.
**Impact:** A non-authoritative attribution surface can be mistaken for wallet truth, creating a false belief that paper PnL is much higher than the bankroll snapshot actually shows.
**Proposed antibody:** Rebuild tracker summaries only from durable settlement/exit events or stamp every non-DB-backed trade with explicit archival provenance; add a reconciliation test that tracker PnL must be derivable from durable event truth (or explicitly marked as legacy/archive-only).

(2 FIXED entries on Healthcheck assumptions + Day0 stale probability waiver
archived to `known_gaps_archive.md` → "Tooling / Operator Health".)

---

## 2026-04-03 — edge-reversal follow-up triage

### [OPEN] Paper positions have no token_id → chain_state=unknown → stale_legacy_fallback → RiskGuard RED
**Location:** `src/execution/executor.py`, `src/state/portfolio.py`, `src/engine/cycle_runtime.py`
**Problem (filed 2026-04-10):** 12 paper positions entered April 7 with no token_id. All have `chain_state="unknown"`, `token_id=""`. Canonical DB projection returns non-ok status → `load_portfolio()` falls back to stale JSON → RiskGuard sees broken portfolio → RED → all new entries blocked since April 7.
**Evidence (2026-04-10):** `load_portfolio falling back to JSON because canonical projection is unavailable: stale_legacy_fallback` in both zeus-paper.log and riskguard.err. 12 positions in `positions-paper.json` with empty token_id. No new trades in cycle logs since April 7 despite active April 11 markets.
**Impact:** Zero new trades for 3 days. Polymarket has 47 active April 11 markets with prices, but system cannot enter due to RED block.
**Proposed antibody:** Add a canonical projection preflight in `load_portfolio()` that explicitly checks position chain state — if > N positions have `chain_state=unknown`, mark projection as `degraded` instead of `ok`, and require explicit handling rather than silent fallback.

### [MITIGATED] Missing monitor-to-exit chain escalates before settlement (2026-04-13)
**Location:** `src/engine/cycle_runtime.py`, `src/engine/monitor_refresh.py`
**Problem:** A subset of positions reach settlement with only lifecycle + settlement events and no intermediate monitor/reversal chain, so `EDGE_REVERSAL` never has a chance to fire.
**Impact:** The system cannot protect itself from fast-moving divergence if the monitor phase does not create an actual executable exit path.
**Antibody deployed:** `execute_monitoring_phase()` now records `monitor_chain_missing` when a settlement-sensitive position cannot form a usable monitor-to-exit chain because refresh failed or exit authority returned `INCOMPLETE_EXIT_CONTEXT`. Refresh failures now produce a `MonitorResult` instead of disappearing from the cycle artifact, and `status_summary` projects `cycle_monitor_chain_missing:<count>` as infrastructure RED.
**Residual:** This is operator-visible cycle escalation, not durable lifetime proof. DB projection/schema support for monitor counts or a durable monitor evidence spine remains a separate package.

### [PARTIALLY FIXED] EDGE_REVERSAL — hard divergence kill-switch at 0.30 added (2026-04-06, math audit)
**Location:** `src/state/portfolio.py`, `src/execution/exit_triggers.py`
**Problem:** Reversal requires two negative confirmations plus an EV gate, so a position can become clearly wrong in settlement truth without ever tripping runtime reversal.
**Impact:** The system may hold losers through large adverse moves when the market changes quickly but not persistently enough for the current confirmation rule.
**Proposed antibody:** Keep the conservative reversal path, but add a separate hard divergence kill-switch (single-shot on extreme divergence / velocity) for high-confidence failures.

### [MITIGATED] Harvester Stage-2 DB shape preflight prevents noisy canonical-bootstrap failures (2026-04-13)
**Location:** `src/execution/harvester.py` / runtime `position_events` helpers
**Problem:** Recent log tails show repeated harvester errors stating that legacy runtime `position_events` helpers do not support canonically bootstrapped databases. The Stage-2 bootstrap path is still being exercised at runtime even though the helper contract cannot handle the current DB shape.
**Live evidence (2026-04-06):** Harvester ran at 12:47–12:55 CDT and produced `settlements_found=141, pairs_created=0, positions_settled=0`. It found settlements but generated zero calibration pairs — consistent with Stage-2 helpers failing on canonically bootstrapped DB. Gamma API fetch also timed out during this run (`WARNING: Gamma API fetch failed: The read operation timed out`).
**Impact:** Harvester cycles can fail noisily and skip settlement/pair creation work, leaving the runtime path partially broken even when the daemon and RiskGuard are alive.
**Antibody deployed:** `run_harvester()` now runs a Stage-2 DB-shape preflight after settled events are fetched and before per-event learning work starts. If runtime support tables are missing, it returns `stage2_status='skipped_db_shape_preflight'` with missing trade/shared table lists and skips only Stage-2 snapshot/calibration/refit work; event parsing and settlement handling still run. Legacy `decision_log` settlement-record storage degrades when that table is absent instead of crashing the cycle.
**Residual:** This is a structured skip, not a migration. It does not create calibration pairs on canonical-only bootstrap DBs, rebuild `p_raw_json`, or replace legacy Stage-2 helpers with a fully canonical learning path.

### [OPEN] ACP router fallback chain is recovering after failure, not stabilizing before dispatch
**Source:** `evolution/router-audit/2026-04-08-router-audit.md`
**Problem:** The current router can classify `auth`, `timeout`, and `network` failures, but dispatch still happens before allowlist/auth/timeout hard prechecks. Result: the fallback chain keeps switching to another failure surface instead of a known-good surface.
**Impact:** Window-level timeout clusters, invalid auth tokens, and Discord gateway/network failures can cascade across the routing stack.
**Proposed antibody:** Add a deterministic pre-dispatch gate for allowlist/auth/timeout, then run semantic routing only over candidates that already passed preflight.

(5 FIXED entries on settlement CI guard / buy-yes proxy / settlement won
ambiguity / control-plane gate drift / LA Gamma Milan / Heartbeat cron RED
suppression archived to `known_gaps_archive.md` → "2026-04-03 —
edge-reversal follow-up triage".)

---

## MEDIUM-CRITICAL: Cross-Layer Epistemic Fragmentation (D1–D6)

Six design gaps identified at the signal→strategy→execution boundary. The signal layer's high hit rate does not compose into profit because each cross-layer handoff loses the semantic that makes the upstream number meaningful. These are architecture-level gaps requiring typed contracts at module boundaries (INV-12 territory).

### [MITIGATED] D1 — Alpha consumers declare EV compatibility (2026-04-13)
**Location:** `src/strategy/market_fusion.py` — `compute_alpha()`
**Problem:** α adjustments (spread, lead time, freshness, model agreement) are validated against Brier score. But profit requires EV > cost. Brier-optimization converges Zeus toward market consensus, which drives edge → 0. The optimization target (accuracy) conflicts with the business objective (profit).
**Impact:** Systematic edge compression. Alpha tuning that improves calibration accuracy simultaneously destroys the trading edge.
**Antibody deployed:** `compute_alpha()` returns `AlphaDecision(optimization_target='risk_cap')`; active entry and monitor consumers call `value_for_consumer('ev')` before using α. Invalid alpha targets now fail construction, and a Brier-target alpha fails closed before Kelly sizing instead of silently flowing into EV decisions.
**Residual:** α is still a conservative risk-cap blend, not an EV-optimized sweep. Closing D1 fully requires deriving and validating an EV-target alpha policy, not just preventing target mismatch.

### [MITIGATED] D2 — Tail alpha scale is explicit calibration treatment (2026-04-13)
**Location:** `src/strategy/market_fusion.py` — tail alpha scaling
**Problem:** `TAIL_ALPHA_SCALE=0.5` scales α toward market on tail bins, directly halving the edge that buy_no depends on (retail lottery-effect overpricing of shoulder bins). The scaling serves calibration accuracy (Brier) but destroys the structural edge that Strategy B (Shoulder Bin Sell) exploits.
**Impact:** Strategy B's primary edge source is systematically attenuated by a calibration-serving parameter.
**Antibody deployed:** `alpha_for_bin()` now routes tail scaling through `DEFAULT_TAIL_TREATMENT = TailTreatment(scale_factor=TAIL_ALPHA_SCALE, serves='calibration_accuracy', ...)` instead of applying a naked constant. Provenance also states this is calibration-serving, not buy_no P&L validated.
**Residual:** Behavior is unchanged and still may attenuate buy_no structural edge. Closing D2 requires a profit-validated tail policy, likely direction/objective-aware, with buy_no P&L evidence.

### [OPEN] D3 — Entry price must remain typed through execution economics
**Location:** `src/strategy/market_analysis.py` — `BinEdge.entry_price`
**Problem:** `BinEdge.entry_price = p_market[i]` (implied probability from mid-price), but actual execution price = ask + taker fee (5%) + slippage. Kelly sizing uses the implied probability as the cost basis, systematically oversizing positions because the real cost is higher.
**Impact:** Every Kelly-sized position is larger than it should be. The magnitude depends on spread width and fee structure.
**Mitigation deployed (2026-04-13; DSA-09 cleanup 2026-04-29):** `evaluator.py` wraps entry price as `ExecutionPrice`, queries token-specific CLOB fee rate when available, and computes `polymarket_fee(p) = fee_rate × p × (1-p)` before Kelly. The fee-adjusted path is now unconditional; the stale `EXECUTION_PRICE_SHADOW` rollback flag was removed from `settings.json` after the shadow-off branch was deleted.
**Remaining antibody:** Carry typed execution cost beyond evaluator, and connect market-specific tick size, neg-risk, and realized fill/slippage reconciliation.

### [OPEN] D4 — Entry-exit epistemic asymmetry (CRITICAL)
**Location:** `src/engine/evaluator.py` (entry), `src/execution/exit_triggers.py` (exit)
**Problem:** Entry requires BH FDR α=0.10 + bootstrap CI + `ci_lower > 0` — high statistical burden. Exit requires only 2-cycle confirmation — low statistical burden. The system admits edges cautiously but exits aggressively, killing true edges via noise before they mature.
**Cross-reference:** Several specific manifestations of this asymmetry are tracked in the "Exit/Entry Epistemic Asymmetry" section above (MC count mismatch [FIXED], CI-aware exit [FIXED], hours_since_open [FIXED], divergence threshold [FIXED]). This gap tracks the *structural* asymmetry: entry and exit should share a symmetric `DecisionEvidence` contract with comparable statistical burden.
**Proposed antibody:** Entry and exit share the same `DecisionEvidence` contract type with symmetric statistical burden. Exit reversal requires bootstrap-grade evidence, not just 2 consecutive point-estimate checks.

(D5 / D6 / Day0-canonical-event closed entries archived to
`known_gaps_archive.md` → "MEDIUM-CRITICAL: Cross-Layer Epistemic
Fragmentation (D1–D6)".)
