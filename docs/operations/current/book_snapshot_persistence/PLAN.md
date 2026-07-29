# book_snapshot_persistence -- Plan

Date: 2026-07-29
Branch: `claude/book-snapshot-persist`
Status: active

## Problem

Every decision cycle, `FamilyDecisionEngine.decide()` assembles the full
executable family order-book ladder (`FamilyBook` -- one `MarketBook` per
sibling, all four native ladders) to price routes and build the de-frictioned
market-implied q. That object is carried on `FamilyDecision.family_book` and
then discarded once the cycle's trade/no-trade call is made. Nothing persists
the decision-time book. This is the data prerequisite for the campaign's
center-evidence work (market-implied center vs our posterior mu) -- without a
durable ladder history there is nothing to compare our forecast against over
time.

This slice adds one new append-only, dedup-by-hash table
(`family_book_snapshots`) and a single fail-soft capture call in the event
reactor so every decided family's book lands durably, at effectively zero
marginal write volume (see "Row-rate math" below).

## Scout map (verified against worktree HEAD `9c9a01819`)

- `FamilyBook` (`src/execution/family_book.py:214`): `omega` (`OutcomeSpace`),
  `markets: Mapping[bin_id, MarketBook]`, `captured_at_utc`, `book_hash`,
  `complete_book`. `complete_book` is STRUCTURAL (set-equality of
  `markets.keys()` vs `omega.bins`) -- never a free-standing flag.
- `MarketBook` (`:150`): `condition_id`, `bin_id`, `yes_token_id`,
  `no_token_id`, `neg_risk`, plus the four `ExecutableLadder` sides
  (`yes_asks`/`yes_bids`/`no_asks`/`no_bids`). `ExecutableLadder.levels` is a
  tuple of `QuoteLevel(price: Decimal, size: Decimal)`, **best-first**.
- `OutcomeBin` (`src/probability/outcome_space.py:46`): `bin_id`,
  `lower_native`/`upper_native` (`float | None` -- `None` marks an open
  shoulder edge), `executable`. Bounds are in the family's **native
  settlement unit**, which is `"C"` or `"F"` depending on
  `EventResolution.measurement_unit` (`src/probability/event_resolution.py:71`)
  -- NOT always Celsius. See "Unit caveat" below.
- `FamilyDecision` (`src/decision/family_decision_engine.py:608`):
  `family_book: Optional[FamilyBook]` (`None` on the ineligible/no-q path),
  `predictive: PredictiveDistribution` (`mu_native`, `sigma_native` --
  already-computed posterior center/width, no extra query needed).
  Construction site: `decide()` builds `family_book` at `:883-887` and
  populates the returned `FamilyDecision` at `:1108`.
- Hook site: `src/engine/event_reactor_adapter.py`, inside
  `_build_event_bound_no_submit_receipt_core`, in the `elif _spine_eligible_event:`
  / nonempty-proofs `else:` branch (opened ~`:13733`). `_spine_fact_decision`
  is bound (to a `FamilyDecision` or `None`) by every path through that
  branch before reaching the two sibling `if prepare_global_auction: /
  elif global_actuation is not None: / else:` blocks at `:13862-13890`. The
  `else` arm there is the only one that calls
  `_record_qkernel_selection_family_facts`; the other two arms SKIP it. The
  capture call is placed **before** `:13862` (once, unconditionally within
  this branch) so it fires on every one of the three arms, not just the
  `else`. `family` (`EventBoundCandidateFamily`, bound at `:13489`,
  fields `family_id`/`city`/`target_date`/`metric`), `decision_time`
  (tz-aware UTC by `:13132`), `event.causal_snapshot_id`, and `trade_conn`
  are all in scope at that point.
- Confirmed: `_spine_fact_decision` is NOT defined outside this branch (the
  `deterministic_global_proofs is not None` sibling branch and the
  `_pre_day0_low_block_reason` / empty-proofs early-outs never reach
  `:13862`), so the capture call only ever needs to guard against
  `_spine_fact_decision is None` or `.family_book is None`, not against the
  name being unbound.

## Table home: TRADE, not WORLD

`family_book_snapshots` is TRADE-owned:

- It is executable-market substrate, the same class as
  `executable_market_snapshots` and `book_hash_transitions` (both trade-owned
  per `architecture/db_table_ownership.yaml`) and matches the standing note
  "executable snapshots live in trades DB" -- the world DB copy of that
  concept has historically been an empty shadow.
