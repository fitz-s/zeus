# P2-H packet: sanitize invalid bias provenance

```yaml
work_packet_id: FEAT-P2H-006
packet_type: feature_packet
objective: "Sanitize invalid or non-finite bias-reference inputs before they shape P2-H mean-offset behavior or leak into forecast context artifacts."
why_this_now: "The mean-offset seam is now live, sample-aware, and MAE-aware. The next bounded reliability step is to stop NaN/negative/invalid provenance values from silently producing unstable offsets or non-JSON-safe context fields."
why_not_other_approach:
  - "Not a broader day0 rewrite: this slice stays on the mean seam that was just activated."
  - "Not leaving malformed provenance untouched: that risks NaN/invalid values entering artifacts and weakens the new reliability work."
truth_layer: "K3 forecast-layer mean seam only; no canonical truth, lifecycle grammar, or control semantics change."
control_layer: "Only changes how analysis-side bias provenance is normalized before mean-offset computation and context emission."
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
  - work_packets/FEAT-P2H-006-sanitize-bias-provenance.md
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
rollback: "Revert this packet commit to restore the previous mean-seam provenance handling."
acceptance:
  - "Non-finite or malformed bias-reference values no longer leak through as unstable mean-offset behavior."
  - "forecast-context fields stay finite/normalized for invalid provenance inputs."
  - "Valid provenance behavior remains unchanged."
evidence_required:
  - "Targeted forecast-uncertainty tests green."
  - "Full pytest suite green."
```
