# Ops packet: prefix root progress/task filenames

```yaml
work_packet_id: FEAT-OPS-003
packet_type: feature_packet
objective: "Rename the generic root progress/task files to prefixed Zeus names and update in-repo references so multiple active team ledgers no longer collide on generic filenames."
why_this_now: "The repo now has multiple concurrent team and lane ledgers (`architects_*` plus root generic files). Keeping bare `progress.md` / `task.md` names creates ambiguous references and operator confusion."
why_not_other_approach:
  - "Not leaving generic names in place: the user explicitly wants prefixed names because multiple ledgers now coexist."
  - "Not creating another duplicate ledger: renaming the existing root files is lower-risk than introducing more parallel truth surfaces."
truth_layer: "Naming and reference cleanup only; no runtime, schema, or lifecycle semantics change."
control_layer: "Clarifies operator/control references to the root Zeus queue and progress surfaces."
evidence_layer: "Reference grep after rename plus git diff review."
zones_touched: []
invariants_touched: [INV-10]
required_reads:
  - AGENTS.md
  - docs/README.md
  - docs/reference/repo_layout.md
  - .claude/baton_state.json
  - zeus_progress.md
  - zeus_task.md
files_may_change:
  - work_packets/FEAT-OPS-003-prefix-root-progress-task.md
  - zeus_progress.md
  - zeus_task.md
  - .claude/baton_state.json
  - docs/README.md
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
  - rg -n "\\bprogress\\.md\\b|\\btask\\.md\\b|\\bzeus_progress\\.md\\b|\\bzeus_task\\.md\\b" .
parity_required: false
replay_required: false
rollback: "Rename the files back and revert the reference updates."
acceptance:
  - "Root `progress.md` is renamed to `zeus_progress.md`."
  - "Root `task.md` is renamed to `zeus_task.md`."
  - "All in-repo references that mean the root Zeus ledgers point at the new names."
  - "No unrelated files are staged."
evidence_required:
  - "Reference grep shows only intended names for the root Zeus ledgers."
  - "Diff review confirms only naming/reference cleanup landed."
```
