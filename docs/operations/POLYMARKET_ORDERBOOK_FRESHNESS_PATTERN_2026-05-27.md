# Polymarket Orderbook Freshness Pattern — Research 2026-05-27

**Purpose:** Inform Zeus design fix for per-cycle budget exhaustion when fetching 11-bin negRisk orderbooks.

---

## 1. Batch Orderbook Endpoint

**EXISTS: YES**

- **Endpoint:** `POST https://clob.polymarket.com/books`
- **Request body:** JSON array of objects `[{"token_id": "<asset_id>", "side": "BUY|SELL (optional)"}, ...]`
- **Response:** Array of full orderbook summaries (bids, asks, tick_size, neg_risk flag, last_trade_price, market condition_id, timestamp, hash)
- **No documented per-call token limit** in the API reference
- **Rate limit:** 500 req / 10s (vs 1,500 req / 10s for the singular `/book`)
- **Python client:** `client.get_order_books([BookParams(token_id=t) for t in token_ids])`
- **TS client:** `GET_ORDER_BOOKS = "/books"` constant in `src/endpoints.ts`

**Sources:**
- https://docs.polymarket.com/api-reference/market-data/get-order-books-request-body
- https://github.com/Polymarket/py-clob-client (README, `get_order_books` example)
- https://github.com/Polymarket/clob-client/blob/main/src/endpoints.ts (archived 2026-05-25)

---

## 2. WebSocket Market Channel

**EXISTS: YES**

- **WSS URL:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- **Subscription message:**
  ```json
  {
    "assets_ids": ["TOKEN_ID_1", "TOKEN_ID_2", "...up to N tokens"],
    "type": "market",
    "initial_dump": true,
    "level": 2
  }
  ```
- **Dynamic re-subscription:** Send `{"operation": "subscribe"|"unsubscribe", "assets_ids": [...]}` on the live socket without reconnecting
- **Delivered events:**
  - `book` — full orderbook snapshot
  - `price_change` — incremental delta (individual level updates)
  - `last_trade_price` — trade execution notification
  - `tick_size_change` — tick size update
  - `best_bid_ask` — top-of-book (requires `custom_feature_enabled: true`)
  - `market_resolved` — settlement outcome (requires `custom_feature_enabled: true`)
- **`initial_dump: true`** triggers a full book snapshot immediately on connect for each subscribed token
- **No documented connection-level rate limit** separate from REST limits

**Source:** https://docs.polymarket.com/api-reference/wss/market

---

## 3. negRisk Event Identity Stability and Single-Call Fetch

**Confirmed: condition_ids and token_ids are STABLE for the event's lifetime.**

The Gamma API `GET https://gamma-api.polymarket.com/events/slug/{slug}` returns a single response that includes:
- Event-level fields: `negRisk: true`, `negRiskMarketID`, `enableNegRisk`
- Nested `markets[]` array: every sub-market with its `conditionId`, `clobTokenIds`, `acceptingOrders`, `closed`, `active` per sub-market
- `clobTokenIds` on each sub-market object gives the CLOB token_id(s) needed for orderbook fetches

**Identity invariant (inferred from protocol design):** negRisk condition_ids are set at market creation by the UMA/CTF contract and are immutable on-chain. They do not change while the event is open. `acceptingOrders` and `closed` flags may change (e.g., a bin stops accepting orders near expiry), but the identity fields (conditionId, clobTokenIds) are constant.

**One-call event fetch:** `GET https://gamma-api.polymarket.com/events/slug/{slug}` delivers the full sub-market roster in a single HTTP call. This is the canonical "bootstrap" call to seed the token_id cache.

**Source:** https://docs.polymarket.com/api-reference/events/get-event-by-slug

---

## 4. Polymarket's Recommended Automated-Trading Freshness Architecture

Polymarket does not publish an explicit "recommended architecture" guide for multi-outcome market-making cadence. However, the API design itself expresses intent clearly:

- **Batch REST `/books`** is the intended polling primitive when you need a full cross-bin snapshot at a point in time (e.g., at cycle start).
- **WebSocket market channel** is the intended push primitive for continuous freshness — subscribe once per token set, receive incremental `price_change` deltas and periodic `book` snapshots without polling.
- **`initial_dump: true`** on WS connect bootstraps the in-memory book state, eliminating the need for an initial REST call.
- The existence of both `GET_ORDER_BOOKS` (batch) and the WS channel as first-class SDK exports indicates the intended pattern is: **WS for continuous operation, batch REST for catch-up / reconnect scenarios**.

