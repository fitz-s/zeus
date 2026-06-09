# P1 Executable Brief — Day0 Make-or-Break Gate

> Authority basis: PR_SPEC.md §3 P1 + §0 process-memory. Created: 2026-06-07.
> Last audited: 2026-06-07. Scope: read-only design + exact-change spec.
> Iron rules: settlement (VERIFIED WU) = only truth; zero-trade is a fault; NEVER fabricate certs/probabilities; q_lcb + fractional Kelly; anti-lookahead by type.

---

## 1. DATA-AVAILABILITY VERDICT

**Two independent legs; one is executable now, one requires forward capture.**

### Fill + Settlement leg — EXECUTABLE NOW

Triple-join universe: **357 (city, target_date, metric) units = 335 distinct city-days** in the dense snapshot window **2026-05-25 .. 2026-06-06** (exclude 2026-06-07: not yet VERIFIED-settled). All three legs are present:

| Leg | Source | Rows / coverage |
|-----|--------|----------------|
| Settlement truth | `settlement_outcomes` in `zeus-forecasts.db`, `authority='VERIFIED'` | 6,637 rows (2024-01-01..2026-06-06); 4,993 with `winning_bin` + `market_slug` |
| Executable fill (depth) | `executable_market_snapshots` in `zeus_trades.db` | 1,299,666 rows; 1,224,513 with `depth_at_best_ask>0`; `captured_at` 2026-05-15..2026-06-07; join key `condition_id = trade_decisions.market_id` matches 1560/1560 (100%) decisions, 46/46 markets; nearest snapshot to each decision ~5-6 s |
| ask/fee | `orderbook_depth_json` + `fee_details_json` on same EMS rows | Multi-level ladders; avg 24-32 ask levels for weather markets; `fee_rate_fraction=0.1` confirmed |

`market_price_history` is NOT the depth source — its `best_bid/best_ask` are 100% NULL (0/10,621 rows) and carries no ladder. Use `executable_market_snapshots` exclusively.

### q_d0 leg — NOT READABLE FROM HISTORY; must be reconstructed offline

| Table | Status | Why unusable |
|-------|--------|-------------|
| `day0_nowcast_runs` | 0 rows | Lane never fired in production; schema also lacks `obs_available_at` |
| `decision_events` | 0 rows | Wired in schema (`db.py:1653`) but never populated |
| `day0_horizon_platt_fits` | 0 rows | Day0 Platt never fit |
| `probability_trace_fact` | 33,203 rows but `p_cal_json` ends 2026-05-18; 0 Day0 same-day rows | Is mainline settlement-capture q, not obs-locked q_d0 |

**Resolution:** fit the Day0 horizon Platt (`src/calibration/day0_horizon_calibration.py HorizonPlattFit`) from 2024-01-01..2026-05-24 obs + settlement history (strict temporal holdout before the test window), then serve q_d0 offline per decision_time from `observation_instants` + running-max. All inputs exist.

### obs-clock: `imported_at` is UNUSABLE; use `utc_timestamp + fixed_lag`

`observation_instants` (zeus-world.db): 2,755,909 rows. `imported_at` is backfill-contaminated: window average lag 37.0 h, minimum 0.3 h on rows with identical `source_role=historical_hourly` — re-imports overwrote the original fetch time. `openmeteo_archive_hourly` rows share one batch import time. **Never read `imported_at` for obs-availability; never use `MAX(imported_at)` (`day0_extreme_updated.py:222` pattern).**

Honest obs-clock: `reconstructed_available_at = utc_timestamp + FIXED_PUBLISH_LAG` (default 75 min; sensitivity-sweep 60/90 min before final verdict).

### obs-timing lag measurability on existing data: CANNOT measure now

`decision_log` (`zeus_trades.db`): 8,087 rows 2026-05-02..2026-06-07. Only 2 `artifact_json` blobs mention `observation_available_at`, both as integrity-error strings (`'missing_observation_available_at'`). Zero rows carry `high_so_far`. `day0_nowcast_runs = 0`. There is no joinable `(honest_obs_available_at, market_price@same_time)` dataset on any queryable surface. **The obs→market lag cannot be measured from existing rows** — forward instrumentation is required for that specific measurement.

