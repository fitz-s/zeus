# P12 External Findings — Live Execution Reality Gap

Version: 2026-04-06
Status: **OPEN — pre-live gate**
Author: p12-external (research agent)
Sources: Polymarket official docs, py-clob-client source/issues, open-source bot repos

---

## Summary

12 external findings + 2 cross-referenced from Rainstorm comparison. After verification against py-clob-client source AND Rainstorm live trading evidence:
- **3 BLOCK LIVE** (must fix before first live order — all confirmed by Rainstorm comparison)
- **5 DEGRADE** (will cause intermittent failures or reduced reliability)
- **3 MINOR** (handled by py-clob-client or low-impact at Zeus's scale)
- **3 STARTUP CHECKS** (one-time verification before going live)

**CORRECTION (post-review):** F1 (heartbeat) and F7 (WebSocket) were initially rated "blocks live" but downgraded after p12-rainstorm demonstrated that Rainstorm trades live successfully with neither. Heartbeat is opt-in safety, not mandatory. REST polling works for fill detection. The true blockers are: partial fill handling (R1), process lock (R3), and min order size retry (R1-ext).

---

## BLOCKS LIVE (3 — Rainstorm-confirmed)

See "Cross-Reference: Rainstorm Comparison Findings" section below for the 3 true blockers (R1 partial fill, R3 process lock, R1-ext min order size retry).

---

## OPTIONAL SAFETY / DOWNGRADED (2)

### F1. Heartbeat Keepalive — Optional Safety Mechanism (DOWNGRADED from "blocks live")

**Problem:** Polymarket CLOB requires a `POST /heartbeats` call every 10 seconds (with 5s buffer). If missed, ALL open orders are automatically cancelled. This is not optional — it's the exchange's liveness mechanism.

**External evidence:**
- https://docs.polymarket.com/trading/orders/create — "If a valid heartbeat is not received within 10 seconds (with a 5-second buffer), all open orders are cancelled."
- https://docs.polymarket.com/api-reference/trade/send-heartbeat — `POST https://clob.polymarket.com/heartbeats` with L2 auth headers. Returns `{"status":"ok"}`.

**Zeus status:** **DOES NOT HAVE.** Zeus's executor is fire-and-forget. `_live_order()` places an order and returns `status="pending"` with a timeout (4h/1h/15min). No background thread sends heartbeats. Every order will be cancelled within ~15 seconds of placement.

**What production bots do:** Run a background thread/async task that sends `POST /heartbeats` every ~8 seconds while any order is open. The py-clob-client does NOT do this automatically — it must be implemented by the bot.

**Revised assessment:** Rainstorm trades live successfully with zero heartbeat implementation. Heartbeat is effectively opt-in: if you never start sending heartbeats, orders persist normally as GTC. Recommended as a future safety feature for market-maker-style operation, not a live blocker.

---

### F7. Fill Detection — REST Polling Sufficient (DOWNGRADED from "blocks live")

**Problem:** After placing an order, Zeus has no way to know if it filled, partially filled, or was cancelled. The executor returns `status="pending"` and nothing ever checks the outcome.

**External evidence:**
- https://docs.polymarket.com/developers/CLOB/websocket/wss-overview — User channel (`wss://ws-subscriptions-clob.polymarket.com/ws/user`) pushes `trade` events (MATCHED → CONFIRMED lifecycle) and `order` events. Requires apiKey/secret/passphrase auth.
- https://docs.polymarket.com/developers/market-makers/trading — "Subscribe to the WebSocket user channel for real-time fill notifications."
- REST alternative: `GET /order?id={orderID}` returns fill state, but is slower (~1s) and rate-limited (900/10sec).

**Zeus status:** **DOES NOT HAVE.** DELTA-16 in runtime delta ledger confirms no WebSocket integration. After `_live_order()` returns "pending", the CycleRunner has no mechanism to detect fills. Position tracking will be wrong. P&L will be wrong. Exit orders may target positions that don't exist.

**What production bots do:** Either (a) WebSocket user channel subscription with fill callbacks, or (b) polling `GET /order?id=` on a timer. WebSocket is strongly preferred for latency and reliability.

**Revised assessment:** Rainstorm trades live successfully with REST-only fill detection (5s sleep + get_order_status). Zeus already has `fill_tracker.py:check_pending_entries()` called from `cycle_runtime.py:272-274` every cycle. The real gap is PARTIALLY_FILLED handling (R1), not the transport layer. WebSocket would improve latency but is not required for live.

---

### F8. Orphaned Order Cleanup on Crash/Restart

**Problem:** If Zeus crashes with open GTC orders, those orders persist on the exchange. On restart, Zeus's internal state won't know about them. Consequences: (a) double-ordering the same position, (b) stale limit orders filling at bad prices hours/days later.

**External evidence:**
- Polymarket docs: `DELETE /cancel-all` cancels all open orders. `GET /orders` returns all open orders.
- The heartbeat mechanism provides partial protection (orders auto-cancel ~15s after crash), BUT only if heartbeat was active. Without F1 implemented, GTC orders persist indefinitely.

**Zeus status:** **DOES NOT HAVE.** Zeus has `get_open_orders()` and `cancel_order()` in `polymarket_client.py` but no startup reconciliation logic.

**What production bots do:** On startup: (1) fetch all open orders via `GET /orders`, (2) cancel any that don't match current strategy state, (3) fetch positions via data API to reconcile internal state.

**Required fix:** Startup reconciliation: cancel-all orphaned orders, fetch and reconcile positions, then proceed with normal operation.

---

## DEGRADES (3)

### F6. Order Type Defaults to GTC — No Auto-Expiry

**Problem:** Zeus's timeout logic (4h opening_hunt, 1h update_reaction, 15min day0) implies orders should auto-expire. But Zeus uses py-clob-client's default GTC (Good Till Cancelled), meaning orders persist forever unless actively cancelled.

**External evidence:**
- https://docs.polymarket.com/trading/orders/create — GTD (Good Till Date) requires `expiration` as Unix timestamp. GTD has 1-minute security threshold: effective lifetime = `now + 60 + N_seconds`.

**Zeus status:** **MISMATCH.** The executor computes timeouts but they're only used for logging — no mechanism cancels orders at timeout. With heartbeat (F1), orders survive; without heartbeat, they're cancelled in 15s anyway.

**Severity:** After F1 is fixed, GTC orders will survive indefinitely. Zeus must either: (a) use GTD with expiration matching its timeout policy, or (b) implement an active cancellation timer. GTD is simpler and safer.

---

### F9. Amount Precision Rules

**Problem:** CLOB enforces: makerAmount (USDC) max 2 decimals, takerAmount (shares) max 4 decimals. If `size × price` doesn't resolve cleanly, order is rejected.

**External evidence:** GitHub issue Polymarket/py-clob-client#121 — `1.74 × 0.58 = 1.0092` → rejection. Error: "invalid amounts, the sell orders maker amount supports a max accuracy of 2 decimals."

**Zeus status:** **PARTIALLY HANDLED.** Zeus quantizes shares (ceil/floor to 0.01) but doesn't verify the resulting USDC amount. py-clob-client may handle this internally — needs dry-run verification.

**Severity:** Intermittent order rejections at certain price/size combinations. Not blocking but will cause missed trades.

---

### F10. Fee Asymmetry Not Modeled — Buy Fees in Shares, Sell Fees in USDC

**Problem:** Weather markets charge 500 BPS (5%) taker fee. Formula: `fee = C × 0.05 × p × (1-p)`. Critically: fees on BUY orders are collected in shares (you receive fewer shares), fees on SELL orders are collected in USDC (you receive less USDC). This asymmetry means Zeus's P&L calculations will be wrong if they assume symmetric fee deduction.

**External evidence:**
- https://docs.polymarket.com/trading/fees — "fees are collected in shares on buy orders and USDC on sell orders." Weather/Other category = 500 BPS. Fees rounded to 5 decimal places.

**Zeus status:** The P12 reality gap doc (§2 Category A) already identifies this: "Fee is deducted in shares (buy) vs USDC (sell) — asymmetric P&L impact." Not yet implemented in position tracking.

**Severity:** Degrades P&L accuracy. Over many trades, cumulative fee tracking error grows.

---

## MINOR / HANDLED BY PY-CLOB-CLIENT (3)

### F2. Fee Rate BPS in Order Signing — AUTO-HANDLED

**Problem:** `feeRateBps` must be in the signed order payload.

**Verification:** py-clob-client's `create_order()` auto-resolves via `__resolve_fee_rate(token_id, fee_rate_bps)` which calls `get_fee_rate_bps()` API endpoint. Default `fee_rate_bps=0` in OrderArgs is resolved before signing.

**Zeus status:** **HANDLED by py-clob-client.** No action needed.

---

### F3. Tick Size Validation — AUTO-HANDLED

**Problem:** Order prices must conform to market tick size.

**Verification:** py-clob-client's `create_order()` auto-resolves via `__resolve_tick_size()` and validates with `price_valid()`. If Zeus's price doesn't conform, py-clob-client will raise an error locally (before API call).

**Zeus status:** **HANDLED by py-clob-client.** Zeus should catch the local validation error gracefully rather than crashing.

---

### F4. negRisk Flag — AUTO-HANDLED

**Problem:** Markets are standard or negative-risk, requiring different exchange contracts.

**Verification:** py-clob-client's `create_order()` auto-fetches via `get_neg_risk(token_id)` if not explicitly set.

**Zeus status:** **HANDLED by py-clob-client.** No action needed.

---

## STARTUP CHECKS (3)

### F5. USDC.e Allowance Pre-Approval

**Problem:** On-chain approvals (USDC.e for BUY, conditional tokens for SELL) must be set before trading. Multiple GitHub issues (py-clob-client #109, #264) confirm cryptic errors when allowances are missing or exhausted.

**What to verify:** Before first live trade, ensure max-uint256 approvals are set for:
- CTF exchange: `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E`
- Neg-risk exchange: `0xC5d563A36AE78145C45a50134d48A1215220f80a`
- Neg-risk adapter: `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296`

**Implementation:** Add startup check that verifies allowance > 0 for both USDC.e and conditional tokens. Alert if insufficient.

---

### F11. py-clob-client feeRateBps Default Behavior Verification

**Problem:** The official Polymarket/agents repo hardcodes `feeRateBps='1'` in its manual builder path. py-clob-client's standard `create_order()` path auto-fetches, but this should be verified in dry-run by inspecting the signed order payload.

**Dry-run verification:** Log the resolved `fee_rate_bps` value from py-clob-client before submitting. Verify it matches `GET /fee-rate?token_id=` response for weather markets (expected: 500 BPS).

---

### F12. Signature Type Verification

**Problem:** Zeus uses `signature_type=2` (proxy wallet). This must match the wallet setup. GitHub issues show `signature_type` mismatch as a common cause of "invalid signature" errors.

**What to verify:** Confirm that the Keychain-stored private key and funder address correspond to a proxy (Gnosis Safe) wallet with `signature_type=2`. If it's an EOA, change to `signature_type=0` (no funder needed).

---

## Rate Limits (Informational)

Polymarket uses Cloudflare throttling (delay, not reject) with sliding windows:
- Order placement: 3,500/10sec burst, 36,000/10min sustained
- Book data: 1,500/10sec
- Position data: 150/10sec

At Zeus's current scale (~1-3 orders per cycle), rate limits are not a concern. However, throttling can cause httpx timeouts (Zeus uses 15s timeout) if requests get queued.

---

## Structural Decision Map

The 12 findings map to 3 structural decisions from P12 §3:

| P12 Structural Decision | Findings | Status |
|---|---|---|
| **SD-2: Order lifecycle state machine** | F1 (heartbeat), F7 (fill detection), F8 (orphan cleanup), F6 (GTD) | NOT IMPLEMENTED — this is the critical path |
| **SD-1: Capital tracking** | F10 (fee asymmetry), F5 (allowance) | PARTIALLY — fee model exists but asymmetry not handled |
| **SD-3: Execution failure recovery** | F9 (precision), F3 (tick size), F12 (signature) | MOSTLY HANDLED by py-clob-client |
| **SD-4: Fee/slippage gap** | F2 (fee rate), F10 (fee asymmetry), F11 (fee verification) | MOSTLY HANDLED by py-clob-client |

**The critical path is SD-2.** Without heartbeat + fill detection + orphan cleanup, Zeus cannot place a live order that survives long enough to fill, cannot know when it fills, and cannot clean up if it crashes.

---

## Sources

- Polymarket Docs: https://docs.polymarket.com/trading/orders/create
- Polymarket Fees: https://docs.polymarket.com/trading/fees
- Polymarket CTF: https://docs.polymarket.com/developers/CTF/overview
- Polymarket Rate Limits: https://docs.polymarket.com/quickstart/introduction/rate-limits
- Polymarket WebSocket: https://docs.polymarket.com/developers/CLOB/websocket/wss-overview
- Polymarket Heartbeat: https://docs.polymarket.com/api-reference/trade/send-heartbeat
- Polymarket MM Guide: https://docs.polymarket.com/developers/market-makers/trading
- Polymarket Cancel: https://docs.polymarket.com/trading/orders/cancel
- py-clob-client source: https://github.com/Polymarket/py-clob-client
- py-clob-client issues: #79 (negRisk), #109 (balance), #121 (precision), #264 (small orders)
- Polymarket/agents: https://github.com/Polymarket/agents
- poly-maker: https://github.com/warproxxx/poly-maker

---

## Cross-Reference: Rainstorm Comparison Findings

p12-rainstorm performed a deep code comparison between Zeus and Rainstorm (the predecessor Polymarket bot). The following findings were verified with exact line numbers and cross-referenced against external evidence.

### CONFIRMED BLOCKERS (from Rainstorm comparison)

#### R1. PARTIALLY_FILLED Status Not Handled (BLOCKS LIVE — capital loss risk)

**Problem:** Zeus's `fill_tracker.py:24-25` defines `FILL_STATUSES={"FILLED","MATCHED"}` and `CANCEL_STATUSES={"CANCELLED","CANCELED","EXPIRED","REJECTED"}`. The status `PARTIALLY_FILLED` is in NEITHER set. A partial fill stays as "still_pending" forever, then times out and VOIDs the entire position — losing the filled capital.

**External cross-reference:** Polymarket docs confirm partial fills are possible. The changelog (https://docs.polymarket.com/changelog/changelog) introduced FAK order type specifically for partial fill capture. The CLOB returns `size_matched` field on order queries showing partial fill amount. WebSocket `trade` events track MATCHED → CONFIRMED lifecycle which includes partial matches.

**Rainstorm solution:** `execution_engine.py:84` handles partial fills as `PENDING_TRACKED` — acknowledges the filled portion while continuing to track the remainder.

**Severity:** BLOCKS LIVE — this is the most dangerous finding. Capital loss on every partial fill.

#### R3. No Process Lock — Double Daemon Risk (BLOCKS LIVE)

**Problem:** Zeus has only a `threading.Lock` in `main.py` for concurrent discovery modes. No process-level lock prevents launching two Zeus daemons simultaneously, which would double-order positions and corrupt state.

**Rainstorm solution:** `process_lock.py` implements `fcntl.flock()` with stale PID reclaim.

**External cross-reference:** This is a standard daemon management pattern. No Polymarket-specific evidence needed — it's a UNIX infrastructure concern.

**Severity:** BLOCKS LIVE — trivially triggered by accidental double-launch.

### CONFIRMED DEGRADES (from Rainstorm comparison)

#### R13. Graceful Shutdown

**Problem:** Zeus uses APScheduler's `BlockingScheduler` which calls `scheduler.shutdown()` on KeyboardInterrupt. If a cycle is mid-order-placement, shutdown is ungraceful.

**External cross-reference:** This connects to F1 (heartbeat). With heartbeat implemented, graceful shutdown becomes critical: the heartbeat thread must stop cleanly so the exchange's 15s auto-cancel kicks in, rather than leaving zombie heartbeat threads that keep stale orders alive.

**Rainstorm solution:** `main.py:72-79` registers SIGTERM/SIGINT handlers with `_shutdown = True` flag that lets the current cycle complete before exiting.

**Severity:** DEGRADES — ungraceful shutdown with heartbeat active could leave orders in undefined state.

### NOT A GAP (verified by Rainstorm comparison)

- **Fee model:** Zeus has a proper price-dependent fee model in `contracts/execution_price.py` (polymarket_fee with `p*(1-p)` formula). Actually better than Rainstorm's flat 0 BPS.
- **Chain reconciliation:** Both systems have it — Zeus via `chain_reconciliation.py` + `cycle_runtime.run_chain_sync()`.
- **API key rotation:** Not a gap — both use deterministic key derivation via py-clob-client's `create_or_derive_api_creds()`.

---

## Revised Structural Decision Map (Combined External + Rainstorm)

| P12 Structural Decision | External Findings | Rainstorm Findings | Status |
|---|---|---|---|
| **SD-2: Order lifecycle state machine** | F1 (heartbeat), F7 (fill detection), F8 (orphan cleanup), F6 (GTD) | R1 (partial fill), R13 (graceful shutdown) | **NOT IMPLEMENTED — critical path** |
| **SD-1: Capital tracking** | F10 (fee asymmetry), F5 (allowance) | — | PARTIALLY |
| **SD-3: Execution failure recovery** | F9 (precision) | R1 (min size retry) | PARTIALLY |
| **SD-4: Fee/slippage gap** | F2 (fee rate), F11 (fee verification) | Fee model verified correct | MOSTLY HANDLED |
| **Infrastructure** | — | R3 (process lock), R13 (shutdown) | NOT IMPLEMENTED |

## Implementation Priority (Combined)

Based on severity and dependency analysis:

1. **Process lock (R3)** — Trivial to implement, prevents catastrophic double-daemon. Do first.
2. **Heartbeat loop (F1)** — Without this, no order survives. Prerequisite for everything else.
3. **Partial fill handling (R1)** — Capital loss on every partial fill. Fix fill_tracker.py status sets.
4. **Fill detection (F7)** — WebSocket user channel or REST polling. Prerequisite for knowing order outcomes.
5. **Orphan cleanup (F8)** — Startup reconciliation. Cancel-all + position sync.
6. **GTD order type (F6)** — Replace GTC with GTD using Zeus's existing timeout values.
7. **Graceful shutdown (R13)** — SIGTERM handler + heartbeat thread cleanup.
8. **Allowance verification (F5)** — Startup check.
9. **Amount precision (F9)** — Verify in dry-run; may be handled by py-clob-client.
10. **Fee asymmetry tracking (F10)** — P&L accuracy improvement.
