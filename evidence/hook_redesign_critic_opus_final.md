# critic-opus FINAL review of hook redesign cutover

**HEAD:** 76b80088 on topology-redesign-2026-05-06
**Reviewer:** critic-opus (agent a140eaefcb1f75004; transcript truncated mid-investigation but critical finding surfaced)
**Date:** 2026-05-06

## Verdict
```
verdict: NO-GO
critical: 1
high: 0+ (review incomplete; agent exited mid-investigation)
medium: 0+
low: 0+
hook_redesign_complete: False
session_pr_authorized: False
operator_decisions_pending: []
```

## CRITICAL — dispatch.py phase1_stub pass-through for 7 of 12 hooks

`.claude/hooks/dispatch.py:693`:
```python
else:
    # Phase 1 pass-through for hooks not yet fully implemented
    return "allow", "phase1_stub"
```

dispatch.py implements actual gate logic for only 5 hooks (the NEW Phase 2 hooks):
- `pre_checkout_uncommitted_overlap` (line 313)
- `pre_edit_hooks_protected` (line 415)
- `pr_create_loc_accumulation` (line 465)
- `pr_open_monitor_arm` (line 563)
- `phase_close_commit_required` (line 625)

The 7 LEGACY hooks fall through to `phase1_stub`:
- `invariant_test` — pytest baseline regression detector (BLOCKING; reversibility_class TRUTH_REWRITE)
- `secrets_scan` — gitleaks against staged content (BLOCKING; ARCHIVE)
- `cotenant_staging_guard` — broad-`git add` heuristic (BLOCKING; WORKING)
- `pre_merge_contamination` — MERGE_AUDIT_EVIDENCE on protected branches (BLOCKING; TRUTH_REWRITE)
- `post_merge_cleanup` — advisory checklist (ADVISORY)
- `pre_edit_architecture` — architecture/** edit gate (BLOCKING; ARCHIVE)
- `pre_write_capability_gate` — Topology Gate 1 (BLOCKING)

**Effect of the Phase 3 cutover:** the legacy shell scripts were moved to `.claude/hooks/legacy/` and `.claude/settings.json` was repointed to `dispatch.py <hook_id>` — but dispatch.py does not implement these 7 hooks. They all return `("allow", "phase1_stub")` → exit 0 → permissive default.

**Result:** the cutover REMOVED 7 of 12 active gates without replacing them. The redesign's headline goal (replace bypass culture with structured overrides) is meaningless when the gates themselves are pass-throughs. This is the original ATTACK 5 (regression in deletion) instantiated.

**Adversarial verification by the agent:** "13 legacy-script tests + 155 topology_doctor tests now FAIL post-cutover." (155 topology_doctor failures are pre-existing per Phase 5.D ledger; 13 legacy-script failures are NEW post-cutover and confirm dispatch.py is missing the legacy logic.)

**Required remediation before PR:**
1. Port the 7 legacy shell scripts' actual logic into dispatch.py: `_run_blocking_check_invariant_test`, `_run_blocking_check_secrets_scan`, `_run_blocking_check_cotenant_staging_guard`, `_run_blocking_check_pre_merge_contamination`, `_run_advisory_check_post_merge_cleanup`, `_run_blocking_check_pre_edit_architecture`, `_run_blocking_check_pre_write_capability_gate`.
2. Each port must preserve the legacy semantics (gitleaks invocation, pytest baseline check via `has-git-subcommand commit` parser, MERGE_AUDIT_EVIDENCE validation, etc.).
3. Smoke tests must EXERCISE each hook against a realistic payload (not just JSON envelope shape) — explicitly test that `invariant_test` rejects a regression below baseline, that `secrets_scan` catches a planted secret, etc.
4. Re-run charter+gates baseline + 13 failing legacy-script tests.
5. Re-dispatch critic-opus for cutover re-verification.

## Process note

Critic agent transcript shows the investigation surfaced the CRITICAL finding within its first ~30 minutes. The brief return ("Running final attack verifications") suggests the agent either (a) hit a token/runtime budget mid-investigation, (b) considered the CRITICAL finding sufficient to halt the GO/NO-GO decision, or (c) the wrapper truncated the result. Either way, the load-bearing finding is preserved here for remediation.

PR is NOT authorized. Hook redesign is NOT complete. Phase 3 cutover must be reverted in spirit (dispatch.py implementations) before legacy retirement is real.