### Forward-capture requirement (obs-timing precision, not G-DAY0 gating)

Forward capture is needed to **confirm the obs-timing edge is real on live data**, but is NOT a prerequisite for the offline G-DAY0 structural verdict. Minimum instrumentation:

1. Persist `observation_available_at` (= `now()` at WU/IEM fetch, already produced at `observation_client.py:403/480`) per `day0_nowcast_runs` write — the `_maybe_write_day0_nowcast` path must thread it through.
2. Populate `best_bid`, `best_ask`, `raw_orderbook_hash` in the Gamma/CLOB market scanner (columns exist in `market_price_history`; currently 100% NULL).
3. **Confirm the Gamma/CLOB scanner is still running** — `market_price_history` ends 2026-05-28 (~10 days stale). If the scanner is down, the forward-capture clock has not started.

Wait estimate once instrumentation is live: ~3–6 weeks (≥20–30 city-days × multiple Day0 markets within `hours_remaining ≤ 6`) before that finer obs-timing precision dataset has power.

### Testable city-day count

| Leg | City-days / units |
|-----|------------------|
| G-DAY0 triple-join (fill + settlement) | 357 (city,td,metric) = 335 distinct city-days |
| OBS input for q_d0 reconstruction | 764 verified-auth city-days with `running_max` in window |
| Depth-joinable decisions | 1,560 decisions / 46 markets (all within 5-6 s of a snapshot) |

**G-DAY0 can produce a directional ROI verdict on existing data. 335 city-days is adequate for a pooled sign verdict; it is thin for per-region/per-daypart slices.**

---

## 2. obs_available_at — Exact Changes (backward-compatible)

All changes are forward-only instrumentation. Existing rows are untouched. Every column addition is `NULLABLE TEXT` with no default (ADD COLUMN is non-rewriting; idempotent on the empty `day0_nowcast_runs`).

### 2a. `src/state/db.py` — schema additions to `day0_nowcast_runs`

**Location:** `_create_day0_nowcast_runs` (lines 3562–3611) + ALTER list (3970–3977).

Add two nullable TEXT columns:
- `observation_available_at TEXT` — UTC ISO timestamp: the wall-clock time Zeus could query the observation that fed this run. Source: `Day0ObservationContext.observation_available_at` (the live field already produced at `observation_client.py:403/480/557`). Never synthesized from `now()`.
- `obs_availability_provenance TEXT` — enumerated: one of `{'live_fetch', 'rolling_hourly_imported_at', 'archive_dissemination_lag', 'UNVERIFIED'}`.

Add to **both** the `CREATE TABLE` (new DBs) and the idempotent `ALTER TABLE` list. Bump `SCHEMA_FORECASTS_VERSION` check. PK and NEI-backstop trigger unchanged. `nowcast_event_id_v1_hash` inputs unchanged (event-id stability preserved).

### 2b. `src/state/day0_nowcast_store.py` — writer

**Location:** `write_nowcast_run` (lines 122–217).

Add keyword params: `observation_available_at: Optional[str] = None`, `obs_availability_provenance: str = 'UNVERIFIED'`. Add to the INSERT column list and VALUES tuple. When `observation_available_at` is provided, assert it parses as UTC ISO. Write the raw value verbatim — do NOT substitute `now()`. Defaults make all existing call signatures valid; absent value writes NULL (honest UNVERIFIED).

### 2c. `src/engine/monitor_refresh.py` — thread context through

**Location:** `_maybe_write_day0_nowcast` (lines 1713–1801).

Pull `observation_available_at` and provenance from the `Day0ObservationContext` that already flows through the monitor. Pass `observation_available_at=ctx.observation_available_at`, `obs_availability_provenance='live_fetch'` into `write_nowcast_run(...)`. Guard: if `ctx.observation_available_at` is absent, pass `None` + `'UNVERIFIED'` — fail-soft, never fabricate `now()`. The helper is already fully fail-soft (try/except at 1790); this cannot break callers.

