# Current State

Last updated: 2026-07-20

Role: single live control pointer for the repo. Thin by law — this file points, it does not narrate. Anything stated here that git or the runtime can show is a defect.

## Status

- Posture: live; operator params in `config/settings.json`; runtime entry `src/main.py` (code-authoritative).
- Deploy is operator-only via `scripts/deploy_live.py restart all` (never bare kickstart), then `resume_entries`.
- Live branch: `live` (renamed from `main` at 2026-07-20 cutover). HEAD = the `live` tip; see `git log` — this file does not track SHAs (they rot here).

## Active work

Active execution packet: `current/finite_evidence_probability_symmetry`

- Live improvement journal: `docs/operations/current/plans/hourly_capital_gains_improvement_loop.md`
- Plan index: `docs/operations/current/plans/INDEX.md`
- Gate-stack simplification: `docs/operations/current/plans/gate_stack_simplification_2026-07-06.md`
- 24/7 loop v3 (ACTIVE design + machinery in `loop/`): `docs/operations/current/plans/allday_improvement_loop_v3_codex_2026-07-09.md` (v2 = method authority: `allday_improvement_loop_design_2026-07-06.md`)

## Current-fact companions

- `docs/operations/current_data_state.md` — data posture (check its own Last-audited header before trusting)
- `docs/operations/current_source_validity.md` — source posture (same rule)
- `docs/operations/known_gaps.md` — known-gap worklist

## Archive

Closed packet bodies: moved to `docs/archive/<YYYY>-Q<N>/` (untracked), with a tracked `.archived` stub (carrying `restore_command`) at the original path — procedure in `docs/authority/ARCHIVAL_RULES.md`. (`docs/archive_registry.md` is historical-only, no longer updated.)

## Routing

- `docs/operations/AGENTS.md` — packet/package routing and closeout rules
- `architecture/history_lore.yaml` — durable lessons
- `docs/operations/current/plans/live_branch_workflow_2026-07-20.md` — the `live`-branch workflow (worktree → cherry-pick/PR → live); AGENTS §5 is the summary

(2026-07-20: the five 2026-07-10/11 landed packets — runtime_open_exposure_snapshot, runtime_claim_contention, chain_absence_livelock, quarantine_chain_freshness, pending_exit_restart_redecision — archived to `docs/archive/2026-Q3/`; `.archived` stubs carry the restore path.)
