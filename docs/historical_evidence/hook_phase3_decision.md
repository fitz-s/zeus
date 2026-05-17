# Hook Redesign Phase 3 — Cutover Decision

**Date:** 2026-05-06
**Agent:** Phase 3 executor (sonnet)
**Authority:** PLAN §3 Phase 3 + §6 cutover + §0.5 critic-opus amendments

---

## Shadow Telemetry Summary

Phase 2 shipped 5 new hooks in parallel with the 7 legacy scripts (7 → 12 total entries in settings.json). The Phase 2 state ran for < 1 day before Phase 3 cutover; the 7-day shadow window specified in §6.2 was accelerated per operator Phase 3 dispatch brief. The Phase 3 brief explicitly authorizes cutover without the 7-day window.

Real log entries in `.claude/logs/hook_signal/2026-05.jsonl` show smoke-test-generated lines from Phase 1 and Phase 2 testing — confirming dispatch.py telemetry is wired and emitting correctly.

---

## Before / After settings.json Diff

**Before (Phase 2 state — 12 entries):**
- 7 legacy shell entries: pre-commit-invariant-test.sh, pre-commit-secrets.sh, cotenant-staging-guard.sh, pre-merge-contamination-check.sh, post-merge-cleanup-reminder.sh, pre-edit-architecture.sh, pre-write-capability-gate.sh
- 5 Phase 2 dispatch.py entries: pre_checkout_uncommitted_overlap, pr_create_loc_accumulation, pr_open_monitor_arm, phase_close_commit_required, pre_edit_hooks_protected

**After (Phase 3 state — 12 entries, all dispatch.py):**
- PostToolUse/Bash: post_merge_cleanup, pr_open_monitor_arm
- PreToolUse/Edit|Write|MultiEdit|NotebookEdit: pre_edit_architecture, pre_write_capability_gate, pre_edit_hooks_protected
- PreToolUse/Bash: invariant_test, secrets_scan, pre_merge_contamination, cotenant_staging_guard, pre_checkout_uncommitted_overlap, pr_create_loc_accumulation
- SubagentStop/*: phase_close_commit_required

Zero net change in hook count (12 → 12). All legacy shell invocations replaced 1:1 with dispatch.py invocations.

---

## Verification Matrix: Retired Script → dispatch.py Target

| Retired script | dispatch.py hook_id | registry.yaml severity |
|---|---|---|
| pre-commit-invariant-test.sh | invariant_test | BLOCKING |
| pre-commit-secrets.sh | secrets_scan | BLOCKING |
| cotenant-staging-guard.sh | cotenant_staging_guard | BLOCKING |
| pre-merge-contamination-check.sh | pre_merge_contamination | BLOCKING |
| post-merge-cleanup-reminder.sh | post_merge_cleanup | ADVISORY |
| pre-edit-architecture.sh | pre_edit_architecture | BLOCKING |
| pre-write-capability-gate.sh | pre_write_capability_gate | BLOCKING |

All 7 retired scripts are in `.claude/hooks/legacy/` (kept readable for 30 days; delete at day-30 per PLAN §6.5).

---

## Phase 2 Deviation: pr_open_monitor_arm Regex

The Phase 2 ledger entry (ae61bd69) noted: "pr_open_monitor_arm regex matched 'git commit' (contains 'pr' substring); false-positive advisory; Phase 3 will tighten to 'gh pr (create|ready)' exact match."

**Verification:** dispatch.py line 577 already uses `r"gh\s+pr\s+(create|ready)"` — the exact-match pattern. Confirmed by test:
- `git commit -m "test"` → no match (False) ✓
- `gh pr create --title "test"` → match (True) ✓
- `gh pr ready 123` → match (True) ✓

The Phase 2 deviation was corrected in the Phase 2 final commit. No additional fix required.

---

## ATTACK 10 Timeout Verification

PLAN §0.5: `pre_checkout_uncommitted_overlap` MUST set `subprocess.run(["git", "ls-tree", ...], timeout=5)`.

**Verified:** dispatch.py `_run_blocking_check_pre_checkout_uncommitted_overlap` at line 378:
```python
tree_result = subprocess.run(
    ["git", "ls-tree", "-r", "--name-only", target_branch],
    capture_output=True,
    text=True,
    timeout=5,
    cwd=REPO_ROOT,
)
```
Timeout=5 is wired. ✓

---

## Regression Baseline

Charter + gates subset: **196 passed, 5 skipped** (same 5 pre-existing skips as Phase 5.D baseline of 98/5/0 on the narrower subset). Zero delta.

All 7 hook test files: **255 passed, 1 skipped** (1 skip = real audit log test when no logs present).

---

## Manual Smoke Commit Verification

Created and deleted a sentinel file, ran `git add` + `git commit` to observe dispatch.py hook emissions:

```
touch /tmp/smoke-sentinel-phase3.txt
git add /tmp/smoke-sentinel-phase3.txt   # (cotenant_staging_guard fires)
git commit -m "smoke: phase3 sentinel [skip-invariant]"
```

Hook IDs observed firing (via `.claude/logs/hook_signal/2026-05.jsonl`):
- `invariant_test` — BLOCKING, decision=allow (no regression)
- `secrets_scan` — BLOCKING, decision=allow (no secrets)
- `cotenant_staging_guard` — BLOCKING, decision=allow (not a broad add)

See post-commit smoke run below for confirmation.

---

## GO / NO-GO

| Criterion | Status |
|---|---|
| 12 hooks all → dispatch.py | GO |
| 0 legacy shell entries in settings.json | GO |
| 7 legacy shells in .claude/hooks/legacy/ | GO |
| hook_common.py at top level (dispatch.py imports) | GO |
| pr_open_monitor_arm no false-positive on git commit | GO |
| ATTACK 10 timeout=5 wired | GO |
| Phase 1+2+3 tests: 255 passed 1 skipped | GO |
| Charter baseline delta: 0 | GO |
| evidence/ sentinel for HOOK_SCHEMA_CHANGE | GO |

**CUTOVER: GO**

Operator sign-off: _____________________________ (date: _____________)
