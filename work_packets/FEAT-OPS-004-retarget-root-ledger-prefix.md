# Ops packet: retarget root ledger prefix

```yaml
work_packet_id: FEAT-OPS-004
packet_type: feature_packet
objective: "Rename the root Zeus ledger pair from zeus_progress/task to a clearer root_* prefix and update the in-repo references that point at them."
why_this_now: "The user judged the zeus_* prefix still too ambiguous because the whole repo is already Zeus. The root ledger pair needs a prefix that distinguishes it from team-specific ledgers by role, not project name."
why_not_other_approach:
  - "Not keeping the zeus_* names: they do not actually disambiguate the root pair from other Zeus team ledgers."
  - "Not creating another duplicate ledger: rename the existing root pair instead of adding another truth surface."
truth_layer: "Naming and reference cleanup only; no runtime, schema, or lifecycle semantics change."
control_layer: "Clarifies operator/control references to the root ledger pair versus team-specific ledgers."
evidence_layer: "Reference grep after rename plus diff review."
zones_touched: []
invariants_touched: [INV-10]
required_reads:
  - AGENTS.md
  - docs/reference/repo_layout.md
  - .claude/baton_state.json
  - root_progress.md
  - root_task.md
files_may_change:
  - work_packets/FEAT-OPS-004-retarget-root-ledger-prefix.md
  - root_progress.md
  - root_task.md
  - .claude/baton_state.json
  - docs/reference/repo_layout.md
files_may_not_change:
  - src/**
  - migrations/**
  - architecture/**
  - docs/governance/**
  - docs/architecture/**
  - tests/**
schema_changes: false
ci_gates_required: []
tests_required:
  - rg -n "\\bprogress\\.md\\b|\\btask\\.md\\b|\\bzeus_progress\\.md\\b|\\bzeus_task\\.md\\b|\\broot_progress\\.md\\b|\\broot_task\\.md\\b" .
parity_required: false
replay_required: false
rollback: "Rename the files back and revert the reference updates."
acceptance:
  - "Root `root_progress.md` is renamed to `root_progress.md`."
  - "Root `root_task.md` is renamed to `root_task.md`."
  - "All in-repo references that mean the root ledger pair point at the new root_* names."
  - "No unrelated files are staged."
evidence_required:
  - "Reference grep shows the intended root_* names on active root-ledger references."
  - "Diff review confirms only naming/reference cleanup landed."
```
