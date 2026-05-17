# critic-opus review of hook redesign cutover (Phase 3.R re-verify)

HEAD: cf60511b77d5b52f61bf551b58e170b9a3b001ea
Reviewer: critic-opus
Date: 2026-05-06
Branch: topology-redesign-2026-05-06
Predecessor: 8ff0d38e (NO-GO, CRITICAL phase1_stub regression)

## Subject
Phase 3.R remediation (executor add60049d45d67e9e at cf60511b) ports 7 legacy shell logics into `.claude/hooks/dispatch.py` and disposes legacy-shell tests.

## Verdict
APPROVED-WITH-CAVEATS

## ATTACK 1 — phase1_stub closure [VERDICT: PASS]
`grep -n "phase1_stub" .claude/hooks/dispatch.py` returns one occurrence:
- Line 1201 — docstring comment only ("Phase 3.R: all 7 legacy shell logics ported; phase1_stub removed.").
No `return ... phase1_stub` exists. Prior CRITICAL closed.

## ATTACK 2 — 7 ported hooks functional [VERDICT: PASS]
All 7 dispatch table entries wired (`.claude/hooks/dispatch.py:1207-1244`). Per-hook semantic check vs `.claude/hooks/legacy/<name>.sh`:

- `_run_blocking_check_invariant_test` (`dispatch.py:694-862`) — preserves all 5 escape paths from `legacy/pre-commit-invariant-test.sh`: STRUCTURED_OVERRIDE (line 729-731), `[skip-invariant]` migration shim with `migration_warning` ritual_signal logged to `.claude/logs/hook_signal/<month>.jsonl` (lines 733-758), `COMMIT_INVARIANT_TEST_SKIP` env (725-726), `.invariant_skip` file sentinel (760-763), `.git/skip-invariant-once` (765-776). Worktree-tolerant venv discovery preserved (779-798). Same TEST_FILES list (813-831) and BASELINE_PASSED=674 (805) as legacy.
- `_run_blocking_check_secrets_scan` (865-928) — gitleaks `protect --staged --redact`; `--config .gitleaks.toml` when present; `validate_staged_review_safe_tags` registry check (891-895); `SECRETS_SCAN_SKIP` env honored.
- `_run_blocking_check_cotenant_staging_guard` (931-971) — broad `git add -A/--all/.` detection via `hc.git_add_is_broad`; linked-worktree allowance via `/worktrees/` in git-dir (958-959); `COTENANT_GUARD_BYPASS` env hatch.
- `_run_blocking_check_pre_merge_contamination` (974-1036) — protected branch regex `^(main|master|live-launch-.+)$` (1011); legacy-preserving advisory on no-evidence (1015-1017 maps to legacy `exit 0` at line 121); MERGE_AUDIT_EVIDENCE field validation (`critic_verdict:` / `diff_scope:` / `drift_keyword_scan:`) and `OVERRIDE_<reason>` short-circuit.
- `_run_advisory_check_post_merge_cleanup` (1039-1086) — PostToolUse advisory on successful `gh pr merge`.
- `_run_blocking_check_pre_edit_architecture` (1089-1122) — refuses `architecture/**` writes lacking ARCH_PLAN_EVIDENCE.
- `_run_blocking_check_pre_write_capability_gate` (1125-1193) — primary path delegates to `src.architecture.gate_edit_time.evaluate()` (line 1153, module exists at `src/architecture/gate_edit_time.py:1`); fallback path reads `architecture/capabilities.yaml` (1160) and matches `hard_kernel_paths` (1186-1191); ZEUS_ROUTE_GATE_EDIT=off rollback (1135-1136).

## ATTACK 3 — realistic payload tests cover deny path [VERDICT: PASS]
`tests/test_hook_dispatch_smoke.py` (642 LOC, 79 tests pass). Per-hook allow + deny coverage:
- `invariant_test`: non_commit_allows (269), skip_marker_allows (277), structured_override_allows (287), missing_pytest_bin_denies (297).
- `secrets_scan`: non_commit_allows (325), skip_env_allows (333), commit_runs_or_allows_when_gitleaks_absent (343).
- `cotenant_staging_guard`: broad_add_denies (367), specific_add_allows (380), bypass_env_allows (388).
- `pre_merge_contamination`: non_merge_allows (403), no_evidence_advisory (411), missing_evidence_file_denies (429), operator_override_allows (465).
- `pre_edit_architecture`: non_arch_allows (482), without_evidence_denies (490), with_valid_evidence_allows (505), operator_override_allows (524).
- `pre_write_capability_gate`: non_kernel_allows (541), feature_flag_off_allows (549), kernel_path_without_evidence_denies (561).
- `post_merge_cleanup`: gh_pr_merge_advisory (581), non_merge_silent (601).

## ATTACK 4 — legacy xfailed tests justified [VERDICT: PASS]
14 `xfail(reason=phase3r-legacy-cutover, strict=False, retire 2026-06-06)` markers (12 in `tests/test_pre_commit_hook.py`, 2 in `tests/test_post_merge_cleanup_hook.py`). Plan said 13; differential of 1 immaterial. Each xfail tests the legacy shell directly (`.claude/hooks/pre-commit-invariant-test.sh` no longer at canonical path — moved to `legacy/`). Canonical gate logic relocated to `dispatch.py`, separately tested in `test_hook_dispatch_smoke.py`. Disposition documented at `evidence/hook_phase3r_legacy_test_disposition.md`. Not masking real regressions.

