# src/supervisor_api AGENTS

Zone: K2 — Execution (supervisor contracts)

## What this code does (and WHY)

This is the typed contract surface between Zeus and Venus. Every object Venus receives carries `env + source + provenance`. The contracts follow the O/P/C/O pattern: Observation (what is happening), Proposal (what should change), Command (what Zeus should do), Outcome (what happened). This boundary exists because Venus must NEVER write to Zeus state directly — it observes through these contracts and commands through the control plane.

## Key files

| File | Purpose | Watch out for |
|------|---------|---------------|
| `contracts.py` | Observation, BeliefMismatch, Proposal, Command, Outcome dataclasses | Changing these changes the external API contract with Venus |

## Domain rules

- Venus reads Zeus state through these contracts — Venus NEVER writes to Zeus state
- Every contract object must carry env, source, and provenance
- Changes here affect the external boundary (Venus must be updated to match)
- Changes require a work packet (K2 zone)

## Common mistakes agents make here

- Adding a field that leaks internal state (DB IDs, raw SQL results) through the contract
- Forgetting provenance/env/source on new contract types
- Making contracts that assume Venus has Zeus-internal context

## References
- Root rules: `../../AGENTS.md`
- Control plane (the write interface): `../control/control_plane.py`
