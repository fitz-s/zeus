# Known Gaps — Venus Evolution Worklist

**NOTE:** Closed entries moved to docs/to-do-list/known_gaps_archive.md on 2026-05-01 (per 2026-04-30 recheck)

每个 gap 是一个 belief-reality mismatch。每个 gap 的终态：变成 antibody（test/type/code）→ FIXED。
如果一个 gap 包含 "proposed antibody"，下一步就是实现它。

**Active surface**: this file lists OPEN, PARTIALLY FIXED,
STALE-UNVERIFIED, and residual-bearing MITIGATED gaps that still demand
attention.

**Antibody archive** (closed FIXED/CLOSED entries — immune-system record of
what we made impossible): `docs/to-do-list/known_gaps_archive.md`. Reference
when a similar pattern resurfaces; do not re-open without proof the antibody
failed.

---

## CRITICAL: Full-flow live audit (2026-04-28)

**Status:** OPEN; read-only audit record.
**Audit scope:** weather contract semantics -> source truth -> forecast signal
-> calibration -> edge -> execution -> holding/monitoring -> exit -> settlement
-> learning/observability.
**Audit posture:** Read-only gap register. Each entry is an open belief-reality mismatch; resolution requires code/test/type antibody.

### Current non-Paris repair overlay (2026-04-30)

This overlay is current for non-Paris blocker status in the active worktree.
Where it conflicts with older `OPEN` headings below, this overlay supersedes
the older heading until those historical entries are individually archived.
Paris `LFPG`/`LFPB` source mismatch remains excluded and open under the
dedicated Paris entry.

**Code-path blockers closed or fail-closed by current source + tests:**

- Day0 non-Paris observation authority: executable Day0 observation is now
  settlement-source-bound by default, unsupported settlement sources fail
  closed instead of using WU/IEM/Open-Meteo fallback as executable truth, WU
  station ids are preserved/checked, WU epoch timestamps parse as fresh when
  current, and stale/coverage-invalid Day0 observations block entry or degrade
  monitor refresh to stale evidence.
- Forecast/vector validity: local-day ENS non-finite values, non-finite member
  extrema, invalid probability vectors, and invalid model-agreement vectors now
  fail closed before posterior/edge construction.
- Executable snapshot identity: entry snapshot capture/threading is present,
  and held-position exits now reuse a fresh snapshot or capture a new
  VERIFIED Gamma + CLOB executable snapshot before sell intent creation;
  stale/unverified/missing capture still fails closed through the executor U1
  gate.
- V2 submit provenance: live placement requires a bound U1-derived submission
  envelope; compatibility `legacy:` envelopes are not accepted on the normal
  live path, and final SDK submission envelopes are persisted and linked from
  `SUBMIT_ACKED`.
- Risk behavior: effective `RiskLevel.RED` now triggers the RED sweep even when
  `force_exit_review` is false; ORANGE favorable exits require complete exit
  authority and net-favorable economics rather than acting like YELLOW or
  gross break-even.
- Fill/exposure truth: entry partial-fill remainder cancel preserves observed
  exposure, exit partial fills reduce local remaining shares/cost basis and
  retries only unsold residual exposure, and existing `FILLED` command
  idempotency collisions preserve `order_id`/`external_order_id`.
- Settlement/learning: harvester settlement lookup is metric/source/station
  aware, LOW settlement writes use LOW identity, pending-exit residual exposure
  can settle, and calibration-pair learning preserves actual snapshot/source
  lineage instead of rebranding live/Open-Meteo p_raw as TIGGE training rows.
- Economics/evidence gates: calibration maturity Level 4 blocks before edge
  selection, collateral buy/sell preflight rejects stale snapshots, current
  CLOB `base_fee` shapes are canonicalized into fee fractions with provenance,
  day0-capture no longer inherits the scanner's non-Day0 min-hour filter, and
  v2 row-count observability prefers world-qualified tables over empty trade
  shadows.
- Monitor microstructure: held YES positions now compute
  `last_monitor_whale_toxicity` from VERIFIED sibling-bin metadata plus fresh
  adjacent CLOB top-book pressure. The detector distinguishes available-clear,
  available-toxic, not-applicable `buy_no`, and unknown market-fact states in
  monitor provenance.

**Remaining non-Paris open items for live trading:**

- Calibration tables/models must be populated or uncalibrated strategies must
  remain explicitly blocked; live evidence must prove P&L after
  fees/slippage/fill drag before strategy promotion.
