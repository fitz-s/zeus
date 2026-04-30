# Native Multi-Bin buy_no Implementation Spec

Status: implementation handoff evidence.
Date: 2026-04-30.
Scope: native executable multi-bin `buy_no` for Polymarket weather markets.

This document does not authorize live deploy, production DB mutation, config
flips, source-routing changes, schema migration by itself, or strategy
promotion. Source edits still require topology admission, scoped AGENTS reads,
planning-lock evidence where required, focused tests, and closeout evidence.

## First Principles

Polymarket weather families are represented as binary child markets. For a
selected child/bin `i`, `buy_yes` pays if the settlement value is in bin `i`;
`buy_no` pays if the settlement value is not in bin `i`. In a multi-bin family,
this makes the payoff probability complement valid:

- `P_yes_i = P_posterior[i]`
- `P_no_i = 1 - P_yes_i`

That complement is only a payoff/probability transform. It is not an executable
market-price transform. Live multi-bin `buy_no` may trade only when the selected
child market has fresh native NO-token quote evidence. `1 - YES_VWMP` must
never be used as live multi-bin NO entry price, VWMP, fee basis, Kelly price, or
order price.

External API reality supports this boundary:

- Polymarket CLOB order creation uses the selected `tokenID`, price, size, and
  side: https://docs.polymarket.com/developers/CLOB/orders/create-order
- CLOB price and book methods are token based:
  https://docs.polymarket.com/developers/CLOB/prices-get
- Gamma market/event payloads expose outcome labels and CLOB token IDs:
  https://docs.polymarket.com/api-reference/events/list-events

Therefore Zeus must bind `buy_no` execution to the NO token ID and its own book,
not to the YES sibling's price complement.

## Current Repo Breakpoints

- `src/strategy/market_analysis.py::supports_buy_no_edges()` currently blocks
  multi-bin `buy_no` because analysis only receives YES-side VWMP and comments
  correctly reject `1 - YES` as executable NO entry.
- `src/strategy/market_analysis_family_scan.py` uses the same predicate, so
  full-family FDR does not test live-unexecutable multi-bin `buy_no`.
- `src/engine/evaluator.py` builds `p_market` from `o["token_id"]`, currently
  the YES token only.
- `src/execution/executor.py`, `create_execution_intent()`, and executable
  snapshot capture already have the right downstream shape: `buy_no` routes to
  `no_token_id` and snapshot selection can select the NO orderbook.
- Some book readers assume first row is best row. Public/live books must be
  normalized before any native NO rollout.

## Native Quote Model

Add side-aware quote types in the existing market types surface, preferably
`src/types/market.py`, not a new contract module unless implementation pressure
proves it necessary.

Suggested structures:

- `SideQuote`: `token_id`, `outcome_label`, `direction`, `best_bid`,
  `best_bid_size`, `best_ask`, `best_ask_size`, `vwmp`, `vwmp_size_basis`,
  `quote_ts`, `raw_orderbook_hash`, `tick_size`, `min_order_size`, `neg_risk`,
  `fee_rate_bps`, `source`, `staleness_ms`, `liquidity_status`.
- `ExecutableBinQuotes`: `yes: SideQuote | None`, `no: SideQuote | None`, plus
  bin/outcome identity.

A quote is live-executable only when:

- token ID exists and outcome label confirms YES/NO mapping; do not trust array
  order alone;
- token ID matches the selected side, with `no_token_id` for `buy_no`;
- quote is fresh enough for the mode;
- orderbook levels parse as finite `Decimal`s, with prices in bounds and
  positive sizes;
- top book is valid after normalization;
- selected token has authoritative tick size, min order size, fee rate, and
  negative-risk metadata before submit.

Missing, stale, empty, crossed, mismatched, or metadata-incomplete NO quote
evidence means no live multi-bin `buy_no`. Shadow should record a specific
reason, for example `BUY_NO_NATIVE_QUOTE_UNAVAILABLE`,
`BUY_NO_NATIVE_QUOTE_STALE`, `BUY_NO_TOKEN_MAPPING_MISMATCH`, or
`BUY_NO_TAKER_DEPTH_CONSTRAINED`.

