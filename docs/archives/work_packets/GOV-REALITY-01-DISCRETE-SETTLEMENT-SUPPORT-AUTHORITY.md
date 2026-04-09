# GOV-REALITY-01-DISCRETE-SETTLEMENT-SUPPORT-AUTHORITY

```yaml
work_packet_id: GOV-REALITY-01-DISCRETE-SETTLEMENT-SUPPORT-AUTHORITY
packet_type: feature_packet
objective: Elevate discrete settlement support, bin contract kind, and bin settlement cardinality into explicit architecture authority so later packets cannot reason from a false market-contract world model.
why_this_now: Runtime and review have already exposed repeated reality drift around finite bin semantics. Continuing P4.3 before this authority upgrade risks building later work on a still-incomplete domain foundation.
why_not_other_approach:
  - Continue P4.3 first and patch reality misunderstandings later | would keep local packet correctness dependent on unstated domain assumptions
  - Fix only runtime code | does not solve the authority gap that lets future packets begin from false premises
truth_layer: Discrete settlement support is domain truth and must be explicit in authority, not buried in implementation detail.
control_layer: This packet is spec/governance-only. It pauses P4.3 advancement and upgrades the authority layer without changing runtime, schema, or packet-family implementation code.
evidence_layer: amendment file landed, control surfaces updated, rollback note, and explicit pause of P4.3 mainline implementation.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-10
required_reads:
  - AGENTS.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/TOP_PRIORITY_zeus_reality_crisis_response.md
  - src/types/market.py
files_may_change:
  - work_packets/GOV-REALITY-01-DISCRETE-SETTLEMENT-SUPPORT-AUTHORITY.md
  - docs/architecture/zeus_discrete_settlement_support_amendment.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
files_may_not_change:
  - AGENTS.md
  - src/**
  - tests/**
  - migrations/**
  - architecture/**
  - docs/governance/**
  - .github/workflows/**
  - .claude/CLAUDE.md
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
tests_required:
  - review only
parity_required: false
replay_required: false
rollback: Revert the amendment file, packet file, and paired slim control-surface updates together; mainline returns to the pre-amendment paused state.
acceptance:
  - discrete settlement support is promoted to explicit authority in a dedicated amendment file
  - packet templates/control surfaces now pause P4.3 advancement in favor of this amendment
  - no runtime, schema, or math implementation is mixed into this packet
evidence_required:
  - amendment file
  - control-surface pause note
  - rollback note
```

## Notes

- This is a P0-class foundation amendment executed now as a governance packet.
- P4.3 remains paused until this authority upgrade is accepted.