- RED direct venue side-effect SLA is intentionally not implemented inside
  `cycle_runner`; the current architecture records durable cancel proxy intent
  and uses the normal command/execution seams. If the operator requires
  immediate venue `cancel_order()` from RED itself, that is a separate
  live-side-effect packet with explicit operator-go, not a docs-only repair.
- True market-wide print-level "whale sweep" remains intentionally not claimed:
  Zeus's current V2 adapter exposes `get_trades`, and Polymarket documents that
  surface as authenticated account trade history. Public market-trade event
  methods are a separate feed and are not wired into this repo path. The live
  code claim is now narrower: orderbook-adjacent pressure detection from fresh
  sibling CLOB books. A future print-level detector would require a separate
  market-stream feed and evidence packet, not a local monitor patch.

**Verification snapshot for this overlay:** focused non-Paris suites passed on
2026-04-30: `tests/test_exit_safety.py` (20), `tests/test_runtime_guards.py`
(178), `tests/test_day0_runtime_observation_context.py` +
`tests/test_model_agreement.py` + `tests/test_ensemble_signal.py` +
`tests/test_market_analysis.py` (88), collateral/executor command suites (94,
1 skipped), V2 adapter/snapshot suites (66), harvester suites (60),
HK/model-agreement alpha boundary (12), RED/structural-linter gates (9),
monitor whale-toxicity unit coverage plus monitor-to-exit seams
(`tests/test_lifecycle.py` 14, selected `tests/test_runtime_guards.py` 2,
selected `tests/test_live_safety_invariants.py` 4), and
calibration-maturity focused checks (2). No live venue side effects or
production DB mutation were performed.

### Money-path coverage verdict

| Money path segment | Current verdict | Primary blockers |
|---|---|---|
| Contract semantics | PARTIAL | Paris station/source mismatch remains excluded/open; non-Paris source truth still needs fresh pre-live audit |
| Source truth | PARTIAL | current source validity must be refreshed before live claims; Paris remains quarantined; no production DB mutation was performed in this repair |
| Forecast signal | PARTIAL | local code now fails closed on non-finite/invalid vectors; live trading still needs current source/data evidence and promotion-grade calibration evidence |
| Calibration | BLOCKED | current calibration model/pair evidence is not promotion-grade; uncalibrated or Level 4 paths must remain blocked or explicitly degraded |
| Edge construction | PARTIAL | Day0 discovery windows, fee-rate parsing, Level 4 maturity gating, and multi-bin `buy_no` reachability are locally repaired; live economics still require staged P&L after fees/slippage/fill drag |
| Execution intent | PARTIAL | entry and held-exit executable snapshot paths are locally wired/fail-closed; live evidence still needs staged scan -> snapshot -> command insertion |
| Venue submission | PARTIAL | normal live path is bound to U1/final SDK submission provenance; legacy compatibility helpers are not deploy evidence |
| Risk/control | PARTIAL | RED/ORANGE behavior is locally executable/fail-closed; direct venue cancel from `cycle_runner` is intentionally a separate live-side-effect SLA decision |
| Fill/holding | PARTIAL | CONFIRMED-only finality, entry/exit partial materialization, and filled-command order-id recovery are locally repaired; residual drift-journal split is not a live-entry blocker |
| Monitoring/exit | PARTIAL | LOW monitor/Day0 and exit partial fills are locally repaired; whale-toxicity is now orderbook-adjacent pressure, not true all-market print-sweep detection |
| Settlement/learning | PARTIAL | harvester HIGH/LOW metric/source/station lineage and pending-exit settlement are locally repaired; live harvester flag and production writes remain operator-gated |
| Observability | PARTIAL | v2 row-count shadow-table false alarm is closed; broader live-readiness projections remain non-authority |

### [OPEN P1] No production executable snapshot producer/refresher was found

