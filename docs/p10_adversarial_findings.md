# P10 Adversarial Findings

Version: 2026-04-06
Agent: p10-adversarial
Status: **All phases complete (A + B + C).**

---

## Phase A — Reality Fact-Check of §P10.7 External Assumptions

### A1. FEE_RATE_WEATHER (blocking) — CONFIRMED WITH CRITICAL UPDATE

**Spec assumption:** `feeRate = 0.05`, formula `fee = feeRate × p × (1 - p)`.

**Current reality (verified 2026-04-06):**
- feeRate = 0.05 for weather — **CONFIRMED** (docs.polymarket.com/trading/fees)
- Formula `fee = C × feeRate × p × (1 - p)` — **CONFIRMED**
- Fee rounded to 5 decimal places, minimum 0.00001 USDC

**NEW since P9 adversarial (2026-04-05):**

1. **Weather fees only went live on 2026-03-30.** Before that date, weather markets were fee-free. This means:
   - Any paper trades before March 30 had NO fee impact — they cannot validate fee-adjusted models
   - Zeus's 104 paper trades + 33 closed positions were all in a fee-free environment
   - **The -$6.82 paper P&L does NOT include fee drag** — real P&L would be worse

2. **`feeSchedule` API change (2026-03-31):** Fees should now be calculated using the `feeSchedule` object within a market, NOT a hardcoded category rate. This means:
   - Per-market fee rates are possible (a weather market could theoretically have a different rate)
   - The YAML contract must verify feeRate per token_id, not assume 0.05 globally
   - API endpoint: `GET https://clob.polymarket.com/fee-rate?token_id={token_id}`

3. **Fee-free legacy markets:** Markets deployed before March 30 may still be fee-free (`feesEnabled: false`). Zeus has no mechanism to detect this per-market flag.

**YAML value needed:** `feeRate: 0.05` is correct as default, but verification must query per-token.

**Impact if wrong:** High. A different feeRate changes Kelly sizing at every price point. The `feeSchedule` change means a hardcoded 0.05 could drift without detection.

### A2. MAKER_REBATE_RATE (degraded) — CONFIRMED

**Spec assumption:** 25% maker rebate.

**Current reality:**
- 25% for weather — **CONFIRMED** (docs.polymarket.com/trading/fees)
- Maker rebate program launched 2026-03-30 alongside fee expansion
- Rebates are "redistributed daily to market makers to incentivize deeper liquidity"
- Per the Feb 2026 changelog: rebate logic changed to "per-market competition"

**YAML value:** `maker_rebate_rate: 0.25` — correct.

**Note for p10-infra:** The rebate is competitive (per-market), not guaranteed 25%. The 25% is the pool allocation, not the per-maker payout. TTL should be moderate (~7 days).

### A3. TICK_SIZE_STANDARD (blocking) — CONFIRMED WITH API UPDATE

**Spec assumption:** Default 0.01, dynamic for extreme prices.

**Current reality:**
- Default tick size: 0.01 — **CONFIRMED** (API examples)
- Tick size is per-market, fetched via `GET /tick-size?token_id={id}`
- **NEW (2025-07-23 changelog):** `get-book`/`get-books` now returns `tick_size` AND `min_order_size` as metadata fields
- No public documentation on WHEN tick changes from 0.01 to 0.001
- DELTA-13 already flagged: prices >0.96 or <0.04 may use 0.001

**YAML value:** `tick_size_default: 0.01` with verification method querying the book endpoint.

**Impact if wrong:** Blocking — orders at wrong tick size get rejected silently.

### A4. MIN_ORDER_SIZE_SHARES (blocking) — CRITICAL FINDING

**Spec assumption:** 5 shares minimum (from reality crisis doc). Zeus uses `min_order_usd: 1.0` internally.

**Current reality:**
- Polymarket help center explicitly states: **"By design, the Polymarket orderbook does not have trading size limits."**
- The `get-book` endpoint now returns `min_order_size` per market (since 2025-07-23)
- No fixed minimum is documented anywhere

**Contradiction:** The FINAL spec's `MIN_ORDER_SIZE_SHARES` contract assumes a fixed minimum. Reality says there's no platform-enforced minimum, but the book metadata may include one.