**Open question before wiring:** confirm the full `Day0ObservationContext` object (carrying `observation_available_at`) is in scope at the `_maybe_write_day0_nowcast` call site. The helper currently receives only `observation_time` (str). A one-line trace is needed before editing. If the context is not threaded that far, it must be added to the call signature.

### 2d. `src/events/triggers/day0_extreme_updated.py` — fix the backfill proxy

**Location:** `scan_observation_instants_rows` (lines 212–251), line 222; mirror at lines ~315 and ~406.

Replace `MAX(imported_at) AS observation_available_at` with a provenance-typed derivation:
- Compute `MIN(imported_at)` (earliest write = closest to true availability for rolling-hourly ingestion).
- Emit a sibling column `obs_availability_provenance`:
  - `'rolling_hourly_imported_at'` when `MAX(imported_at) - MIN(utc_timestamp) < 6h` (live rolling ingestion)
  - `'archive_dissemination_lag'` otherwise — set `observation_available_at = utc_timestamp + per_source_typical_lag` (WU_ICAO ~+35 min after the hour; ASOS/METAR ~+10 min), NOT `imported_at`.
- The downstream guard at `build_day0_extreme_updated_event` (line ~49–51) already rejects `available_at > decision_time`; a stricter (earlier) availability is fail-safe.
- Mirror identically at lines ~315 and ~406 for provenance consistency across all three emit paths.

**Open question:** canonical per-source typical dissemination lag (WU_ICAO historical.json, ASOS/METAR via Ogimet, openmeteo_archive_hourly) — operator/provenance docs must supply these constants before the archive-plane fix is authoritative.

### 2e. `src/state/db.py` — market scanner write gap (P1b dependency)

`best_bid`, `best_ask`, `raw_orderbook_hash` columns already exist in `market_price_history` but are 100% NULL. The Gamma/CLOB scanner must populate them from the CLOB orderbook endpoint. No schema change needed. This is a writer instrumentation gap — purely additive; existing 10,621 mid-only rows are unaffected.

---

## 3. Fill-Model + Simulator Design

### 3a. Live path — CURRENT_REUSABLE, no changes

`src/strategy/live_inference/executable_cost.py` (lines 70–85): ask/depth/fee-correct as-is. Confirmed:
- `_levels_for_direction`: `buy_yes→yes_asks`, `buy_no→no_asks` (native NO ask, never 1-yes complement)
- `price_type='ask'` for buys
- `_book_walk_average` (170–180): walks depth ladder, `min(remaining, level.size)` per level, raises `ExecutableCostError('NO_DEPTH')` on exhaustion
- Buy side: `raw.with_taker_fee(book.fee_rate)`; sell side: `polymarket_fee(value, rate)`
- `assert_not_midpoint_cost / assert_not_last_trade_cost / assert_not_no_complement_cost` (88–100) + `reject_forbidden_cost_source` (103–105) ban all prohibited sources by type

`quote_book_from_executable_snapshot` (136–155) builds the book from the same `orderbook_depth_json` the backtest reads — live/backtest fill parity is structural if the simulator reuses this function, not reimplements it.

### 3b. NEW: `src/backtest/day0_fill_simulator.py`

Inputs: `condition_id`, `decision_time` (ISO), `direction` (`buy_yes`/`buy_no`), `requested_shares`, fee context.

**Snapshot selection (anti-lookahead by type):**
```sql
SELECT * FROM executable_market_snapshots
WHERE condition_id = ? AND captured_at <= decision_time
ORDER BY captured_at DESC LIMIT 1
```
Strictly `<=` decision_time — never the nearest-future snapshot (mirrors `replacement_forecast_replay.py:191-196` queryable-time discipline). Reject if `gap(decision_time - captured_at) > FRESHNESS_WINDOW` (reuse `FRESHNESS_WINDOW_DEFAULT=30s` from `executable_market_snapshot.py:27`; measured median gap 5-6 s is well inside it).

