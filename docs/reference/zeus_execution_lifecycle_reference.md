# Zeus Execution & Lifecycle Reference

Durable reference for position lifecycle management, order execution mechanics,
chain reconciliation, exit trigger semantics, and settlement harvest.

Authority: executable source, tests, machine manifests, and authority docs win
on disagreement with this document.

---

## 1. Position Lifecycle State Machine

### 1.1 Phases

9 states in `LifecyclePhase` (str, Enum). The 9th — `UNKNOWN` — exists as
a catch-all for unmapped runtime strings; code that produces UNKNOWN is a bug,
not a feature.

```python
class LifecyclePhase(str, Enum):
    PENDING_ENTRY       = "pending_entry"
    ACTIVE              = "active"
    DAY0_WINDOW         = "day0_window"
    PENDING_EXIT        = "pending_exit"
    ECONOMICALLY_CLOSED = "economically_closed"
    SETTLED             = "settled"
    VOIDED              = "voided"
    ADMIN_CLOSED        = "admin_closed"
    UNKNOWN             = "unknown"
```

Terminal phases: `SETTLED`, `VOIDED`, `ADMIN_CLOSED`.

`QUARANTINED` is retired from this enum entirely (T5, quarantine excision
`docs/rebuild/quarantine_excision_2026-07-11.md`) — no writer mints it. A
confirmed-fill/chain-absence dispute or a terminal-restore that needs
operator review keeps the position's TRUE phase (`ACTIVE`/`PENDING_EXIT`) and
the dispute lives in a typed `ReviewWorkItem` (`src/contracts/review_work_item.py`,
`src/state/review_work_items.py`), never in this enum. The T5 schema
migration (`scripts/migrations/2026_07_quarantine_phase_retirement.py`) has
run against the live DBs (2026-07-12): the `position_current.phase` /
`chain_state` CHECK constraints no longer admit `"quarantined"` /
`"quarantine_expired"` / `"entry_authority_quarantined"`, and the mixed-epoch
load bridge that used to remap a legacy row carrying one of those literals
(formerly `src/state/portfolio.py`'s `_normalize_runtime_lifecycle_state` /
`_normalize_runtime_chain_state`) has been retired (BRIDGE RETIREMENT packet,
post-T5-migration cleanup) — a row can no longer carry the retired literal at
all, so `Position` construction raises instead of remapping.

### 1.2 Legal transitions (from `LEGAL_LIFECYCLE_FOLDS`)

```
None             → {pending_entry}
pending_entry    → {pending_entry, active, day0_window, voided}
active           → {active, day0_window, pending_exit, settled, voided}
day0_window      → {day0_window, pending_exit, settled, voided}
pending_exit     → {pending_exit, active, day0_window, economically_closed,
                    settled, admin_closed, voided}
economically_closed → {economically_closed, settled, voided}
unknown          → {unknown, voided}
```

`fold_lifecycle_phase()` enforces these transitions. Any illegal transition
raises `ValueError`. This is the single enforcement point — all runtime
state helpers delegate to `fold_lifecycle_phase()`.

### 1.3 Runtime state strings vs lifecycle phase

The `state` field on a `Position` object is a runtime string, not a
`LifecyclePhase` enum directly. `phase_for_runtime_position()` maps between
them using three inputs: `state`, `exit_state`, and `chain_state`.

Mapping rules (evaluated in priority order):

| Runtime string(s) | Phase |
|----|-----|
| `state="voided"` | VOIDED |
| `state="settled"` | SETTLED |
| `state="economically_closed"` | ECONOMICALLY_CLOSED |
| `state="admin_closed"` | ADMIN_CLOSED |
| `state="pending_exit"` | PENDING_EXIT |
| `exit_state ∈ PENDING_EXIT_RUNTIME_STATES` or `chain_state="exit_pending_missing"` | PENDING_EXIT |
| `state="pending_tracked"` | PENDING_ENTRY |
| `state="day0_window"` | DAY0_WINDOW |
| `state ∈ {"entered", "holding"}` | ACTIVE |
| anything else | UNKNOWN (warning logged) |

