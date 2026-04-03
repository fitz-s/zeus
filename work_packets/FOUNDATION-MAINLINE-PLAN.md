# FOUNDATION-MAINLINE-PLAN

```yaml
work_packet_id: FOUNDATION-MAINLINE-PLAN
packet_type: feature_packet
objective: Freeze the post-current-phase architecture mainline by extracting the stage map, workstreams, automation strategy, verification path, and team-opening gate from the tribunal overlay and mature foundation source package.
why_this_now: The current-phase `P-*` queue is closed. Without a frozen mainline plan, the next stage would start from memory and momentum rather than explicit authority.
why_not_other_approach:
  - Start team execution immediately | team lanes would open without a frozen mainline sequence or staffing gate
  - Drive foundation work from chat summaries only | stage boundaries, automation plan, and completion targets would drift
truth_layer: The next-stage stage map and goals come from `zeus_final_tribunal_overlay` for current-phase closure logic and `zeus_mature_project_foundation` for the durable architecture mainline.
control_layer: This packet plans the next phase only; it does not yet mutate runtime, schema, or authority law outside the planning artifact it freezes.
evidence_layer: Stage map, completion ladder, source-package crosswalk, team-opening gate, and rollback note.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-10
required_reads:
  - zeus_final_tribunal_overlay/ZEUS_FINAL_TRIBUNAL.md
  - zeus_final_tribunal_overlay/AGENTS.md
  - zeus_final_tribunal_overlay/docs/governance/zeus_first_phase_execution_plan.md
  - zeus_final_tribunal_overlay/docs/governance/zeus_foundation_package_map.md
  - zeus_mature_project_foundation/architecture/self_check/authority_index.md
  - zeus_mature_project_foundation/docs/architecture/zeus_durable_architecture_spec.md
  - zeus_mature_project_foundation/docs/governance/zeus_change_control_constitution.md
  - zeus_mature_project_foundation/architecture/maturity_model.yaml
files_may_change:
  - work_packets/FOUNDATION-MAINLINE-PLAN.md
  - architects_progress.md
  - architects_task.md
files_may_not_change:
  - src/**
  - migrations/**
  - architecture/**
  - docs/governance/**
  - docs/architecture/**
  - .github/workflows/**
  - .claude/CLAUDE.md
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
tests_required:
  - planning review only
parity_required: false
replay_required: false
rollback: Revert the planning packet file and paired Architects ledger updates together.
acceptance:
  - The next-stage task phases are explicitly named and ordered.
  - Stage goals and completion targets are mapped back to the tribunal overlay and mature foundation source package.
  - The team-opening gate is explicit and remains blocked until planning is complete.
  - The current-phase `P-*` queue remains closed and is not silently reopened.
evidence_required:
  - stage map
  - source-package crosswalk
  - completion ladder
  - team-opening gate note
  - rollback note
```

## Notes

- If planning reveals a contradiction between the tribunal overlay and the mature foundation package, record the contradiction explicitly instead of papering it over.
- If any future execution request is unclear, return to:
  - `zeus_final_tribunal_overlay`
  - `zeus_mature_project_foundation`
