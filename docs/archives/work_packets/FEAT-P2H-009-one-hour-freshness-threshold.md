# P2-H packet: one-hour Day0 freshness threshold

```yaml
work_packet_id: FEAT-P2H-009
packet_type: feature_packet
objective: "Tighten the Day0 freshness gate so observations older than one hour are no longer treated as fresh for full-finality logic."
why_this_now: "The external Day0 review explicitly flagged trusted observations older than one hour during active heating as a deployment stop-sign. Current code still treats any positive freshness factor as fresh, which leaves a too-permissive full-finality path."
why_not_other_approach:
  - "Not a broader daytime attenuation rewrite: that is a larger heuristic decision and should follow a separate packet."
  - "Not leaving fresh_observation at >0: that contradicts the external Day0 review's explicit stop-sign."
truth_layer: "K3 Day0 forecast seam only; no lifecycle, truth-contract, or control-plane semantics change."
control_layer: "Only changes the boolean freshness gate that controls when Day0 can claim fresh trusted finality."
evidence_layer: "Targeted Day0 forecast tests plus full pytest suite."
zones_touched: [K3_extension]
invariants_touched: [INV-06, INV-10]
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/governance/zeus_change_control_constitution.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/zones.yaml
  - architecture/negative_constraints.yaml
  - AGENTS.md
  - src/signal/forecast_uncertainty.py
  - tests/test_forecast_uncertainty.py
  - tests/test_day0_signal.py
files_may_change:
  - work_packets/FEAT-P2H-009-one-hour-freshness-threshold.md
  - src/signal/forecast_uncertainty.py
  - tests/test_forecast_uncertainty.py
  - tests/test_day0_signal.py
files_may_not_change:
  - src/state/**
  - src/engine/**
  - src/riskguard/**
  - architecture/**
  - docs/governance/**
  - migrations/**
schema_changes: false
ci_gates_required: []
tests_required:
  - tests/test_forecast_uncertainty.py
  - tests/test_day0_signal.py
  - pytest -q
parity_required: false
replay_required: false
rollback: "Revert this packet commit to restore the previous positive-freshness-factor freshness gate."
acceptance:
  - "Observations older than one hour no longer count as fresh for Day0 full-finality logic."
  - "Fresh trusted observations at or within one hour still retain the existing positive path."
  - "Full pytest suite stays green."
evidence_required:
  - "Targeted Day0 forecast tests green."
  - "Full pytest suite green."
```