Where `PENDING_EXIT_RUNTIME_STATES = {"exit_intent", "sell_placed",
"sell_pending", "retry_pending", "backoff_exhausted"}`.

T5 (`docs/rebuild/quarantine_excision_2026-07-11.md`, REPLACEMENT PHASE LAW):
the explicit `state="quarantined"` row and the `chain_state ∈ {"quarantined",
"quarantine_expired"}` fallback row are retired — no writer mints either
input, and the T5 schema migration has run so the DB CHECK no longer admits
either literal. A confirmed-fill/chain-absence dispute keeps its TRUE phase
(`ACTIVE`/`PENDING_EXIT`, reached via the normal `has_positive_exposure`/
`has_exit_fallback` rows above) and the dispute lives in a `ReviewWorkItem`,
never in the phase itself. A raw `state="quarantined"` string reaching this
mapping falls through to `UNKNOWN` and fails loudly downstream (e.g.
`fold_lifecycle_phase` raises) rather than being remapped — the mixed-epoch
load bridge that used to remap it to `"holding"` before `Position`
construction has been retired (BRIDGE RETIREMENT packet, post-T5-migration
cleanup); the literal can no longer occur on any live row.

### 1.4 Runtime state helper contracts

Each `enter_*_runtime_state()` function:
1. Derives the current `LifecyclePhase` via `phase_for_runtime_position()`
2. Validates the from-phase is in a specific allowed set
3. Calls `fold_lifecycle_phase()` (the fold table check)
4. Returns a runtime state string (not the enum value directly)

| Helper | Required from-phase | Returns |
|--------|-------------------|---------|
| `enter_filled_entry_runtime_state` | `pending_entry` | `"entered"` |
| `enter_voided_entry_runtime_state` | `pending_entry` | `"voided"` |
| `rescue_pending_runtime_state` | `pending_entry` | `"entered"` |
| `enter_day0_window_runtime_state` | `{active, pending_entry, day0_window}` | `"day0_window"` |
| `enter_pending_exit_runtime_state` | (any fold-legal) | `"pending_exit"` |
| `enter_economically_closed_runtime_state` | `pending_exit` | `"economically_closed"` |
| `enter_settled_runtime_state` | `{active, day0_window, economically_closed, pending_exit}` | `"settled"` |
| `enter_admin_closed_runtime_state` | `pending_exit` | `"admin_closed"` |
| `enter_voided_runtime_state` | `{pending_entry, active, day0_window, pending_exit, economically_closed, unknown}` | `"voided"` |
| `release_pending_exit_runtime_state` | N/A (reverse fold) | previous_state or `"holding"` / `"day0_window"` |

`release_pending_exit_runtime_state` is the only "backward" transition: it
restores a position from `pending_exit` to its previous state when an exit
order is cancelled. The candidate state comes from `previous_state` or is
inferred from `day0_entered_at`.

### 1.5 Order status → initial runtime state

`initial_entry_runtime_state_for_order_status()` maps CLOB order status
strings to initial runtime states:

| Order status | Runtime state |
|-------------|---------------|
| `"filled"` | `"entered"` |
| `{"canceled", "cancelled", "rejected", "expired", "dead"}` | `"voided"` |
| anything else | `"pending_tracked"` |

**Key file**: `src/state/lifecycle_manager.py`

---

## 2. Chain Reconciliation

### 2.1 Three-state classifier (`ChainState`)

Before reconciliation runs, `classify_chain_state()` converts raw API
response into one of three states:

```python
class ChainState(str, Enum):
    CHAIN_SYNCED  = "chain_synced"   # API returned positions
    CHAIN_EMPTY   = "chain_empty"    # API returned 0 positions, trusted
    CHAIN_UNKNOWN = "chain_unknown"  # API failed or response is suspect
```

Classification logic:
- `fetched_at=None` → CHAIN_UNKNOWN (API did not respond)
- Non-empty positions → CHAIN_SYNCED
- Empty positions + any active local position has `chain_verified_at` within
  `_STALE_GUARD_SECONDS` (6 hours) → CHAIN_UNKNOWN (API response suspect)
- Empty positions + all local positions have stale `chain_verified_at`
  → CHAIN_EMPTY (safe to trust the empty response)

