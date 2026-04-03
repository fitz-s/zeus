# P2-H packet: max-based Day0 temporal closure

```yaml
work_packet_id: FEAT-P2H-008
packet_type: feature_packet
objective: "Replace the multiplicative Day0 temporal-closure composition with a strongest-signal closure rule to reduce over-certainty from correlated inputs."
why_this_now: "The external Day0 math review identified correlated-factor multiplication in temporal closure as the weakest part of the current Day0 stack. This packet applies the narrowest K3-only response: stop multiplying correlated closure signals and instead let the strongest closure signal dominate."
why_not_other_approach:
  - "Not a broader Day0 rewrite: this slice changes only the closure combiner inside the existing seam."
  - "Not leaving the multiplicative form in place: it likely over-locks observed highs too early in the day."
truth_layer: "K3 Day0 forecast seam only; no lifecycle, truth-contract, or control-plane semantics change."
control_layer: "Only changes how existing Day0 closure signals combine into observation dominance."
evidence_layer: "Targeted Day0 tests plus full pytest suite."
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
  - work_packets/FEAT-P2H-008-max-based-temporal-closure.md
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
rollback: "Revert this packet commit to restore the prior multiplicative temporal-closure combiner."
acceptance:
  - "Day0 temporal closure is driven by the strongest bounded closure signal rather than the product of correlated complements."
  - "Fresh trusted post-sunset finality and pre-sunrise caps remain intact."
  - "Full pytest suite stays green."
evidence_required:
  - "Targeted Day0 forecast tests green."
  - "Full pytest suite green."
```
