# Polymarket CLOB V2 — Capability and System-Level Impact Report

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: web research (docs.polymarket.com + Polymarket GitHub V2 SDK repos, 2026-04-26 fetch) + Zeus codebase grep evidence (2026-04-26)

This report is the authority for Zeus's V2 migration packet. The execution plan in `plan.md` derives all slice scoping decisions from this analysis.

---

## 1. Executive verdict

**Polymarket CLOB V2 has shipped as a discrete, named product family.** It is not RFC, not rumor. Three official SDK packages (TypeScript, Python, Rust) hit `v1.0.0` GA on **2026-04-17**:

- `@polymarket/clob-client-v2` (TS, latest v1.0.2 on 2026-04-23)
- `py-clob-client-v2` (Python, GA v1.0.0 on 2026-04-17)
- `polymarket_client_sdk_v2` (Rust, V1/V2 auto-detect built in)

V2 host: `clob-v2.polymarket.com` (V1 stays on `clob.polymarket.com`).
EIP-712 domain version bumps from `"1"` → `"2"`.
Trading collateral migrates from USDC.e → **pUSD** (Polygon `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`).

**V1 is still being patched** (`py-clob-client v0.34.6` on 2026-04-19, two days after V2 GA). **No public V1 EOL date has been announced**. V1 and V2 run in parallel as of the cut date.

For Zeus the migration is real and structural, but not currently time-pressed.

---

## 2. V2 capability inventory

### 2.1 Launch and packaging