The py-clob-client README (https://github.com/Polymarket/py-clob-client) highlights `get_order_books` under "Find markets, prices, and orderbooks" as the primary discovery pattern, consistent with a "scan once, then maintain via WS" architecture.

**No official blog post or architecture guide was found that explicitly documents cadence recommendations.** The above is inferred from API design.

---

## 5. Rate Limits: REST vs WebSocket

**REST CLOB API (https://clob.polymarket.com):**

| Endpoint | Limit |
|---|---|
| `GET /book` (singular) | 1,500 req / 10s |
| `POST /books` (batch) | 500 req / 10s |
| `GET /price`, `/midpoint` | 1,500 req / 10s |
| `POST /prices`, `/midpoints` | 500 req / 10s |
| General (all endpoints combined) | 9,000 req / 10s |
| `POST /order` | 5,000 req / 10s burst; 48,000 req / 10 min sustained |

**WebSocket:** No documented connection-level rate limit. WS is push-based and bypasses per-request REST limits entirely. Standard practice for WS endpoints is a connection-count limit rather than message-rate limit, but Polymarket does not publish connection limits.

**Implication for 11-bin event:** 11 sequential `GET /book` calls = 11 requests counting against 1,500/10s. One `POST /books` with all 11 = 1 request against 500/10s. WS subscription = 0 REST requests for continuous updates.

**Source:** https://docs.polymarket.com/api-reference/rate-limits

---

## 6. Recommendation for Zeus

### Which mechanism best fixes the problem

**Primary fix: WebSocket market channel** (`wss://ws-subscriptions-clob.polymarket.com/ws/market`)

**Rationale:** Zeus's core problem is per-cycle budget exhaustion from sequential per-bin REST fetches. The WS channel eliminates per-cycle fetching entirely — subscribe once to all 11 token_ids for an event (with `initial_dump: true`), receive push updates for all bins, and maintain an in-memory `{token_id → OrderBook}` cache that `capture_executable_market_snapshot` reads without any per-cycle HTTP cost.

**Secondary / fallback: Batch REST `POST /books`**

If WS is unavailable or reconnect is needed, one `POST /books` with all 11 token_ids fetches all bins in a single round-trip (1 request vs 11). This is also the correct catch-up mechanism on WS reconnect: call `/books` once to resync all bins, then re-subscribe to the WS.

### Zeus mapping sketch

1. **Bootstrap (once per event or on reconnect):** `GET https://gamma-api.polymarket.com/events/slug/{slug}` → extract `markets[].clobTokenIds` → persist as the event's static token_id roster (stable for event lifetime, no re-discovery needed per cycle).

2. **Continuous freshness (per session):** Open one WS connection to `wss://ws-subscriptions-clob.polymarket.com/ws/market`, subscribe with all 11 token_ids and `initial_dump: true`. Route incoming `book` and `price_change` events to an in-memory `{token_id → OrderBook}` dict maintained by a background coroutine.

3. **`capture_executable_market_snapshot` change:** Instead of issuing HTTP requests, read from the in-memory cache. Staleness check: compare each bin's `timestamp` field against `now - max_staleness_threshold`; bins stale beyond threshold are flagged but do NOT block the other bins from being evaluated (per-bin staleness should not abort the whole event).

4. **Entry evaluator change:** Accept partial orderbook coverage (bins that are stale or temporarily offline) rather than requiring 11/11 fresh bins. The negRisk constraint (sum-of-probs ≈ 1) can still be checked on available bins.

---

## Source Index

| Claim | Source URL |
|---|---|
| `POST /books` endpoint exists, request/response schema | https://docs.polymarket.com/api-reference/market-data/get-order-books-request-body |
| `/books` rate limit 500 req/10s | https://docs.polymarket.com/api-reference/rate-limits |
| `get_order_books` Python client method | https://github.com/Polymarket/py-clob-client |
| `GET_ORDER_BOOKS = "/books"` TS constant | https://github.com/Polymarket/clob-client/blob/main/src/endpoints.ts |
| WS market channel URL, subscription format, event types | https://docs.polymarket.com/api-reference/wss/market |
| Gamma event-by-slug with nested markets[] and clobTokenIds | https://docs.polymarket.com/api-reference/events/get-event-by-slug |
| negRisk flag at event and market level | https://docs.polymarket.com/api-reference/events/get-event-by-slug |
| All REST rate limits table | https://docs.polymarket.com/api-reference/rate-limits |

**Note on TS clob-client:** `github.com/Polymarket/clob-client` was archived on 2026-05-25. Its endpoint constants are valid historical reference; the active Python SDK (`py-clob-client`) and the new unified SDKs at https://docs.polymarket.com/dev-tooling are the forward-looking implementations.

---

## 7. WS Market Channel Integration Plan (deferred follow-up to PR #64 batch-orderbook)

**Status:** Plan only. Implementation deferred to a dedicated PR after PR #64 ships and is validated live.

**Problem this solves:** After PR #64, calls/event for an 11-bin negRisk event are ~23 sequential HTTP calls
(1 batch POST /books + 11×market_info + 11×fee = 23). At ~1.4s/call, that is ~23s best-case but the
per-outcome CLOB calls are still sequential, so any latency spike causes budget exhaustion. The `/clob-markets/<cid>`
and `/fee-rate?token_id=` endpoints have no batch equivalents (confirmed: `POST /clob-markets` → 405,
`POST /fee-rate` → 405). WS eliminates per-cycle HTTP overhead for the orderbook leg entirely and is
the only structurally clean path to closing EDGE_INSUFFICIENT permanently.

### 7.1 Call topology before and after

| Phase | Before PR #64 | After PR #64 | After WS plan |
|---|---|---|---|
| Orderbook leg | 11 × GET /book (seq) | 1 × POST /books | 0 HTTP (WS push) |
| market_info leg | 11 × GET /clob-markets/<cid> (seq) | 11 × GET /clob-markets/<cid> (seq) | 11 × GET /clob-markets/<cid> (seq, no change yet) |
| fee leg | 11 × GET /fee-rate (seq) | 11 × GET /fee-rate (seq) | 11 × GET /fee-rate (seq, no change yet) |
| **Total** | **33** | **23** | **22** (market_info+fee still sequential) |

Note: the WS plan closes the orderbook HTTP leg. The market_info + fee legs remain per-outcome HTTP for now (see §7.6 for the fee-semantics investigation gate).

### 7.2 WS subscription topology

**Component: `ZeusOrderbookSubscriber`** (new, in `src/data/ws_orderbook.py`)

- Maintains a single `websockets` (or `websocket-client`) connection to `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- On connect: sends `{"type": "market", "assets_ids": [all_known_token_ids], "initial_dump": true}`
- Background thread (or asyncio task if scheduler migrates to async) drains the WS message queue; writes incoming `book` and `price_change` events to `_cache: dict[str, CachedBook]`
- `CachedBook` = `{"raw": dict, "received_at": float}` (monotonic timestamp at write)
- Dynamic re-subscription: exposes `subscribe(token_ids)` / `unsubscribe(token_ids)` that sends `{"operation": "subscribe|unsubscribe", "assets_ids": [...]}` without reconnecting
- Thread-safety: `_cache` protected by a `threading.RLock` (reads/writes brief; no contention expected at Zeus's cadence)

**Plug-in point:** `PolymarketClient` (`src/data/polymarket_client.py:240`) acquires an instance of `ZeusOrderbookSubscriber` on init when `ZEUS_WS_ORDERBOOK=1` env flag is set. Zeus is currently sync-scheduler-driven; the subscriber runs its WS reader in a daemon thread.

### 7.3 Freshness / staleness contract

Every `CachedBook` entry carries `received_at` (monotonic). Before using the cached book in `capture_executable_market_snapshot`, the following guard applies:

```python
MAX_WS_BOOK_STALENESS_SECONDS = float(os.environ.get("ZEUS_WS_BOOK_MAX_STALE_S", "10"))

def _book_from_ws_cache(cache: ZeusOrderbookSubscriber, token_id: str) -> dict | None:
    entry = cache.get(token_id)  # returns CachedBook | None
    if entry is None:
        return None  # not yet seen; fall back to HTTP
    age = time.monotonic() - entry["received_at"]
    if age > MAX_WS_BOOK_STALENESS_SECONDS:
        return None  # stale; fall back to HTTP for this bin
    return entry["raw"]
```

Fail-closed: a None return → `capture_executable_market_snapshot` falls back to the existing per-token `_fetch_orderbook_snapshot` path (the same fail-safe as the per-bin-skip in `_prefetch_selected_orderbooks`).

**WS disconnect / cache freeze:** When the WS reader thread detects a disconnect (`websockets.ConnectionClosed` or equivalent), it sets a `_frozen_at` timestamp. All `get()` calls then subtract `(now - _frozen_at)` from the apparent age of every entry — effectively aging the cache forward from disconnect time. Once `MAX_WS_BOOK_STALENESS_SECONDS` has elapsed since disconnect, ALL entries return None and all bins fall back to HTTP. No silent stale data.

### 7.4 How it plugs into `capture_executable_market_snapshot`

The existing additive `prefetched_orderbook: dict | None = None` parameter (added in PR #64,
`src/data/market_scanner.py:2508`) already carries the right contract: when non-None, skip the per-token
`_fetch_orderbook_snapshot`. The WS plan would add a second additive parameter
`prefetched_market_info: dict | None = None` with the same pattern for the market_info leg (deferred,
pending §7.6 gate).

In `refresh_executable_market_substrate_snapshots` (`src/data/market_scanner.py:3314`), the
`_prefetch_selected_orderbooks` call (added in PR #64, `src/data/market_scanner.py:3434`) would be
replaced with a cache read when the WS subscriber is active:

```python
# Pseudocode — actual impl in follow-up PR
if ws_subscriber is not None:
    prefetched_books = {
        _selected_token_for_direction(outcome, direction): book
        for book in [_book_from_ws_cache(ws_subscriber, _selected_token_for_direction(outcome, direction))]
        if book is not None
        # absent tokens fall back per-bin via prefetched_orderbook=None path
    }
else:
    prefetched_books = _prefetch_selected_orderbooks(clob, selected_candidates)
```

This keeps `_prefetch_selected_orderbooks` as the HTTP fallback, so the system degrades gracefully when WS is disabled or reconnecting.

### 7.5 Failure modes and mitigations

| Failure | Detection | Mitigation |
|---|---|---|
| WS disconnect | `ConnectionClosed` in reader thread | Set `_frozen_at`; entries age out in ≤`MAX_WS_BOOK_STALENESS_SECONDS`s; all bins fall back to HTTP |
| WS message parse error | `json.JSONDecodeError` or missing `asset_id` | Skip entry; log warning; do not update cache; next cycle falls back to HTTP for that bin |
| WS book for wrong token_id | `asset_id` not in subscription set | Ignore (cache write is keyed by `asset_id`; spurious entries are harmless) |
| WS never delivers `book` event for a token (new subscription) | `CachedBook` absent from cache | `get()` returns None → HTTP fallback |
| Cache grows unbounded | Token sets churn across events | `unsubscribe` + `del cache[token_id]` on token roster change; `ZeusOrderbookSubscriber` should cap to last N×2 known tokens |

### 7.6 Fee-semantics investigation gate (NOT part of this plan, flagged for follow-up)

During the PR #64 investigation, the CLOB `/clob-markets/<cid>` response was found to contain a `fd`
(fee details) sub-object: `{"r": 0.05, "e": 1, "to": true}`. However, for the same market's `yes_token`,
`GET /fee-rate?token_id=<yes_token>` returns `{"base_fee": 1000}` (bps). These are numerically different
(5% fraction ≠ 10% bps), suggesting they measure different fee components (LP/maker schedule vs taker
base fee). The `fd.r` field is not in `canonicalize_fee_details`'s recognized field list
(`FEE_RATE_FRACTION_FIELDS` / `FEE_RATE_BPS_FIELDS` in `src/contracts/executable_market_snapshot.py:53`).

**If Polymarket's protocol documentation confirms that `fd.r` is the same semantic quantity as
`/fee-rate.base_fee` (just expressed as fraction vs bps), the per-outcome fee call can be eliminated:
extract from `raw_clob_market["fd"]` in `capture_executable_market_snapshot` and canonicalize via a
mapping `r` → `fee_rate_fraction`. This reduces calls from 23 to 12 per 11-bin event.**

**This MUST NOT be implemented before the semantic equivalence is confirmed from upstream docs/protocol
specification, not numerical inference.** Data-provenance failure mode: substituting one fee metric for
another silently corrupts snapshot `fee_details` across all live trades.
