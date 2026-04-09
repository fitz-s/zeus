# BUG-LOAD-PORTFOLIO-MODED-DB-PROBE

```yaml
work_packet_id: BUG-LOAD-PORTFOLIO-MODED-DB-PROBE
packet_type: repair_packet
objective: Make `load_portfolio()` probe the mode-correct trade DB instead of unsuffixed `zeus.db`, so paper-mode canonical loader truth is not shadowed by unrelated stale rows in the mixed legacy file.
why_this_now: Fresh verification on the accepted trailing-loss branch changed the immediate diagnosis. `query_portfolio_loader_view()` returns `ok` on `zeus-paper.db`, but `load_portfolio()` still falls back to JSON because it probes `zeus.db`, where an unrelated stale trade keeps the loader in `stale_legacy_fallback`. The comparator-only packet was therefore superseded before implementation.
why_not_other_approach:
  - Continue with the comparator-only packet first | that misses the immediate paper-mode fallback trigger
  - Widen into both `portfolio.py` and `db.py` together | keep the packet narrow; comparator cleanup remains a follow-up seam
  - Accept JSON fallback as normal | that preserves the exact portfolio-truth degradation this investigation exposed
truth_layer: `load_portfolio()` for paper/live modes should probe the corresponding mode-isolated trade DB and only fall back to JSON when that mode-specific canonical loader truth is unavailable.
control_layer: keep the packet bounded to `src/state/portfolio.py`, packet-bounded tests, and control surfaces. Do not widen into `src/state/db.py` comparator logic or settlement dedupe here.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted load-portfolio/runtime pytest output, and a direct probe note showing `zeus-paper.db -> ok` while unsuffixed `zeus.db -> stale_legacy_fallback`.
zones_touched:
  - K1_governance
  - K2_runtime
invariants_touched:
  - INV-03
  - INV-08
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
  - work_packets/RISK-TRUTH-01-TRAILING-LOSS-AUTHORITY.md
  - src/state/portfolio.py
  - tests/test_runtime_guards.py
  - tests/test_db.py
files_may_change:
  - work_packets/BUG-LOAD-PORTFOLIO-MODED-DB-PROBE.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/state/portfolio.py
  - tests/test_runtime_guards.py
  - tests/test_db.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/state/db.py
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
  - .venv/bin/pytest -q tests/test_runtime_guards.py -k 'load_portfolio'
  - .venv/bin/pytest -q tests/test_db.py -k 'load_portfolio'
parity_required: false
replay_required: false
rollback: Revert the mode-aware DB probe change, paired tests, and control-surface updates together; repo returns to the accepted trailing-loss boundary with `load_portfolio()` still probing unsuffixed `zeus.db`.
acceptance:
  - `load_portfolio()` probes the mode-isolated DB for the current mode rather than the unsuffixed legacy DB
  - a paper-mode fixture where `zeus-paper.db` is healthy and `zeus.db` is stale no longer forces JSON fallback
  - the packet leaves `src/state/db.py` comparator/shadow cleanup explicitly open as follow-up work
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted load-portfolio/runtime pytest output
  - direct mode-db probe note
```

## Notes

- This packet explicitly supersedes the just-frozen comparator-only packet as the immediate fix for the active paper fallback symptom.
- The comparator/shadow seam in `src/state/db.py` remains real, but it becomes the next packet after mode-aware probing is repaired.
- If implementation proves a consumer outside `portfolio.py` must change immediately, stop and freeze the next packet instead of widening this one silently.

## Evidence log

- work-packet grammar output: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python scripts/check_work_packets.py` -> `work packet grammar ok`
- kernel-manifest check output: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- targeted load-portfolio/runtime pytest output:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/pytest -q tests/test_runtime_guards.py -k 'load_portfolio'` -> `5 passed, 77 deselected`
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/pytest -q tests/test_db.py -k 'load_portfolio'` -> `1 passed, 38 deselected`
- compile proof: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m py_compile src/state/portfolio.py tests/test_runtime_guards.py tests/test_db.py` -> success
- direct mode-db probe note:
  - `query_portfolio_loader_view(zeus-paper.db)` -> `ok`
  - `query_portfolio_loader_view(zeus.db)` -> `stale_legacy_fallback` with stale trade `08d6c939-038`
  - direct real-state `load_portfolio(state/positions-paper.json)` no longer emits the stale-fallback warning and now loads the paper positions from the sibling mode DB
  - route-selection smoke probe confirmed `load_portfolio()` called `get_connection(.../zeus-paper.db)` and did not call `get_connection(.../zeus.db)` for an explicit `positions-paper.json` fixture
- pre-close critic review: native `critic` subagent `Aristotle` -> `PASS` after confirming the packet removes the immediate wrong-path fallback while keeping the deeper comparator/shadow and settlement-authority drift explicit
- pre-close verifier review: native `verifier` subagent `Nietzsche` -> `PASS` after confirming scope purity, targeted tests, and the direct mode-db probe evidence
