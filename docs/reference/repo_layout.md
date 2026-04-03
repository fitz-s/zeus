# Repo Layout

## Source and Execution
- `src/`: core application code.
- `scripts/`: operational scripts and replay/audit entrypoints.
- `tests/`: automated tests.
- `config/`: runtime config templates and static settings.

## Documentation
- `docs/architecture/`: system architecture, design constraints, integration plans.
- `docs/specs/`: formal specifications.
- `docs/plans/`: execution plans and promotion checklists.
- `docs/progress/`: milestone progress records.
- `docs/strategy/`: strategy assumptions and data-utilization guidance.
- `docs/reviews/`: retrospective and strategic review artifacts.
- `docs/reports/`: generated or curated analysis reports.
- `docs/reference/`: maps, glossary, and repository-level reference docs.

## Runtime and Local State (not for Git tracking)
- `state/*-paper.json`, `state/*-paper.db`: mode-specific live state.
- `state/*.db-wal`, `state/*.db-shm`: sqlite runtime artifacts.
- `logs/*.log`, `logs/*.err`: runtime logs.
- `.omx/`, `.omc/`: local orchestration/runtime metadata.

## Root Directory Policy
- Keep root minimal: dependency manifests, `pytest.ini`, and active operator files like `zeus_progress.md` / `zeus_task.md`.
- Keep `WORKSPACE_MAP.md` at repository root as the top-level navigation entry.
- Do not place generated reports in root; write them to `docs/reports/`.
