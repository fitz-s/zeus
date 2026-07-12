# Local-Bookkeeping Excision — KEEP List Census

Read-only census (2026-07-12). Mission: build the KEEP list for an operator-ordered
"local bookkeeping excision" (law: no local booking of chain-knowable content; required
info syncs directly and only from chain). The excision must NOT delete genuine local
truth — facts that do NOT exist on chain and are Zeus's own epistemic/intent state.

Repo-law framing (AGENTS.md §2): truth path is `chain/CLOB facts -> canonical DB/events
-> projections`. The A1–A6 typed vocabulary in `src/contracts/canonical_lifecycle.py`
already draws most of the boundary: **A1 command truth and A4 exposure are explicitly
"NOT venue truth"; A2 order status is venue-CLOB fact; A5 phase / A6 exit are
"PROJECTION, not a source."** Excision target = anything the chain is authoritative for.
KEEP = the rest. Live row counts from `state/zeus_trades.db` (87GB) opened READ-ONLY.

---

## KEEP — genuine local truth, cannot come from chain

### 1. EPISTEMIC (Zeus's belief/decision state — chain never holds this)

| # | Truth | Table / module (file:line) | Why not chain |
|---|---|---|---|
| K1 | Decision certificates (frozen q) | `decision_certificates` (36,528 rows); immutable `payload_hash`/`certificate_hash`, `decision_time` | Walk-forward law (AGENTS §0): probability frozen at decision time as immutable certificate; settlement grades against it. Chain has no notion of "what Zeus believed when it decided." |
| K2 | No-trade reasons / decision chain | `src/state/decision_chain.py:1` (NoTradeCase — "record WHY with the same rigor as when it does trade"); `trade_decisions` (3,106), `decision_log` | Rejection reasoning is pure Zeus epistemics; nothing is submitted, so chain sees nothing. |
| K3 | Monitor re-evaluation series | `position_events` event_type `MONITOR_REFRESHED` (131,469 rows) — prob/edge/quote at each monitor instant | Records the live probability + book quote Zeus observed at a past instant; not reconstructable from chain, and not the frozen cert. |
| K4 | Forecast posteriors, calibration, ensembles | forecasts DB: `forecast_posteriors`, `calibration_pairs`, `platt_models`, `ensemble_snapshots`, `day0_nowcast_runs`, `day0_metric_fact`, `probability_trace_fact` | Atmospheric signal → probability. Off-chain entirely. |
| K5 | Settlement VALUE (temperature) | `settlement_outcomes` via single writer `src/state/settlement_writers.py`; `SettlementSemantics.assert_settlement_value()` | Settlement value is a Weather Underground integer temperature (AGENTS §2), not a chain fact. Resolution-payout half is BORDERLINE B4. |

### 2. INTENT (Zeus's own submissions — needed for shared-wallet attribution)

| # | Truth | Table / module (file:line) | Why not chain |
|---|---|---|---|
| K6 | Command journal | `venue_commands` / `venue_command_events`; `src/state/venue_command_repo.py:6` ("Durable command journal — append-only… only this module may write, NC-18") | A1 `CommandTruthState` is "Zeus-local command/outbox lifecycle. NOT venue truth" (`canonical_lifecycle.py:78`). Shared-wallet co-trading means chain/venue fills can't be attributed to Zeus without the local intent record. |
| K7 | Idempotency keys | `venue_commands.idempotency_key`, `find_command_by_idempotency_key` (`venue_command_repo.py`) | Zeus's own dedup identity; never on chain. |
| K8 | Submission envelopes / snapshots | `venue_submission_envelopes` (2,216); `signed_order_blob`, `canonical_pre_sign_payload_hash`, `raw_request_hash` | The exact signed payload Zeus emitted — its own cryptographic submission receipt. |
| K9 | Exposure obligations | `entry_exposure_obligations` (4); `src/state/entry_exposure_obligation.py`, `src/contracts/review_work_item.py` (BLOCKER-1) | In-flight entry intent + worst-case unbounded exposure for risk accounting before chain confirms. |
| K10 | Collateral reservations | `collateral_reservations` (1,102); `src/state/collateral_ledger.py:7` ("pUSD BUY collateral vs CTF SELL inventory, fail-closed") | In-flight spend intent reserved against a wallet Zeus shares; distinct from chain balance (see BORDERLINE B3). |

### 3. VENUE-truth-not-chain (off-chain CLOB book — chain never sees unmatched orders)