Do not require both bid and ask for every shadow observation. For phase-1 live
entries, require executable ask/depth for the intended buy path as a liquidity
guard; log ask-only/no-bid and bid-only/no-ask cases distinctly in shadow rather
than hiding them under a generic unsupported reason.

## Top-Book and Depth Normalization

Before live enablement, create one shared parser/helper for Polymarket
orderbooks and route all evaluator/snapshot/monitor call sites through it.

Required behavior:

- parse prices and sizes as `Decimal`;
- reject non-finite values, non-positive size, out-of-range price, and crossed
  books;
- best bid is max bid price, best ask is min ask price;
- aggregate same-price size at the best level;
- preserve raw hash/timestamp/source;
- compute VWMP by walking visible depth for the intended side and requested
  size, or return an explicit depth-constrained status;
- never assume returned arrays are sorted, even if a docs example prints
  `book.bids[0]`.

This helper is not overbuild; without it Zeus can price the wrong level and make
every native NO contract unsafe.

## Decision Quote vs Execution Snapshot Quote

The evaluator quote is a decision candidate input. The executable snapshot quote
is the final pre-submit pricing authority.

Flow:

1. Evaluator ingests `ExecutableBinQuotes` for every child market.
2. FDR and edge construction use decision-time native side quotes to decide
   which hypotheses are worth considering.
3. After a decision is selected, capture an executable snapshot for the selected
   token and side.
4. Validate token/outcome identity against the decision: `buy_no` requires
   snapshot token equals `no_token_id` and outcome label `NO`.
5. Recompute from the snapshot quote before `create_execution_intent()` or venue
   submission: `p_market`, `entry_price`, and `vwmp` in held-side/native space;
   fee-adjusted Kelly and final size; limit price after tick rounding;
   min-order and visible-depth constraints; family cap/projection with the final
   proposed size.
6. Proceed only if the repriced edge still passes all conservative thresholds.

Raw orderbook hash drift between evaluator quote and snapshot is not by itself a
reject. It is evidence that repricing is required. Reject only if repricing
fails identity, freshness, depth, metadata, min-size/tick, risk, family-cap, or
edge thresholds. Use explicit reasons such as `BUY_NO_REPRICE_REJECTED`,
`BUY_NO_SNAPSHOT_TOKEN_MISMATCH`, or `BUY_NO_NATIVE_SNAPSHOT_UNAVAILABLE`.

No first live phase may defer repricing as future work. If repricing is not
implemented, native multi-bin `buy_no` remains shadow-only.

## Edge and FDR Contract

The full-family FDR universe must equal the executable hypothesis universe:

- always test `buy_yes` when the native YES quote is executable;
- test `buy_no` only when native NO quote evidence is executable;
- every FDR-selected row must materialize as a corresponding `BinEdge`;
- no selected hypothesis may disappear during
  `filtered = [edge for edge in edges if key in selected_edge_keys]`.

For a multi-bin `buy_no` `BinEdge`:

- `direction = "buy_no"`;
- `p_model = 1 - p_cal_yes[i]`;
- `p_posterior = 1 - p_posterior_yes[i]`;
- `p_market`, `entry_price`, and `vwmp` are native NO-side quote values;
- `selected_token_id = no_token_id`;
- `selected_outcome_label = "NO"`;
- quote evidence ties the decision quote to the NO token/orderbook
  hash/timestamp;
- final intent/submission values are repriced from the executable snapshot, not
  blindly copied from the decision quote.

Probability stays distribution-native; execution price stays held-side/native.
These two spaces must remain explicitly separated.

## Minimal Persistence Contract

Phase 1 should not add quote facts to `Position` or `position_current` just for
audit neatness. Quote provenance belongs first in immutable
decision/snapshot/event/venue-command evidence.

Required persisted provenance for native multi-bin `buy_no`:

- decision quote evidence: selected side, selected token, outcome label, quote
  timestamp/hash, native entry/VWMP basis;