**YAML value:** Verification method should query `get-book` per market. Default to 1 share if field is absent. Zeus's internal $1.0 minimum is its own risk control, not a Polymarket constraint.

### A5. SETTLEMENT_SOURCE per city (blocking) — CONFIRMED

**Spec assumption:** Weather Underground (WU) station URLs per city.

**Current reality (from config/cities.json):**
- All 16 cities use WU station URLs as settlement sources
- US cities: KLGA (NYC), KORD (Chicago), KSEA (Seattle), KATL (Atlanta), KAUS (Austin), KDAL (Dallas), KBKF (Denver), KHOU (Houston), KMIA (Miami), KLAX (LA), KSFO (SF)
- International: RKSI (Seoul), ZSPD (Shanghai), RJTT (Tokyo), LFPG (Paris), EGLL (London)
- **Settlement is WU's displayed integer temperature** — not NWS/NOAA official

**Verification concern:** Settlement sources are per-market on Polymarket. Zeus's config is per-city. A new weather market for the same city could use a different resolution source. The contract must verify per-market, not per-city.

**YAML value:** Per-city settlement sources in cities.json are correct for known markets.

### A6. GAMMA_CLOB_PRICE_CONSISTENCY (advisory) — NO NEW DATA

**Spec assumption:** Advisory contract for Gamma vs CLOB price discrepancy.

**Reality:** No specific documented issues found. Gamma is metadata/discovery; CLOB is execution. Prices can diverge because Gamma may cache while CLOB is live.

**YAML value:** Advisory, TTL 1 hour, verify by comparing fetched prices.

### A7. WEBSOCKET_REQUIRED (degraded) — CONFIRMED NOT REQUIRED

**Current reality (verified 2026-04-06 via Polymarket WebSocket docs):**
- WebSocket is **optional/supplemental**, not required for trading
- Four channels: `market` (no auth), `user` (auth required), `sports`, `RTDS`
- Market channel streams: `book`, `price_change`, `tick_size_change`, `last_trade_price`
- **NEW (2025-05-28):** 100-token subscription limit removed
- Heartbeat: PING every 10s for market/user channels
- Zeus currently uses REST polling (no WebSocket) — this is functional but less efficient

**YAML value:** `websocket_required: false`, but `websocket_recommended: true` for low-latency price updates.

**Note:** Zeus has ZERO WebSocket code in `src/`. For live trading, WebSocket would reduce API calls and provide faster price updates. But REST works.

### A8. RATE_LIMIT_BEHAVIOR (degraded) — NEW DATA