**Location:** `src/state/snapshot_repo.py`, `src/engine/cycle_runtime.py`,
`src/execution/exit_lifecycle.py::_latest_exit_snapshot_context`.
**Original problem:** The executable snapshot gate is present, but repository search found
`ExecutableMarketSnapshotV2(...)` construction and `insert_snapshot(...)` calls
only in tests and the snapshot repository module, not in a live runtime producer.
Exit lifecycle also expects the latest fresh `executable_market_snapshots` row
by token and returns an empty context when none exists, deliberately letting the
executor fail closed. Current audit DBs had zero `executable_market_snapshots`.
**Active residual:** Entry-side snapshot production/threading is now present,
but held-position exit still depends on a previously-created fresh snapshot row.
Both entry and exit can still remain blocked when the live snapshot table is
empty; the unresolved owner is the exit-token refresher / producer symmetry.
**2026-04-30 recheck:** The entry side now has `capture_executable_market_snapshot()`
in `src/data/market_scanner.py`, `cycle_runtime` calls it when live entry lacks
snapshot facts, and runtime tests prove capture/commit before executor intent.
This gap remains open for the exit/held-position side: `_latest_exit_snapshot_context()`
can consume a fresh row by selected token, but no symmetric exit-token refresher
was found in the live monitoring/exit path.
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
**2026-04-30 recheck:** Entry `_live_order()` now binds the persisted
pre-submit envelope into `PolymarketClient` before submission, but the adapter
still has a compatibility fallback and the exit submit path persists a
pre-submit envelope without binding it into the client. The gap remains open.
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
5. **Forecast signal validity:** Keep the Open-Meteo empty-snapshot antibody in
   place and finish local-day finite-extrema plus probability-simplex gates for
   p_raw/p_cal. This must land before learning/harvester or live tradings.
6. **Market discovery authority:** CLOSED 2026-04-30 for scan-authority gating
   and closed/non-accepting child filtering. Residual source validity belongs to
   the contract/source audit and Day0 observation authority items.
7. **Executable identity closure:** Entry-side snapshot capture/threading is
   present. Add symmetric exit snapshot refresh/production and eliminate
   compatibility placeholders from live V2 submit.
8. **Execution economics closure:** Repair live CLOB fee-rate parsing, unit
   conversion, and fee evidence before claiming Kelly sizing reflects current
   Polymarket costs.
9. **Execution price-shape closure:** Entry VWMP tick alignment and entry
   `max_slippage` enforcement are CLOSED 2026-04-30. Remaining economics work is
   live fee evidence and realized execution-cost attribution. Every configured
   execution budget must be behavior-changing or explicitly removed.
10. **Venue submission provenance closure:** Persist the final SDK submit
   envelope/result as append-only canonical evidence and link it to the command
   ack, so pre-submit intent evidence and post-submit side-effect evidence are
   both durable.
11. **LOW semantic closure:** CLOSED 2026-04-30 for LOW monitor metric threading
   and LOW Day0 shoulder/rich-context handling. Remaining LOW risk is under the
   source-role/station/freshness Day0 observation authority gap.
12. **Calibration maturity semantics:** CLOSED 2026-04-30 for local executable
   selection: Level 4 raw-probability buckets block before edge selection. Live
   promotion still requires populated calibration evidence.
13. **Strategy direction reachability:** CLOSED 2026-04-30 for native
   multi-bin `buy_no` source/test reachability. Residual live tradings still
   require calibration/P&L evidence and operator promotion gates.
14. **Calibration readiness:** Populate and validate metric-aware calibration
   pairs/models only after source/snapshot lineage is clean. Until then,
   `p_cal=p_raw` must remain an explicit no-go or degraded strategy state, not
   a silent "calibrated" surface.
15. **Risk-action closure:** CLOSED 2026-04-30 for local semantics: RED sweep
   actuation and ORANGE favorable-exit intent creation are covered by tests.
   Direct venue cancel inside `cycle_runner` remains a separate operator-go SLA.
16. **Partial-fill lifecycle and fill finality:** CLOSED 2026-04-30 for local
   entry/exit materialization and CONFIRMED-only finality. Remaining
   optimistic-vs-confirmed drift journaling is a reconciliation/audit refinement.
17. **Collateral freshness closure:** CLOSED 2026-04-30 for buy/sell executor
   preflight freshness rejection.
18. **Filled-command recovery closure:** CLOSED 2026-04-30 for `FILLED`
   command recovery preserving order ids.
19. **Monitor microstructure closure:** CLOSED 2026-04-30 for the claimed local
   behavior: orderbook-adjacent pressure is fed into monitor-to-exit provenance.
   True all-market trade-print whale-sweep detection is not claimed without a
   future market-stream feed.
20. **Settlement/learning closure:** CLOSED 2026-04-30 for local HIGH/LOW
   metric/source/station lineage, pending-exit residual settlement, and
   decision-snapshot/source lineage in calibration pairs. Live harvester writes
   remain feature-flag/operator gated.