- executable snapshot evidence: selected side, selected token, outcome label,
  raw orderbook hash, top bid/ask/depth, tick size, min order size, neg-risk,
  fee rate, quote timestamp;
- reprice result: old decision quote hash, snapshot quote hash, repriced
  entry/VWMP, repriced Kelly/size/limit, pass/reject reason;
- venue command/envelope metadata sufficient to prove the submitted token and
  price came from the selected snapshot.

Acceptable first-phase storage options:

- extend existing executable snapshot/event/envelope payloads with nullable
  selected-quote fields; and
- add a narrow nullable `native_buy_no_family_key` to `venue_commands` only if
  restart-safe cap queries cannot be implemented from existing command/event
  metadata.

Avoid `position_current` schema changes in the first packet unless a concrete
runtime reader proves it cannot derive the needed family key or quote evidence
from command/event/snapshot history. For active positions missing native-buy-no
family metadata, fail closed for same city/date/metric `buy_no` cap checks
rather than allowing an unbounded duplicate.

## Family Exposure Contract

One-per-family is a rollout guard, not a durable payoff law. Multiple `buy_no`
positions in the same exhaustive family are correlated but can be economically
valid; they are not mutually exclusive in P&L.

Initial live canary:

- feature config: `max_native_multibin_buy_no_per_family = 1`;
- shadow evaluates and logs all native `buy_no` candidates;
- live records which candidates were suppressed by the family cap;
- later graduation replaces the count cap with an exposure-budget cap after
  replay/live evidence.

Family key:

- preferred: stable family market ID from market metadata;
- fallback: `familyhash:` plus the first 16 hex chars of sha256 over canonical
  JSON `{city, target_date, temperature_metric, sorted_child_condition_or_outcome_ids}`;
- explicitly exclude cycle time, quote hash, decision snapshot ID, selected
  range, and selected token from the family key.

## Feature Flags and Rollout

Add config helpers, not scattered raw env checks:

- `NATIVE_MULTIBIN_BUY_NO_SHADOW=false` by default;
- `NATIVE_MULTIBIN_BUY_NO_LIVE=false` by default;
- live implies shadow;
- non-boolean flag values fail closed at startup/config load;
- live enablement additionally requires top-book helper, side-aware evaluator
  quotes, snapshot repricing, selected-token envelope proof, monitor/exit token
  checks, and acceptance tests.

Disabled mode should preserve current behavior: binary `buy_no` may keep its
existing complement semantics; live multi-bin `buy_no` remains excluded or
reported unsupported.

## Execution API Contract

Existing execution routing can remain: `buy_no -> no_token_id`. The
implementation must prove the whole path uses the selected side token:

- `create_execution_intent(direction="buy_no")` emits selected token equal to
  `no_token_id`;
- fee lookup, tick size, min order, negative risk, Kelly size, and limit price
  use the NO snapshot;
- venue envelope and pre-submit gate verify selected token/outcome/price
  provenance;
- command metadata uses enum-derived values such as
  `IntentKind.ENTRY.value == "ENTRY"`; do not invent lowercase intent-kind
  strings;
- command state checks must use existing enum state names, not prose
  approximations.

Thin-book rule: if the intended size exceeds visible depth at the executable
price/limit, cap size to executable visible depth only when all risk/min-order
checks still pass; otherwise reject as `BUY_NO_TAKER_DEPTH_CONSTRAINED`. Do not
assume top price is executable for arbitrary size.

## Monitor, Exit, and Settlement

Monitor/exit code must use the held token for current price and executable exit
evaluation:

- `buy_yes` positions use YES token;
- `buy_no` positions use `no_token_id`;
- any YES-space transform used for model CI/explainability must stay internal
  and must not be stored or passed as executable price.

Exit snapshot capture must select the held NO token for `buy_no`, require
`outcome_label="NO"`, and avoid broad fallback matching that can pick the YES
sibling.

Settlement is unchanged semantically: selected child YES resolves true if
settlement is in the bin; `buy_no` wins when that selected child YES resolves
false. Harvester/redemption must continue redeeming the held NO token.