**Fill walk:** call `quote_book_from_executable_snapshot(snapshot)` — DO NOT reparse JSON independently. Walk ladder: `filled_shares = min(requested_shares, total_available_depth)`. On `requested_shares > depth`: return a PARTIAL fill (not an exception), emit typed receipt `DAY0_FILL_DEPTH_EXCEEDED(requested, available, fill_ratio)`. Compute fees via the same `with_taker_fee/polymarket_fee` as live. On zero ask levels: typed `NO_DEPTH` — never a silent zero fill.

**Output:** `{filled_shares, average_fill_price, fee, slippage_vs_top_ask, fill_ratio, snapshot_id, snapshot_captured_at, snapshot_age_sec, depth_exceeded: bool}`.

**File-header provenance block required** (Created/Last-audited/Authority basis: PR_SPEC.md §3 P1).

### 3c. NEW: `src/strategy/live_inference/partial_fill_kelly.py`

The one genuine gap shared by both live and backtest. Kelly sizing assumes full fill at quoted edge; depth slippage yields a worse `average_fill_price` → realized edge shrinks → as-placed Kelly fraction is over-sized.

Implement: `recompute_kelly_after_partial(q_lcb, average_fill_price, cost, kelly_fraction_cap) → corrected_stake`. Re-derives `edge_realized = q_lcb - average_fill_price - cost` and recomputes fractional Kelly on the achieved price. If a partial fill occurred, caps remaining intent at the re-adjusted size on the next ladder level — never chases deeper levels past `edge_realized <= delta`. Used by both the G-DAY0 harness (honest ROI) and the live Day0 path (P3) via the same import.

**Open question:** should partial-fill Kelly chase deeper book levels until `edge_realized <= delta`, or stop at the first level and re-queue remaining intent next cycle? Affects both live P3 behavior and simulator `fill_ratio` semantics — operator/strategy decision required.

---

## 4. G-DAY0 Backtest Protocol

### Universe

357 (city, target_date, metric) units, window 2026-05-25..2026-06-06. Exclude 2026-06-07 (not yet VERIFIED-settled).

### Step 0 — offline build (one-time, before test window)

Fit `HorizonPlattFit` from `src/calibration/day0_horizon_calibration.py` on obs + settlement history **2024-01-01..2026-05-24** only (strict temporal holdout). Serve q_d0 via `src/signal/day0_high_nowcast_signal.py` / `day0_low_nowcast_signal.py`. Running-max monotone law already enforced at `day0_high_nowcast_signal.py:19-21` — do not bypass.

### Step 1 — per unit, per intraday decision_time t (grid: local 09:00/11:00/13:00/15:00)

**(a) OBS-LOCK (anti-lookahead by type):**
Select `observation_instants` rows for `(city, target_date)` where `reconstructed_available_at = utc_timestamp + FIXED_PUBLISH_LAG <= t`. Default lag = 75 min; sensitivity-sweep 60/90 min. **Assert the harness never reads `imported_at`.** Compute `running_max` / `running_min` from only those rows.

**(b) q_d0:**
`Platt(running_extreme_so_far, hours_remaining_to_local_midnight, daypart) → bin vector`. Take `q_lcb` via existing q_lcb law. **Pin the exact q_lcb/Kelly constants from the live Day0 path** — do not re-derive.

**(c) PRICE + FILL:**
Pick `executable_market_snapshots` row for matching `event_slug` condition with `captured_at <= t`, `MAX(captured_at)`. Walk `orderbook_depth_json` asks for the Kelly-sized notional; compute volume-weighted average fill price + slippage past best ask; apply fee from `fee_details_json`.

**(d) ADMIT:**
Trade only if `q_lcb - effective_ask - cost > delta`. Direction: native YES/NO token only. Size: fractional Kelly on `q_lcb`.

