# R3 M5 work record — Exchange reconciliation sweep

Date: 2026-04-27
Branch: plan-pre5
Task: R3 M5 exchange reconciliation sweep — venue-vs-journal findings, linkable missing trade facts, and operator resolution queue
Status: COMPLETE, post-close critic+verifier PASS; R1 unfrozen

Changed files:

Implementation:
- `src/execution/exchange_reconcile.py`
- `src/state/db.py`

Tests:
- `tests/test_exchange_reconcile.py`
- `tests/test_digest_profile_matching.py`

Routing / registries / docs:
- `architecture/topology.yaml`
- `architecture/source_rationale.yaml`
- `architecture/module_manifest.yaml`
- `architecture/test_topology.yaml`
- `docs/reference/modules/execution.md`
- `docs/reference/modules/state.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/INVARIANTS_LEDGER.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/boot/M5_codex_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/_confusion/M5_findings_vs_trade_fact_write_surface_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/M5.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/drift_reports/2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/receipt.json`

Summary:

- Added `exchange_reconcile.py` with `ReconcileFinding`, idempotent finding writer, unresolved finding queue, operator resolution API, and read-only sweep.
- Added `exchange_reconcile_findings` schema and unresolved partial unique index to `init_schema` without importing execution modules from state DB boot.
- Implemented exchange ghost order, local orphan order, unrecorded trade, position drift, heartbeat suspected cancel, and cutover wipe findings.
- Implemented linkable missing exchange trade fact append for known local commands while preserving the no-new-`venue_commands` boundary.
- Added M5 topology profile + digest regression after initial routing misclassified M5 as heartbeat work.

Verification:

```text
python3 -m py_compile src/execution/exchange_reconcile.py src/venue/polymarket_v2_adapter.py src/execution/exit_safety.py src/execution/exit_lifecycle.py src/execution/executor.py src/state/venue_command_repo.py src/state/db.py tests/test_exchange_reconcile.py tests/test_digest_profile_matching.py tests/test_v2_adapter.py: PASS
pytest -q -p no:cacheprovider tests/test_exchange_reconcile.py: 16 passed
pytest -q -p no:cacheprovider tests/test_exchange_reconcile.py tests/test_v2_adapter.py tests/test_digest_profile_matching.py::test_r3_m5_exchange_reconcile_routes_to_m5_profile_not_heartbeat tests/test_venue_command_repo.py tests/test_exit_safety.py tests/test_user_channel_ingest.py tests/test_heartbeat_supervisor.py tests/test_cutover_guard.py: 133 passed, 6 skipped, 18 known deprecation warnings
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M5: GREEN=10 YELLOW=0 RED=0
python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 exchange reconciliation sweep implementation
```

Known non-goals / risks:

- Live venue enumeration was not exercised; tests use fakes.
- Findings resolution policy remains an operator policy decision; M5 provides queue + resolution API, not silent auto-resolution.
- `exit_lifecycle` M4 proven-absence unblocking is not wired to M5 yet; M5 provides the durable finding/fact substrate.

Next:

- Proceed to R1 phase entry under its inherited Q-FX-1 operator gate. T1 remains blocked until R1 completes.

Pre-close critic remediation:
- Critic Dewey the 2nd BLOCK: FAILED/RETRYING linkable trades could append fill command events, and empty venue enumerations lacked explicit freshness/success proof.
- Fix: `_append_linkable_trade_fact_if_missing()` appends command fill events only for non-FAILED/non-RETRYING trade states.
- Fix: `run_reconcile_sweep()` requires adapter `read_freshness` success proof for open orders, and for trades/positions when those enumeration surfaces are available.
- Regressions: `test_failed_or_retrying_trade_fact_does_not_advance_command_fill_state` and `test_stale_or_unsuccessful_venue_reads_are_not_absence_proof`.

Second pre-close critic remediation:
- Critic Archimedes the 2nd BLOCK: real `PolymarketV2Adapter` returned `[]` when enumeration methods were missing, letting unsupported reads look like absence; FAILED/RETRYING trades still suppressed local orphan findings.
- Fix: `PolymarketV2Adapter.get_open_orders/get_trades/get_positions` now raise `V2ReadUnavailable` when the SDK does not expose the read method, so unsupported reads cannot prove absence.
- Fix: only MATCHED/MINED/CONFIRMED trades suppress local-absence findings; FAILED/RETRYING trades can be recorded as trade facts but still allow local orphan/cutover/heartbeat findings.
- Regressions: `test_real_adapter_missing_read_surface_is_not_absence_proof` and `test_failed_trade_does_not_suppress_local_orphan_finding`.

Pre-close review:
- Critic Dewey the 2nd: BLOCK on FAILED/RETRYING trade-state fill mutation and missing venue-read freshness proof.
- Critic Archimedes the 2nd: BLOCK on real adapter unsupported reads returning `[]` and FAILED/RETRYING trades suppressing local absence findings.
- Final critic Rawls the 2nd: APPROVE after both remediations.
- Verifier Linnaeus the 2nd: PASS.
- Artifact: `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M5_pre_close_2026-04-27.md`.

Post-close critic remediation:
- Critic Huygens the 2nd BLOCK: explicit `read_freshness={"ok": true, "fresh": false}` was still accepted as absence proof.
- Fix: `_assert_adapter_read_fresh()` now requires explicit `ok`/`fresh` markers to be boolean `True`; explicit `fresh: false` fails closed even when `ok: true`.
- Regression: `test_explicit_fresh_false_is_not_absence_proof` includes `captured_at` and proves no findings or command state mutation occur when venue absence reads are explicitly stale.
- Post-remediation reruns: `python3 -m py_compile src/execution/exchange_reconcile.py tests/test_exchange_reconcile.py` PASS; focused M5/V2/digest gate `38 passed`; broader M5 dependency gate `133 passed, 6 skipped, 18 known deprecation warnings`; M5 drift remains `GREEN=10 YELLOW=0 RED=0`.

Closeout dry-run:
- `python3 scripts/topology_doctor.py closeout ... --summary-only` -> PASS; `ok=true`, `blocking_issues=[]`; warnings only for code-review-graph freshness/partial coverage, `src/state/db.py` downstream drift, and existing context-budget overages.

Post-close review closeout:
- Critic Lorentz the 2nd: APPROVE after Huygens fresh=false blocker remediation.
- Verifier Pasteur the 2nd: PASS after `M5_post_close_2026-04-27.md` artifact creation; local verification reran py_compile, broader M5 gate (`133 passed, 6 skipped`), and M5 drift (`GREEN=10 YELLOW=0 RED=0`).
- Artifact: `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M5_post_close_2026-04-27.md`.
- Status decision: R1 unfrozen at inherited Q-FX-1 operator gate; T1 remains blocked by R1.