- The hook already holds `trade_conn` with no ATTACH. `predictive.mu_native`
  /`sigma_native` are already-loaded fields on the in-memory `FamilyDecision`
  -- no world/forecasts read is needed to populate any column. A trade-owned
  table means the writer is a single-connection `INSERT OR IGNORE`, so
  **INV-37 (no independent-connection cross-DB writes) does not enter into
  it at all** -- there is no cross-DB write here, ATTACH-mediated or
  otherwise. Had this been world-owned, every write would need the
  ATTACH+SAVEPOINT ceremony `log_selection_family_fact` uses at
  `event_reactor_adapter.py:9653,9686` for no reason, since nothing about
  the payload requires a world-side read or write.

## Table design

```sql
CREATE TABLE family_book_snapshots (
    snapshot_id         TEXT PRIMARY KEY,
    family_id           TEXT NOT NULL,
    city                TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    decision_time       TEXT NOT NULL,
    captured_at_utc     TEXT NOT NULL,
    book_hash           TEXT NOT NULL,
    complete_book        INTEGER NOT NULL,
    ladder_json         TEXT NOT NULL,
    market_center_c     REAL,
    our_mu_c            REAL,
    our_sigma_c         REAL,
    decision_snapshot_id TEXT,
    schema_version      INTEGER NOT NULL
)
```

- `snapshot_id` = `sha256(family_id|book_hash|decision_time_utc_iso)` --
  deterministic identity for this exact (family, book, decision instant)
  triple, but NOT the dedup key.
