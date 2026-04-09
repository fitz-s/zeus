# Docs Index

All docs use `lower_snake_case.md` naming unless a date prefix is required.

## Folders
- `architecture/`: system architecture, invariants, integration designs.
- `archives/`: historical handoffs, audits, findings, traces, research, and other non-live records.
- `specs/`: formal specs and contract definitions.
- `plans/`: execution plans and release checklists.
- `progress/`: progress snapshots.
- `strategy/`: strategy and data utilization decisions.
- `reviews/`: post-hoc and strategic review artifacts.
- `reports/`: generated/curated operational reports.
- `reference/`: workspace maps and domain references.

## Conventions
- Prefer stable names (no spaces, no all-caps file names).
- Use date-prefixed names only for time-bound plans/reports, e.g. `2026-03-31-go-live-readiness.md`.
- New generated reports should be written into `docs/reports/`.
- Move historical investigations, audits, findings, and scratch-style research out of top-level `docs/` and into `docs/archives/` subfolders.

## Active top-level docs
- `../ZEUS_AUTHORITY.md` — root authority guide summarizing foundations, invariants, negative constraints, and boundary rules
- `docs/architecture/zeus_durable_architecture_spec.md` — present-tense principal architecture authority
- `docs/zeus_FINAL_spec.md` — terminal target-state / endgame authority
- `CURRENT_STATE.md` — single live control-entry pointer
- `docs/known_gaps.md` — active operational gap / antibody register
- `docs/archives/**` — historical only; not principal authority

## Naming Rules (Mandatory)
- `plan` files must include explicit scope/topic in file name.
- Use: `<topic>_plan.md` or `<topic>_<phase>_plan.md`.
- `progress` files must include explicit scope/topic in file name.
- Use: `<topic>_progress.md` or `<topic>_<phase>_progress.md`.
- Forbidden examples: `plan.md`, `progress.md`, `live_plan.md`.
- Allowed examples: `zeus_live_plan.md`, `zeus_progress.md`, `riskguard_progress.md`.
