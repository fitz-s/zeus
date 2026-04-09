# RISK-TRUTH-01-TRAILING-LOSS-AUTHORITY

```yaml
work_packet_id: RISK-TRUTH-01-TRAILING-LOSS-AUTHORITY
packet_type: repair_packet
objective: Replace baseline-driven `daily_loss` / `weekly_loss` semantics with trailing 24h / 7d equity-loss truth, while making degraded history explicit instead of silently reusing all-time or session baselines.
why_this_now: The user confirmed that `daily_loss` must mean current equity versus 24-hours-ago equity, not all-time loss or an arbitrary session slice. Fresh repo evidence shows paper `daily_loss = 13.26` while rows ~24h earlier already had `total_pnl = -13.26`, proving the current value is semantically wrong. The same symptom also exposes deeper truth drift, so this packet must fix the trailing-loss authority surface without hiding the wider risk-truth inconsistencies.
why_not_other_approach:
  - Patch the displayed number only | that would hide a deeper risk-truth contract bug
  - Widen immediately into portfolio fallback and settlement-authority unification | those are likely next packets, but this packet must first lock trailing-loss semantics cleanly
  - Keep `daily_baseline_total` / `weekly_baseline_total` and just improve seeding | the user requirement is trailing 24h / 7d loss, not arbitrary baseline anchoring
truth_layer: `daily_loss` is the non-negative equity delta between now and the latest trustworthy risk-state row at-or-before now-minus-24-hours. `weekly_loss` is the non-negative equity delta between now and the latest trustworthy risk-state row at-or-before now-minus-7-days. If no trustworthy reference exists, the truth must degrade explicitly rather than reusing all-time loss.
control_layer: keep this packet bounded to `src/riskguard/riskguard.py`, packet-bounded tests, and control surfaces. Do not widen into `src/state/db.py`, `src/state/portfolio.py`, `src/observability/status_summary.py`, or broader PnL/settlement unification unless implementation proves a real consumer mismatch and a new packet is frozen.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted riskguard/pnl pytest output, and a note demonstrating 24h-equal equity => zero daily loss plus explicit degraded-truth behavior.
zones_touched:
  - K1_governance
  - K2_runtime
invariants_touched:
  - INV-03
  - INV-05
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
  - tests/AGENTS.md
  - src/riskguard/AGENTS.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - work_packets/INTEGRATE-TRUTH-MAINLINE-WITH-DATA-EXPANSION.md
  - src/riskguard/riskguard.py
  - tests/test_riskguard.py
  - tests/test_pnl_flow_and_audit.py
  - docs/adversarial_test_results.md
files_may_change:
  - work_packets/RISK-TRUTH-01-TRAILING-LOSS-AUTHORITY.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/riskguard/riskguard.py
  - tests/test_riskguard.py
  - tests/test_pnl_flow_and_audit.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/state/db.py
  - src/state/portfolio.py
  - src/observability/status_summary.py
  - src/control/**
  - src/execution/**
  - src/engine/**
  - tests/test_architecture_contracts.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_riskguard.py -k 'trailing_loss or daily_loss or weekly_loss'
  - .venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'daily_loss or weekly_loss'
parity_required: false
replay_required: false
rollback: Revert the trailing-loss helper, riskguard loss semantics, and paired test/control-surface updates together; this returns the branch to the pre-packet baseline-driven loss behavior while preserving the broader truth-unification backlog explicitly.
acceptance:
  - `daily_loss` equals `max(0, equity_ref_24h - equity_now)` and no longer mirrors all-time loss when total PnL was already unchanged 24h earlier
  - `weekly_loss` equals `max(0, equity_ref_7d - equity_now)` under the same trustworthy-reference rules
  - trustworthy reference rows are eligible only when `details_json` parses, `initial_bankroll`, `total_pnl`, and `effective_bankroll` are finite, and `abs((initial_bankroll + total_pnl) - effective_bankroll) <= 0.01`
  - per-window contract is exactly `daily_loss`, `daily_loss_level`, `daily_loss_status`, `daily_loss_source`, `daily_loss_reference` and the weekly counterparts with the same suffixes
  - allowed status values are exactly `ok | insufficient_history | inconsistent_history | no_reference_row`
  - `*_reference` is either `null` or exactly `{row_id, checked_at, initial_bankroll, total_pnl, effective_bankroll}`
  - status mapping is pinned: `ok` = eligible row found at-or-before cutoff; `no_reference_row` = `risk_state` has no rows at all; `insufficient_history` = rows exist but none are at-or-before the cutoff; `inconsistent_history` = rows exist at-or-before the cutoff but none satisfy the eligibility rule
  - when status is not `ok`, the numeric loss field is `0.0`, the corresponding loss level is forced `YELLOW`, and `*_source = no_trustworthy_reference_row` so degraded truth stays explicit instead of being silently treated as healthy or reusing all-time baseline
  - the packet leaves broader portfolio-fallback and settlement-authority unification work explicitly open instead of disguising them as solved
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted riskguard/pnl pytest output
  - trailing-loss truth note
```

## Notes

- This is packet 1 of a broader risk/PnL truth-unification family.
- The critic/reviewer lane for this packet must look beyond the single `daily_loss` symptom and watch for deeper truth drift that this fix reveals, especially fallback portfolio truth and mixed settlement authority.
- Status contract is pinned:
  - `ok`: a trustworthy reference row exists at-or-before the trailing cutoff
  - `insufficient_history`: rows exist, but none are yet at-or-before the trailing cutoff
  - `no_reference_row`: the `risk_state` table has no rows at all
  - `inconsistent_history`: rows exist at-or-before the cutoff, but none are trustworthy under the pinned eligibility rule
- If implementation proves that a downstream consumer cannot tolerate `null` loss fields or the new truth-status metadata without a targeted change, stop and freeze the next packet rather than widening this one silently.

## Evidence log

- work-packet grammar output: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python scripts/check_work_packets.py` -> `work packet grammar ok`
- kernel-manifest check output: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- targeted riskguard/pnl pytest output:
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/pytest -q tests/test_riskguard.py` -> `44 passed`
  - `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/pytest -q tests/test_riskguard.py::TestRiskGuardTrailingLossSemantics tests/test_pnl_flow_and_audit.py::test_inv_status_surfaces_trailing_loss_audit_fields` -> `7 passed`
- compile proof: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m py_compile src/riskguard/riskguard.py tests/test_riskguard.py tests/test_pnl_flow_and_audit.py` -> success
- trailing-loss truth note:
  - `daily_loss` / `weekly_loss` now read from historical `risk_state` rows at-or-before trailing cutoffs, not `portfolio.daily_baseline_total` / `weekly_baseline_total`
  - eligible reference rows now require parsed finite `initial_bankroll`, `total_pnl`, and `effective_bankroll`, plus `abs((initial_bankroll + total_pnl) - effective_bankroll) <= 0.01`
  - degraded history is explicit: `*_loss = 0.0`, `*_loss_level = YELLOW`, `*_loss_source = no_trustworthy_reference_row`, and `*_loss_reference = null`
  - regression coverage now includes the bad-boundary-row path where an inconsistent row at the cutoff is skipped in favor of an older trustworthy reference
- pre-close critic review: native `critic` subagent `Darwin` -> `PASS` on `f6a49e4` after confirming degraded history remains visible and wider portfolio/settlement drift stays explicit
- pre-close verifier review: native `verifier` subagent `Singer` -> `PASS` on `f6a49e4` after confirming trailing-loss contract fields, skip-boundary regression, and targeted evidence
