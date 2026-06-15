# Stage 7b — family_book implementation report

Created: 2026-06-14
Authority basis: docs/rebuild/consult_build_spec.md (Create src/execution/family_book.py, lines 619-650; Stage 7 block 1146-1164); docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (GREENFIELD; prefer Actual-live).

## What was built

The executable family route surface over all sibling markets (Stage 7b). It is the
EXECUTABLE companion to the structural `OutcomeSpace` (Omega) and the economic
`Instrument` payoff vectors (Stage 7a): per sibling bin it carries the four NATIVE
order-book ladders (yes/no asks/bids) that the leaf `executable_cost` walker prices a
route leg against, so the Stage 7 route comparisons (direct NO vs synthetic sibling-YES
basket, full-YES basket arb, etc.) have ONE coherent, captured-at-an-instant surface to
read every sibling's depth from.

**Native cost stays leaf.** `executable_cost.py` (the live native-ladder walker) is
REUSED UNCHANGED — no live file was edited. `family_book` does not re-implement book
walking and computes no cost itself; each `MarketBook` reassembles a leaf
`NativeQuoteBook` (via `native_quote_book()`) and hands it to `executable_cost`. The
midpoint / last-trade / NO-complement bans the leaf enforces therefore still apply to
every family route leg — there is no family-level cost path that could bypass them.

## Files written (NEW ONLY — no live file touched)

- `src/execution/family_book.py`
- `tests/execution/test_family_book.py`

### Symbols in src/execution/family_book.py

Dataclasses (spec EXACT field names, all `frozen=True`):

- `ExecutableLadder` (spec 623-629): `levels: tuple[QuoteLevel, ...]`, `side: Literal["ask","bid"]`, `fee_rate: float`, `min_tick_size: Decimal`, `min_order_size: Decimal`.
- `MarketBook` (spec 631-642): `condition_id`, `bin_id`, `yes_token_id`, `no_token_id`, `yes_asks`, `yes_bids`, `no_asks`, `no_bids` (each an `ExecutableLadder`), `neg_risk: bool`.
- `FamilyBook` (spec 643-650): `omega: OutcomeSpace`, `markets: Mapping[str, MarketBook]`, `captured_at_utc: datetime`, `book_hash: str`, `complete_book: bool`.

Functions / methods:

