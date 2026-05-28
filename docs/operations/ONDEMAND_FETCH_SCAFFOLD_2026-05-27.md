# SCAFFOLD: On-Demand Per-Bin Book Fetch (Model-First, Predicted-Bins-Only)

```
# Created: 2026-05-27
# Last reused or audited: 2026-05-27 (re-verified against post-#348 HEAD 3656f450e8)
# Authority basis: substrate-coverage remediation; batch-books commit 8192bb971e;
#                  #348 typed BinEdge.entry_price + σ_market sampling;
#                  operator directive "market event constant, bin event should not block freshness";
#                  operator simplification "fetch books only for model-predicted bins"
# Status: DESIGN ONLY (READ-ONLY architect deliverable). Touches the LIVE ENTRY PATH
#         (evaluate_candidate per-bin book loop). MANDATORY OPUS CRITIC before implementation.
```

## Summary

Primary-source tracing (verified against post-#348 HEAD 3656f450e8) overturns the problem
statement's mechanism and yields a SMALLER fix than "triage-then-fetch across events":

1. The live entry path does NOT read its edge-math orderbook from the background substrate.
   It fetches books **live, per-bin, per-direction** via `_buy_entry_price_from_clob` →
   `clob.get_orderbook(token_id)` INSIDE `evaluate_candidate`, for EVERY executable bin of
   EVERY candidate event. (`src/engine/evaluator.py:4527` YES, `:4585` NO; getter at `:382`.)
2. The background `refresh_executable_market_substrate_snapshots` (`market_scanner.py:3314`)
   supplies the **event/outcome list + tradeability identity** (read at `cycle_runtime.py:4234`),
   NOT the edge-math book. Its per-bin book completeness is an upstream tradeability filter
   (`executable_mask`, evaluator.py:3395-3397, gate at :4524) — not the edge input.
3. **`p_cal` — the calibrated model distribution over all 11 bins — is fully computed at
   `evaluator.py:4400-4411`, BEFORE the per-bin book loop at `:4506`, with NO market HTTP**
   (it reads DB calibrators via `get_calibrator`, not CLOB). 

Therefore the model already knows, before any book fetch, which bins can possibly bear edge.
**~8 of 11 bins have `p_cal ≈ 0`** (the model assigns them no mass) — fetching their books is
pure waste: a bin the model gives 0 probability cannot produce a positive edge regardless of
its price. The fix is to **gate the book loop on `p_cal` mass**: fetch books ONLY for the
argmax bin + its mass-bearing neighbors (~1-3 bins), batched in ONE POST /books, and skip the
rest. This collapses the dominant HTTP cost without any cross-event triage machinery.

## The seam (post-#348 file:line where on-demand inserts)

Live flow, re-verified at HEAD 3656f450e8:

1. `run_cycle(OPENING_HUNT)` — `src/engine/cycle_runner.py:567`
2. → `_execute_discovery_phase` (`cycle_runner.py:1039` → `:550`) →
   `_runtime.execute_discovery_phase` — `src/engine/cycle_runtime.py:3691`
3. **Substrate read** — `cycle_runtime.py:4234-4235`: `read_persisted_weather_markets(conn, ...)`
   → `markets = list(market_snapshot.events)`. Per-market eval-budget gate (default 360s,
   `_LIVE_DISCOVERY_EVAL_BUDGET_DEFAULT_SECONDS`, cycle_runtime.py:99) at `:4203` / loop `:4475-4495`.
4. **Candidate loop** — `cycle_runtime.py:4475`; `candidate` built `:4632`;
   `decisions = deps.evaluate_candidate(candidate, conn, portfolio, clob, limits, ...)` — `:4701`.
5. **Inside `evaluate_candidate`** (`src/engine/evaluator.py:3239`):
   - `p_cal = calibrate_and_normalize(...)` — **`evaluator.py:4400`** (DB-only, no market HTTP);
     uncalibrated fallback `p_cal = p_raw.copy()` at `:4411`. Validity gate `:4413`.
   - `p_market = np.zeros(len(bins))` — `:4473`.
   - **PER-BIN BOOK LOOP** `for i, o in enumerate(outcomes):` — **`evaluator.py:4506`**:
     - YES book: `yes_quote = _buy_entry_price_from_clob(clob, o["token_id"])` — **`:4527`**
     - YES EQE/σ_market: `_buy_entry_evidence_from_clob(...)` — `:4550`
     - fee: `_fee_rate_for_token(clob, ...)` — `:4540` (YES), `:4591` (NO)
     - NO book: `no_quote = _buy_entry_price_from_clob(clob, no_token_id)` — **`:4585`**
   - downstream: `p_market`/EQE feed MarketAnalysis bootstrap → typed `BinEdge` (see #348 note).

**Insertion point — SEAM (single, in-evaluator):** at the top of the loop body
`evaluator.py:4506`, after `idx` is resolved and the `executable_mask[idx]` gate at `:4524`,
add a **`p_cal` mass gate**: skip the live book fetch for bins where `p_cal[idx] < τ_mass`
AND `idx` is not in the predicted-bin set. Pre-loop, compute the predicted-bin set from the
already-available `p_cal`, and **batch-prefetch books for exactly those token_ids** via
`clob.get_orderbook_snapshots(...)` (commit 8192bb971e), then thread that map into
`_buy_entry_price_from_clob` (see plumbing below).

## Bin-selection rule (the on-demand "triage", now intra-event on p_cal)

Predicted-bin set `S` per event, from `p_cal` (already computed at evaluator.py:4400):
1. `peak = argmax(p_cal)`.
2. Include `peak` and its contiguous neighbors while cumulative mass < `τ_cover` (default 0.90)
   OR until `K_bins` bins selected (default cap **3**). I.e. the smallest contiguous bin window
   around the peak covering ≥90% of model mass, capped at 3 bins.
3. Always include any bin with `p_cal[idx] ≥ τ_mass` (default **0.05**) even if non-contiguous
   (guards a bimodal model distribution). Union.
4. Bins NOT in `S` keep `p_market[idx] = 0` / EQE None (their current behavior when a quote
   is unavailable) and are recorded `skipped_zero_model_mass` — they cannot bear edge so this
   does not suppress any real trade (a 0-probability bin yields `edge = p_model − cost ≤ 0`).

`τ_mass`, `τ_cover`, `K_bins` are env-tunable (`ZEUS_ONDEMAND_BIN_MASS_FLOOR`,
`ZEUS_ONDEMAND_BIN_COVER`, `ZEUS_ONDEMAND_MAX_BINS`). Ship fixed defaults first.

WHY THIS IS SAFE vs cross-event triage: it never ranks events against each other on stale
cached prices (the prior design's main false-negative risk). Every event is still evaluated;
only provably-edgeless bins (model mass ≈ 0) skip their book fetch. The decision to skip is
made on the SAME `p_cal` the trade decision uses — zero skew risk.

## Reuse + new functions

**Already-built blocks (commit 8192bb971e):**
- `PolymarketClient.get_orderbook_snapshots(token_ids) -> {token_id: book}` —
  `polymarket_client.py:421`. ONE POST /books; maps by `asset_id` (`:473`), partial-tolerant,
  byte-identical shape to GET /book (`:444-446`). **This is the on-demand book engine.**
- `capture_executable_market_snapshot(..., prefetched_orderbook=...)` —
  `market_scanner.py:2508/2517`, consumes prefetched book at `:2612` via
  `_normalize_prefetched_orderbook`. Unchanged; the post-decision capture path stays as-is.

**#348 typed-edge note (must reconcile):** `BinEdge.entry_price` is now a typed `ExecutionPrice`
(`src/types/market.py:291`) and `entry_quote_evidence` (EQE) feeds `entry_cost_mean` /
`entry_cost_uncertainty` (σ_market) into the bootstrap (`market.py:303-318`, INV-38/39/40).
#348 changed WHAT is done with the fetched price (typed provenance + σ_market sampling), NOT
WHERE the book is fetched — the fetch is still `_buy_entry_price_from_clob` at `evaluator.py:4527/4585`.
So the on-demand gate sits UPSTREAM of the typed-edge construction and is provenance-neutral:
a book from POST /books carries the same `vwmp`/depth provenance as one from GET /book. The
σ_market / EQE path (`_buy_entry_evidence_from_clob`, `:4550`) must receive the SAME prefetched
book so its depth-walk provenance is identical.

**New functions:**
1. `select_predicted_bins(p_cal, bins, *, mass_floor, cover, max_bins) -> set[int]` (evaluator.py)
   — the bin-selection rule above. Pure, unit-testable, no I/O.
2. `prefetch_predicted_bin_books(clob, outcomes, predicted_idx, *, probe_no) -> dict[str, dict]`
   — collects YES (+NO when `probe_native_no_quotes`) token_ids for predicted bins only, calls
   `clob.get_orderbook_snapshots(token_ids)`, returns `{token_id: book}`. Thin wrapper over the
   batch method; mirrors `_prefetch_selected_orderbooks` (`market_scanner.py:3275`) token-collection.
3. **Book-injection plumbing** — `_buy_entry_price_from_clob(clob, token_id)` (`evaluator.py:371`)
   and `_buy_entry_evidence_from_clob` (`evaluator.py:333`) have NO prefetch hook today. Add an
   optional `prefetched_book: dict | None` param to both; when present, use it instead of
   `clob.get_orderbook(token_id)` (`:382`) — back-compatible (None → live fetch), mirroring the
   `capture_executable_market_snapshot(prefetched_orderbook=...)` contract.

### Plumbing options (executor picks; recommendation)
- **RECOMMENDED:** pre-loop `prefetch_predicted_bin_books(...)` → pass the map into the loop →
  `_buy_entry_price_from_clob(clob, token_id, prefetched_book=books.get(token_id))`. Explicit,
  testable, byte-identical provenance, no client mutable state.
- Alt (rejected): per-instance book cache on `PolymarketClient` — hides freshness in mutable
  state, cache-invalidation risk.
- The substrate-prepopulate (capture_executable_market_snapshot) path is NOT needed for the
  edge book; the trade decision's post-decision capture already refreshes market_info fresh.

## Budget / HTTP math (re-verified)

Irreducible REST per bin actually fetched: 1 GET /book (or its share of 1 batch POST /books) +
1 fee (`_fetch_fee_details`/`get_fee_rate`, per-token, NO batch). market_info (`/markets/{cid}`,
11/event, settlement-authority) is consumed in the POST-DECISION capture path
(`cycle_runtime.py:4149` → `market_scanner.py:2593`), NOT in the per-bin edge loop — so it is
charged ONCE per chosen trade, not per evaluated bin.

| | Book calls / event | Calls / cycle (~48 events) | Est wall-clock @ ~1s |
|---|---|---|---|
| **Before** (live, all 11 bins, per-token GET /book + fee, ×YES/NO) | up to 11 GET /book + 11 fee (×2 w/ NO probe) ≈ 22-44 | ~48 × 30+ ≈ **1400+ serial** | hits 360s budget wall → truncate → ~0 trades |
| **After** (predicted bins only, ≤3, ONE batch POST /books + ≤3 fee ×sides) | 1 batch /books + ≤3 fee (×2) ≈ 1 + 6 | ~48 × 7 ≈ **~336** | **~6s** (batch books 1 round-trip/event); fees dominate residual |
| further: batch-fee follow-up | 1 /books + 0 batched-fee | ~48-96 | <5s | (fee endpoint per-token today; future) |

Per-event book leg drops from 11 round-trips to **1 batch /books**; per-event fee leg drops from
11 to ≤3 (predicted bins). All ~48 events now evaluate fully inside the 360s opening_hunt budget.

## Safety (fail-closed guards MUST be preserved)

- **Archived/closed settlement authority** — unchanged. The on-demand gate touches ONLY the
  per-bin orderbook (price/depth) inside the edge loop. The post-decision
  `capture_executable_market_snapshot` (`cycle_runtime.py:4149`) still calls `_fetch_clob_market_info`
  fresh (`market_scanner.py:2593`) and `_assert_clob_identity` (`:2617`) /
  `_build_executable_tradeability_status` (`:2422`). Invariant
  `test_clob_archived_blocks_even_when_gamma_accepts` still fires (market_info never from book).
- **Books are NOT a tradability authority** (`polymarket_client.py:444-446`) — injecting a
  prefetched book changes no authority decision.
- **`executable_mask`** (evaluator.py:3395, gate :4524) still excludes non-executable bins; the
  p_cal gate is an ADDITIONAL skip layered on top, never a relaxation.
- **Per-bin staleness non-blocking** (operator directive): a predicted-bin token absent from the
  POST /books map falls back to `clob.get_orderbook` inside `_buy_entry_price_from_clob` (None →
  live), and a single failing bin must NOT abort the event (mirror `_prefetch_selected_orderbooks`
  best-effort, `market_scanner.py:3304-3308`).
- **σ_market / EQE provenance parity** — `_buy_entry_evidence_from_clob` (`:4550`) MUST get the
  same prefetched book so the depth-walked `entry_cost_uncertainty` (INV-40, market.py:303-318)
  is byte-identical to the live path; otherwise #348's bootstrap CI would differ ON vs OFF.

## Migration / rollout

- **Flag-gated:** `ZEUS_ONDEMAND_PREDICTED_BINS_ONLY` (default OFF first ship). OFF = current
  behavior byte-for-byte (fetch all executable bins live). ON = p_cal-gated predicted-bins +
  batch /books injection.
- **A/B:** for a fixed cycle/decision_time, assert ON produces the SAME EdgeDecisions and the
  SAME typed `BinEdge` (entry_price, σ_market, edge magnitude) as OFF — because skipped bins had
  `p_cal ≈ 0` and could not have produced an edge. Any divergence = a real edge was on a skipped
  bin = τ_mass too high; tune.
- **Background `refresh_executable_market_substrate_snapshots` STAYS** (supplies event/outcome
  list + tradeability identity at cycle_runtime.py:4234). It NO LONGER needs per-bin book
  completeness for the trade decision, so its wall-clock budget
  (`ZEUS_MARKET_DISCOVERY_SNAPSHOT_BUDGET_SECONDS`) can be RELAXED — but FIRST grep all consumers
  of `read_persisted_weather_markets` / `executable_snapshot_id` to confirm none still need
  per-bin book freshness (open question below).

## Test plan (relationship tests FIRST, per Fitz methodology)

Relationship tests (write BEFORE implementation; must be RED first):
1. **Predicted-bin == full-eval parity:** for an event whose `p_cal` mass sits on bins {peak−1,
   peak, peak+1}, `evaluate_candidate(flag ON)` returns the SAME EdgeDecisions AND the same typed
   `BinEdge.entry_price` / `entry_cost_uncertainty` as flag OFF (skipped bins had no edge).
2. **Skip is provably edgeless:** assert no bin with `p_cal[idx] < τ_mass` ever yields
   `should_trade=True` in the OFF run (justifies skipping it ON). RED if any low-mass bin trades.
3. **Bimodal union:** an event with two separated `p_cal` peaks selects BOTH peak windows; both
   get books; neither is skipped.
4. **Batch == per-token book parity:** the book for a predicted token via
   `clob.get_orderbook_snapshots([tok])` is byte-identical (hash, depth) to `clob.get_orderbook(tok)`
   — so `entry_price` provenance + σ_market are unchanged (guards #348 INV-38/40).
5. **Per-bin staleness non-blocking:** predicted-bin token missing from the batch map falls back
   to live GET /book; a single failing predicted bin does NOT abort the event.
6. **Archived guard on on-demand path:** flag ON, CLOB market_info `archived=True` still blocks
   the trade even with a healthy on-demand book and Gamma accepting (re-run
   `test_clob_archived_blocks_even_when_gamma_accepts` semantics through the ON path).
7. **Budget bound:** flag ON, K_bins=3 → book calls/event = 1 batch + ≤3 fee×sides; assert via
   counting fake clob across ~48 events ≤ 360s-equivalent.

Function tests: `select_predicted_bins` (peak window, mass floor, cap, bimodal), env-flag OFF
byte-equivalence, p_cal validity gate still fires before bin selection.

## Risks + open questions (operator decision)

**Top-3 risks:**
1. **τ_mass too high skips a real edge (HIGH).** A bin the model gives small-but-nonzero mass
   could be mispriced enough to bear edge (`edge = p_model − cost`); if τ_mass excludes it, a
   real trade is lost. Mitigation: τ_mass default LOW (0.05) + cover-based contiguous window
   (90%) + bimodal union. Relationship test #2 proves no OFF-run trade sits below τ_mass before
   shipping. OPEN: is 0.05 conservative enough, or gate on `p_cal − cheap_cached_price` instead
   of mass alone? (Mass-only is simpler and skew-free; recommend mass-only first.)
2. **σ_market / EQE provenance divergence ON vs OFF (HIGH — #348 interaction).** If
   `_buy_entry_evidence_from_clob` does NOT receive the same prefetched book as
   `_buy_entry_price_from_clob`, the depth-walked `entry_cost_uncertainty` (INV-40) differs →
   bootstrap CI differs → different edge sizing. Both must read the identical injected book.
   Relationship test #1 + #4 guard this.
3. **Live-entry money-path change (HIGH — needs opus critic).** Threading `prefetched_book` into
   the per-bin loop and gating fetches modifies the path that produces the typed `BinEdge` Kelly
   sizes against. A token_id mis-key in the batch map (`asset_id` mapping, polymarket_client.py:473)
   would feed the WRONG bin's price into edge. YES/NO selection
   (`_selected_token_for_direction`, market_scanner.py:3259) must be re-verified per predicted bin.

**Open questions:**
- Confirm no other consumer of `read_persisted_weather_markets` / `executable_snapshot_id` needs
  per-bin book freshness before relaxing the background refresh budget.
- K_bins cap = 3 vs allow up to 5 for wide/flat model distributions? Budget allows headroom.
- Gate on model mass alone (recommended, skew-free) vs mass × cached-mispricing (catches a
  mispriced low-mass bin but reintroduces stale-cache dependence)? Operator call.

## Flag for opus critic before implementation

Touches the LIVE ENTRY MONEY PATH at the per-bin book loop (`evaluator.py:4506-4591`) that
feeds #348's typed `BinEdge.entry_price` + σ_market bootstrap. Per repo policy
(`feedback_opus_critic_on_architectural_scaffold`), **opus critic dispatch is MANDATORY** on the
executor's implementation, focused on: (1) batch-book token_id keying per YES/NO predicted bin,
(2) σ_market/EQE receiving the identical injected book (INV-38/40 parity), (3) τ_mass not
suppressing a real edge, (4) archived guard preserved on the on-demand path.
