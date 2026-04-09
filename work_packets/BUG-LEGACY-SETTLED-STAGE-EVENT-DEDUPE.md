# BUG-LEGACY-SETTLED-STAGE-EVENT-DEDUPE

```yaml
work_packet_id: BUG-LEGACY-SETTLED-STAGE-EVENT-DEDUPE
packet_type: repair_packet
objective: Deduplicate legacy `POSITION_SETTLED` stage events before they feed authoritative settlement queries, so settlement sample counts and strategy settlement summaries stop disagreeing with headline realized PnL.
why_this_now: Fresh verification after freezing the fallback-reader dedupe packet showed the live paper mismatch still comes first from duplicated legacy `POSITION_SETTLED` rows in `position_events_legacy`, not from `decision_log` fallback flattening. `query_authoritative_settlement_rows()` prefers `query_settlement_events()` first, and the active duplicate trade ids (`0c108102-032`, `6f8ce461-902`, `9e97c78f-2a8`) are doubled there. The fallback-reader packet is therefore superseded before implementation by this tighter packet on the real first-counting seam.
why_not_other_approach:
  - Keep implementing the decision_log fallback dedupe packet first | it would not fix the live mismatch while stage-event duplicates still win first
  - Jump to RiskGuard output parity immediately | the root duplication still enters earlier at the stage-event reader
  - Merge stage-event dedupe with comparator cleanup | separate bottom-layer truth seams; keep the packet narrow
truth_layer: legacy stage-event compatibility rows may still exist, but authoritative settlement queries must not count the same settled trade more than once when multiple legacy `POSITION_SETTLED` rows exist for that trade.
control_layer: keep the packet bounded to `src/state/db.py`, packet-bounded tests, and control surfaces. Do not widen into `src/state/decision_chain.py` fallback dedupe or `src/riskguard/**` output parity in this packet.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted settlement-query pytest output, and a direct repro note showing duplicate stage events before repair and deduped latest-wins rows after repair.
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
  - src/state/db.py
  - tests/test_db.py
files_may_change:
  - work_packets/BUG-LEGACY-SETTLED-STAGE-EVENT-DEDUPE.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/state/db.py
  - tests/test_db.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/state/portfolio.py
  - src/state/decision_chain.py
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
  - .venv/bin/pytest -q tests/test_db.py -k 'authoritative_settlement or query_settlement_events'
parity_required: false
replay_required: false
rollback: Revert the stage-event dedupe change, paired tests, and control-surface updates together; repo returns to the accepted mode-db-probe boundary with duplicate legacy stage events still inflating authoritative settlement rows.
acceptance:
  - duplicate legacy `POSITION_SETTLED` rows for the same `runtime_trade_id` are deduped at the stage-event query path using a deterministic latest-wins rule
  - targeted tests prove duplicate stage events collapse to one logical authoritative settlement row
  - the packet leaves `src/state/decision_chain.py` fallback-reader dedupe and any RiskGuard output parity assertions explicitly open unless they prove necessary later
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted settlement-query pytest output
  - direct duplicate-stage-event repro note
```

## Notes

- This packet supersedes the just-frozen fallback-reader dedupe packet as the immediate fix for the live mismatch.
- The fallback-reader dedupe remains a later compatibility cleanup slice, but it is not the first-counting seam for the active paper contradiction.
- If implementation proves the RiskGuard output layer also needs a parity assertion, freeze that next packet explicitly instead of widening silently.

## Evidence log

- work-packet grammar output: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python scripts/check_work_packets.py` -> `work packet grammar ok`
- kernel-manifest check output: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- targeted settlement-query pytest output:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/pytest -q tests/test_db.py -k 'authoritative_settlement or query_settlement_events'` -> `7 passed, 36 deselected`
  - direct seam subset: `python3 -m pytest -q tests/test_db.py -k 'query_settlement_events_latest_wins_by_runtime_trade_id or query_settlement_events_preserves_distinct_trade_ids_when_deduping_duplicates or query_authoritative_settlement_rows_dedupes_legacy_stage_rows_by_trade_id'` -> `3 passed, 40 deselected`
- compile proof: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m py_compile src/state/db.py tests/test_db.py` -> success
- direct duplicate-stage-event repro note:
  - before repair, real `zeus-paper.db` returned `22` authoritative settlement rows with only `19` unique trade ids
  - after repair, the same probe returns `19` authoritative rows / `19` unique trade ids
  - `query_learning_surface_summary(...).settlement_sample_size` now reports `19` and by-strategy totals align with headline realized PnL
  - synthetic low-limit probe now returns distinct latest rows under a limit (`['t3', 't2']` for limit=2) instead of letting duplicates crowd them out
- pre-close critic review: native `critic` subagent `Euclid` -> `PASS` after confirming the packet fixes the first active counting seam while keeping comparator/shadow, fallback-reader, and output parity debt explicit
- pre-close verifier review: native `verifier` subagent `Jason` -> `PASS` after confirming targeted tests, compile proof, and real-state settlement-count convergence