- V2 SDKs first GA tagged 2026-04-17 (TS + Python); pre-release stream began 2026-04-03 ([clob-client-v2 releases](https://github.com/Polymarket/clob-client-v2/releases), [py-clob-client-v2 releases](https://github.com/Polymarket/py-clob-client-v2/releases)).
- Quickstart docs install V2 by default: `pip install py-clob-client-v2`, `npm install @polymarket/clob-client-v2 ethers@5`, `cargo add polymarket-client-sdk --features clob` ([docs.polymarket.com/trading/quickstart](https://docs.polymarket.com/trading/quickstart)).
- Rust SDK exposes V1/V2 auto-detect via `GET /version`; SDK caches detected protocol per client lifetime.

### 2.2 API surface

| Aspect | V1 | V2 | Source |
|---|---|---|---|
| Host | `clob.polymarket.com` | `clob-v2.polymarket.com` | rs-clob-client-v2 README |
| EIP-712 domain version | `"1"` | `"2"` | rs-clob-client-v2 README |
| Order types | GTC, GTD, FOK, FAK | Same set | docs.polymarket.com/trading/orders/create |
| L1 auth | EIP-712 wallet sig (create/derive API key) | Same | docs.polymarket.com/trading/clients/l1 |
| L2 auth | HMAC-SHA256 (key/secret/passphrase) | Same | docs.polymarket.com/trading/clients/l1 |
| Batch order | up to 15 per request | Same | docs.polymarket.com/trading/orders/create |
| Builder code | HMAC-keyed only | Native order field (HMAC also still works) | docs.polymarket.com/trading/gasless |

### 2.3 Order schema diff

V2-only fields: `metadata`, `builder_code` (now native), `defer_exec`, `timestamp`.
V1-only fields removed in V2: `taker`, `nonce`, `fee_rate_bps`.

Tick size still 0.1/0.01/0.001/0.0001 with `INVALID_ORDER_MIN_TICK_SIZE` rejection; per-token via `getTickSize()`/`getNegRisk()`.

`post_only` flag for GTC/GTD orders only; rejected on FOK/FAK with `INVALID_POST_ONLY_ORDER_TYPE`. Added in `clob-client-v2 v0.2.6` and `py-clob-client-v2 v0.0.3` (2026-04-10).

No `reduce_only` flag documented.

### 2.4 Mandatory heartbeat (new and load-bearing)

Missing heartbeat for ~10s **cancels all open orders server-side**. Each request must carry the latest `heartbeat_id` (empty on first call). `getHeartbeat()` helper added in `clob-client-v2 v0.2.7` and `py-clob-client-v2 v0.0.4` (2026-04-16).

This is the largest behavioral delta — see §4.1 for Zeus-side architectural impact.

### 2.5 Order state machine extension

V2 statuses: `live`, `matched`, `delayed`, `unmatched`. The `delayed` / `ORDER_DELAYED` / `DELAYING_ORDER_ERROR` states are new transitional values between `live` and `matched|cancelled`. Server-side enforced: `maxOrderSize = balance − Σ(openOrderSize − filledAmount)` validated at submit.

### 2.6 WebSocket / streaming

Public market WS endpoint surfaced: `wss://ws-subscriptions-clob.polymarket.com/ws/market` (the URL is shared with V1 docs — V2-specific WS host not separately documented in any page reachable as of 2026-04-26).

Initial subscribe: `{type: "market", assets_ids: [...], custom_feature_enabled: true}`; dynamic `subscribe`/`unsubscribe` ops post-connect.

Event topics: `book`, `price_change`, `last_trade_price`, `tick_size_change`, `best_bid_ask`, `new_market`, `market_resolved`. Last three require `custom_feature_enabled: true`.

Authenticated WS (Rust SDK exposes): `subscribe_orderbook`, `subscribe_prices`, `subscribe_midpoints`, `subscribe_orders`, `subscribe_trades`.

**No sequence numbers, ping-pong cadence, or gap-fill semantics surfaced.** Only `hash` field on book events as a consistency primitive. This matters for Zeus if it adopts WS — there is no protocol-level ordering guarantee.

### 2.7 Custody model and collateral

- pUSD replaces USDC.e as trading collateral on V2. Polygon contract `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`. CTF: `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`.
- Signature types: `0 = EOA`, `1 = POLY_PROXY`, `2 = GNOSIS_SAFE`, `3 = EIP-1271` (Rust exposes; Python likely follows).
- Gasless host moved: `https://relayer-v2.polymarket.com/`.

### 2.8 Fees

Formula unchanged: `fee = C × feeRate × p × (1 − p)`.

Per-category rates (V2 docs as of 2026-04-26): Crypto 720 bps, Sports 300, Finance/Politics/Tech 400, Economics/**Weather**/Other 500, Geopolitics 0. Maker rebates 20–25% by category.

V2 removes `fee_rate_bps` from the order itself — fee is applied at match time and discoverable via `getClobMarketInfo(conditionID)`. V1 had `fee_rate_bps` as an order field that could be passed by caller.

### 2.9 Resolution oracle

Still UMA-based: `questionId` = hash of UMA ancillary data → `getConditionId(oracle, questionId, outcomeSlotCount)`.

No V2-specific dispute-window changes documented.

### 2.10 Neg-risk markets

Still binary YES/NO under CTF, routed through a different exchange contract. Conversion operation new in V2 era: a NO token in one outcome can be exchanged for YES tokens in the other outcomes (CTF NO-set primitive).

`get_neg_risk(token_id)` SDK helper retained — Zeus's antibody test at `tests/test_neg_risk_passthrough.py:66-83` will need a V2-equivalent.

### 2.11 Risk / surveillance

Heartbeat-driven kill-switch (~10s missed → cancel all open orders) is the explicit V2-era circuit breaker.

Geoblock endpoint exists (`/api-reference/geoblock`).

KYC tiers, rate limits, and IP geofencing for V2 not documented in any reachable page.

---

## 3. Three paradigm shifts (K << N reframing)

V2 surfaces ~12 BREAKING differences. They compress to **3 unbreakable paradigm decisions**:

| Paradigm | V1 | V2 | Maps to BREAKING items |
|---|---|---|---|
| **A. Transport** | request/response, no liveness contract | persistent session + 10s mandatory heartbeat | host change, `getHeartbeat()`, mass-cancel-on-miss |
| **B. Collateral** | USDC.e (account unit) | pUSD (purpose-built unit) | balance ABI, redemption ABI, gasless relayer host |
| **C. Order state machine** | live → matched/cancelled (2 terminal) | live → delayed → matched/cancelled (3 terminal + server-side cancel) | `delayed` status, OrderArgs schema diff, fee removed from order, server-side max-order-size |

**Operating consequence**: Zeus needs 3 structural decisions, not 12 patches. This shapes the slice structure in `plan.md`.

---

## 4. Zeus architecture mismatches (deep)

### 4.1 The three-layer heartbeat collision

Zeus already has **two layers of heartbeat / failure-closure** machinery:

| Layer | File evidence | Scope | Failure action |
|---|---|---|---|
| Daemon liveness | `state/daemon-heartbeat.json`, `scripts/check_daemon_heartbeat.py:30`, `scripts/deep_heartbeat.py:14` (Layer 1 diagnostics) | process-level | operator alarm, no automatic action |
| Tombstone fail-closure | `state/auto_pause_failclosed.tombstone`, `scripts/verify_truth_surfaces.py:38,1947-1992` (check `p4.4_8.auto_pause_tombstone_absent`) | application-level | reject new writes, leave open orders alone |
| **V2 protocol heartbeat (NEW)** | `getHeartbeat()` + `heartbeat_id` round-trip | protocol-level | **server-side cancel all open orders** |

The V2 layer **is not redundant** with the existing two — it is a third independent layer with a side-effect (order cancellation) the existing two never had. Required new couplings:

1. Heartbeat coroutine death must **automatically write the tombstone**. Otherwise Zeus does not know its open orders were silently cleared, and the next cycle's "orphan open-order cleanup" path (`src/engine/cycle_runner.py:287`) will find phantom orders.
2. The heartbeat coroutine has to run **across cycles**, but Zeus's current daemon is `_cycle_lock = threading.Lock()` (`src/main.py:30`) — a strict serial model. Adding a cross-cycle coroutine **introduces concurrency to the daemon for the first time**. The lock semantics must be redesigned.

This is not "add a new coroutine." It is "open a concurrency seam in the daemon supervisor."

### 4.2 REST polling vs push: a hidden strategy assumption

Evidence:

- `src/main.py:22,84` — `run_cycle(mode)` is scheduler-driven, fully synchronous.
- `src/engine/cycle_runner.py:194` — `def run_cycle(mode) → dict` returns a cycle summary.
- `src/engine/monitor_refresh.py:1-4` — "Blueprint v2 §7 Layer 1: Recompute probability for held positions. Uses full p_raw_vector with MC instrument noise." — full per-cycle recompute, not reactive.
- `grep` returns no `asyncio` import in the engine path, no WebSocket library, no event-driven primitive.

V2 offers WS topics: `book / price_change / last_trade_price / tick_size_change / best_bid_ask / new_market / market_resolved / subscribe_orders / subscribe_trades`.

The strategic question: **Zeus's monitor_refresh cycle is not just a technical choice, it is a strategy choice.**

- The day0 router decides "boundary ambiguous" based on the vwmp it sees at cycle boundaries (`src/strategy/market_fusion.py::vwmp`).
- Cycle interval implicitly assumes "short-term price jitter is absorbed within a cycle."
- A reactive (WS-driven) re-evaluation triggers `recompute_native_probability` on every `price_change`, making the decision surface continuous rather than discrete. The day0 boundary-ambiguous evaluation could see a different signal trace.

V2 does not require Zeus to abandon REST polling. But adopting WS would **change strategy semantics**. Therefore:

- V2 migration scope: REST polling preserved by default.
- WS adoption: tracked as a separate strategic slice (`Phase 2 / Strategic`), gated on critic-opus review of strategy-layer impact.

### 4.3 fill_tracker: 2-state → 3-state machine

Evidence `src/execution/fill_tracker.py`:

- `:24-25` — `FILL_STATUSES = {"FILLED", "MATCHED"}`, `CANCEL_STATUSES = {"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"}`
- `:329` — `payload = clob.get_order_status(pos.entry_order_id)` synchronous REST poll
- `:390-398` — `_normalize_status` accepts three field names: `status / state / orderStatus`

V2 adds `delayed` / `ORDER_DELAYED`:

- Falls into neither set → fall-through to "still waiting" branch
- Current logic does not crash, but **semantically misclassifies**: a `delayed → cancelled` transition that the client misses leaves Zeus believing the order is still live → `_mark_entry_voided` never fires → capital perpetually locked

This is a **latent capital-leak risk**, not a visible bug. Mitigation: add a third class `TRANSITIONAL_STATUSES = {"DELAYED", "ORDER_DELAYED"}` plus a wall-clock timeout that escalates to tombstone on prolonged delay.

### 4.4 The fee-rate two-layer call stack

Evidence:

- `src/data/polymarket_client.py:116-128` — direct `httpx.get(f"{CLOB_BASE}/fee-rate", ...)` returning `feeSchedule.feeRate`
- `src/contracts/execution_price.py:130` — `polymarket_fee(price, fee_rate=0.05)` formula `fee_rate × p × (1-p)`
- `src/config.py:392` — T6.4 `fee_rate parameter` for `polymarket_fee()`

V2 changes:

- `fee_rate_bps` is removed from order schema — fee applied at match time.
- Fee retrieved via `getClobMarketInfo(conditionID)` (likely SDK-wrapped).
- **Formula unchanged**.

Zeus impact:

- `polymarket_client.get_fee_rate` direct httpx call → **likely deletable** if V2 SDK wraps `getClobMarketInfo` (must confirm against SDK source in Phase 0).
- `polymarket_fee()` formula → **kept**. Local EV estimation, Kelly sizing, `TickSize.clamp_to_valid_range` all depend on it.
- T6.4 fee_rate config (`src/config.py:392`) → **kept**, repurposed from "field on outgoing order" to "local EV input."

### 4.5 USDC.e → pUSD: collateral switch reaches far past code

This is the deepest system shock and the easiest to undercount.

**Code-level (shallow)**:

- `src/data/polymarket_client.py:266-275` — USDC balance query + redemption path
- `src/main.py:379` — `Startup wallet check: $%.2f USDC available` log copy
- `src/execution/harvester.py:1244-1264` — T2-G redemption (claim winning USDC on-chain, comment "USDC still claimable later")

**Beyond code**:

a. **PnL accounting**. USDC.e ↔ pUSD conversion is unlikely to be exactly 1:1 (bridge fees, spread). Zeus's "total PnL" metric will gain a structural FX component. Decision needed: classify FX as trading PnL or carry cost? Affects all downstream reports.

b. **Funding dynamics**. The Gnosis Safe must hold pUSD, not USDC.e. **How is pUSD obtained? No public document explains the bridge path.** Operator knowledge gap.

c. **Redemption cutover**. `polymarket_client.py:275` notes "USDC stays claimable indefinitely." V2-side: pUSD is also claimable indefinitely, but the redemption ABI differs. **Settled-but-unredeemed V1 positions do not auto-migrate.** Cutover prerequisite: redeem all V1 positions first.

d. **External / audit recognition**. Whether pUSD is recognized as USDC-equivalent by external accounting / tax / audit consumers is outside Zeus's control. Operator decision.

**Conclusion**: pUSD is the **true blocker** of V2 cutover. Not technical, but operations + accounting. **The bridge path must be explicit before any cutover commitment.**

---

## 5. Functions deletable on V2 migration

Sorted by certainty:

| Function | Deletable? | Evidence and rationale |
|---|---|---|
| Explicit `nonce` / `taker` / `fee_rate_bps` fields in OrderArgs construction | **YES** | V2 schema removes them. Current `polymarket_client.py:155` does not pass them explicitly — the SDK supplies V1 defaults. After V2 SDK swap they vanish. |
| `polymarket_client.get_fee_rate` direct httpx call (`:116-128`) | **LIKELY YES** | V2 retrieves fee via `getClobMarketInfo`. Verify in Phase 0. |
| `polymarket_client.get_orderbook` direct httpx `/book` (`:81`) | **MAYBE** | V2 SDK may provide an equivalent method. Verify in Phase 0. |
| Manual EIP-712 signing code | **N/A** | grep confirms Zeus has none — SDK handles it. Domain-version bump rides on package swap. |

**Functions that remain valuable post-V2 (do not delete)**:

| Function | Why it stays |
|---|---|
| `tests/test_neg_risk_passthrough.py` SDK contract antibody | Repurpose for V2 SDK; this antibody design is the early-warning signal for SDK-renames and **must** be replicated, not removed |
| `RealizedFill / SlippageBps / TickSize / ExecutionPrice` typed contracts (`src/contracts/`) | Zeus's internal abstractions, protocol-version-independent. T5.a-d investment is fully retained. |
| `polymarket_fee()` local formula | Unchanged formula; EV / Kelly / TickSize.clamp all depend on it |
| `auto_pause_failclosed.tombstone` mechanism | Operator-level kill-switch; V2 heartbeat is server-level, the two are complementary |
| `_cycle_lock` serial gate | Serial is safer; V2 does not require concurrency |
| `monitor_refresh` periodic recompute | Strategy-layer cycle; protocol-version-independent |
| `fill_tracker._normalize_status` three-field tolerance | V2 schema may still use `status` or `state`; defensive normalization stays |
| Daemon heartbeat (`daemon-heartbeat.json`) + tombstone | Complementary to V2 protocol heartbeat, not redundant |

**Conclusion**: Deletable code is small (OrderArgs fields + 2-3 direct httpx calls). The contracts / heartbeat / lock / cycle architecture are **assets after V2**. **V2 is a wrapping-layer reinforcement, not a rewrite opportunity.**

---

## 6. New functionality required (mandatory + recommended + strategic + antibody)

### 6.1 Mandatory (cannot run V2 without these)

| ID | Function | Closest existing code | Estimated effort |
|---|---|---|---|
| M1 | Heartbeat coroutine (≤10s tick `getHeartbeat()`, failure → write tombstone) | `scripts/deep_heartbeat.py` (diagnostic, not a driver) | 2-3 days incl. concurrency redesign |
| M2 | `delayed` status branch in fill_tracker state machine | `src/execution/fill_tracker.py:24-25,329-358` | 1 day + antibody test |
| M3 | pUSD balance + redemption path | `polymarket_client.py:266-275`, `harvester.py:1244-1264` | 1 day code + N days operator |
| M4 | V2 SDK installation + import swap | `requirements.txt:14`, `polymarket_client.py:60,148,200,268` | 0.5 day |
| M5 | OrderArgs V2 schema (`metadata`, `builder_code`, `defer_exec`, `timestamp`) | `polymarket_client.py:155` | 0.5 day |

### 6.2 Recommended (best practice, optional but cheap)

| ID | Function | Value |
|---|---|---|
| R1 | `/version` probe at startup | Confirms protocol version, prevents misconnect to V1 host |
| R2 | `getClobMarketInfo` cache (fee_rate + tick_size + neg_risk in one call) | Reduces N×REST calls to 1 |
| R3 | EIP-712 v2 domain self-test signature at startup | Verifies SDK installation correctness |
| R4 | Builder code native field migration (HMAC → `OrderArgs.builder_code`) | Required if Zeus joins builder fee-share program |

### 6.3 Strategic (V2 capabilities Zeus could elect to use)

| ID | Function | Benefit | Risk |
|---|---|---|---|
| S1 | `post_only` flag for maker-only entry | Avoid taker fee (5% × p × (1-p) ≈ 1.25% at p=0.5) | Weather market liquidity is thin; maker-only may never fill |
| S2 | Batch order endpoint (15 per request) | Day0 multi-market scan acceleration | Partial-failure rollback semantics complex |
| S3 | WS `book / price_change / best_bid_ask` | Reactive monitor_refresh, lower latency | Architecture change; strategic decision separate from migration |
| S4 | WS `subscribe_orders / subscribe_trades` | fill_tracker becomes reactive, frees cycle time | Same as S3 |
| S5 | `delayed` duration in telemetry | Detect matching-engine performance regressions | Low |

### 6.4 System-level antibody tests

| ID | Antibody | Purpose |
|---|---|---|
| A1 | Cross-protocol contract test (same OrderArgs signed by V1 + V2 SDK simultaneously, verify both signatures valid) | Detect protocol drift at SDK boundary |
| A2 | Heartbeat-failure injection test (kill heartbeat coroutine, verify tombstone written + subsequent submit fail-closed) | Validate the new failure category is structurally handled |
| A3 | pUSD ↔ USDC.e boundary test (V1 path balance is USDC.e, V2 path balance is pUSD, no cross-contamination) | Prevent wallet check from misreporting collateral across protocol |

**Total new functionality**: ~17 items. Mandatory 5, recommended 4, strategic 5, antibody 3. Mandatory effort ≈ 5 working days (excluding pUSD operator-side, which is unbounded until the bridge path is known).

---

## 7. Net architectural picture after V2

```
                        Zeus Daemon (post-V2)

  +--------------------+    +-----------------------+
  | Scheduler          |--->| run_cycle(mode)       |  <- existing
  +--------------------+    +-----------------------+
              |                           |
              |                           v
              |              +-----------------------+
              |              | monitor_refresh       |  <- existing
              |              | exit_lifecycle        |
              |              | fill_tracker (3-state)|  <- M2
              |              +-----------------------+
              |                           |
              v                           v
  +----------------------+    +-----------------------+
  | Heartbeat coroutine  |    | clob_protocol         |  <- new (Phase 1)
  | (≤10s tick)          |    | typed atom (v1|v2)    |
  | M1                   |    +-----------------------+
  +----------------------+              |
              |                          v
              |              +-----------------------+
              v              | py_clob_client        |  <- M4
  +----------------------+   |   OR py_clob_client_v2|
  | tombstone on failure |   +-----------------------+
  +----------------------+              |
                                        v
                              +---------------------+
                              | V1 host  OR  V2 host|
                              +---------------------+
```

The diagram is deliberately schematic — exact module boundaries are decided in `plan.md`. Key insights:

- The new heartbeat coroutine is the only concurrent component.
- All protocol-version-conditional behavior funnels through one typed atom (`clob_protocol`), not through scattered `if/else`.
- fill_tracker stays in the existing synchronous cycle path; only its state-set widens.

---

## 8. Sources

V2 capability research (2026-04-26 fetch):

- [docs.polymarket.com](https://docs.polymarket.com)
- [docs.polymarket.com/trading/quickstart](https://docs.polymarket.com/trading/quickstart)
- [docs.polymarket.com/trading/orders/create](https://docs.polymarket.com/trading/orders/create)
- [docs.polymarket.com/trading/orderbook](https://docs.polymarket.com/trading/orderbook)
- [docs.polymarket.com/trading/clients/l1](https://docs.polymarket.com/trading/clients/l1)
- [docs.polymarket.com/trading/overview](https://docs.polymarket.com/trading/overview)
- [docs.polymarket.com/trading/fees](https://docs.polymarket.com/trading/fees)
- [docs.polymarket.com/trading/gasless](https://docs.polymarket.com/trading/gasless)
- [docs.polymarket.com/trading/ctf/overview](https://docs.polymarket.com/trading/ctf/overview)
- [github.com/Polymarket/clob-client-v2](https://github.com/Polymarket/clob-client-v2)
- [github.com/Polymarket/clob-client-v2/releases](https://github.com/Polymarket/clob-client-v2/releases)
- [github.com/Polymarket/py-clob-client-v2](https://github.com/Polymarket/py-clob-client-v2)
- [github.com/Polymarket/py-clob-client-v2/releases](https://github.com/Polymarket/py-clob-client-v2/releases)
- [github.com/Polymarket/rs-clob-client-v2](https://github.com/Polymarket/rs-clob-client-v2)
- [github.com/Polymarket/py-clob-client](https://github.com/Polymarket/py-clob-client) (V1, comparison)
- [pypi.org/project/py-clob-client/](https://pypi.org/project/py-clob-client/)

Zeus codebase evidence (grep-verified 2026-04-26):

- `src/data/polymarket_client.py:17,18,60-69,81,118,148-161,167,200,266-275`
- `src/main.py:22,30,84,379`
- `src/engine/cycle_runner.py:194,287`
- `src/engine/monitor_refresh.py:1-4`
- `src/execution/fill_tracker.py:24-25,329,390-398`
- `src/execution/harvester.py:1244-1264`
- `src/contracts/tick_size.py:110`, `execution_price.py:130`
- `src/config.py:392`
- `tests/test_neg_risk_passthrough.py:66-83`, `test_polymarket_error_matrix.py:39`
- `architecture/source_rationale.yaml:606-608`, `test_topology.yaml:134`
- `requirements.txt:14`
- `state/auto_pause_failclosed.tombstone` (existing)
- `state/daemon-heartbeat.json` (existing)
- `scripts/check_daemon_heartbeat.py:30`, `deep_heartbeat.py:14`
