# P12 — Live Execution Reality Gap Audit

Version: 2026-04-06
Status: **OPEN — pre-live gate**
Authority: docs/zeus_FINAL_spec.md §Endgame, AGENTS.md §2

---

## §0 Why P12 exists

P9 solved epistemic fragmentation (cross-layer semantic collapse).
P10 solved external reality drift (fee formula, tick size, settlement sources).
P11 defined the Venus boundary.

**None of them addressed the paper→live execution gap.**

Zeus has run 107 paper trades. Paper mode:
- Simulates fills instantly (`paper_fill()`)
- Never signs EIP-712 orders
- Never queries real USDC balance
- Never manages real order lifecycle (pending → partial → filled → cancelled)
- Never handles CLOB rejection, timeout, or network failure
- Never deals with CTF token custody (conditional tokens on-chain)
- Never competes with other market participants for liquidity

Every one of these is an assumption that will be tested the moment `ZEUS_MODE=live` starts. P12 is the systematic audit of this gap category — not a list of individual fixes, but a structural mapping of what paper mode hides from live mode.

---

## §1 The design failure

Paper and live share the same CycleRunner, evaluator, strategy, and contracts. This is good (INV: paper/live parity). But they diverge at the execution boundary:

```
                    SHARED (paper = live)
                    ┌─────────────────────┐
                    │ Signal layer         │
                    │ Strategy layer       │
                    │ Evaluator            │
                    │ Sizing (Kelly)       │
                    │ Contracts (P9/P10)   │
                    └────────┬────────────┘
                             │
                    DIVERGENCE POINT
                             │
              ┌──────────────┴──────────────┐
              │                             │
         PAPER MODE                    LIVE MODE
    ┌─────────────────┐          ┌─────────────────┐
    │ Instant fill     │          │ EIP-712 signing  │
    │ Infinite USDC    │          │ Real USDC balance│
    │ No order book    │          │ Order book depth │
    │ No latency       │          │ Network latency  │
    │ No rejection     │          │ Rejection/timeout│
    │ No partial fills │          │ Partial fills    │
    │ No collateral    │          │ CTF collateral   │
    │ No fee deduction │          │ Real fee (P9)    │
    │ No competition   │          │ Other traders    │
    └─────────────────┘          └─────────────────┘
```

The structural question is: **what properties must survive the crossing from the shared layer into live execution?**

---

## §2 Assumption categories (not individual bugs)

### Category A — Capital accounting
What Zeus believes about how much money it has, can spend, and has spent.

- `get_balance()` returns free USDC only, not portfolio total value
- Open orders do not lock USDC (match-time settlement model)
- Multiple orders in one cycle can all reference the same USDC
- Balance snapshot at cycle start may be stale by cycle end
- Conditional token holdings have market value ≠ entry price
- Unredeemed winning positions lock USDC until `redeem()` is called
- Fee is deducted in shares (buy) vs USDC (sell) — asymmetric P&L impact

### Category B — Order lifecycle
What happens between "Zeus decides to trade" and "trade is confirmed."

- Order placement = signed intent, not execution
- Fill is not guaranteed — depends on counterparty
- Partial fills are possible — what happens to the remaining size?
- Orders can timeout or be cancelled by the exchange
- Price may move between decision and fill (slippage)
- Multiple orders in flight simultaneously — ordering/priority?
- Exit orders (sell) have different collateral requirements than entry (buy)

### Category C — Authentication and signing
What Zeus needs to prove its identity to the CLOB.

- EIP-712 signature with correct `feeRateBps` in payload
- API key/secret/passphrase rotation and expiry
- Signature type (0=EOA, 1=Magic, 2=proxy) must match wallet type
- Keychain resolver availability at daemon runtime
- What happens if keychain is locked (screen locked, restart)?

### Category D — Network and infrastructure
What can go wrong between Zeus and Polymarket.

- REST API latency and timeout handling
- WebSocket vs REST polling for price data
- Rate limiting (unknown limits, no handling for Polymarket CLOB)
- CLOB downtime or maintenance windows
- Polygon network congestion affecting settlement
- DNS resolution, TLS, proxy issues at daemon level

### Category E — Competitive dynamics
What changes when real money is at stake and others are trading.

- Order book depth at Zeus's price levels — is there enough liquidity?
- Front-running risk — is Zeus's strategy observable?
- Price impact of Zeus's own orders on thin books
- Other bots competing for the same weather edges
- Market maker behavior around settlement time

---

## §3 Structural decisions needed (not patches)

**SD-1: Capital tracking must be real-time, not snapshot-based.**
Paper mode has infinite USDC. Live mode needs: free balance, pending order reservation, position mark-to-market. This is one tracking system, not three patches.

**SD-2: Order lifecycle must be first-class, not fire-and-forget.**
Paper mode: decide → fill (instant). Live mode: decide → sign → submit → pending → partial/filled/rejected/timeout. Each state needs defined behavior. This is one state machine, not individual error handlers.

**SD-3: Execution failure must be recoverable, not fatal.**
Paper mode never fails. Live mode: network timeout, CLOB rejection, insufficient balance, collateral check failure. The recovery policy (retry? skip? halt?) needs to be one decision, not per-failure-mode patches.

**SD-4: The fee/slippage gap between decision and execution must be bounded.**
Paper mode: decision price = execution price. Live mode: slippage, fee, partial fill price. Kelly sizing assumed a cost. The actual cost may differ. What's the maximum acceptable divergence?

---

## §4 Resolution approach

This is NOT a coding packet. This is an investigation packet.

**Phase 1: Dry-run with logging.**
Run `ZEUS_MODE=live` with a LIVE_LOCK-like mechanism that signs and submits orders but with `size_usd=0` or to a test market. Log every API call, response, timing, and failure. Collect data on what actually happens.

**Phase 2: Gap analysis from dry-run data.**
Compare dry-run reality against paper-mode assumptions. Each divergence → RealityContract in P10 format.

**Phase 3: Structural fixes.**
Implement SD-1 through SD-4 based on dry-run evidence, not speculation.

**Phase 4: Minimal live with kill-switch.**
First real trade with smallest possible size. Venus monitors. Kill-switch on any unexpected behavior.

---

## §5 Relationship to Endgame

The FINAL spec §E1 says "Zeus runs live, unattended, for 7 calendar days." P12 is the gate between P10/P11 completion and Endgame entry. Without P12, the endgame would test Zeus's live execution assumptions for the first time with real money — exactly the pattern that produced the 17 reality gaps P10 fixed.

P12 is the immune system for the paper→live crossing.

---

## §6 What P12 is NOT

- Not a backtest or replay system
- Not new signal math
- Not architecture expansion
- Not a new spec layer (this is the last — P12 closes after live is verified)
- Not permission to delay Endgame indefinitely — P12 has a bounded scope (the 5 categories above) and a binary exit condition (dry-run passes, structural decisions implemented, minimal live succeeds)
