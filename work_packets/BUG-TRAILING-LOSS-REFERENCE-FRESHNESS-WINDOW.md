# BUG-TRAILING-LOSS-REFERENCE-FRESHNESS-WINDOW

```yaml
work_packet_id: BUG-TRAILING-LOSS-REFERENCE-FRESHNESS-WINDOW
packet_type: repair_packet
objective: Make trailing 24h/7d loss use a trustworthy reference row near the requested lookback window instead of silently falling back to an arbitrarily older consistent row.
why_this_now: Fresh evidence after the accepted loader packet shows the current trailing-loss implementation still violates the user's stated requirement. On the clean branch, `_trailing_loss_reference()` currently returns row `6888` at `2026-04-08T04:15:08+00:00` as the 24h reference for `2026-04-09T17:58:43+00:00`, which is `13.73h` older than the true 24h cutoff. That makes `daily_loss=13.26` even though the implementation was supposed to mean now-minus-24h loss rather than an arbitrary older slice.
why_not_other_approach:
  - Treat stale runtime artifacts as the only remaining issue | fresh clean-branch probes prove the reference-selection code still allows an overly old row
  - Keep latest trustworthy row before cutoff semantics | that explicitly contradicts the user's requirement and hides the missing 24h reference problem
  - Rewrite full risk_state history handling now | too wide; first bound the reference window semantics
truth_layer: trailing loss must be anchored to a trustworthy row close to the requested cutoff, otherwise the result must degrade explicitly rather than inventing a 24h/7d loss from a much older slice.
control_layer: keep this packet bounded to `src/riskguard/riskguard.py`, packet-bounded tests, and control surfaces. Do not widen into `src/observability/**`, `src/state/db.py`, or runtime artifact refresh in this packet.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted trailing-loss pytest output, and a direct probe note showing the current implementation selecting a reference `13.73h` older than the 24h cutoff.
zones_touched:
  - K1_governance
  - K2_runtime
invariants_touched:
  - INV-03
  - INV-07
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
  - src/riskguard/AGENTS.md
  - tests/AGENTS.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - work_packets/RISK-TRUTH-01-TRAILING-LOSS-AUTHORITY.md
  - src/riskguard/riskguard.py
  - tests/test_riskguard.py
files_may_change:
  - work_packets/BUG-TRAILING-LOSS-REFERENCE-FRESHNESS-WINDOW.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/riskguard/riskguard.py
  - tests/test_riskguard.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/state/**
  - src/observability/**
  - src/control/**
  - src/execution/**
  - src/engine/**
  - tests/test_pnl_flow_and_audit.py
  - tests/test_runtime_guards.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - /Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/pytest -q tests/test_riskguard.py -k 'TrailingLossSemantics'
parity_required: false
replay_required: false
rollback: Revert the trailing-loss reference-window change, paired tests, and control-surface updates together; repo returns to the accepted-but-now-superseded trailing-loss boundary.
acceptance:
  - trailing 24h/7d loss no longer uses an arbitrarily older reference row outside the allowed freshness window
  - when no trustworthy row exists close enough to the cutoff, the result degrades explicitly instead of manufacturing a loss from an older slice
  - packet-bounded tests prove both the happy path and the too-old-reference path
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted trailing-loss pytest output
  - direct trailing-reference probe note
```

## Notes

- This packet intentionally revisits the earlier trailing-loss work because later repo truth disproved that the accepted semantics were strict enough.
- The critic/reviewer lane must keep asking whether the new reference-window rule truly enforces near-cutoff semantics rather than merely changing status strings.
- If implementation proves risk-state artifact refresh must be bundled immediately, stop and freeze that operational packet separately.

## Evidence log

- 2026-04-09: direct probe showed `_trailing_loss_reference()` selecting row `6888` at `2026-04-08T04:15:08+00:00` for a 24h cutoff at `2026-04-08T17:58:43+00:00`, i.e. `13.73h` older than the requested daily-loss reference point.
- 2026-04-09: `python3 scripts/check_work_packets.py` -> `work packet grammar ok`
- 2026-04-09: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- 2026-04-09: `python3 -m py_compile src/riskguard/riskguard.py tests/test_riskguard.py` -> success
- 2026-04-09: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/pytest -q tests/test_riskguard.py -k 'TrailingLossSemantics'` -> `7 passed, 38 deselected`
- 2026-04-09: direct live-state probe after the fix returned `24h -> insufficient_history` and `7d -> inconsistent_history`, proving the too-old row is no longer accepted.