- `MarketBook.native_quote_book()` — reassembles the leaf `NativeQuoteBook` (all four sides + tick / min-order / fee / per-market neg_risk) so the leaf walker prices a leg. Keeps native cost leaf.
- `MarketBook.__post_init__` — typing invariant: an asks slot must hold an `"ask"` ladder, a bids slot a `"bid"` ladder (a mis-slotted ladder mis-prices a buy/sell). Shape guarantee, not a cost gate.
- `FamilyBook.__post_init__` — coerces `captured_at_utc` to tz-aware UTC; refuses a market keyed under a bin_id it does not declare; refuses a stranger market (bin_id not in Omega); and forces `complete_book` to equal the structural set-equality (a passed value that disagrees is refused).
- `FamilyBook.missing_bin_ids()` — Omega bin ids with no MarketBook.
- `build_family_book(*, omega, markets, captured_at_utc)` — PREFERRED constructor; derives `complete_book` (the structural set-equality) and `book_hash` so neither can disagree with the membership.
- `market_book_from_snapshot(snapshot, *, bin_id)` — builds one sibling `MarketBook` from an `ExecutableMarketSnapshot`; the four ladders come from the leaf `quote_book_from_executable_snapshot`; `neg_risk` is threaded from `snapshot.neg_risk`.
- `family_book_from_snapshots(*, omega, snapshots_by_bin_id, captured_at_utc)` — assembles a FamilyBook from per-sibling snapshots, each carrying its own neg_risk.
- `compute_book_hash(*, omega, markets, captured_at_utc)` — deterministic sha256 over the whole captured surface (Omega topology hash, capture instant, and per sibling in stable bin_id order: condition/bin/token ids, neg_risk, all four ladders' (price,size) levels + tick/order/fee).
- `FamilyBookError` — fail-closed exception.
- `_require_utc(value)` — tz-aware UTC coercion / fail-closed.

## Spec lines implemented

- 619-650 — the three dataclasses with EXACT field names (ExecutableLadder 623-629, MarketBook 631-642, FamilyBook 643-650).
- 1146-1164 (Stage 7) — the executable family route surface over all sibling markets, native cost stays leaf; neg_risk per market is the flag the NO route rule branches on.
- 651 (spec note) — "executable_cost should remain a native ladder walker … leaf-only": honored by REUSE (composition over it), with zero edits.

## The corrected transformation (operator law: bad output mathematically impossible)

Two defects the spec replaces are made UNCONSTRUCTABLE, not caught by a detector:

1. **`complete_book` is a STRUCTURAL consequence, not a flag.** It is computed ONCE as
   the set-equality `frozenset(markets keyed by bin_id) == frozenset(omega bin ids)` over
   the FULL Omega (including the non-executable tail/shoulder bins kept so the partition
   is complete). A family missing even one sibling — or covering only the tradeable
   middle — cannot be `complete_book=True` because the set equality cannot hold. The
   constructor re-derives it and refuses any passed value that disagrees, so a hardcoded
   `True` cannot stand. There is no "incomplete-but-flagged-complete" state to gate.

2. **`neg_risk` is threaded PER MARKET, never a family-wide scalar.** Each `MarketBook`
   carries its own `neg_risk` from its own `ExecutableMarketSnapshot.neg_risk`, and the
   leaf `NativeQuoteBook` it hands the walker carries that same per-market flag. There is
   no single family-level neg_risk field that could collapse the per-sibling distinction
   the Stage 7 NO route rule needs ("if negRisk=False only direct NO; if negRisk=True
   compare direct NO vs synthetic sibling-YES basket").

No gate/cap/clamp/haircut/sanity-check/shadow-flag was added: the carrier holds captured
depth, the leaf prices it, and the two structural facts (coverage, per-market neg_risk)
are derived at construction.

## Drift resolved (recorded per operator law)

- **GREENFIELD — no live edits.** The spec's Stage 7 file list says "modify
  executable_cost.py to stay leaf-only." Per the drift ledger that modification is
  deferred to Stage 11 and `executable_cost` is ALREADY leaf-only, so it was REUSED
  unchanged (imported `QuoteLevel`, `NativeQuoteBook`, `executable_cost`,
  `quote_book_from_executable_snapshot`). Zero live files were touched. Verified by the
  money-path / live_inference suites (331 passed).

- **`ExecutableLadder.levels` element type.** The spec leaves the level element type
  implicit. Resolved toward the live type: `levels: tuple[QuoteLevel, ...]` where
  `QuoteLevel` is the live `executable_cost.QuoteLevel` (`price: Decimal`, `size:
  Decimal`) — the SAME primitive the leaf walker consumes, so a family route leg walks
  these levels through the leaf with no conversion.

- **Per-market venue facts (tick / min-order / fee) live on the ladder.** The spec's
  `ExecutableLadder` carries `fee_rate`, `min_tick_size`, `min_order_size` per side; the
  live leaf `NativeQuoteBook` carries them once per market. Resolved by carrying them on
  the ladder (spec field names) and reconstructing the single-market `NativeQuoteBook`
  from the `yes_asks` ladder's copies in `native_quote_book()` (the four ladders of one
  market share one venue's tick/min-order/fee). No drift in behavior — the leaf reads the
  same values.

- **neg_risk source.** Threaded from `ExecutableMarketSnapshot.neg_risk` (the live
  per-market venue fact), matching the leaf's existing `neg_risk=snapshot.neg_risk`
  passthrough in `quote_book_from_executable_snapshot`.

- **No `OutcomeSpace.index`.** Like Stage 7a, the live `OutcomeSpace` exposes `bins` (a
  tuple of `OutcomeBin`), not an `index` method; bin membership / coverage is derived from
  `b.bin_id for b in omega.bins`.

## Tests added (RED-on-revert, spec-named)

- `tests/execution/test_family_book.py::test_family_book_complete_requires_all_sibling_markets`
- `tests/execution/test_family_book.py::test_family_book_threads_neg_risk_per_market`

Plus supporting carrier/leaf-reuse invariants:
`test_market_book_native_quote_book_is_leaf_walkable`,
`test_market_book_rejects_misdeclared_ladder_side`,
`test_family_book_hash_is_deterministic_and_sensitive`,
`test_family_book_requires_utc_capture_instant`.

### RED-on-revert verification (both spec tests proven to fail on revert, then restored)

- complete_book test: reverting `complete_book` to the EXECUTABLE-subset denominator (drops the tail bins) made the test fail with `assert True is False` ("covering only the executable middle is NOT a complete book"). Restored.
- neg_risk test: reverting to a single family-wide scalar neg_risk (read off the first snapshot, applied to all) made the test fail with `MarketBook[b21] neg_risk must be threaded from its OWN snapshot (False)`. Restored.

## Full test results

family_book suite:

```
......                                                                   [100%]
6 passed in 0.82s
```

Money-path unaffected:

```
........................................................................ [ 21%]
........................................................................ [ 43%]
........................................................................ [ 65%]
........................................................................ [ 87%]
...........................................                              [100%]
331 passed in 4.30s
```

(tests/money_path + tests/strategy/live_inference — confirms zero live-file impact and
the leaf `executable_cost` reused unchanged.)
