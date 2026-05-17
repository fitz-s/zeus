# Wave41 Plan — D3 Explicit Fill Price Authority

Status: APPROVED

## Scope

Repair one D3 object-meaning boundary:

`exchange trade payload -> venue_trade_facts.fill_price -> command fill finality -> portfolio economics`

The scoped invariant is: a realized fill price must come from an explicit
fill-price field. A generic exchange `price` field is not enough authority to
become fill economics, because it may denote quote, submitted, matched, or
display price depending on upstream payload shape.

## Evidence

- `src/execution/fill_tracker.py` already extracts only explicit fill fields:
  `avgPrice`, `avg_price`, `fillPrice`, `fill_price`.
- `src/execution/exchange_reconcile.py` still accepted `price` as fill price
  when appending missing linkable trade facts.
- This allows a bare generic price to cross into `venue_trade_facts.fill_price`
  and then into `FILL_CONFIRMED` / `PARTIAL_FILL_OBSERVED` events.

## Repair

1. Add an explicit fill-price extractor in `exchange_reconcile`.
2. Stop using generic `price` for `fill_price`.
3. Add a relationship test proving a confirmed trade with `size` and generic
   `price`, but no explicit fill-price field, records a finding instead of a
   trade fact or fill-finality event.

## Verification

- Focused `tests/test_exchange_reconcile.py` slice:
  `4 passed`.
- `py_compile` for touched source/test files: pass.
- `git diff --check` for touched files: pass.
- Planning-lock/map-maintenance after patch.
- Critic `APPROVE`: generic `price` now fails closed; explicit fill-price
  fields still preserve fill authority; future adapter-documented `price`
  semantics should normalize upstream rather than be inferred here.

## Non-Scope

- No schema migration.
- No canonical DB mutation.
- No venue/API side effects.
- No reinterpretation of existing historical trade facts.
