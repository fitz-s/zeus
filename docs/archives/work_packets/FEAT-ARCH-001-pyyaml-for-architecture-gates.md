# Architecture gate dependency packet

```yaml
work_packet_id: FEAT-ARCH-001
packet_type: feature_packet
objective: "Add the missing YAML parser dependency required by the new architecture manifests, scripts, and tests."
why_this_now: "The newly added architecture gates and tests import `yaml`, and the current virtualenv fails test collection because PyYAML is not installed."
why_not_other_approach:
  - "Not rewriting the new manifest/tests/scripts away from YAML: YAML is now the declared manifest format across kernel law surfaces."
  - "Not leaving the dependency implicit: that keeps local and CI collection broken."
truth_layer: "Dependency/install surface only; no canonical runtime truth or lifecycle semantics change."
control_layer: "Enables architecture scripts/tests to import their declared parser; no runtime control behavior change."
evidence_layer: "requirements diff, local install success, architecture tests collect and pass, full pytest suite green."
zones_touched: []
invariants_touched: [INV-10]
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/governance/zeus_change_control_constitution.md
  - AGENTS.md
  - requirements.txt
  - tests/test_architecture_contracts.py
files_may_change:
  - work_packets/FEAT-ARCH-001-pyyaml-for-architecture-gates.md
  - requirements.txt
files_may_not_change:
  - src/**
  - architecture/**
  - docs/governance/**
  - migrations/**
schema_changes: false
ci_gates_required: []
tests_required:
  - tests/test_architecture_contracts.py
  - pytest -q
parity_required: false
replay_required: false
rollback: "Remove PyYAML from requirements and revert the packet commit if the repo stops using YAML manifests."
acceptance:
  - "The virtualenv can import yaml."
  - "Architecture manifest tests collect and pass."
  - "Full pytest suite passes again."
evidence_required:
  - "requirements update committed."
  - "tests/test_architecture_contracts.py green."
  - "full pytest suite green."
```
