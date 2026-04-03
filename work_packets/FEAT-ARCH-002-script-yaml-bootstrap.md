# Architecture gate script bootstrap packet

```yaml
work_packet_id: FEAT-ARCH-002
packet_type: feature_packet
objective: "Let architecture gate scripts find the repo-local YAML parser even when invoked with system python3."
why_this_now: "After adding PyYAML to the repo virtualenv, the architecture scripts still fail under the documented `python3 scripts/...` invocation path because system python lacks yaml."
why_not_other_approach:
  - "Not requiring operators to remember a different interpreter for only some scripts: the repo already calls these as plain python3 scripts."
  - "Not adding another global/system dependency: the repo already owns a working local virtualenv."
truth_layer: "Tooling bootstrap only; no runtime truth/lifecycle/control semantics change."
control_layer: "Restores the ability to execute architecture gate scripts from the documented repo path."
evidence_layer: "Direct script execution with python3 after bootstrap plus existing test suite staying green."
zones_touched: []
invariants_touched: [INV-10]
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/governance/zeus_change_control_constitution.md
  - scripts/AGENTS.md
  - scripts/check_kernel_manifests.py
  - scripts/check_module_boundaries.py
files_may_change:
  - work_packets/FEAT-ARCH-002-script-yaml-bootstrap.md
  - scripts/_yaml_bootstrap.py
  - scripts/check_kernel_manifests.py
  - scripts/check_module_boundaries.py
files_may_not_change:
  - src/**
  - architecture/**
  - docs/governance/**
  - migrations/**
schema_changes: false
ci_gates_required: []
tests_required:
  - python3 scripts/check_kernel_manifests.py
  - python3 scripts/check_module_boundaries.py
  - pytest -q
parity_required: false
replay_required: false
rollback: "Revert this packet commit to restore the prior script import behavior if invocation policy changes."
acceptance:
  - "Both architecture scripts run successfully via system python3 from the repo root when the repo virtualenv contains PyYAML."
  - "No runtime/source semantics outside tooling bootstrap change."
evidence_required:
  - "Direct successful python3 script runs."
  - "Full pytest suite green."
```
