# Plan: Object-Meaning Invariance Wave 6 Command Recovery Authority

Status: planning-lock evidence only
Created: 2026-05-05

## Goal

Repair the scoped boundary where unknown live venue-submit side effects are
recovered into command state and allocation authority. A venue order-status
string must not become fill finality, exposure capacity, or allocation clearance
unless the system has explicit venue fill-economics evidence on the U2 fact
path.

## Scope

Allowed repair surface for this wave:

- `src/execution/command_recovery.py`
- `src/execution/exchange_reconcile.py`
- `src/execution/fill_tracker.py`
- `src/engine/cycle_runtime.py`
- `src/ingest/polymarket_user_channel.py`
- `src/risk_allocator/governor.py`
- `src/state/venue_command_repo.py`
- focused relationship/static tests proving the boundary

Out of scope:

- production DB mutation, migrations, backfills, rebuilds, or live state edits
- live venue submit/cancel/redeem side effects
- CommandState or LifecyclePhase grammar changes
- exchange-reconcile policy expansion beyond downstream verification
- synthetic trade facts, synthetic position lots, or legacy-data promotion

## Invariants

1. `SUBMIT_UNKNOWN_SIDE_EFFECT` recovery may prove ACK, rejection, or operator
   review need, but may not convert order-status `CONFIRMED` into command
   `FILLED` without the same explicit venue fill-economics authority consumed
   by fill/reconcile fact paths.
2. `REVIEW_REQUIRED` and `UNKNOWN` venue commands remain unresolved live-money
   side-effect evidence for allocation purposes; they must keep new risk
   fail-closed until resolved by an admitted command/fact path.
3. `venue_commands.state`, `venue_trade_facts.state`, and `position_lots.state`
   remain distinct objects: command lifecycle is not economic exposure truth.
4. No repaired path may require live/prod mutation or silently relabel legacy
   command rows as corrected truth.
5. Submitted order result fields (`OrderResult.status`, `fill_price`, and
   `shares`) are not fill-economics authority unless the durable command
   journal reached `FILLED`.
6. Economic-intent duplicate blocking must preserve unresolved object identity
   after recovery/operator-handoff transitions (`UNKNOWN`, `REVIEW_REQUIRED`),
   not only while the row is literally `SUBMIT_UNKNOWN_SIDE_EFFECT`.
7. Exchange/user-channel trade finality requires a venue trade identity and
   positive fill economics. Synthetic reconcile subjects, missing size, or
   missing price remain finding/review evidence, not `FILL_CONFIRMED`.

## Verification Plan

- Topology navigation through the admitted R3 fill-finality and A2 allocator
  profiles.
- Planning-lock using this file as `--plan-evidence`.
- Relationship tests for unknown-side-effect recovery proving `CONFIRMED`
  order status becomes `REVIEW_REQUIRED`, not `FILL_CONFIRMED`.
- Fill tracker relationship tests proving order-only `CONFIRMED` without trade
  identity quarantines the pending entry and cannot mark fill authority.
- Runtime materialization tests proving reported `fill_price` is ignored until
  command journal finality is `FILLED`.
- Command journal duplicate tests proving `UNKNOWN` and `REVIEW_REQUIRED`
  unresolved rows still block same economic-intent replacement submits.
- M5 exchange reconcile tests proving linkable trades without venue trade
  identity become findings, not synthetic trade facts or command finality.
- M3 user-channel tests proving confirmed/matched trade messages without
  positive price/size enter review, not trade fact finality or exposure lots.
- Allocator relationship test proving `REVIEW_REQUIRED`/`UNKNOWN` command rows
  block allocation like unresolved side effects.
- Downstream sweep of fill tracker, exchange reconcile, command repo,
  collateral release, risk allocator, and live safety invariant tests.