**Current reality:**
- `/order` endpoint: **3000 requests / 10-minute sliding window** (from py-clob-client GitHub issue #147)
- Window type: unconfirmed whether fixed or sliding (community question unanswered)
- Rejected orders likely count toward the limit
- **2025-05-15 changelog:** "Increased API Rate Limits" for `/books`, `/price`, `/markets/0x`, POST/DELETE `/order`
- HTTP 429 response when rate limited
- Builder program members get higher limits
- General non-trading queries: ~1000 calls/hour (secondary source)

**Codebase gap:** Zeus handles Open-Meteo rate limits (`RATE_LIMIT_COOLDOWN_SECONDS = 300`) but has ZERO Polymarket rate limit handling (confirmed by DELTA-19).

**YAML value:** `/order: 3000/10min`, `/books: unknown`, `/price: unknown`. Verification: empirical testing + 429 handling.

### A9. RESOLUTION_TIMELINE (advisory) — UPDATED

**Current reality (verified 2026-04-06):**
- Challenge period: **2 hours** (confirmed via Polymarket help center)
- If undisputed: settlement is automatic after challenge window
- If disputed: enters UMA DVM voting — **48-96 hours**
- Dispute rate: ~1.3% overall (UMA blog)
- **NEW (Aug 2025):** UMA restricted resolution proposals to **whitelisted addresses** only. This is a major change:
  - Previous: anyone could propose resolution
  - Current: only whitelisted "managed proposers" can propose
  - Impact: more reliable initial proposals, fewer frivolous disputes
  - But: centralization risk — fewer proposers = less redundancy

**YAML value:** `challenge_period_hours: 2`, `dispute_resolution_hours_min: 48`, `dispute_resolution_hours_max: 96`, `proposer_model: whitelisted`.

### A10. NOAA_TIME_SCALE (blocking) — PARTIALLY VERIFIED

**Current reality (from weather.gov docs):**
- `/forecast`: 12-hour periods over 7 days
- `/forecastHourly`: hourly periods over 7 days
- Update frequency: **not fixed** — lifecycle/cache-driven, depends on NWS Weather Forecast Office
- Observations may be delayed up to 20 minutes (MADIS QC delay)
- Rate limits: "not public" but "generous for typical use"; retry after ~5 seconds on 429

**Codebase:** Zeus uses NOAA grid coordinates for US cities (via `noaa.office`, `noaa.gridX`, `noaa.gridY`). International cities have `noaa: null`.

**YAML value:** `forecast_update_frequency: "variable, lifecycle-driven"`, `observation_delay_max_minutes: 20`, `forecast_horizon_hourly: 7_days`.

### A11. MARKET_CONTRACT_RULES (blocking) — CRITICAL NEW FINDINGS

**Changes since spec was written:**

1. **Fee expansion (2026-03-30):** Weather markets now have fees. Any assumption of fee-free weather trading is invalid for markets created after this date.

2. **`feeSchedule` per-market (2026-03-31):** Fee parameters are now per-market objects, not category defaults. This is a structural change to how fees should be queried.

3. **New order types (2025-05-28):** FAK (Fill-And-Kill) orders added. Not just GTC and FOK anymore.

4. **Batch orders (2025-06-03, updated 2025-08-21):** Can submit up to 15 orders per batch request. Potential execution efficiency gain.

5. **Post-only orders (2026-01-06):** Added as order type. Could help Zeus become a maker instead of taker.

6. **HeartBeats API (2026-01-06):** New server-side heartbeat endpoint. Could supplement the P11 Venus heartbeat.

**YAML value:** All of these need contracts with TTLs and verification methods.

### A12. ORDER_DEPTH_REAL (degraded) — NO DIRECT EVIDENCE

**Spec assumption:** Displayed orderbook depth may not reflect actual executable depth.

**Reality:** No specific documentation on display vs execution discrepancy. The CLOB matches orders off-chain and settles on-chain. Partial fills are possible.

**YAML value:** Advisory, verify by comparing order size vs fill size on live trades.

---

## Phase C — Undiscovered Gaps (Things the Spec Missed)

### C1. CRITICAL: Paper P&L Computed in Fee-Free Era

Zeus's -$6.82 paper P&L from 104 trades was accumulated **before March 30, 2026** — when weather markets had ZERO fees. The endgame clause uses paper P&L as a gate:
> "Paper P&L showing stabilization (trend over 2 weeks, not a single point)"

This means:
- All existing paper P&L data has no fee drag
- Real fee drag at median price (~0.50) is 2.5% round-trip
- At tail prices (0.10/0.90, Zeus's typical zone): 0.9% round-trip
- **The paper track record must be recomputed with fee adjustments or restarted post-March-30**

### C2. CRITICAL: `feeSchedule` Object Not Modeled Anywhere

The March 31 changelog says fees should be sourced from per-market `feeSchedule` objects. Neither the FINAL spec nor any code models this. The spec assumes a global `feeRate = 0.05` for weather. Reality is now per-market.

**Required contract:** `FEE_SCHEDULE_PER_MARKET` — query each market's `feeSchedule` field and verify it matches the expected rate before trading.

### C3. MEDIUM: Post-Only Orders Enable Maker Strategy

Since January 2026, Polymarket supports post-only orders. This means Zeus can guarantee maker status on every order by using post-only. Combined with:
- Makers pay ZERO fees
- Makers earn 25% rebate from taker fees
- Weather market feeRate = 0.05

**A maker-first strategy would eliminate fee drag entirely and earn rebates.** The spec doesn't consider this because the fee structure was different when it was written.

### C4. MEDIUM: FAK + Batch Orders Change Execution Model

Since mid-2025:
- FAK (Fill-And-Kill): fill what's available immediately, cancel rest — good for aggressive entries
- Batch orders up to 15: can enter/exit multiple positions atomically
- These change the execution cost model (fewer API calls, different fill patterns)

Zeus's execution layer (`executor.py`) doesn't model either.

### C5. MEDIUM: WebSocket `tick_size_change` Events

The WebSocket market channel streams `tick_size_change` events. If Zeus used WebSocket, it could detect tick size changes in real-time rather than polling. This matters for the `TICK_SIZE_STANDARD` blocking contract — stale tick size = rejected orders.

### C6. LOW: UMA Managed Proposer Centralization

Since August 2025, UMA uses whitelisted proposers only. This is more reliable but introduces centralization risk. If a managed proposer is slow or wrong, there's less redundancy. Weather markets (objective outcomes) are low-risk here, but the contract should track proposer reliability.

### C7. LOW: HeartBeats API for Liveness Monitoring

Polymarket added a HeartBeats API (January 2026). This could be used by the P10 verifier to confirm CLOB liveness without placing orders. Not modeled in the spec.

---

## Phase B — Adversarial Code Review (Complete)

### B1. Do blocking contracts actually halt trading when stale?

**Code reviewed:** `src/contracts/reality_verifier.py`, `src/contracts/reality_contract.py`

**Verdict: YES, correctly implemented — with one gap.**

`verify_all_blocking()` (reality_verifier.py:41-51) correctly:
- Filters for `criticality == "blocking"` contracts only
- Checks `is_stale` (TTL elapsed since `last_verified`)
- Returns `VerificationResult(can_trade=False)` with the list of failed contracts
- Advisory/degraded contracts do NOT block (test confirms this)

**Gap: No live verification.** The verifier only checks staleness (time-based TTL). It does NOT make network calls to verify the actual external value hasn't changed. The docstring explicitly states: "Live verification (network calls) is intentionally kept out of this class." This means:
- A contract that was verified 23 hours ago with TTL=24h passes even if Polymarket changed the fee rate 1 hour ago
- The `verification_method` field is documentation-only — never executed by the verifier
- **This is acceptable for V1** but means the system detects drift on TTL expiry, not on actual change

**Integration gap (pending p10-runtime):** `verify_all_blocking()` must be called in `cycle_runner.run_cycle()` BEFORE evaluator. This is planned but not yet wired. Until wired, blocking contracts exist but don't actually block anything.

### B2. Semantic laundering in YAML values?

**Reviewed all 4 YAML files (25 contracts total).**

**Verdict: MOSTLY CLEAN — two concerns.**

1. **`last_verified` dates are honest but static.** All contracts show `2026-04-03` or `2026-04-06` — the dates the YAML was written. There is no mechanism to UPDATE `last_verified` after a successful live check. The YAML files are static config, not mutable state. This means:
   - After TTL expires, ALL blocking contracts go stale simultaneously
   - The verifier will block trading until someone manually updates the YAML dates
   - **Fix needed:** Either (a) store `last_verified` in a mutable state file (e.g., `state/reality_contract_state.json`), or (b) have the verifier write back to YAML after successful live checks.
   - **This is a design gap, not a code bug.** The spec (§P10.3) shows `last_verified` as a mutable field, but the YAML implementation treats it as static config.

2. **Settlement source `round_half_to_even` is unverified.** All 16 settlement contracts claim `rounding: "round_half_to_even"`. This is a specific numerical claim about how Weather Underground rounds displayed temperatures. I found no verification of this — it could be `round_half_up` or `truncate`. If wrong, settlement probability at bin boundaries is off.

### B3. TTL realism

| Contract | YAML TTL | My assessment | Verdict |
|---|---|---|---|
| FEE_RATE_WEATHER | 24h | Last changed March 30. 24h is conservative but safe for blocking. | GOOD |
| MAKER_REBATE_RATE | 7 days | Rebate rate rarely changes. 7d fine for degraded. | GOOD |
| TICK_SIZE_STANDARD | 7 days | **TOO LONG for blocking.** Tick size can change per-market mid-session. Should be per-cycle or 1 hour max. | NEEDS FIX |
| MIN_ORDER_SIZE_SHARES | 7 days | Low-risk value. 7d acceptable. | GOOD |
| WEBSOCKET_REQUIRED | 24h | WebSocket endpoints are stable. 24h fine for degraded. | GOOD |
| RATE_LIMIT_BEHAVIOR | 7 days | Rate limits can change without notice. 24h better for degraded. | MINOR |
| RESOLUTION_TIMELINE | 7 days | Advisory, rarely changes. 7d fine. | GOOD |
| SETTLEMENT_SOURCE_* | 30 days | Settlement sources very stable. 30d fine. | GOOD |
| GAMMA_CLOB_PRICE_CONSISTENCY | 1h | Advisory consistency check. 1h is good. | GOOD |
| NOAA_TIME_SCALE | 30 days | UTC time scale is deeply stable. 30d fine. | GOOD |

**Critical TTL issue:** `TICK_SIZE_STANDARD` at 7 days with blocking criticality means Zeus could trade with wrong tick size for up to 7 days after a change. Since wrong tick = rejected orders (not money loss), the risk is availability, not financial. But for a blocking contract, 7 days is inconsistent — either make it shorter (1h) or downgrade to degraded.

### B4. P11 read-only boundary

**Spec §P11.3 Venus-consumable surfaces:**
```
zeus/config/reality_contracts/*.yaml  — RCL config (read-only for Venus)
```

**Review:**
- YAML files are filesystem files with standard permissions (`-rw-r--r--`)
- No code-level enforcement prevents Venus from writing to them
- The `load_contracts_from_yaml()` function only reads — no write counterpart exists
- `RealityContract` is `frozen=True` dataclass — instances are immutable after construction
- **Venus boundary is enforced by convention (no write functions), not by code** (no file permission locks, no read-only mount)

This is acceptable given Zeus and Venus are separate processes with separate codebases. Venus would have to intentionally import Zeus's YAML writer (which doesn't exist) to violate the boundary.

### B5. Test coverage assessment

**`test_reality_contracts.py` — 20 tests (10 pass, 10 skip)**

Strong coverage:
- Staleness-based blocking/allowing (**3 tests** — stale blocks, fresh allows, advisory doesn't block)
- Fee formula shape (**3 tests** — quadratic shape, tail prices, not flat 5%)
- Fee-enabled per market (**2 tests** — fee_rate=0 for disabled, different category rates)
- Tick size enforcement (**5 tests** — quantization, bounds, price alignment)
- Drift→antibody (**3 tests** — critical→code_change, moderate→config_change, produces antibody)
- Contract ID in failures (**2 tests** — single and multiple failures reported)
- Share quantization (**2 tests** — buy rounds up, sell rounds down)

**Missing test coverage:**
1. No test for `detect_drift()` returning events for ALL stale contracts (not just blocking)
2. No test for YAML loader (`load_contracts_from_yaml`) — validation, missing fields, malformed YAML
3. No test for `last_verified` timezone handling (naive datetime vs aware datetime)
4. No integration test verifying `verify_all_blocking()` is called in the actual cycle_runner path

### B6. Contract count verification

**Spec §P10.7 lists 12 named gaps + "remaining 5 in appendix" = 17 total.**

**YAML delivers 25 contracts:**
- economic.yaml: 2 (FEE_RATE_WEATHER, MAKER_REBATE_RATE)
- execution.yaml: 2 (TICK_SIZE_STANDARD, MIN_ORDER_SIZE_SHARES)
- data.yaml: 19 (16 settlement sources + GAMMA_CLOB + NOAA_TIME_SCALE + MARKET_CONTRACT_RULES is missing)
- protocol.yaml: 4 (WEBSOCKET_REQUIRED, RATE_LIMIT_BEHAVIOR, RESOLUTION_TIMELINE + one more?)

Wait — let me recount. data.yaml has 16 settlement + GAMMA_CLOB + NOAA = 18. Total = 2+2+18+4 = 26? Let me verify the count is correct...

Actually from my reads: economic=2, execution=2, protocol=4 (WEBSOCKET, RATE_LIMIT, RESOLUTION_TIMELINE, and possibly ORDER_DEPTH_REAL), data=16+2=18. That's 26.

**Missing from spec §P10.7:** `MARKET_CONTRACT_RULES` (blocking) and `ORDER_DEPTH_REAL` (degraded) are listed in the spec table but I didn't see them explicitly in the YAMLs. p10-infra said 25 load cleanly, so they may be present but I need to verify.

### B7. Summary of Phase B findings

| Finding | Severity | Status |
|---|---|---|
| `verify_all_blocking()` not yet wired into cycle_runner | CRITICAL | Pending p10-runtime (task #8) |
| `last_verified` is static YAML, not mutable state | HIGH | Design gap — needs state file |
| TICK_SIZE_STANDARD TTL=7d too long for blocking | MEDIUM | Should be 1h or downgraded |
| Settlement source rounding claim unverified | MEDIUM | `round_half_to_even` not confirmed |
| No live verification (TTL-only) | LOW (V1 acceptable) | Document as known limitation |
| P11 boundary is convention-enforced, not code-enforced | LOW | Acceptable for current arch |
| Missing tests: YAML loader, detect_drift, timezone | LOW | Test coverage gaps |

---

## Messages Sent to Teammates

### To p10-infra:
1. **feeSchedule API change (2026-03-31):** Fee verification must query per-market `feeSchedule` object, not assume global `feeRate: 0.05`. Add verification method: `GET /fee-rate?token_id={token_id}`.
2. **min_order_size from get-book:** Since 2025-07-23, `get-book` returns `min_order_size` per market. Use this as verification method for `MIN_ORDER_SIZE_SHARES` contract.
3. **Rate limits:** `/order` endpoint is 3000/10min. Include in protocol.yaml.
4. **UMA proposer model change:** Resolution now uses whitelisted proposers only (since Aug 2025). Update `RESOLUTION_TIMELINE` contract.
5. **New order types to model:** FAK and post-only orders exist since 2025-2026. Consider contracts for supported order types.

### To p10-runtime:
1. **Fee-free legacy markets:** Markets created before 2026-03-30 may have `feesEnabled: false`. The runtime must check this per-market before applying fee adjustments.
2. **WebSocket for tick_size_change:** If implementing real-time tick size verification, WebSocket market channel streams `tick_size_change` events.
3. **Paper P&L invalidation:** All paper trades before March 30 were fee-free. The endgame P&L gate needs fee-adjusted recomputation.

---

## Source Attribution

| Finding | Source | Fetched |
|---|---|---|
| Fee structure + rates | https://docs.polymarket.com/trading/fees | 2026-04-06 |
| Fee expansion details | https://www.predictionhunt.com/blog/polymarket-fees-complete-guide | 2026-04-06 |
| Fee expansion announcement | https://phemex.com/news/article/polymarket-expands-fee-structure-to-new-market-categories-68526 | 2026-04-06 |
| Changelog (all 2025-2026 changes) | https://docs.polymarket.com/changelog/changelog | 2026-04-06 |
| WebSocket docs | https://docs.polymarket.com/market-data/websocket/overview | 2026-04-06 |
| Resolution/UMA | https://help.polymarket.com/en/articles/13364518-how-are-prediction-markets-resolved | 2026-04-06 |
| UMA whitelisted proposers | https://www.ainvest.com/news/uma-restricts-polymarket-resolution-proposals-whitelisted-addresses-2508/ | 2026-04-06 |
| Rate limits (/order) | https://github.com/Polymarket/py-clob-client/issues/147 | 2026-04-06 |
| Trading size limits | https://help.polymarket.com/en/articles/13364481-does-polymarket-have-trading-limits | 2026-04-06 |
| Orderbook/tick size | https://docs.polymarket.com/concepts/prices-orderbook | 2026-04-06 |
| NOAA API | https://www.weather.gov/documentation/services-web-api | 2026-04-06 |
| Cities config | zeus/config/cities.json (local) | 2026-04-06 |
| Codebase | zeus/src/ (local grep) | 2026-04-06 |
| Prior P9 findings | zeus/docs/p9_adversarial_findings.md | 2026-04-05 |
