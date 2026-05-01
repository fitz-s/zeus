# M4 retro — 2026-04-27

## Learnings

1. The M4 card's `schedule_exit` seam had drifted; current code separates signal evaluation (`exit_triggers`) from actuation (`exit_lifecycle`/`executor`).
2. Cancel vocabulary spans two planes: command grammar uses `CANCEL_ACKED`/`CANCELLED`, while M4/U2 semantics can say `CANCEL_CONFIRMED` or `CANCEL_UNKNOWN`. Tests should pin the mapping rather than add grammar casually.
3. Exact idempotency checks are insufficient for exit safety; the economic hazard is same `(position_id, token_id)` with any active/unknown prior sell chain.
4. `src/state/db.py` should not import execution modules during schema initialization; duplicate idempotent DDL is safer than a state→execution import at DB boot.

## Protocol gaps

- New phase profiles may be missing from `architecture/topology.yaml`; add a route regression before interpreting a misroute as task scope.
- Slice-card function names can drift faster than phase intent; boot should verify symbol existence before coding.

## What changed because of this

- Added M4 topology profile and digest regression.
- Added `M4_schedule_exit_and_cancel_grammar_2026-04-27.md` confusion artifact.
- Implemented typed cancel outcomes without expanding command grammar.