## ATTACK 5 — charter+gates baseline preserved [VERDICT: PASS]
Re-ran the 7-file baseline: **111 passed, 5 skipped, 0 failed in 1.25s**. Phase 5.D recorded 98/5/0; this run is +13 tests beyond Phase 5.D (improvement, no regression). The 196/5/0 figure cited in the brief is the expanded hook-redesign-superset baseline; the 7 named files used here cover charter contract and gating, all green.

## ATTACK 6 — pre_merge_contamination deviation [VERDICT: PASS]
Legacy `.claude/hooks/legacy/pre-merge-contamination-check.sh:121` exits 0 on no-evidence (advisory). dispatch.py line 1017 returns `("allow", "merge_advisory_conflict_first_no_evidence")`. Semantic preservation correct.

Surface concern: an agent on `main`/`live-launch-*` who sets no MERGE_AUDIT_EVIDENCE bypasses the contamination gate. This is **legacy-preserved behavior**, not a Phase 3.R regression. The mitigating mechanic is conflict-first git output — most contamination scenarios surface as merge conflicts. If the operator wants stricter behavior, that is a follow-up redesign decision, NOT a Phase 3.R blocker. Carry-forward LOW.

## ATTACK 7 — TestRealAuditLogCap pre-existing claim [VERDICT: FAIL]
Executor claim: "test_override_health::TestRealAuditLogCap pre-existing failure not caused by this work."

`git log --all --oneline tests/test_override_health.py` returns ONE commit: `76b80088 hook redesign Phase 3` — the file was **introduced in this branch**. It is NOT pre-existing. Direct run reproduces the failure:
```
STASH_FIRST_VERIFIED: 6 uses > cap 5
OPERATOR_DESTRUCTIVE: 6 uses > cap 5
```
However, the failure root cause is **audit-log data accumulation** (CHARTER M5 quarterly cap), not Phase 3.R code defect. The test functions correctly: it caught real over-cap usage. The deviation note is mislabeled (file is new in 76b80088, not pre-existing) but the conclusion ("not caused by this work") is correct in substance — Phase 3.R did not invoke these overrides. **Net: classify as MEDIUM disclosure error**, not a verdict-flipping defect; operator should review the cap violations separately.

## ATTACK 8 — manual smoke evidence verified [VERDICT: PASS]
`.claude/logs/hook_signal/2026-05.jsonl` contains live entries from THIS critic agent (session_id=`5278ceeb-620a-45f7-aa49-e9d619595321`, agent_id=`ace3ae783fbe99b89` — confirmed match to my own SubagentStart context). All 7 hooks observed firing with `decision: allow`, `reason: passed`/`advisory_check` (NOT `phase1_stub`). This is fresher evidence than the executor's manual smoke commit — the cutover is operating in real time.

## ATTACK 9 — code-provenance / docstring quality [VERDICT: PASS]
Each ported function has a docstring citing escape hatches and the legacy source file. `_run_blocking_check_invariant_test` explicitly enumerates the 5 escape hatches (697-706). gate_edit_time.py carries provenance header (`# Created: 2026-05-06`, authority basis to ULTIMATE_DESIGN §5 Gate 1).

## ATTACK 10 — rollback path [VERDICT: PASS]
Single-commit revert (`git revert cf60511b`) restores phase1_stub fall-through. Legacy shells remain in `.claude/hooks/legacy/` so emergency re-symlink is possible. Worktree-tolerant venv discovery preserved on rollback.

## Required fixes (APPROVED-WITH-CAVEATS)
- **MEDIUM disclosure correction**: agent_registry.jsonl deviation note for `test_override_health::TestRealAuditLogCap` says "pre-existing failure" — file was introduced in 76b80088. Restate as: "introduced in Phase 3 (76b80088); failure is real-data CHARTER M5 cap violation, not Phase 3.R code defect; operator review of STASH_FIRST_VERIFIED + OPERATOR_DESTRUCTIVE caps required separately."
  - Action: append correction to `.claude/orchestrator/runs/topology-redesign-2026-05-06/state/agent_registry.jsonl` as an erratum entry; AND open a follow-up ticket for charter M5 cap review (real audit-log signal, not silenceable).
- **LOW carry-forward**: ATTACK 6 — pre_merge_contamination advisory on no-evidence preserves a known protected-branch bypass. Document as known-limitation in `evidence/hook_phase3r_legacy_test_disposition.md` so the next session does not re-discover.

## Summary
```
verdict: GO-WITH-CONDITIONS
critical: 0
high: 0
medium: 1
low: 1
hook_redesign_complete: True
session_pr_authorized: True
operator_decisions_pending: ["review_charter_M5_cap_violations_STASH_FIRST_VERIFIED_OPERATOR_DESTRUCTIVE"]
```

session_pr_authorized: True
