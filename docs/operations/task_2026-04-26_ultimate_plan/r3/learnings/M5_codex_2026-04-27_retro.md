# M5 retro — 2026-04-27

## Learnings

1. M5 route phrases overlap with heartbeat/cutover and cancel/replace language; topology needs a strong M5 profile before source navigation.
2. The M5 card's "findings only" wording conflicts with the linkable missing-trade acceptance test. The safe split is findings for exchange-only/unlinkable state and U2 trade facts for exchange trades with an existing local command foreign key.
3. Empty exchange positions are dangerous if trade enumeration was just appended; tests should distinguish unavailable methods from successful empty returns.
4. Idempotence must be enforced at the findings layer, not by generating deterministic UUIDs; unresolved `(kind, subject_id, context)` is the dedupe identity.

## Protocol gaps

- Add topology profiles at phase boot when a new R3 slice introduces a new module.
- Confusion artifacts should be created as soon as slice-card invariant text conflicts with acceptance tests.

## What changed because of this

- Added M5 topology profile and digest regression.
- Added confusion checkpoint for findings-vs-trade-fact write surface.
- Implemented idempotent unresolved findings and an explicit operator resolution queue.