21. **Observability repair:** CLOSED 2026-04-30 for v2 row-count world-vs-trade
   shadow qualification. Status remains a projection, not deploy authority.
22. **End-to-end proof:** Run an end-to-end dry-run that exercises
    market scan -> snapshot -> decision -> command insert -> V2 envelope ->
    user-channel or polling finality -> position projection -> monitor exit
    without venue side effects unless operator gates explicitly authorize them.

### Required acceptance coverage before live trading

- Unit tests for every patched seam above.
- Integration test for candidate -> decision -> executable snapshot -> command
  insertion.
- Fixture-backed Gamma/source tests for Paris station identity and HK HKO Day0
  station routing. Open-shoulder and mixed closed-child coverage is archived as
  closed 2026-04-30.
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
  at least one executable decision path. Weather multi-bin shoulder-sell /
  `buy_no` source reachability is covered by the 2026-04-30 native-NO tests;
  live-alpha promotion still requires calibration/P&L evidence.
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
  created/refreshed before both entry and exit commands; entry coverage exists,
  exit refresher coverage remains active.
- Execution-budget tests proving dynamic limit price improvement cannot exceed
  the configured slippage budget without explicit override evidence.
- Fee-rate API compatibility tests proving current `base_fee` responses parse
  into the correct fee formula units and malformed fee responses fail closed with
  explicit provenance.
- Tick-quantization tests for entry are archived as closed 2026-04-30; keep
  regression coverage when changing execution price planning.
- Venue-submission provenance tests proving the final SDK envelope/result is
  durably appended and linked to `SUBMIT_ACKED`, not only returned transiently
  from `PolymarketClient.place_limit_order()`.
- Monitor microstructure tests proving orderbook-adjacent whale-toxicity is fed
  by fresh sibling CLOB facts, behavior-changing when toxic, clear when
  pressure is absent, and unknown when scan/orderbook authority is insufficient.
- Harvester settlement tests proving HIGH/LOW metric identity, VERIFIED
  source/station enforcement, and settlement terminalization of pending-exit
  residual exposure.
- Calibration-learning lineage tests proving live/Open-Meteo p_raw cannot be
  stored as TIGGE `training_allowed=1` rows and that every training row carries a
  non-null decision snapshot id.
- Status-summary tests with attached world/trade DB name collisions.
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

No active remediation items remain in this section. The Polymarket LOW market
series structural boundary is archived in `docs/to-do-list/known_gaps_archive.md`.

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
`docs/to-do-list/known_gaps_archive.md` → "CRITICAL: Instrument Model".

---

## CRITICAL: Exit/Entry Epistemic Asymmetry

Instrument-level antibodies all closed (MC count parity / CI-aware exit /
hours_since_open / MODEL_DIVERGENCE_PANIC threshold). See
`docs/to-do-list/known_gaps_archive.md` → "CRITICAL: Exit/Entry Epistemic Asymmetry".

The structural relationship gap remains OPEN as **D4** under "MEDIUM-CRITICAL:
Cross-Layer Epistemic Fragmentation" below.

---

## CRITICAL: Day0 Signal Quality

All entries antibody-closed (continuous observation_weight / continuous
post-peak sigma decay). See `docs/to-do-list/known_gaps_archive.md` → "CRITICAL: Day0 Signal
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
`docs/to-do-list/known_gaps_archive.md` → "MEDIUM: Data Confidence".)

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

(3 closed entries on strategy_tracker JSON authority, Healthcheck assumptions,
and Day0 stale probability waiver archived to `docs/to-do-list/known_gaps_archive.md` →
"Tooling / Operator Health".)

---

## 2026-04-03 — edge-reversal follow-up triage

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
suppression archived to `docs/to-do-list/known_gaps_archive.md` → "2026-04-03 —
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
`docs/to-do-list/known_gaps_archive.md` → "MEDIUM-CRITICAL: Cross-Layer Epistemic
Fragmentation (D1–D6)".)

---

## Deferred items (from open_work_items.md, consolidated 2026-05-01)

### [DEFERRED] Two-System Independence Phase 4

Revisit due ~2026-06-26 (8 weeks from Phase 3 completion 2026-05-01).
Divergent strategy restart-policy needs may require a process split — escalate
to architect when the window opens.

### [LOW] Backfill script unification

18 `scripts/backfill_*.py` scripts remain ad-hoc. No live trading impact.
Scope a unification package when convenient.