| # | Truth | Table / module (file:line) | Authority vs cache |
|---|---|---|---|
| K11 | Open/resting order state | `venue_order_facts` (35,639); A2 `VenueOrderStatus` LIVE/PARTIALLY_MATCHED (`canonical_lifecycle.py:57`) | Venue API is authority, but it is the ONLY authority — chain has no unmatched-order concept. Local row is a rebuildable cache of venue REST/WS truth, NOT chain. Keep the table; authority is the venue, re-syncable. |
| K12 | Maker rest / staleness | derived, not stored: `src/state/order_state_predicates.py:1` ("no stored state… storing a recomputable classification would create a stale copy of staleness") | Correctly already NOT booked. Exemplar of the excision's own principle. |

### 4. RISK / CONTROL (operator domain — stays local)

| # | Truth | Table |
|---|---|---|
| K13 | Kill switch / overrides / pause | `control_overrides_history` (2), `risk_actions` (3) |
| K14 | Readiness / suppression | `readiness_state` (402), `token_suppression` (388), `strategy_health` |

Riskguard level / HALT / entries-pause are operator-domain runtime state (AGENTS §2, INV-05). No chain equivalent.

### 5. AUDIT LOG (append-only transition receipts)

| # | Truth | Table |
|---|---|---|
| K15 | Position transition receipts | `position_events` as an append-only log — records **when Zeus observed** each transition, provenance the chain does not carry. Keep the *log*; the *current derived value* it encodes must come from chain (see BORDERLINE B2 / CACHE-OK). |

**KEEP count: 15 entries.**

---

## BORDERLINE — flag for operator/consult adjudication

1. **Position lifecycle phase (`position_current.phase`).** `PositionPhase` is "A PROJECTION over command/order/trade/chain/settlement truth, not a source" (`canonical_lifecycle.py:147`). Intent phases `pending_entry`/`pending_exit` (+ runtime states `exit_intent`/`sell_placed`/`retry_pending`/`backoff_exhausted`, `lifecycle_manager.py:19`) encode intent; `active`/`day0_window`/`economically_closed`/`settled` are chain-derivable. **Question: is `phase` re-derivable every read via `derive_position_phase` (`canonical_projections.py`) from KEEP inputs (venue_commands + position_events) so the stored column is pure cache — including the intent phases? Confirm the intent phases have a non-chain rebuild source before dropping the column.**

2. **`position_events` chain-mirroring event_types.** `CHAIN_SIZE_CORRECTED` (15,403), `CHAIN_SYNCED` (47), `ENTRY_ORDER_FILLED` (230) / `EXIT_ORDER_FILLED` (78), `SETTLED` (1,224) mirror chain/venue facts. **Question: keep as observation-provenance receipts (when Zeus learned the fact), or excise as redundant chain bookings? (Recommend KEEP-as-receipt but forbid reading current size/fill/settlement value from them — read from chain.)**

3. **Collateral reservation vs chain balance.** `collateral_reservations` is in-flight intent (KEEP K10), but `collateral_ledger_snapshots` may also book a wallet *balance* that is chain-knowable (pUSD/CTF). **Question: which `collateral_ledger` columns are reservation-intent (KEEP) vs materialized wallet balance (CACHE, sync from chain)?**

4. **`settlements` resolution half.** `settlement_writers.py` writes the WU temperature VALUE (KEEP K5). But `uma_resolution` (via `src/state/uma_resolution_listener.py`) mirrors the on-chain UMA `SettlementResolved` event — the fact of resolution and payout ARE chain-knowable. **Question: does any payout/resolution field in settlement grading duplicate chain resolution and thus belong in CACHE-OK rather than KEEP?**

5. **`ReviewWorkItem` lane.** `review_work_items` (8). Its contract: "rebuildable from the facts it references — deleting every ReviewWorkItem row must never lose truth, only lose retry-cadence bookkeeping" (`review_work_item.py`). Not chain-knowable (so not an excision target), but rebuildable local scheduling, not authority. **Question: KEEP the retry-cadence bookkeeping as genuine local operational state, or treat as rebuildable and out of scope? (Recommend KEEP.)**

**BORDERLINE count: 5 entries.**

---

## CACHE-OK — local copies acceptable only as rebuildable read-through cache, never authority

