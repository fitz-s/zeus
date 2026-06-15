# Stage 9 — `market_coherence` implementation report

Created: 2026-06-14
Authority basis: `docs/rebuild/consult_build_spec.md` lines 804-852 (the
`Create src/decision/market_coherence.py` block) reconciled against
`docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md` (GREENFIELD — no live edits).

## What this is

The typed **calibration-incident report** that REPLACES the one-sided market-anchor NO
`q_lcb` cap (`src/strategy/live_inference/market_anchor.py`). The old cap silently
LOWERED the tradable NO lower bound toward an alpha-blended market belief — a hidden q
mutation. Stage 9 does the opposite: it builds the market's own implied family
distribution from the book, compares it to the model joint q in **logit space**, and
emits a typed report whose `status` the decision layer reads. When the disagreement is
large AND the book is deep enough to trust AND nothing licenses the model's superiority,
it returns `INCOHERENT_BLOCK_LIVE` — a live-money block recorded as an incident, never a
number quietly changed in q.

The Tokyo `q=0.47` vs deep `ask=0.001` case dies HERE, before trade score.

## Files written

| File | Symbols |
|---|---|
| `src/decision/market_coherence.py` | `MarketImpliedQ` (dataclass, spec 808-815), `MarketCoherenceReport` (dataclass, spec 817-824), `assess_market_coherence` (the full algorithm 826-851), `build_market_implied_q` (steps 1-3, 828-832), `project_to_simplex` (step 2, line 830), `logit_gap` (spec line 840), `MARKET_IMPLIED_Q_BASIS`, `LOGIT_GAP_BLOCK_THRESHOLD` (=2.5, spec 848), `LicensedModelSuperiority` type, internal `_read_bin_market` / `_BinMarket` / `_clamp_prob` / `_logit` / `_kl` |
| `tests/decision/test_market_coherence.py` | the 3 spec-named RED-on-revert tests + 5 supporting contract tests |

No live file was touched. `src/decision/` already existed (created by Stage 0's
`decision_receipt.py`), so no new package needed.

## Spec lines implemented (EXACT field names, frozen dataclasses)

- **`MarketImpliedQ` (808-815):** `q: np.ndarray`, `basis:
  Literal["DEFRICTIONED_FAMILY_BOOK_MIDPOINT_PROJECTION_V1"]`, `depth_score: float`,
  `spread_score: float`, `projection_error: float`, `book_hash: str`.
- **`MarketCoherenceReport` (817-824):** `status:
  Literal["COHERENT","INCOHERENT_BLOCK_LIVE","INSUFFICIENT_MARKET_DEPTH","NO_MARKET_Q"]`,
  `max_abs_logit_gap: float`, `kl_model_to_market: float`, `kl_market_to_model: float`,
  `offending_bins: tuple[str, ...]`, `reason: str`.
- **Algorithm (826-851):**
  1. De-frictioned implied family distribution from the book — per-sibling YES
     **midpoint** `(best_yes_bid + best_yes_ask)/2` (the bid-ask spread IS the friction;
     the midpoint removes it).
  2. Euclidean **simplex projection** (Duchi sort-based) onto `{q>=0, Σq=1}` — removes
     the book over/under-round friction; `projection_error` is the L2 move.
  3. **Depth/spread gate** before USING the market q (`depth_score >= min_depth AND
     spread_score <= max_spread`). Fails → `INSUFFICIENT_MARKET_DEPTH`, no block.
  4. Per-candidate `logit_gap_i = abs(logit(clamp(q_model_i)) - logit(clamp(q_market_i)))`
     (line 840).
  5. Block iff `depth_score >= min_depth AND spread_score <= max_spread AND logit_gap_i >=
     2.5 AND not licensed_model_superiority_class(case, bin_i)` (lines 846-849).

## Operator-law compliance (corrected transform, not a detector/cap)

- The report **is** the spec transformation (a typed calibration incident), not a
  bolted-on gate catching a bad value behind a still-broken transform. The model q is
  **never mutated** — proven by `test_tokyo_..._blocks_before_scoring`, which snapshots
  `jq.q_by_bin_id` and asserts it is identical after assessment.
