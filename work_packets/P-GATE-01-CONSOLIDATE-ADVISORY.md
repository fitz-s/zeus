# P-GATE-01-CONSOLIDATE-ADVISORY

```yaml
work_packet_id: P-GATE-01-CONSOLIDATE-ADVISORY
packet_type: refactor_packet
objective: Consolidate advisory gate drift by freezing a machine-checkable blocking-vs-advisory verdict for the current architecture workflow, semgrep lane, and replay-parity lane.
why_this_now: The first advisory workflow is landed, but its blocking/advisory split still depends on scattered operator memory, current semgrep findings, and workflow-only convention. This packet makes that verdict explicit and testable before any later severity promotion.
why_not_other_approach:
  - Promote semgrep or replay parity now | current findings and staged canonical tables make that promotion premature and noisy
  - Leave the verdict only in Architects chat/progress notes | cloud-visible workflow policy would still drift without a repo-local machine check
truth_layer: The authoritative current-phase CI verdict lives in the advisory workflow plus a dedicated policy-check script and test, not in chat history or operator memory.
control_layer: Blocking jobs remain manifests/module-boundaries/packet-grammar/kernel-invariants; semgrep and replay-parity remain advisory until later packets resolve current findings and prerequisites.
evidence_layer: Local script output, workflow YAML parse, targeted architecture tests, semgrep advisory output, replay-parity staged output, and attack-only adversarial review artifacts.
zones_touched:
  - K0_frozen_kernel
invariants_touched:
  - INV-03
  - INV-10
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/governance/zeus_foundation_package_map.md
  - docs/governance/zeus_first_phase_execution_plan.md
  - docs/governance/zeus_top_tier_decision_register.md
  - docs/architecture/zeus_durable_architecture_spec.md
  - .github/workflows/AGENTS.md
  - scripts/AGENTS.md
  - tests/AGENTS.md
  - .github/workflows/architecture_advisory_gates.yml
  - tests/test_architecture_contracts.py
files_may_change:
  - work_packets/P-GATE-01-CONSOLIDATE-ADVISORY.md
  - .github/workflows/architecture_advisory_gates.yml
  - scripts/check_advisory_gates.py
  - tests/test_architecture_contracts.py
  - architects_progress.md
  - architects_task.md
files_may_not_change:
  - architecture/**
  - docs/governance/**
  - docs/architecture/**
  - migrations/**
  - src/**
  - .claude/CLAUDE.md
schema_changes: false
ci_gates_required:
  - python3 scripts/check_kernel_manifests.py
  - python3 scripts/check_module_boundaries.py
  - python3 scripts/check_work_packets.py
  - .venv/bin/pytest -q tests/test_architecture_contracts.py tests/test_cross_module_invariants.py
tests_required:
  - tests/test_architecture_contracts.py
parity_required: false
replay_required: false
rollback: Revert the workflow, advisory-gate policy script/test, packet file, and paired Architects ledger updates as one coherent slice.
acceptance:
  - The advisory workflow has a machine-checkable verdict for blocking vs advisory jobs.
  - Semgrep remains advisory with an explicit, testable promotion condition.
  - Replay parity remains advisory with an explicit, testable promotion condition.
  - No external-workspace-dependent audit is made blocking.
  - The packet stays inside the listed file boundary.
evidence_required:
  - advisory gate policy script output
  - workflow YAML parse output
  - pytest output for architecture contracts and cross-module invariants
  - semgrep advisory output
  - replay parity staged output
  - attack-only adversarial review notes
  - rollback note
```

## Notes

- This is still a current-phase advisory-enforcement packet, not a promotion packet.
- Attack-only adversarial reviews may identify risks or contradictions, but they do not decide authority verdicts.
- If fixing semgrep warnings or semgrep findings requires editing `architecture/**` or `src/**`, stop and freeze a new packet.