| # | Local copy | Authority | Evidence (file:line) |
|---|---|---|---|
| C1 | `position_current.chain_shares`, `chain_avg_price`, `chain_cost_basis_usd`, `chain_seen_at`, `chain_absence_at` (248–251 non-null rows) | **Chain** ERC1155 balance | `src/state/chain_mirror_reconciler.py:1` mirrors position_current against chain on a 10-min cadence; reconciliation order Chain > Chronicler > Portfolio (AGENTS §2). Literal chain mirrors — prime excision target. |
| C2 | `venue_trade_facts` on-chain confirmation fields (MINED/CONFIRMED, `transaction_hashes_json`) (636 rows) | **Chain** | A3 `VenueTradeStatus` MINED/CONFIRMED (`canonical_lifecycle.py:68`); `src/state/fill_dedup.py:1` — "append-only WebSocket observation log." Fill-evidence cache; on-chain confirmation is chain-authoritative. |
| C3 | `position_current.phase` (if fully re-derivable) | derive from KEEP inputs | `canonical_projections.derive_position_phase` (pending BORDERLINE B1) |
| C4 | `executable_market_snapshot_latest` (subset of 9,834,819 `executable_market_snapshots`) | Gamma/CLOB venue | `src/state/snapshot_repo.py:1` — *latest* snapshot is a market-state cache; **snapshots CITED by a `venue_command` are frozen decision evidence = KEEP** (immutable, "never edits the evidence a prior venue_command cited"). Split by citation. |
| C5 | `uma_resolution` (resolution fact/timing) | **Chain** UMA OO Settle event | `src/state/uma_resolution_listener.py` — local table mirroring on-chain event; keep only as read-through of chain resolution. |
| C6 | `position_current.settlement_price`, `realized_pnl_usd` (177–197 non-null) | derivable from settlement VALUE (KEEP) + chain size (CACHE) | pure downstream projection |

**CACHE-OK count: 6 entries.**

---

## `position_events` event_type classification (live row counts)

Source: `select event_type, count(*) from position_events group by event_type` (READ-ONLY).

| event_type | count | Class |
|---|---|---|
| MONITOR_REFRESHED | 131,469 | EPISTEMIC (Zeus's re-eval receipt — prob/edge/quote at instant) — KEEP |
| CHAIN_SIZE_CORRECTED | 15,403 | CHAIN-MIRROR — receipt KEEP, value from chain (B2) |
| EXIT_ORDER_REJECTED | 6,292 | INTENT (venue reject of Zeus order) — KEEP |
| REVIEW_REQUIRED | 1,950 | INTENT/CONTROL (dispute lane) — KEEP |
| SETTLED | 1,224 | CHAIN/WORLD-MIRROR — receipt KEEP, value from WU/chain (B2/B4) |
| POSITION_OPEN_INTENT | 960 | INTENT — KEEP |
| ENTRY_ORDER_POSTED | 960 | INTENT — KEEP |
| EXIT_INTENT | 889 | INTENT — KEEP |
| ADMIN_VOIDED | 868 | INTENT/CONTROL (operator) — KEEP |
| MANUAL_OVERRIDE_APPLIED | 843 | INTENT/CONTROL (operator) — KEEP |
| ENTRY_ORDER_VOIDED | 728 | INTENT — KEEP |
| ENTRY_ORDER_FILLED | 230 | VENUE/CHAIN-MIRROR — receipt KEEP, value from chain (B2) |
| EXIT_RETRY_RELEASED | 201 | INTENT (retry scheduling) — KEEP |
| DAY0_WINDOW_ENTERED | 150 | PROJECTION (time-derived phase) — KEEP-as-receipt |
| EXIT_ORDER_FILLED | 78 | VENUE/CHAIN-MIRROR — receipt KEEP, value from chain (B2) |
| EXIT_ORDER_POSTED | 72 | INTENT — KEEP |
| CHAIN_SYNCED | 47 | CHAIN-MIRROR — receipt KEEP, value from chain (B2) |

`venue_commands` by (intent_kind, state): ENTRY CANCELLED 627, ENTRY FILLED 126, ENTRY EXPIRED 86, EXIT FILLED 76, ENTRY REJECTED 41, EXIT REJECTED 21, ENTRY SUBMIT_REJECTED 7, EXIT CANCELLED 7, EXIT EXPIRED 3, EXIT PARTIAL 1.

---

## Hard boundary for the plan

- **KEEP is non-negotiable:** decision_certificates, decision/no-trade log, venue_commands + events, venue_submission_envelopes, entry_exposure_obligations, collateral_reservations, venue_order_facts (off-chain CLOB), forecast posteriors/calibration/settlement-VALUE, risk/control tables, review_work_items, and `position_events` *as an append-only log*.
- **Excision target = chain-mirror columns** on `position_current` and the chain-confirmation half of `venue_trade_facts`/`uma_resolution` — read those directly from chain, never book as authority.
- **The five BORDERLINE questions must be adjudicated before any DROP**, especially B1 (intent phases' non-chain rebuild source) and B4 (settlement resolution-payout vs WU-value split); getting them wrong deletes genuine local truth.

Key files to cite: `src/contracts/canonical_lifecycle.py:57-168` (A1–A6 boundary),
`src/state/chain_mirror_reconciler.py`, `src/state/venue_command_repo.py`,
`src/state/snapshot_repo.py`, `src/contracts/review_work_item.py`,
`src/state/order_state_predicates.py:1` (excision principle already applied).
