# WAVE 4 Critic — Hook Ecosystem (commits bac1d71a7a + 8553710b43)

**VERDICT: ACCEPT-WITH-RESERVATIONS** (one MINOR observation; one ADVISORY note on handler semantics; no CRITICAL/MAJOR findings)

**Reviewer**: fresh-context sonnet critic
**Time**: 2026-05-16
**Mode**: THOROUGH (no escalation triggered — zero CRITICAL, zero MAJOR)

---

## Overall Assessment

The WAVE 4 ecosystem work is well-executed and matches the scout audit. BLOCKING tiers (`pr_create_loc_accumulation`, `pre_merge_comment_check`) are preserved at `registry.yaml:77,98`. Both JSON files parse. All 5 handler functions exist. Codex mirror parity holds at 16 entries. The "80 LOC → 300 LOC" label drift is fixed. Catalog size correctly advanced 16→17. New event keys (`SessionStart`/`WorktreeCreate`/`WorktreeRemove`) are wired in `settings.json` with valid Claude Code event names. Stash@{0} NO-OP claim is empirically validated.

One minor disposition concern (handler is purely path-trigger-based and never inspects `state/maintenance_state/install_metadata.json` for actual floor compliance — by design, but worth noting). Pre-existing pytest collection guard issue on `promote_calibration_v2_stage_to_prod.py` is correctly out of WAVE 4 scope.

---

## Pre-commitment predictions vs reality

| Predicted risk | Found? |
| :--- | :--- |
| BLOCKING demotion of pr_create_loc_accumulation | NO — preserved at line 77 |
| JSON parse failure after settings.json edit | NO — both parse cleanly |
| Handler function name mismatch | NO — all 5 functions exist with correct signature |
| Stash@{0} had real content worker missed | NO — diff vs canonical extract = additive (WAVE 4.5 only) |
| Codex mirror missing maintenance_worker_dry_run_floor | NO — present per worker note |

---

## Per-probe disposition table

| # | Probe | Result | Disposition |
| :--- | :--- | :--- | :--- |
| 1 | BLOCKING-tier integrity | `registry.yaml:77,98` both `severity: BLOCKING` | PASS |
| 2 | JSON validity | settings.json + .codex/hooks.json both parse | PASS |
| 3 | Handler existence (5 hooks) | All present at lines 639, 1018, 1041, 1104, 1154 in dispatch.py | PASS |
| 4 | Codex mirror parity | 16 hook entries in .codex/hooks.json (matches Zeus 16 wired + extras) | PASS |
| 5 | Stash@{0} NO-OP | `dispatch.py.patch` is 0 bytes; diff vs extract = only WAVE 4.5 additions | PASS |
| 6 | "80 LOC" → "300 LOC" | grep returns zero hits in settings.json | PASS |
| 7 | maintenance_worker_dry_run_floor semantics | Path-trigger advisory; does NOT read install_metadata.json | PASS (by design) — see MINOR below |
| 8 | catalog_size 16→17 | `catalog_size: 17` at registry.yaml:9; 17 enumerated `- id:` entries | PASS |
| 9 | New event keys valid | SessionStart/WorktreeCreate/WorktreeRemove present at settings.json:105,117,129 | PASS |
| 10 | Pre-existing pytest guard | `promote_calibration_v2_stage_to_prod.py` last-modified at eba80d2b9d (pre-this-branch) | CARRY-FORWARD (not WAVE 4 blocker) |

---

## Findings

### CRITICAL (blocks execution)
None.

### MAJOR (causes significant rework)
None.

### MINOR (suboptimal but functional)

1. **`maintenance_worker_dry_run_floor` handler is purely path-based; never inspects actual state**
   - Evidence: `dispatch.py:1154-1217` — handler matches against 4 hardcoded `_GOVERNANCE_PATHS` strings against `tool_input.file_path` or `tool_input.command`. No read of `state/maintenance_state/install_metadata.json`.
   - Confidence: HIGH
   - Why this matters: The hook fires on ANY edit to governance files, including legitimate maintenance edits where the operator IS following the dry-run floor. The "floor" in the hook name suggests a state check (is the floor satisfied?), but the actual semantics is "remind operator of checklist". This is fine for an advisory hook — it never blocks — but the name `..._floor` overstates the rigor. Bypass via `ZEUS_MW_DRY_RUN_VERIFIED=1` is documented in the message itself.
   - Fix (optional, ≤5 LOC): Rename to `maintenance_worker_governance_edit_advisory` to remove the implication of state-checking. OR document inline that `state/maintenance_state/install_metadata.json` introspection is a v2 enhancement. Not a blocker.

---

## What's Missing

- **No regression test for the new hook handlers.** None of the 5 new handlers have a `tests/test_*hook*` unit test verifying the trigger logic. This is consistent with the existing pattern (most advisory handlers in `dispatch.py` lack unit tests), so it's not a WAVE 4 regression, but it does mean a future refactor could silently break the path-match logic in `maintenance_worker_dry_run_floor` without CI detection.
- **No boot self-test invocation in commit message verification.** Both commit messages reference `boot_self_test_only` but the critic did not independently re-run it. Recommended for executor before PR open: `python3 .claude/hooks/dispatch.py boot_self_test_only` — should report `OK: all 17 registry hooks have handlers`.

---

## Multi-perspective notes

- **Executor**: The two commits are atomic, well-scoped, and the commit messages cite exact file:line refs for unchanged BLOCKING tiers. A reviewer of the diff would have no trouble following intent.
- **Stakeholder**: WAVE 4 satisfies the scout audit's 4 unwired hooks + 6 codex mirror gaps + 80→300 LOC drift fix. New `maintenance_worker_dry_run_floor` addresses the audit's "Suggested Additions" §4.1.
- **Skeptic**: The only real lever-arm risk is silent demotion of BLOCKING entries. That risk is mitigated: registry lines 77 + 98 still read `severity: BLOCKING`, both commit messages explicitly call this out, and the new advisory hook itself emits a checklist reminding operators to verify these lines are unchanged. Defense-in-depth is appropriate here.

---

## Realist Check

No CRITICAL or MAJOR findings to recalibrate. MINOR finding (handler naming) has trivial real-world impact (operator might briefly wonder what "floor" means, then read the checklist).

---

## Verdict Justification

**ACCEPT-WITH-RESERVATIONS** rather than clean ACCEPT because of one MINOR naming/semantics observation on `maintenance_worker_dry_run_floor`. The hook works as designed, the design is appropriate (advisory + bypass env var), but the name suggests state-checking semantics it does not implement. This is a documentation/naming polish, not a defect. Operator may merge as-is or apply the rename. Stayed in THOROUGH mode throughout — no escalation triggers fired.

All 10 pre-committed probes either PASS or carry forward outside scope. BLOCKING tier integrity (the highest-risk surface per operator memory `feedback_pr_300_loc_threshold_with_education`) is verifiably intact.

---

## Open Questions (unscored)

- Should `maintenance_worker_dry_run_floor` evolve to actually read `install_metadata.json`? If so, what's the floor schema? — Defer to operator; not a WAVE 4 question.
- Is the `pre-existing pytest collection guard` on `promote_calibration_v2_stage_to_prod.py` (last-modified at `eba80d2b9d`, pre-this-branch) tracked elsewhere? — Carry-forward to WAVE 6 baseline-delta analysis.

---

## Carry-forward (NOT WAVE 4 blockers)

- `scripts/promote_calibration_v2_stage_to_prod.py` pytest collection antibody — tracked at PR #114 ancestry, predates this branch by multiple commits.