When the result is CHAIN_UNKNOWN, reconciliation skips Rule 2 (void) entirely.
This is the core known-absence vs unknown-status safety gate.

### 2.2 Reconciliation rules

`reconcile()` iterates local positions and applies three rules:

**Rule 1: Local + chain match → SYNCED.**
Updates local position with chain truth: `chain_state="synced"`,
`chain_shares`, `chain_verified_at`, `entry_price` (from chain avg_price),
`cost_basis_usd` (from chain cost).

Size mismatch handling (when `|chain.size - local_shares| > 0.01`, and the
position is not aggregate-backed):
- If a canonical lifecycle baseline is available → emit `SIZE_CORRECTED`
  event → update shares to chain value.
- If no canonical baseline exists yet for this `position_id` → apply the
  chain-truth correction via the SAME `CHAIN_SIZE_CORRECTED` event shape the
  chain-mirror reconciler uses (an append-only event + a `chain_shares`
  projection update), keeping the position in its CURRENT phase — no
  state/chain_state mutation. *(P0b, 2026-07-04 — chain size is truth
  regardless of whether a canonical lifecycle baseline exists yet; the prior
  behavior minted a durable `quarantined`/`size_mismatch_unresolved` dead
  end, exactly the invented state the chain-mirror reconciler exists to
  drain.)*
- If no `position_current` row exists at all for the `position_id` (e.g. a
  legacy/pre-canonical position) → the in-memory correction still stands but
  there is no durable row to correct; the next cycle that sees a canonical
  row for this `position_id` picks the correction back up. No quarantine, no
  invented state.

**Rule 2: Local but NOT on chain → VOID.**
Only fires when the per-cycle classifier reports `ChainSnapshotCompleteness.
CHAIN_EMPTY` (fresh, complete, authoritatively empty). `CHAIN_UNKNOWN`
(missing/stale/incomplete API response) is short-circuited earlier in the
function — degraded snapshots are NEVER evidence of absence. Additional
skip conditions:
- Position is pending exit (exit lifecycle owns the decision)
- Position has `entry_fill_verified=True` with `chain_state ∈ {"local_only",
  "unknown"}` (recently filled, chain may be lagging)

`Position.chain_verified_at` is a positive-observation timestamp ONLY
(PR C0 fix, 2026-05-27). Absence observations land in
`Position.last_chain_absence_observed_at`. The classifier reads positive
observations only — see `src/state/chain_state.py` module docstring.

**Rule 3: Chain but NOT local → emit ChainOnlyFact.**
Writes a typed `ChainOnlyFact` review-queue entry to
`PortfolioState.chain_only_facts` and a corresponding suppression row via
`record_token_suppression()`. Earlier versions of this module synthesized a
fake `Position(direction="unknown", ...)`; that path is removed (PR C2 +
PR E2, 2026-05-27). Skips tokens in `portfolio.ignored_tokens`
(settled/redeemed tokens that should not be resurrected).

### 2.3 Pending entry rescue

When a `pending_tracked` position is found on chain:
1. Check for canonical rescue baseline (`position_current.phase = pending_entry`)
2. If no baseline → skip (missing canonical baseline counter incremented)
3. If baseline available → copy chain truth (price, shares, cost), set
   `state = rescue_pending_runtime_state()` → `"entered"`, emit rescue event
4. Rescue event is dual-written: legacy `CHAIN_RESCUE_AUDIT` in
   `position_events` + structured `rescue_events` row with
   `temperature_metric`, `authority`, `authority_source` (from
   `resolve_rescue_authority()`)

Authority resolution for rescue:
- Position with `temperature_metric ∈ {"high", "low"}` → `VERIFIED` +
  `"position_materialized"`
- Position with missing/invalid metric → `"high"` fallback + `UNVERIFIED` +
  `"position_missing_metric:{value}"`

### 2.4 ChainOnlyFact review escalation

