# BUG-LEGACY-SETTLEMENT-FALLBACK-DEDUPE

```yaml
work_packet_id: BUG-LEGACY-SETTLEMENT-FALLBACK-DEDUPE
packet_type: repair_packet
objective: Deduplicate legacy settlement fallback rows before they feed learning/risk summaries, so settlement sample counts and strategy settlement summaries stop disagreeing with headline realized PnL.
why_this_now: With the immediate wrong-path portfolio fallback repaired, the next visible truth contradiction is still live in paper risk rows: headline `realized_pnl` comes from `outcome_fact`, but fallback settlement summaries still flatten duplicate decision-log settlement artifacts and inflate counts/totals. Fresh repro confirmed the duplication enters in the legacy fallback reader before summary aggregation.
why_not_other_approach:
  - Jump back to `src/state/db.py` comparator cleanup first | real but not the source of the duplicated settlement summaries
  - Widen into RiskGuard headline math now | the current mismatch root is the fallback reader, not the headline source
  - Ignore the mismatch because headline pnl is right | duplicate summaries still corrupt cross-module truth and operator interpretation
truth_layer: legacy settlement fallback may remain as a staged compatibility path, but it must not double-count the same settled trade across multiple decision-log artifacts.
control_layer: keep the packet bounded to `src/state/decision_chain.py`, packet-bounded tests, and control surfaces. Only widen into RiskGuard output parity if implementation proves a second bounded packet is required.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted settlement-fallback pytest output, and a direct repro note showing duplicate artifacts before repair and deduped latest-wins output after repair.
zones_touched:
  - K1_governance
  - K2_runtime
invariants_touched:
  - INV-03
  - INV-09
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
  - src/state/AGENTS.md
  - tests/AGENTS.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - work_packets/BUG-LOAD-PORTFOLIO-MODED-DB-PROBE.md
  - src/state/decision_chain.py
  - tests/test_db.py
files_may_change:
  - work_packets/BUG-LEGACY-SETTLEMENT-FALLBACK-DEDUPE.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/state/decision_chain.py
  - tests/test_db.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/state/db.py
  - src/state/portfolio.py
  - src/riskguard/**
  - src/observability/**
  - src/control/**
  - src/execution/**
  - src/engine/**
  - tests/test_truth_surface_health.py
  - tests/test_riskguard.py
  - tests/test_pnl_flow_and_audit.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_db.py -k 'legacy_settlement'
parity_required: false
replay_required: false
rollback: Revert the legacy-settlement dedupe change, paired tests, and control-surface updates together; repo returns to the accepted mode-db-probe boundary with duplicate fallback settlements still inflating summary counts.
acceptance:
  - duplicate decision-log settlement artifacts for the same `trade_id` are deduped on the fallback read path using a deterministic latest-wins rule
  - targeted tests prove duplicate artifacts collapse to one logical settlement row
  - the packet leaves `src/state/db.py` comparator/shadow cleanup and any output-layer parity assertions explicitly open unless they prove necessary during implementation
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted settlement-fallback pytest output
  - direct duplicate-artifact repro note
```

## Notes

- This packet treats the fallback reader as a compatibility surface that still must obey single-truth counting.
- If implementation proves the RiskGuard output layer also needs a small parity assertion to lock the fix, freeze a follow-up packet or amend scope explicitly rather than widening silently.

## Evidence log

- Pending implementation.
