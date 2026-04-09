# P2-H packet: trusted-only nowcast source weight

```yaml
work_packet_id: FEAT-P2H-007
packet_type: feature_packet
objective: "Tighten the Day0 nowcast source gate so untrusted observation sources no longer contribute a half-strength blend weight."
why_this_now: "The external Day0 math review flagged `source_factor = 0.5` for untrusted sources as too permissive and not externally respectable. This is a narrow K3 slice that directly addresses that critique without widening the Day0 authority surface."
why_not_other_approach:
  - "Not a broader temporal-closure rewrite: that has larger blast radius and should follow a separate packet."
  - "Not leaving 0.5 in place: it encodes unjustified confidence in potentially bad observations."
truth_layer: "K3 Day0 forecast seam only; no lifecycle, truth-contract, or control-plane semantics change."
control_layer: "Only changes how untrusted Day0 observation provenance contributes to nowcast blend weighting and related context."
evidence_layer: "Targeted Day0 forecast-uncertainty tests plus full pytest suite."
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
  - work_packets/FEAT-P2H-007-trusted-source-nowcast.md
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
rollback: "Revert this packet commit to restore the prior 0.5 untrusted-source blend behavior."
acceptance:
  - "Untrusted Day0 observation sources contribute zero nowcast source weight instead of half-strength weight."
  - "Trusted sources retain existing positive blend behavior."
  - "Existing fresh trusted post-sunset finality behavior remains intact."
evidence_required:
  - "Targeted Day0 forecast tests green."
  - "Full pytest suite green."
```
