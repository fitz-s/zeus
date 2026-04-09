# P2-H packet: MAE-aware mean-offset attenuation

```yaml
work_packet_id: FEAT-P2H-005
packet_type: feature_packet
objective: "Make the bounded P2-H mean-offset seam attenuate when the referenced model-bias row has high historical MAE."
why_this_now: "The mean-offset seam is now live on the analysis path and already sample-aware. The next bounded reliability step is to stop high-error bias rows from moving the analysis mean as strongly as low-error rows."
why_not_other_approach:
  - "Not a larger day0 backbone rewrite: that touches a different sub-surface and has higher behavioral blast radius."
  - "Not authority/lifecycle work: current priority is to keep P2-H inside K3 without crossing into kernel or governance zones."
  - "Not a new learned policy: we only attenuate an existing bounded heuristic using already-available provenance fields."
truth_layer: "K3 forecast-layer mean seam only; no canonical truth, lifecycle grammar, or control-plane semantics change."
control_layer: "Only changes how analysis-side member maxima are gently offset before bootstrap; no risk/control/lifecycle actuation changes."
evidence_layer: "Targeted forecast-uncertainty tests plus full pytest suite."
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
files_may_change:
  - work_packets/FEAT-P2H-005-mae-aware-mean-offset.md
  - src/signal/forecast_uncertainty.py
  - tests/test_forecast_uncertainty.py
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
  - pytest -q
parity_required: false
replay_required: false
rollback: "Revert this packet commit to restore the previous sample-aware-only mean-offset behavior."
acceptance:
  - "High-MAE bias rows attenuate or suppress mean offset in analysis_mean_context()."
  - "analysis_member_maxes() reflects the same attenuation without touching non-bias cases."
  - "No authority/control/lifecycle code changes occur."
evidence_required:
  - "Targeted forecast-uncertainty tests green."
  - "Full pytest suite green."
```

## Implementation notes
- Keep the attenuation monotone and bounded.
- Use only already-available `bias_reference` fields.
- Do not introduce new governance keys or truth surfaces.
