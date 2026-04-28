# R3 M4 work record — Cancel/replace + exit safety

Date: 2026-04-27
Branch: plan-pre5
Task: R3 M4 cancel/replace + exit safety — typed cancel parser, replacement gate, and exit mutex
Status: COMPLETE, post-close PASS

Changed files:

Implementation:
- `src/execution/exit_safety.py`
- `src/execution/executor.py`
- `src/execution/exit_lifecycle.py`
- `src/state/venue_command_repo.py`
- `src/state/db.py`

Tests:
- `tests/test_exit_safety.py`
- `tests/test_digest_profile_matching.py`

Routing / registries / docs:
- `architecture/AGENTS.md`
- `architecture/docs_registry.yaml`
- `workspace_map.md`
- `docs/AGENTS.md`
- `docs/README.md`
- `docs/operations/AGENTS.md`
- `docs/reference/AGENTS.md`
- `architecture/topology.yaml`
- `architecture/source_rationale.yaml`
- `architecture/module_manifest.yaml`
- `architecture/test_topology.yaml`
- `docs/reference/modules/execution.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/INVARIANTS_LEDGER.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/boot/M4_codex_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/_confusion/M4_schedule_exit_and_cancel_grammar_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/M4.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/drift_reports/2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/receipt.json`

Summary:

- Added `exit_safety.py` with `CancelOutcome`, `parse_cancel_response`, `ExitMutex`, replacement-sell gate, typed cancel command handler, terminal mutex release hook, and remaining-share reader.
- Wired `execute_exit_order()` to block broad same-position/token replacement sells before command persistence/SDK contact, acquire the mutex after command insert, and keep unknown/active prior exits from duplicating sells.
- Wired `exit_lifecycle` stale-cancel retry to stop fail-open replacement when cancel is unavailable, failed, or unknown.
- Wired `venue_command_repo.append_event()` to release exit mutexes on existing terminal command states, alongside collateral reservation release.
- Added `exit_mutex_holdings` schema to `init_schema` without importing execution modules during DB initialization.
- Resolved M4 card drift: no `schedule_exit` exists; actual actuation seam is `exit_lifecycle`/`executor`. `CANCEL_CONFIRMED`/`CANCEL_UNKNOWN` are typed semantic outcomes or U2 facts, not new command grammar.
- Pre-close critic BLOCK found that released-row mutex reacquire was read-then-unconditional-update; fixed it to compare-and-swap on the observed `(mutex_key, command_id, released_at)` and fail closed on stale rows.

Verification:

```text
python3 -m py_compile src/execution/exit_safety.py src/execution/exit_lifecycle.py src/execution/executor.py src/state/venue_command_repo.py src/state/db.py tests/test_exit_safety.py tests/test_digest_profile_matching.py: PASS
pytest -q -p no:cacheprovider tests/test_exit_safety.py: 10 passed
pytest -q -p no:cacheprovider tests/test_exit_safety.py tests/test_digest_profile_matching.py::test_r3_m4_cancel_replace_routes_to_m4_profile_not_heartbeat tests/test_executor.py tests/test_executor_command_split.py tests/test_live_execution.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py: 58 passed, 2 skipped
pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py tests/test_venue_command_repo.py tests/test_collateral_ledger.py: 101 passed, 4 known deprecation warnings
pytest -q -p no:cacheprovider tests/test_exit_safety.py tests/test_digest_profile_matching.py tests/test_executor.py tests/test_executor_command_split.py tests/test_live_execution.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py tests/test_venue_command_repo.py tests/test_collateral_ledger.py: 158 passed, 2 skipped, 4 known deprecation warnings
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M4: GREEN=10 YELLOW=0 RED=0
python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 cancel replace exit safety implementation
python3 scripts/topology_doctor.py --planning-lock ...: topology check ok
python3 scripts/topology_doctor.py --map-maintenance ... --map-maintenance-mode advisory: topology check ok
```

Known non-goals / risks:

- M5 reconciliation/unblock is intentionally not implemented; unknown cancel remains blocked.
- Partial-fill reservation accounting remains command-terminal release based; M4 preserves remaining-size facts and does not introduce sub-command reservation splitting.
- Live venue cancel/submit was not exercised; tests use injected fakes and monkeypatches.

Next:

- M4 closed after post-close third-party critic+verifier PASS. M5/R1 are ready for phase-entry planning/topology boot; R1 still carries Q-FX-1 operator gate. No live venue submission, CLOB cutover, or production DB mutation is authorized.

Post-close third-party review:
- Critic Chandrasekhar the 2nd: APPROVE.
- Verifier Halley the 2nd: initial procedural BLOCK because post-close artifact/status updates were not yet written; rerun PASS after clarification that the pending labels awaited this verdict.
- Remediation: created `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M4_post_close_2026-04-27.md`; after rerun PASS, updated status surfaces and unfroze M5/R1 for phase-entry planning/topology boot.

Pre-close review:
- Initial critic Laplace the 2nd: BLOCK on non-atomic released-row mutex reacquire.
- Remediation: compare-and-swap released-row acquire plus `test_mutex_reacquire_released_row_fails_closed_on_stale_compare`.
- Final critic Noether the 2nd: PASS.
- Verifier Socrates the 2nd: PASS.
- Artifact: `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M4_pre_close_2026-04-27.md`.

Closeout dry-run:
- `python3 scripts/topology_doctor.py closeout ... --summary-only` -> PASS; `ok=true`, `blocking_issues=[]`; warnings only for code-review-graph freshness/partial coverage, source downstream drift, and existing context-budget overages.

Pre-close critic remediation:
- `Laplace the 2nd` BLOCK: `ExitMutex.acquire()` released-row reacquire was not atomic.
- Fix: released-row reacquire now performs conditional `UPDATE ... WHERE mutex_key = ? AND command_id = ? AND released_at = ?`; stale rows return `False` unless the same command is already active.
- Regression: `tests/test_exit_safety.py::test_mutex_reacquire_released_row_fails_closed_on_stale_compare`.