**Note on `captured_at` provenance:** `executable_market_snapshots.captured_at` is believed to be a true wall-clock fetch time (live plane). This must be confirmed before trusting the 5-6 s decision→snapshot gap as honest queryable-time — analogous to the `day0_extreme_updated.py:222 MAX(imported_at)` provenance risk. `captured_at` must not be a backfill-derived value.

**Note on ask-size units:** `orderbook_depth_json` level `size` is in SHARES (sampled values: 147, 20, 30). `requested_shares` from `trade_decisions` must be derived consistently as `size_usd / price` → shares. Verify against `min_order_size/min_tick` from the same snapshot.

### Step 2 — settle

Join `winning_bin` from `settlement_outcomes` (`authority='VERIFIED'` only; `zeus-forecasts.db`). Position pays 1 if selected bin == `winning_bin`, else 0. `realized_PnL = payout - fill_cost - fees`.

**Cross-DB join hazard (INV-37):** obs (`zeus-world.db`), market prices (`zeus-forecasts.db`), and trades (`zeus_trades.db`) are three separate K1-split DBs. Any join must use ATTACH + SAVEPOINT, never independent connections.

### Step 3 — aggregate over SELECTIVE traded subset

`after_cost_ROI = sum(PnL) / sum(capital_deployed)`. Also report: `n_trades`, `n_units_with_>=1_trade`, `capital_deployed`, settlement win-rate, bootstrap 90% CI (1000 resamples over units).

### HARD-STOP

**Pooled after-cost ROI ≤ 0 → Day0 profit thesis FAILS. Day0 stays mask-only/shadow. No further P3 build.**

### Anti-lookahead guards (mandatory, enforced by type)

| Guard | Mechanism |
|-------|-----------|
| obs-clock | `reconstructed_available_at = utc_timestamp + fixed_lag`; harness asserts it never reads `imported_at` |
| snapshot selection | `captured_at <= decision_time` strictly; future snapshot → typed `BLOCKED`, not a warning |
| Platt temporal holdout | `sample_period_end < min(test_window.target_date)`; violation fails harness precondition |
| Settlement authority | `authority='VERIFIED'` assertion before any PnL; QUARANTINED/UNVERIFIED → raises |
| Selective gate | `q_lcb - effective_ask - cost > delta` enforced before booking; violation → fails |

### PASS bar (all must hold simultaneously)

1. Pooled after-cost ROI > 0
2. Bootstrap 90% CI lower bound > 0 (1000 resamples over city-days)
3. ROI sign **stable** across both window halves (2026-05-25..05-31 vs 2026-06-01..06-06) AND across all three lag values (60/75/90 min)
4. Settlement win-rate on traded subset consistent with q_lcb (calibration not inverted)
5. ≥ ~40 distinct city-days actually trade (selective subset not degenerate-thin)

A single run that is positive but flips sign under lag/window re-probe = **NOT a pass**. Treated as ROI ≤ 0 for the hard-stop.

### Independent re-probe (single run ≠ trust)

Re-run end-to-end from a fresh process with:
- FIXED_PUBLISH_LAG swept 60/75/90 min
- Decision grid shifted +30 min
- Window split: first-half vs second-half computed separately
- Held-out subset of conditions/days — confirm parity and depth-reject behavior reproduce

### Sample-size caveats

335 city-days / 357 units is adequate for a **pooled directional ROI verdict**. It is too thin for reliable per-region/per-metric/per-daypart sub-verdicts. Reporting:
- All units: pooled ROI sign
- ≥10-captures-only: same (density filter)
- Low-metric separately (1,098 VERIFIED low vs 5,539 high — low verdict is under-powered; report separately, never pool with high-dominated ROI)
- Pre-2026-05-15 history: **NO depth data; do not extrapolate** a depth-aware ROI onto the pre-EMS window

---

## 5. Antibody Tests (write before implementation)