- The block is made impossible-at-source: the incoherent candidate never reaches scoring
  because `status == INCOHERENT_BLOCK_LIVE` is the decision-layer contract. Same shape as
  `family_book.complete_book` / `joint_q`'s single normalization.
- The depth precondition runs FIRST, so an illiquid/absent market can NEVER fabricate a
  block (`INSUFFICIENT_MARKET_DEPTH` / `NO_MARKET_Q` emit no offending bins).
- No flag/cap/clamp/haircut/shadow on a value. The only clamps are the **logit-domain
  ε-clamp** (so `logit(0.001)` is finite — a math primitive that preserves the incident
  ordering, not a value cap) and the simplex projection (the spec's required transform).
- The one degenerate branch in `project_to_simplex` was replaced (post SLOP warning) with
  an **assertion stating the projection's totality invariant** (`cond[0]` is always
  `1 > 0` for any finite vector), not a substitute-output fallback.

## Drift resolved

The module-specific drift line was **GREENFIELD — no live edits**, honored fully (new
files only). Beyond that, two reconciliations against live types:

1. **De-frictioned market q source = `FamilyBook`, not a bare price.** The spec prose says
   "de-frictioned family-implied market q from the book." Live `FamilyBook`
   (`src/execution/family_book.py`) carries per-sibling `MarketBook` native YES bid/ask
   **ladders** (`yes_asks` / `yes_bids` as `ExecutableLadder`s of `QuoteLevel`s), keyed by
   `bin_id`, aligned to the same `OutcomeSpace` (Omega) as `JointQ`. Resolved by reading
   the YES midpoint per bin off the live ladders and aligning the implied q 1:1 to
   `omega.bins` by `bin_id` — exactly the alignment `JointQ.q` uses. No new type invented.

2. **Spec's "~6.8 logit gap" is the RAW-pair figure; the report gap is ~5.1.** The spec
   illustrates the Tokyo incident as `logit(0.47) - logit(0.001) ≈ 6.8`. That is the gap
   on the un-projected pair (asserted directly in `test_logit_gap_tokyo_is_about_6_8`).
   The realized report gap over the full 11-bin Omega is ~5.1, because the simplex
   projection nudges the 0.001 market value up slightly across the partition and the model
   0.47 is its normalized mass. Both are large, unambiguous incidents far above the 2.5
   threshold — the block fires identically. The test asserts `>= 2.5` and `> 4.5` (the
   projected reality) rather than the raw `> 6.0`, with the raw 6.8 figure pinned in a
   separate primitive test. This is a presentation reconciliation, not a behavior change:
   the incident still dies before scoring.

## RED-on-revert proof

Each spec-named test was proven to fail when its corrected transform is reverted to the
broken behavior, then pass again when restored:

- **`test_tokyo_q_047_vs_deep_ask_0001_blocks_before_scoring`** — reverting the typed
  block to the old silent one-sided q haircut (never append to `offending_bins`) → FAILED.
- **`test_insufficient_depth_does_not_fabricate_market_gate`** — dropping the depth/spread
  precondition (always "sufficient", logit comparison runs on a thin book) → FAILED (thin
  book fabricates `INCOHERENT_BLOCK_LIVE`).
- **`test_licensed_model_superiority_class_can_override_with_receipt`** — removing the
  `not licensed_model_superiority(...)` guard (block fires unconditionally) → FAILED.

All three reverted runs failed on the intended assertion; the module was restored
byte-identical and all 8 tests pass.

## Test results

`tests/decision/test_market_coherence.py` — **8 passed in 0.90s**:

- `test_logit_gap_tokyo_is_about_6_8` (raw ~6.8 figure pinned)
- `test_project_to_simplex_is_a_true_projection`
- `test_build_market_implied_q_defrictions_to_yes_midpoint`
- `test_tokyo_q_047_vs_deep_ask_0001_blocks_before_scoring` (SPEC)
- `test_insufficient_depth_does_not_fabricate_market_gate` (SPEC)
- `test_licensed_model_superiority_class_can_override_with_receipt` (SPEC)
- `test_no_two_sided_yes_quotes_yields_no_market_q`
- `test_model_agreeing_with_deep_market_is_coherent`

Money-path regression — `tests/money_path tests/strategy/live_inference` — **331 passed in
4.05s** (greenfield module wires nothing live; no live behavior changed).
