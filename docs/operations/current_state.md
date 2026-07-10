# Current State

Last updated: 2026-07-07

Role: single live control pointer for the repo. Thin by law — this file points, it does not narrate. Anything stated here that git or the runtime can show is a defect.

## Status

- Posture: live; operator params in `config/settings.json`; runtime entry `src/main.py` (code-authoritative).
- Deploy is operator-only via `scripts/deploy_live.py restart all` (never bare kickstart), then `resume_entries`.
- Main HEAD: see `git log` — this file does not track SHAs (they rot here).

## Active work

Active execution packet: none (work routes through the plans index below; freeze a packet here before starting a new implementation slice).

- Live improvement journal: `docs/operations/current/plans/hourly_capital_gains_improvement_loop.md`
- Plan index: `docs/operations/current/plans/INDEX.md`
- Gate-stack simplification: `docs/operations/current/plans/gate_stack_simplification_2026-07-06.md`
- 24/7 loop v3 (ACTIVE design + machinery in `loop/`): `docs/operations/current/plans/allday_improvement_loop_v3_codex_2026-07-09.md` (v2 = method authority: `allday_improvement_loop_design_2026-07-06.md`)

## Current-fact companions

- `docs/operations/current_data_state.md` — data posture (check its own Last-audited header before trusting)
- `docs/operations/current_source_validity.md` — source posture (same rule)
- `docs/operations/known_gaps.md` — known-gap worklist

## Archive

Closed packet bodies: `docs/archive_registry.md` (bodies untracked under `docs/archive/`).

## Routing

- `docs/operations/AGENTS.md` — packet/package routing and closeout rules
- `architecture/history_lore.yaml` — durable lessons
- 2026-07-10 packet `current/runtime_open_exposure_snapshot` landed (head 6aa976985). <!-- zpkt landed: current/runtime_open_exposure_snapshot -->
- 2026-07-10 packet `current/runtime_claim_contention` landed (head 666642225). <!-- zpkt landed: current/runtime_claim_contention -->
- 2026-07-10 packet `current/chain_absence_livelock` landed (head 16cec04f6). <!-- zpkt landed: current/chain_absence_livelock -->
