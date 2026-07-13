# src/reduce AGENTS — LX-2R-a (position-economics reducer, synthetic phase)

Module book: none yet (rebuild-in-flight; design lives in docs/rebuild/)
Machine registry: `architecture/module_manifest.yaml`
Design authority: `docs/rebuild/local_ledger_excision_2026-07-12.md` §Read-model 诚实不变量,
`docs/rebuild/consult_answers/local_ledger_excision_wave1_review_2026-07-13.txt`,
`docs/rebuild/consult_answers/local_ledger_excision_wave1_local_verifier_2026-07-13.md`

## WHY this zone matters

`src/reduce` is the target-form derive-on-read replacement for the economics
columns currently stored (and repeatedly clobbered/repaired) on
`position_current` — see the local-ledger-excision plan's disease definition.
A `PositionEconomics` row is a **pure function** of already-durable trade-DB
facts: `venue_trade_facts` (via `src.state.fill_dedup`'s canonical +
economic-identity CTEs), `position_events` `POSITION_IDENTITY_SUPERSEDED`
facts, and `payout_observations`. It writes nothing.

**Synthetic-fixture-only by design.** Both wave-1 dual reviews (external +
local) ruled NO-GO on the full LX-2R activation unit (cent-equivalence, live
read-model backfill, generation publication against a real DB) while
explicitly clearing *isolated reducer implementation* to proceed in
parallel with LX-1R source-spine repair. Nothing outside `tests/reduce/`
imports this package yet. No promotion flag exists here because nothing
calls this package at all (§C6 no-shadow-modes axiom).

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `position_economics.py` | `reduce_position_economics(conn, position_id, ...) -> PositionEconomics`: the fold. Refuses (typed `ReducerRefusal` subclasses) on missing fill-sync watermark, unmigrated identity-supersession schema, missing condition attribution for an open position, an oversold fill, or an unrecognized intent kind carrying economic facts. | HIGH — future money-path authority once a real LX-2R/LX-3R packet wires it live; today read-only against synthetic fixtures. |
| `generation.py` | `Generation`/`CoverageVector` contract + `GenerationStore`: table-backed, all-or-nothing publication (never a half-written generation). | MEDIUM — contract surface, not yet wired to any live cutover. |
| `schema/generation_schema.py` | `ensure_tables(conn)` for the generation-store tables. NOT wired into `src.state.db`'s init paths this packet. | LOW — additive DDL, idempotent. |

## Domain rules

- **Never reimplement alias-graph dedup.** Exactly-once fill economics comes
  entirely from `src.state.fill_dedup.economic_trade_facts_for_command` — if
  a new alias rule is needed, it belongs in `fill_dedup.py`, not here.
- **Never reimplement identity-merge arithmetic.** Duplicate-position
  consolidation is consumed via `POSITION_IDENTITY_SUPERSEDED` facts on
  `position_events`, never by recomputing a merge from `position_current`.
- **PENDING, never zero.** An open position whose condition has not resolved
  (`UNKNOWN`, `UNRESOLVED`, or no observation row at all) reports
  `payout_status="PENDING"` and `payout_pnl_usd=None` — never a fabricated
  zero. Any change that makes an unresolved payout collapse to a number is a
  regression against the packet's core invariant.
- **Fail-closed is a feature.** Missing coverage raises a named
  `ReducerRefusal` subclass naming the missing input. Do not add a fallback
  path that silently computes a number over an unproven-complete corpus.
- **BUILD-INTO-TARGET.** This package is a clean-namespace target component.
  `position_current`, `projection.py`, `edli_position_bridge.py`,
  `fill_dedup.py`, and every live reader (bankroll/riskguard/monitor/exit)
  are out of scope for this package — touch them only in a future
  LX-2R/LX-3R activation-control packet, never here.