## Acceptance Tests

Minimum tests before the live flag can be true:

1. Multi-bin shoulder fixture with overpriced YES and native NO ask/depth
   creates a `buy_no` `BinEdge` with native NO `p_market`, `entry_price`, and
   `vwmp`.
2. Same fixture without native NO orderbook excludes `buy_no` from the FDR
   universe or emits explicit `BUY_NO_NATIVE_QUOTE_UNAVAILABLE`; no complement
   fallback.
3. Full-family FDR selects a multi-bin `buy_no`, and evaluator materializes the
   same edge key.
4. Regression forbids `1 - p_market_yes` as live multi-bin NO entry/VWMP.
5. `create_execution_intent()` for `buy_no` selects `no_token_id`.
6. Executable snapshot row has `outcome_label="NO"`, selected token
   `no_token_id`, and NO orderbook top bid/ask/hash.
7. Snapshot hash drift triggers repricing; drift alone does not reject.
8. Repricing proceeds when edge survives and rejects with explicit reason when
   edge, depth, tick, min-size, fee, or token identity fails.
9. Unsorted orderbooks produce max-bid/min-ask top book with aggregated
   best-level size.
10. Fee, tick size, min order, and neg-risk are selected from the NO token
    snapshot for `buy_no`.
11. Monitor and exit use `no_token_id` for active `buy_no` positions.
12. Settlement/harvester tests prove selected-bin false means `buy_no` win and
    redemption uses NO token.
13. Shadow logs all native `buy_no` candidates and separately logs live
    family-cap suppression.
14. Feature flags keep live disabled by default and reject invalid flag values.

## Phased Implementation

Phase 0: quote substrate.

- Add side-aware quote types and shared orderbook normalization helper.
- Fix first-row top-book assumptions in evaluator/snapshot/monitor paths.
- Add top-book and no-complement regression tests.

Phase 1: decision universe.

- Thread YES/NO quotes into evaluator and `MarketAnalysis`.
- Make `supports_buy_no_edges()` depend on native NO quote availability for the
  selected bin.
- Align `find_edges()` and full-family FDR to one executable hypothesis
  universe.
- Keep live disabled; record unsupported/unavailable reasons in shadow.

Phase 2: executable snapshot repricing.

- Capture selected-side snapshot post-decision.
- Reprice edge, Kelly, size, tick/min-order, limit, and family cap from the
  snapshot.
- Persist decision quote, snapshot quote, and reprice result in
  snapshot/event/envelope evidence.
- Keep `position_current` unchanged unless a runtime reader proves a narrow
  need.

Phase 3: monitor/exit/settlement closure.

- Tighten monitor and exit snapshot token matching for `buy_no`.
- Add settlement and redemption regression tests for native multi-bin NO
  positions.

Phase 4: live canary.

- Enable `NATIVE_MULTIBIN_BUY_NO_SHADOW` first.
- After shadow evidence, allow `NATIVE_MULTIBIN_BUY_NO_LIVE` with
  `max_native_multibin_buy_no_per_family=1`.
- Record all suppressed candidates and graduate only with replay/live exposure
  evidence.

## Non-Goals

- No live deployment authorization.
- No production DB mutation.
- No settlement-source change.
- No durable strategy promotion claim.
- No calibration/tail-alpha P&L validation claim.
- No blanket schema expansion for audit neatness.
- No guarantee that native NO quotes will always be independent of YES
  complements; even mirrored books must be consumed as native executable token
  evidence.

## Implementation Preconditions

Before source edits, run topology navigation with typed intent
`r3 strategy reachability selection parity implementation` for the actual
changed files. If work expands into state schema, control, risk, live venue
submission, production DB mutation, or CLOB cutover, stop and open the
appropriate packet/planning-lock path.

Planning-lock must pass for the final changed file set. Map maintenance must
pass for any new/renamed docs/source/test file. Focused tests must include
FDR/edge tests, executable snapshot tests, monitor/exit tests, settlement tests,
and feature-flag/config tests touched by the implementation.