The 48-hour position-quarantine timer (formerly minting
`chain_state="quarantine_expired"`) is RETIRED (P0b, 2026-07-04 — "a 48h
timer on an invented state"). A confirmed-fill/chain-absence dispute now
resolves through the chain-mirror reconciler's two-consecutive-mirror-runs
force-resolve (`src.state.chain_mirror_reconciler.classify_local_position`,
~10min cadence) — orders of magnitude faster than the retired 48h backstop,
and the dispute lives in a `ReviewWorkItem`, not a position phase.

`check_quarantine_timeouts()` survives only as the entry point for a
different, still-live mechanism: ChainOnlyFact 48h review escalation. For
each open `ChainOnlyFact` in `portfolio.chain_only_facts` whose
`review_state != "resolved"`, once `first_seen_at` is older than
`CHAIN_ONLY_REVIEW_WINDOW_HOURS` (48h) it logs a WARNING that operator
review is required — read-only escalation (the fact's `review_state` is
derived, not written here); resolution is operator-driven via the
suppression row. A missing/unparseable `first_seen_at` also logs a WARNING.
This never freezes unrelated entries; the function always returns `0`
(position-phase expiry itself was removed).

**Key files**: `src/state/chain_reconciliation.py`, `src/state/chain_state.py`

---

## 3. Order Execution

### 3.1 Entry path

`create_execution_intent()` builds an `ExecutionIntent` from edge context:

1. Compute limit price via `compute_native_limit_price()` in the native/held-side
   probability space, offset by `settings["execution"]["limit_offset_pct"]`
2. Dynamic limit: if `best_ask` is within 5% of limit price → jump to ask
3. Route token: `buy_yes` → `token_id`, `buy_no` → `no_token_id`
4. Set timeout from discovery mode (hard-coded, no defaults):
   - `opening_hunt` → 14400s (4h)
   - `update_reaction` → 3600s (1h)
   - `day0_capture` → 900s (15min)
   - Unknown mode → `ValueError` (fail-closed)
5. Slice policy: `"iceberg"` if `size_usd > 100`, else `"single_shot"`
6. Reprice policy: `"dynamic_peg"` for day0_capture, `"static"` otherwise

`execute_intent()` then:
1. Computes shares with BUY quantization: `math.ceil(shares * 100 - 1e-9) / 100.0`
2. Rejects if `token_id` is empty
3. Calls `_live_order()` which places via `PolymarketClient.place_limit_order()`
4. Returns `OrderResult` with `status="pending"` and the venue order ID
5. Emits Discord trade alert

### 3.2 Exit path

`create_exit_order_intent()` → `execute_exit_order()`:

1. Compute exit limit: `current_price - 0.01` (1 cent below current)
2. If `best_bid < base_price` and slippage ≤ 3% → use best_bid instead
3. Bind to the current executable snapshot, clamp to its venue range
   `[tick, 1 - tick]`, and align to that same tick
4. SELL quantization: `math.floor(shares * 100 + 1e-9) / 100.0` (round DOWN)
5. Reject if shares round to zero or no token_id
6. Place via `PolymarketClient.place_limit_order(side="SELL")`
7. Idempotency key: `"{trade_id}:exit:{token_id}"`

### 3.3 Share quantization invariant

The BUY/SELL asymmetry is critical for position safety:
- BUY rounds UP → ensures minimum position is taken (slightly overspend)
- SELL rounds DOWN → prevents selling more shares than held

The epsilon (`1e-9`) prevents floating-point boundary errors from flipping
the rounding direction.

**Key file**: `src/execution/executor.py`

---

## 4. Exit Triggers

### 4.1 Actual trigger evaluation order

The live path is `Position.evaluate_exit()` in `src/state/portfolio.py`. Its
decision precedence is:

1. **RED_FORCE_EXIT** — explicit non-Day0 RED sweep intent preempts ordinary
   probability checks.
2. **EVIDENCE_UNAVAILABLE / missing authority** — unavailable or malformed
   current belief holds fail closed; the narrow settlement-imminent exception
   still requires an executable bid.
3. **Day0 physical authority** — absorbing structural facts and authorized
   zero-probability evidence dominate the forecast posterior.
4. **SETTLEMENT_IMMINENT** — compares executable sell value with the current
   held-side value; a confirmed winner may hold instead of being blindly sold.
5. **WHALE_TOXICITY observation** — records `whale_toxicity_observed` only. It
   has no independent liquidation authority and cannot bypass probability,
   CI, or hold-vs-sell economics.
6. **FLASH_CRASH_PANIC / VIG_EXTREME** — independent market-mechanics evidence
   remains actionable under its own guards.
7. **CI separation and direction-specific economics** — a confirmed belief
   reversal or executable sell-value dominance may exit; overlap, positive
   held edge, or superior hold value remains a hold.

The removed `MODEL_DIVERGENCE_PANIC` and unconditional whale branches must not
be reintroduced as price/order-flow-only liquidation rules.

### 4.2 Buy-yes exit: `_evaluate_buy_yes_exit()`

Uses 2-consecutive-cycle confirmation with conservative evidence edge:
1. Compute `evidence_edge = conservative_forward_edge(forward_edge, ci_width)`
   (pessimistic CI-adjusted edge)
2. Compare against `buy_yes_edge_threshold(entry_ci_width)` (entry-time CI
   scales the threshold — wider entry CI → harder to trigger exit)
3. If evidence_edge ≥ threshold → reset `neg_edge_count` to 0
4. If evidence_edge < threshold → increment `neg_edge_count`
5. If `neg_edge_count < consecutive_confirmations()` (currently 2) → no exit
6. If count reached → EV gate: `net_sell = shares × best_bid` vs
   `net_hold = HoldValue.compute(shares × p_posterior, 0, 0).net_value`
7. If selling ≤ holding EV → hold despite reversal
8. Otherwise → `EDGE_REVERSAL` signal

### 4.3 Buy-no exit: `_evaluate_buy_no_exit()`

Different math because buy-no has ~87.5% base win rate:

1. Near-settlement gate: if `hours_to_settlement < near_settlement_hours()` →
   only exit on deeply negative forward edge (`< buy_no_ceiling()`)
2. Otherwise: same consecutive-confirmation pattern but with
   `buy_no_edge_threshold()` (different threshold than buy-yes)
3. EV gate uses `p_market[0]` for sell value (from edge context's market
   array) instead of `best_bid`
4. Trigger name: `BUY_NO_EDGE_EXIT` (distinct from `EDGE_REVERSAL`)

### 4.4 Probability direction invariant

All probabilities in exit triggers operate in native direction space:
- buy_yes positions → P(YES)
- buy_no positions → P(NO)

Monitor refresh (`refresh_position()`) converts before returning: if direction
is `buy_no`, `p_cal_native = 1.0 - p_cal_yes`. The exit trigger code never
flips probabilities internally. This was a historical incident: a double-
inversion caused 7/8 buy_no positions to false-exit in 30-90 minutes.

**Key file**: `src/state/portfolio.py` (`Position.evaluate_exit`)

---

## 5. Monitor Refresh

### 5.1 Two refresh paths

`refresh_position()` dispatches to one of two signal paths based on
`entry_method`:

| Entry method | Refresh function | When used |
|-------------|-----------------|-----------|
| `ENS_MEMBER_COUNTING` | `_refresh_ens_member_counting()` | Multi-day-out positions |
| `DAY0_OBSERVATION` | `_refresh_day0_observation()` | Day0 window positions |

Special case: if `pos.state == "day0_window"` but `entry_method !=
DAY0_OBSERVATION`, the position is re-wrapped with `entry_method=
DAY0_OBSERVATION` for the refresh call. This handles positions that entered
via ENS member counting but have since transitioned to day0 window.

### 5.2 ENS member counting refresh

The full probability chain for monitor refresh:
1. Fetch fresh ensemble (`fetch_ensemble()`)
2. Build full bin vector from sibling outcomes (`_build_all_bins()`)
3. Compute `p_raw_vector` via `EnsembleSignal.p_raw_vector()` with MC noise
4. Get calibrator for the position's `temperature_metric` (HIGH/LOW separation)
5. If calibrator + multi-bin: `calibrate_and_normalize()` (Platt + simplex)
6. If calibrator + single-bin: `calibrator.predict_for_bin()`
7. If no calibrator: use raw p
8. Authority gate: check for UNVERIFIED calibration rows → if found, refuse
   to update probability (return stale value)
9. Compute alpha via `compute_alpha()` with calibration level, ensemble
   spread, model agreement, lead days, hours since open, authority verification
10. Apply persistence anomaly discount to alpha (historical temperature
    delta check)
11. Direction conversion: buy_no → `1.0 - p_cal_yes`
12. Posterior: `α × p_cal_native + (1-α) × p_market`
13. Stash bootstrap context on position for fresh CI computation

### 5.3 Bootstrap CI recomputation

After probability refresh, `refresh_position()` recomputes bootstrap CI:
- Multi-bin + bootstrap context available → call `MarketAnalysis._bootstrap_bin()`
  (buy_yes) or `._bootstrap_bin_no()` (buy_no) with `edge_n_bootstrap()` samples
- Single-bin or no context → fall back to entry-time CI width scaled around
  the fresh forward edge
- NaN guard: if bootstrap produces NaN CI bounds, fall back to stale CI

**Key file**: `src/engine/monitor_refresh.py`

---

## 6. Settlement Harvest

### 6.1 `run_harvester()` flow

1. Open split connections: `trade_conn` (position events) and `shared_conn`
   (ensemble snapshots, calibration pairs)
2. Preflight: check both DBs have required tables (`_preflight_harvester_stage2_db_shape`)
3. Fetch settled events from Gamma API (paginated, 200/page)
4. For each settled event:
   a. Match city via `_match_city()` from title/slug
   b. Extract target date
   c. Find winning bin via `winningOutcome == "Yes"` (price fallback removed)
   d. Write settlement truth to `settlements` table
   e. Resolve decision-time snapshot contexts for calibration learning
   f. For each learning-ready context: generate calibration pairs
      (`harvest_settlement()`) — one pair per bin (winner=1, others=0)
   g. If pairs created: `maybe_refit_bucket()` (update Platt model)
   h. Settle held positions (`_settle_positions()`)
5. `commit_then_export()`: DB commit first, then JSON portfolio/tracker export

### 6.2 Gamma API pagination safety

`_fetch_settled_events()` has a critical safety boundary:
- First-page HTTP error → warning + empty return (next cycle retries)
- Mid-pagination HTTP error (offset > 0) → `RuntimeError` (refuse to return
  partial results as complete — prevents silent settlement drops on page 2+)

### 6.3 Settlement dedup

Three overlapping dedup layers prevent duplicate settlement writes:

1. **DB-level dedup** (`_dual_write_canonical_settlement_if_available()`):
   reads `position_current.phase` from DB — if already in terminal phase
   (`{settled, voided, admin_closed}` = `_TERMINAL_PHASES`), skip. This is the
   authoritative dedup anchor. The in-memory `pos` object may be stale
   (loaded from JSON fallback cache) but the DB phase is real. T5
   (`docs/rebuild/quarantine_excision_2026-07-11.md`): `_TERMINAL_PHASES` used
   to also union in the legacy raw string `quarantined` (P0c, pre-migration
   tolerance — no writer minted it, but a pre-T5-schema-migration row could
   still carry it). The T5 schema migration has run and the DB CHECK no
   longer admits the literal, so that union has been retired
   (BRIDGE RETIREMENT packet, post-T5-migration cleanup); `_TERMINAL_PHASES`
   is now an alias of `TERMINAL_STATES`.

2. **Iterator-level dedup** (`_settle_positions()`): queries
   `position_current` for all positions in this (city, target_date) market.
   Positions whose DB phase is already terminal (same `_TERMINAL_PHASES`
   above) are skipped before any P&L computation.

3. **Runtime state skip** (`_settle_positions()`): skips positions in states
   `{pending_tracked, admin_closed, voided, settled}` and positions with
   active exit states. The `quarantined`/`quarantine_expired` runtime-string
   checks this used to also include are retired for the same reason as (1).

### 6.4 P&L computation

```
shares = size_usd / entry_price
exit_price = 1.0 if position wins, 0.0 if position loses

For buy_yes: wins if bin_label == winning_label
For buy_no:  wins if bin_label != winning_label

If economically_closed: settlement_price = pos.exit_price (pre-settlement exit)
pnl = closed.pnl (from compute_settlement_close or mark_settled)
```

### 6.5 Post-settlement actions

1. **Redemption**: if winning position has `condition_id` → call
   `PolymarketClient.redeem()` to claim USDC on-chain. Failure is
   non-fatal (USDC is claimable later).
2. **Token suppression**: settled token added to `ignored_tokens` via
   `record_token_suppression()` — prevents reconciliation from resurrecting it
3. **Chronicle**: `log_event("SETTLEMENT", ...)` + `log_settlement_event()`
4. **Canonical write**: `_dual_write_canonical_settlement_if_available()`
5. **Trade decisions update**: update `trade_decisions.status` → `"settled"`
6. **Strategy tracker**: `strategy_tracker.record_settlement()`
7. **Calibration**: each bin generates a calibration pair via
   `add_calibration_pair()` (HIGH track) or `add_calibration_pair_v2()`
   (LOW track), feeding future Platt model refits

### 6.6 Settlement source mapping

`_SOURCE_TYPE_MAP` converts config-level settlement source types to DB
constants: `{"wu_icao": "WU", "hko": "HKO", "noaa": "NOAA", "cwa_station": "CWA"}`.

Settlement truth is the winning bin from Polymarket (market authority).
The temperature value comes from the weather station (observation authority).
These are separate truth surfaces that should not be conflated.

**Key file**: `src/execution/harvester.py`

---

## 7. ExecutionPrice Contract

### 7.1 Typed pricing boundary

`ExecutionPrice` is a frozen dataclass with four fields:
- `value: float` — the numeric price (must be finite, non-negative)
- `price_type: Literal["vwmp", "ask", "bid", "implied_probability", "fee_adjusted"]`
- `fee_deducted: bool` — whether taker fee has been applied
- `currency: Literal["usd", "probability_units"]`

Live venue submission adds a snapshot-bound order-price contract: every
BUY/SELL, single/batch order must have unit price in inclusive
`[tick, 1 - tick]` for the current executable snapshot and be tick-aligned.
`VenueSubmissionEnvelope.assert_live_submit_bound()` enforces the range
immediately before the adapter SDK boundary. Before the tick is known, only the
strict probability domain `(0, 1)` can be asserted; it is not submit authority.

### 7.2 Kelly safety gate

`assert_kelly_safe()` enforces three conditions before Kelly sizing can proceed:
1. `price_type ≠ "implied_probability"` — implied probability is an estimate,
   not a cost. Using it causes Kelly to treat `P_market` as entry cost,
   systematically oversizing.
2. `fee_deducted = True` — Kelly sees the all-in cost. Without fee,
   taker fee (~1.25% at p=0.50) is unaccounted, causing oversizing.
3. `currency = "probability_units"` — Kelly formula operates in [0,1] space.

### 7.3 Polymarket fee model

`polymarket_fee(price, fee_rate=0.05)` computes the **non-linear** taker fee:
```
fee = fee_rate × p × (1 - p)
```
This is zero at the extremes and maximal at p=0.50:
- At p=0.90: fee = 0.0045 (0.45%)
- At p=0.50: fee = 0.0125 (1.25%)
- At p=0.10: fee = 0.0045 (0.45%)

`with_taker_fee()` creates a new `ExecutionPrice` with type `"fee_adjusted"`.
Double-application is blocked — calling `with_taker_fee()` on an already
fee-adjusted price raises `ExecutionPriceContractError`.

**Key file**: `src/contracts/execution_price.py`

---

## 8. Cross-References

- Math pipeline: `docs/reference/zeus_math_spec.md`
- Domain model: `docs/reference/zeus_domain_model.md`
- Architecture law: `docs/authority/zeus_current_architecture.md`
- Risk/strategy: `docs/reference/zeus_risk_strategy_reference.md`
- Source AGENTS:
  - `src/execution/AGENTS.md` — execution domain rules
  - `src/state/AGENTS.md` — state/lifecycle domain rules
  - `src/engine/AGENTS.md` — orchestration domain rules
  - `src/contracts/AGENTS.md` — contract/semantics domain rules