These are relationship tests — cross-module invariants that make error categories unconstructable, not just unwriteable in one syntax (§0 twist #2 law).

### obs_available_at antibodies

**T1 — RELATIONSHIP (nowcast write-through):**
`write_nowcast_run` from a live `Day0ObservationContext` → `read_nowcast_runs` round-trip → persisted `observation_available_at` equals `ctx.observation_available_at` byte-for-byte (no `now()` re-synthesis) AND `observation_available_at <= decision_time`. Makes write-time-as-availability substitution unconstructable for the nowcast lane.

**T2 — PROXY-HONESTY (rolling-hourly vs re-backfill):**
Fixture with one rolling-hourly row (`imported_at = utc_timestamp + 1h`) and one re-backfill row for the same city-day (`imported_at = utc_timestamp + 3d`). Assert new derivation yields `availability ≈ utc_timestamp + publish_lag`, NOT `MAX(imported_at) = utc_timestamp + 3d`. Makes the single-poison-row MAX defect a failing test.

**T3 — GUARD-BY-TYPE (anti-lookahead):**
`build_day0_extreme_updated_event` with an observation whose derived `observation_available_at > decision_time` must raise `ValueError('observation_available_at is after decision_time')`. A fabricated-future availability cannot be emitted; anti-lookahead by type, not convention.

**T4 — VOCAB (provenance enumeration):**
`obs_availability_provenance` value must be one of `{'live_fetch', 'rolling_hourly_imported_at', 'archive_dissemination_lag', 'UNVERIFIED'}`. CHECK constraint or test. UNVERIFIED is visible, never silently treated as honest (Fitz #4: authority field).

**T5 — MEASURABILITY GATE:**
G-DAY0 raises `INSUFFICIENT_DATA` (or returns a typed refusal) until ≥N city-days carry `obs_availability_provenance='live_fetch'` AND matching `market_price_history` rows with non-NULL `best_ask` exist. Prevents declaring a profit edge on a dataset that cannot support it.

### Fill-simulator antibodies

**T6 — LIVE/BACKTEST FILL PARITY:**
Same `(condition_id, decision_time, direction, shares)` → `day0_fill_simulator` average fill price == `executable_cost.executable_cost` on the same snapshot. Forces the simulator to reuse, not reimplement, the depth-walk — closes the duplicate-logic drift category structurally.

**T7 — ANTI-LOOKAHEAD (price):**
A snapshot with `captured_at > decision_time` is never selectable. `selected.captured_at <= decision_time` asserted by type; a future snapshot inserted into the fixture must be provably ignored.

**T8 — DEPTH-LIMIT:**
`requested_shares > sum(level.size)` → `fill_ratio < 1` + typed `DAY0_FILL_DEPTH_EXCEEDED`. A phantom full fill beyond book depth is unconstructable.

**T9 — NATIVE-NO-ASK ONLY:**
`buy_no` walks `no_asks`; a book with only `yes_asks` raises `NO_DEPTH` for `buy_no` — never silently falls back to `1 - yes_ask` (extends `executable_cost`'s existing complement ban into the simulator).

**T10 — PARTIAL-FILL KELLY MONOTONICITY:**
When `average_fill_price > top_ask` (depth slippage), `recompute_kelly_after_partial` returns a strictly smaller stake than the pre-fill Kelly. The realized-edge shrink is load-bearing, not advisory.

### G-DAY0 harness antibodies

**T11 — ANTI-LOOKAHEAD (obs):**
Every obs row entering a decision at t has `reconstructed_available_at = utc_timestamp + fixed_lag <= t`. A unit test feeds a row whose `imported_at < t` but `utc_timestamp + lag > t` and asserts it is **excluded**. Harness asserts it never reads `imported_at` anywhere in the pipeline.

**T12 — TEMPORAL HOLDOUT:**
Platt fit `sample_period_end < min(test_window.target_date)`. A fit row whose sample period overlaps the test window fails the harness precondition.

**T13 — DEPTH-WALK FILL:**
Fill price ≥ best ask and increases with size (depth exhausted → next level). A fill returning best-ask price for a notional exceeding `depth_at_best_ask` must fail.

**T14 — SETTLEMENT AUTHORITY:**
Every settled unit pulls `winning_bin` from `settlement_outcomes WHERE authority='VERIFIED'`. A QUARANTINED row entering PnL aggregation raises.

**T15 — SELECTIVE-GATE INTEGRITY:**
No trade booked unless `q_lcb - effective_ask - cost > delta`. A trade admitted at `<= delta` fails.

**T16 — RE-PROBE SIGN STABILITY:**
CI-level assertion that ROI sign is identical across all three lag values and both window halves. Divergent sign → harness reports FAIL, not a nuanced PASS.

---

## 6. Blocking Open Questions

The following must be resolved before the associated implementation can be considered correct. They are not deferrable to post-ship.

| # | Question | Blocks |
|---|----------|--------|
| OQ-1 | **Is the Gamma/CLOB market scanner still running?** `market_price_history` ends 2026-05-28 (~10 days stale). If down, the forward-capture clock has not started. Operator must confirm / restart before any wait estimate is meaningful. | P1b forward capture plan; T5 measurability gate |
| OQ-2 | **Is the full `Day0ObservationContext` object (carrying `observation_available_at`) in scope at the `_maybe_write_day0_nowcast` call site in `monitor_refresh.py`?** The helper currently receives only `observation_time` (str). A one-line trace at the call site is needed before editing §2c. If not in scope, it must be added to the call signature. | §2c implementation |
| OQ-3 | **What are the canonical per-source typical dissemination lags** for the archive plane (WU_ICAO `historical.json`, ASOS/METAR via Ogimet, `openmeteo_archive_hourly`)? These constants must be sourced from provenance docs, not guessed. The archive-plane fix at `day0_extreme_updated.py:222` is provisional until they are provided. | §2d archive-plane fix |
| OQ-4 | **Is the Day0 nowcast lane actually firing in production?** `day0_nowcast_runs = 0`. If `read_latest_platt_fit()` returns `None` in prod (no Platt fit ever persisted), `_maybe_write_day0_nowcast` is skipped silently at `monitor_refresh.py:1756`. Schema changes produce no rows if the lane never fires. Needs runtime confirmation before instrumentation has anything to capture. | §2c; forward capture plan |
| OQ-5 | **Should partial-fill Kelly chase deeper book levels until `edge_realized <= delta`, or stop at first level and re-queue?** Affects live P3 behavior and simulator `fill_ratio` semantics. Operator/strategy decision. | §3c design; T10 semantics |
| OQ-6 | **Is `executable_market_snapshots.captured_at` a true wall-clock fetch time, or can any rows be backfill/import-derived?** Determines whether the 5-6 s decision→snapshot gap is honest queryable-time. Same provenance question as `day0_extreme_updated.py:222`. If backfill rows exist, the gap measurement is not trusted for G-DAY0 ROI. | G-DAY0 ROI trust; Step 1c |
| OQ-7 | **What is the acceptable N for G-DAY0 power?** How many independent city-day × Day0-market observations within `hours_remaining <= 6` are required for the obs→market lag edge to be statistically distinguishable from zero at the operator's intended trade size? Sets the forward-capture wait duration; should be derived from variance of the lag, not assumed. | T5; forward-capture wait estimate |
| OQ-8 | **Pin the exact q_lcb/Kelly constants from the live Day0 path.** The G-DAY0 harness's selective gate must use the same LCB shrinkage + fractional-Kelly fraction the live engine would apply, so the backtest's ADMIT decisions match what live would actually trade. Confirm the constants and hard-code them into the harness. | G-DAY0 ADMIT gate |
| OQ-9 | **Does `event_slug → (city, target_date, metric)` parsing cover 100% of in-window slugs?** Multi-word cities (Hong Kong, Buenos Aires, São Paulo, Cape Town) must parse correctly. The 357-unit join round-trips, but the ~42 one-to-two-capture slug-days and any non-matching formats should be audited so the universe is not silently under-counted. | G-DAY0 universe completeness |
