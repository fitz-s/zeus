# Packet Closeout Evidence — Contamination Remediation — 2026-04-28

Scope: closeout verification for the initial Codex drift handoff remediation packet after Batches A-H and H0 profile admission. This evidence excludes TIGGE/data-readiness, `architecture/history_lore.yaml` remediation, live side effects, production DB mutation, and operator-gated live deployment.

## Handoff open-cluster disposition

- §5.1 `tests/test_supervisor_contracts.py`: first-four gate; tests aligned to current supervisor env contract; production `src/supervisor_api/contracts.py` unchanged and `paper` remains invalid.
- §5.2 `tests/test_riskguard.py`: Batch D; stale tests aligned to current fail-closed RiskGuard law; no `src/riskguard/**` production edit.
- §5.3 `tests/test_runtime_guards.py`: Batch G test fixture alignment plus Batch H production lifecycle-backfill bug fix.
- §5.4 `tests/test_tick_size.py` / `src/execution/executor.py`: Batch E finite exit-price validation before cutover side effects, with safety-half regression.
- §5.5 `tests/test_topology_doctor.py` docs-mode failures: Batch F non-history synthetic visible-path regression; `architecture/history_lore.yaml` out of scope.
- §5.6 `tests/test_structural_linter.py`: Batch B rebuild-settlements verification; no separate implementation needed.

## Targeted aggregate closeout gates

```text
python3 scripts/topology_doctor.py --planning-lock --changed-files <scoped remediation surfaces> --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> {"ok": true, "issues": []}

python3 scripts/digest_profiles_export.py --check
=> OK: architecture/digest_profiles.py matches YAML

direct topology schema check via scripts.topology_doctor._check_schema(load_topology(), load_schema())
=> issue_count: 0

python3 -m py_compile <changed Python/script/test surfaces>
=> passed

pytest tests/test_supervisor_contracts.py tests/test_pnl_flow_and_audit.py
=> 97 passed, 5 skipped

pytest tests/test_db.py tests/test_harvester_metric_identity.py tests/test_harvester_dr33_live_enablement.py tests/test_settlements_unique_migration.py
=> 76 passed, 19 skipped

pytest tests/test_rebuild_pipeline.py tests/test_structural_linter.py tests/test_settlement_semantics.py tests/test_authority_gate.py
=> 51 passed, 1 existing SyntaxWarning

pytest tests/test_sigma_floor_evaluation.py
=> 7 passed

pytest tests/test_riskguard.py
=> 47 passed

pytest tests/test_tick_size.py tests/test_executor.py tests/test_execution_price.py tests/test_unknown_side_effect.py
=> 68 passed, 1 skipped, 1 xfailed, 1 warning

pytest tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py tests/test_topology_doctor.py
=> 262 passed, 16 deselected

pytest tests/test_runtime_guards.py tests/test_entry_exit_symmetry.py tests/test_day0_exit_gate.py tests/test_decision_evidence_entry_emission.py tests/test_exit_evidence_audit.py
=> 169 passed

topology_doctor --tests --json (filtered)
=> command_exit_status=1, global_issue_count=5, touched_issue_count=0

topology_doctor --docs --json (filtered)
=> command_exit_status=1, global_issue_count=23, contamination_packet_issue_count=0

topology_doctor --scripts --json (filtered)
=> command_exit_status=1, global_issue_count=9, rebuild_issue_count=0

git diff --check -- <scoped remediation surfaces and evidence>
=> passed

protected forbidden surfaces diff byte count
=> 0 for src/supervisor_api/contracts.py, src/contracts/settlement_semantics.py, src/riskguard/riskguard.py, src/riskguard/risk_level.py, src/state/portfolio_loader_policy.py, src/engine/lifecycle_events.py, src/state/ledger.py, src/engine/cycle_runtime.py, src/state/projection.py, docs/operations/current_source_validity.md, docs/operations/current_data_state.md, architecture/history_lore.yaml
```

## Full-suite closeout gate

Clean wrapper rerun:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider --no-header --maxfail=30
=> 3484 passed, 107 skipped, 16 deselected, 1 xfailed, 1 xpassed, 31 warnings, 7 subtests passed in 170.89s
=> full_pytest_exit_status=0
```

Warnings were deprecation warnings from existing compatibility tests plus the existing `tests/test_structural_linter.py` SyntaxWarning already recorded in Batch B evidence.

## Live-readiness informational gate

```text
python3 scripts/live_readiness_check.py --json
=> gate_count=17; passed_gates=16; status=FAIL; live_readiness_exit_status=1
=> only failing gate: G1-02 host/Zeus-egress / staged-live-smoke evidence missing; live_deploy_authorized=false
```

This is expected and operator-gated. The remediation packet does not authorize live deployment, staged smoke, production DB mutation, or credentialed/live venue side effects.

## Hong Kong / WU correction status

The packet preserves the operator correction: Hong Kong has no WU ICAO. Batch B rejects both `wu_icao_history` and legacy `wu_icao` for Hong Kong/HKO and test coverage asserts zero settlement writes plus `source_family_mismatch: 2`. WU source strings remain only for WU-family non-Hong-Kong fixtures (e.g. NYC) or explicit rejection/guardrail text.

## Co-tenant / out-of-scope status

Unrelated dirty work remains in the worktree (for example `architecture/invariants.yaml`, `architecture/script_manifest.yaml`, untracked attribution/edge-operation artifacts, and other co-tenant files). This closeout evidence does not approve or modify those surfaces. No `git add -A` or destructive git command was used.
