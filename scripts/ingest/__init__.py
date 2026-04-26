# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: Package marker for the standalone ingest-tick lane.
# Reuse: Re-read the G10-scaffold plan (docs/operations/task_2026-04-26_g10_ingest_scaffold/)
#        before adding new scripts here. The forbidden-import contract is
#        machine-enforced by tests/test_ingest_isolation.py.
# Authority basis: docs/operations/task_2026-04-26_live_readiness_completion/plan.md
#   §5 K3.G10-scaffold + workbook G10 (archived).
"""scripts.ingest — standalone tick entry points for the data-ingest lane.

This package decouples ingest scheduling from the live-trading daemon
(`src/main.py`). Each tick script in this directory:

- Is runnable as `python scripts/ingest/<name>.py`
- Imports ONLY from src.data.* / src.state.db.* / src.config.* / src.contracts.*
  (NEVER from src.engine, src.execution, src.strategy, src.signal,
  src.supervisor_api, src.control, src.observability, or src.main)
- Carries lifecycle headers per Zeus convention
- Returns process exit code (0 = success, non-zero = failure)

The forbidden-import contract is enforced by `tests/test_ingest_isolation.py`
(AST-walk every module in this directory). Violating it is a CI failure.

This is a SCAFFOLD layer — it parallels (does not yet replace) the bundled
tick functions in `src/main.py`. Cutover (removing the bundled versions)
is tracked as G10-cutover under the parent live-readiness-completion packet.
"""