- Dedup key: `UNIQUE INDEX (family_id, book_hash)`. Same pattern as
  `observation_prints` (`CREATE UNIQUE INDEX` + app-level `INSERT OR
  IGNORE`, not a DDL-level `ON CONFLICT` clause on the index -- SQLite
  doesn't support conflict clauses on `CREATE INDEX`, only on table-level
  constraints, and the codebase's proven convention is the index+`INSERT OR
  IGNORE` pair). A book re-decided across cycles with an unchanged
  `book_hash` is a free no-op -- this is the entire volume control (see
  row-rate math below). Because the dedup key excludes `decision_time`, a
  re-decided identical book computes a *different* `snapshot_id` internally
  but that value is simply never written (the row already in the table
  keeps its original `snapshot_id`).
- Append-only: `BEFORE UPDATE`/`BEFORE DELETE` triggers `RAISE(ABORT, ...)`,
  verbatim structure of `observation_prints_schema.py`.
- Extra index: `(family_id, decision_time)` -- the query shape the
  center-evidence campaign needs ("book history for this family over time");
  cheap, not speculative.

### Estimator: `market_center_c`

Simple price-weighted midpoint over the omega bins, computed only from data
already inside the captured `FamilyBook` (no ladder-walk, no simplex
projection -- this is evidence, not the de-frictioned market q Stage 9
already builds):

- `NULL` when `family_book.complete_book` is `False` -- a partial ladder
  cannot honestly imply a family-wide center.
- Otherwise, per omega bin with a two-sided best YES quote (`market.yes_asks`
  and `market.yes_bids` both non-empty): `weight = (best_yes_ask.price +
  best_yes_bid.price) / 2` (de-frictioned YES mid, same construction Stage 9
  already uses at `market_coherence.py:_read_bin_market`); `rep_native` =
  bin midpoint `(lower+upper)/2` for an interior bin, or the single defined
  boundary for an open shoulder edge (`lower_native` for open-high, i.e.
  "X or higher"; `upper_native` for open-low, i.e. "X or below").
  `market_center_c = sum(weight_i * rep_native_i) / sum(weight_i)`.
- `NULL` if no bin has both a two-sided quote and parseable bounds (all
  weight is zero) -- an illiquid complete book still degrades to NULL rather
  than a fabricated center.

### Unit caveat (documented, not "fixed")

`market_center_c` / `our_mu_c` / `our_sigma_c` are stored in the family's
**native settlement unit** exactly as `OutcomeBin.lower_native/upper_native`
and `PredictiveDistribution.mu_native/sigma_native` already are -- `"C"` for
some cities, `"F"` for others, per `EventResolution.measurement_unit`. The
column name suffix `_c` names the field ("center"/"mu"/"sigma"), it does not
assert Celsius. No unit-conversion machinery is added here: converting would
be new machinery for a distinction the campaign's evidence work can already
make by joining back to `EventResolution` per family, and fabricating a
silent C-only convention would be the "name that lies" failure mode --
documented instead of "fixed".

## Writer + hook

- `src/state/schema/family_book_snapshots_schema.py`: `CREATE_TABLE_SQL`,
  the two indexes, the two append-only triggers, `ensure_table(conn)`, and
  the low-level `append_snapshot(conn, **fields) -> bool` (`INSERT OR
  IGNORE`, `True` iff a new row landed). Mirrors
  `observation_prints_schema.py` structure.
- `src/events/family_book_snapshot.py`: the business logic that needs
  `FamilyBook`/`FamilyDecision` types (kept out of `state/schema/*`, which
  stays DDL-only):
  - `market_center_native(family_book) -> float | None` -- the estimator
    above, pure and independently unit-testable.
  - `_ladder_json(family_book, max_levels=5) -> str` -- per bin_id
    `{condition_id, yes_token_id, no_token_id, neg_risk, yes_ask, yes_bid,
    no_ask, no_bid}`, each side capped at the first 5 (already best-first)
    `[price, size]` pairs, `Decimal` -> `float`.
  - `append_family_book_snapshot(conn, *, decision, family, decision_time,
    causal_snapshot_id) -> str | None` -- the fail-soft public entry point.
    Returns the new row's `snapshot_id` iff a row was actually inserted;
    `None` on: `decision is None`, `decision.family_book is None`, a
    dedup-ignored write (the book was already seen), or ANY exception
    (caught broadly, logged as a warning, never re-raised) -- this is
    telemetry-grade persistence, never decision authority, so a failure here
    must never delay or fail the decision cycle.
- Hook: one call to `append_family_book_snapshot(trade_conn, decision=...,
  family=..., decision_time=..., causal_snapshot_id=...)` in
  `event_reactor_adapter.py`, placed immediately before the `if
  prepare_global_auction:` block at `:13862`, using `_spine_fact_decision`
  and the already-in-scope `family`/`decision_time`/`event`/`trade_conn`.
  Fires on all three downstream arms (`prepare_global_auction`,
  `global_actuation is not None`, and the plain
  `_record_qkernel_selection_family_facts` arm) since it runs before the
  branch, not inside any one arm.

## Registry wiring (trade_class)

1. `src/state/schema/family_book_snapshots_schema.py` (new, above).
2. `src/state/db.py`:
   - `init_schema_trade_only`: call `ensure_table(conn)` alongside the other
     trade-DB schema modules (near `book_hash_transitions_schema`).
   - `_TRADE_CLASS_TABLES`: add `"family_book_snapshots"`.
3. `architecture/db_table_ownership.yaml`: new entry, `db: trade`,
   `schema_class: trade_class`, `created_by: "db.py:init_schema_trade_only +
   src.state.schema.family_book_snapshots_schema.ensure_table"`, `pk_col:
   snapshot_id`, `required_columns` per the DDL above, notes stating this
   table is EVIDENCE for the center-evidence campaign, never decision
   authority.
4. `src/state/domains.py`: `CANONICAL_OWNER['family_book_snapshots'] =
   Domain.TRADE`.
5. `architecture/_schema_fingerprint.txt`: regenerated via
   `python scripts/check_schema_fingerprint.py --write-pin` (content hash,
   not a hand-edit).

## Row-rate math (dedup as the volume control)

Scout figures: >=60 decision cycles/hour, 51 families. Upper bound absent
dedup: `60 * 51 = 3060` decision-attempts/hour that reach the hook. With
`UNIQUE(family_id, book_hash)` dedup, a row is written only when a family's
book actually changed since it was last captured for that family -- i.e. at
most once per DISTINCT `book_hash` per family, however many cycles re-decide
the same unchanged book. Real books change far slower than the decision
loop (order-book churn, not per-cycle), so the realistic write rate is
bounded by book-hash-transition frequency, not decision-cycle frequency --
observationally close to `book_hash_transitions`' existing per-market
transition rate rather than the 3060/hour upper bound.

## Tests

- Schema/trigger tests: append-only enforced (`UPDATE`/`DELETE` raise
  `sqlite3.IntegrityError` matching `"append-only"`), dedup ignores a repeat
  `(family_id, book_hash)`, unique index present.
- `market_center_native` unit tests: a known two-sided ladder resolves to
  the known analytic center; an incomplete book (`complete_book=False`)
  resolves to `None`; a complete-but-all-thin book (no two-sided quotes)
  also resolves to `None`.
- `append_family_book_snapshot` unit test: built from real
  `FamilyDecision`/`FamilyBook` dataclasses (not mocks) -- confirms the row
  lands with the right columns, confirms dedup on a repeat call, confirms
  `None` on `decision.family_book is None` and on a simulated write
  exception (fail-soft).
- Hook-placement test: constructs the branch conditions so
  `prepare_global_auction` is truthy (the arm that skips
  `_record_qkernel_selection_family_facts`) and proves the capture still
  fires.
- Registry-parity: `tests/test_domains_reproduces_registry.py` passes
  unmodified (it reads the registry generically); extend the trade-DB table
  set assertions if any exist for a positive-presence check.
- Full new suite + `tests/test_observation_prints_ledger.py` (pattern
  sanity, unmodified) + `tests/test_schema_fingerprint.py` (after
  `--write-pin`) + `tests/test_world_only_tables_not_on_trade.py`
  (unmodified, proves the new table doesn't collide with that antibody) +
  any test importing `event_reactor_adapter` that exercises the hook
  region.
