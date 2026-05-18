# WAVE-2 PR-Level Critic Brief (PRE-OPEN)

**Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17`
**Base**: `origin/main` at `800ca68b38` (post PR #139)
**Scope**: 68 commits / 96 files / +14,320/-164 LOC
**Role**: Fresh-context adversarial critic. NO scope-narrowing primer.
**Cwd**: `/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/zeus-deep-alignment-audit-skill`

## Mandatory pre-work
1. `cat /tmp/wave2_critic_commits.txt` — full commit list.
2. Read `docs/operations/task_2026-05-17_post_karachi_remediation/agent_registry.jsonl` — every prior phase critic + remediation. Do NOT re-flag fixes already shipped.
3. Read `docs/operations/task_2026-05-17_post_karachi_remediation/WAVE2_PHASE_CRITIC.md` — prior phase verdict + remediation log.
4. Reproduce regression baseline: `cd ../../.. && .venv/bin/python -m pytest .claude/worktrees/zeus-deep-alignment-audit-skill/tests/state/test_f109_consolidator_boot_wire.py .claude/worktrees/zeus-deep-alignment-audit-skill/tests/test_k1_reader_isolation.py .claude/worktrees/zeus-deep-alignment-audit-skill/tests/test_settlement_command_coverage_invariant.py .claude/worktrees/zeus-deep-alignment-audit-skill/tests/test_observation_instants_v2_freshness.py .claude/worktrees/zeus-deep-alignment-audit-skill/tests/test_operator_script_lock_contract.py --rootdir=/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/zeus-deep-alignment-audit-skill -q`. Expected: 148 passed, 5 skipped. Report deviation.

## Probe contract (full-spectrum + PR-specific)

Spot-trace EACH probe; do not rely on commit message claims.

1. **F109 consolidator boot wire**: `src/main.py:1300-1340` + `:1427`. If `consolidate()` raises, does the daemon continue? If it succeeds but voids the WRONG row (e.g. chain snapshot stale), what's the audit trail? Karachi single-row safety.
2. **K1 helper SELECT/INSERT correctness sweep**: any remaining bare `FROM <world-class table>` under `get_forecasts_connection_with_world()` in `src/**` or `scripts/**`? Grep beyond the test's allow-list.
3. **Karachi-bridge TRIGGER**: `scripts/migrations/202605_*_trade_decisions_*.py` + `src/state/trade_decisions_synthesizer.py`. SAVEPOINT-safe? What happens on TRIGGER failure inside `log_trade_entry`?
4. **F44 obs_v2_live_tick**: APScheduler job registration in `src/data/ingest_main.py`. Does the boot order guarantee scheduler is alive when tick fires? Catch-up policy on missed ticks?
5. **F35/F39/F9/F34 opt-in defaults**: confirm `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED`, `ZEUS_COST_OF_FILL_OPTIMIZER_ENABLED`, `ZEUS_AUTO_PROMOTE_CALIBRATION_PAIRS_V2`, `ZEUS_BRIDGE_VIA_INGEST_MAIN` all default OFF/preserve-legacy. Grep for any test or boot path that flips them ON implicitly.
6. **Provenance headers**: every new file under `src/`, `tests/`, `scripts/` in the diff has `# Created:` + `# Authority basis:` header? Audit per `CLAUDE.md` "File-header provenance rule".
7. **No-manual-precedent audit**: any new operator script doing raw `sqlite3.connect()` without writer-lock? See `tests/test_operator_script_lock_contract.py` allow-list — anything new that bypasses?
8. **Karachi blast radius**: walk every commit's effect on position `c30f28a5-d4e` (token, condition_id from `state/zeus-world.db` if needed). Specifically TRIGGER + consolidator + F44 tick + F39 opt-in.
9. **RUN-12 settlement_commands coverage**: does the antibody actually catch a settlement_commands miss? Sed-break/restore meta-verify.
10. **Test-suite drift**: any new `@pytest.mark.xfail` / `skip` lacking explicit removal-criterion comment? Any antibody that's a no-op tautology?
11. **Cycle/lifecycle hard-fail wiring**: `src/engine/lifecycle_events.py` sentinel skip + `update_trade_lifecycle` hard-raise. Does this fail-closed on the actual Karachi-failure pattern (silent except)?
12. **`station_migration_alerts.json` runtime artifact**: tracked vs gitignored? If tracked, why is it stashed during rebase rather than .gitignore'd?

## Verdict format
- Per-probe PASS / NEEDS-FIX / FAIL with file:line citation (grep-verified at critic-time, not from commit message).
- For each NEEDS-FIX / FAIL: severity (SEV-1 / SEV-2 / SEV-3) + suggested fix shape.
- Final: APPROVE-FOR-OPEN / NEEDS-FIX-BEFORE-OPEN / BLOCK.

## Anti-rubber-stamp guard
- Do NOT prime yourself with "WAVE-1 critic already reviewed this" — re-read WAVE2_PHASE_CRITIC.md probe list and validate each fix landed.
- File path citations are required; "looks ok" is rejected.
- Two-probe minimum per cross-module relationship claim (per `feedback_one_failed_test_is_not_a_diagnosis`).

Write your verdict to:
`docs/operations/task_2026-05-17_post_karachi_remediation/WAVE2_PR_CRITIC_VERDICT.md`
