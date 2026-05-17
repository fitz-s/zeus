# critic-opus review of hook redesign PLAN.md

**HEAD:** ba635a13 on topology-redesign-2026-05-06
**Reviewer:** critic-opus (agent ac0e874ad47ad8053)
**Date:** 2026-05-06
**Subject:** docs/operations/task_2026-05-06_hook_redesign/PLAN.md

## Verdict
```
verdict: GO-WITH-CONDITIONS
critical: 0
high: 3
medium: 4
low: 3
proceed_to_phase_1: True
operator_decisions_pending:
  OD-HOOK-1: dispatch.py crash → ADVISORY fail-open + BLOCKING fail-closed (rec) vs uniform fail-open
  OD-HOOK-2: structured-override evidence forgery — operator-signed sentinel for ARCHIVE/TRUTH_REWRITE
  OD-HOOK-3: evidence/ vs docs/evidence/ path prefix
```

Phase 1 may proceed in parallel-install mode (no settings.json change). HIGH findings must land in Phase-1 spec or as Phase-2 carry-forwards before any cutover.

## Verification of plan-cited integrity claims

| Claim | Verified | Result |
|---|---|---|
| 1,337 LOC across 8 hook scripts | `wc -l .claude/hooks/*.{sh,py}` → 1337 | EXACT |
| 187 `[skip-invariant]` / 60d | re-measured 188 | OFF-BY-1 (immaterial) |
| `pre-commit-invariant-test.sh` 79-126 4 escape hatches | grep'd | EXACT |
| Claude Code Hooks JSON contract | WebFetch confirmed `permissionDecision: allow\|deny\|ask\|defer`, precedence deny>defer>ask>allow | CONFIRMED |
| Topology 5-layer analogue | confirmed in `docs/operations/task_2026-05-06_topology_redesign/ultraplan/` | CONFIRMED |
| Regression baseline | 98 passed / 5 skipped (subset of Phase 5.D's 144) | GREEN |

**Citation rot found (2 paths):**
- `~/.claude/skills/orchestrator-delivery/SKILL.md` — does NOT exist (LOW)
- `docs/evidence/*` cited but repo uses `evidence/` (MEDIUM, OD-HOOK-3)

## 10 Adversarial Attack Verdicts

### ATTACK 1 [FAIL — HIGH] — Forgery / privilege escalation
`STRUCTURED_OVERRIDE` env-var forgeable; agent can fabricate evidence files via `cat <<EOF`. Override gate degrades to "evidence-shaped-text-required" — semantically equivalent to free-form `[skip-invariant]` plus typing.

**Required fix:** override evidence files signed by `git commit -S` operator-only key, OR `operator_signature: true` sentinel outside agent write surface, OR evidence references orchestrator registry agent_id agent cannot rewrite.

### ATTACK 2 [FAIL — HIGH] — Self-bypass via `.claude/settings.json` edit
Redesign does not gate Edit|Write on `.claude/settings.json`, `.claude/hooks/registry.yaml`, `.claude/hooks/overrides.yaml`, `.claude/hooks/dispatch.py`. Agent can flip BLOCKING→ADVISORY, set `auto_expires_after: never`, or remove matcher entry.

**Required fix:** add `pre_edit_hooks_protected` hook covering `.claude/{settings.json,hooks/**}` with `HOOK_SCHEMA_CHANGE` override class.

### ATTACK 3 [PASS] — Citation integrity
5 paths grep-verified EXACT. 2 minor rot captured separately.

### ATTACK 4 [FAIL — HIGH] — Test hides defect
`tests/test_hook_dispatch_smoke.py ~100 LOC` spec is too loose; trivial stub could pass.

**Required fix:** parametrize over `registry.hooks`, assert `hookEventName==spec.event`, assert `permissionDecision in {allow,deny,ask,defer}`, assert advisory hooks emit no `permissionDecision`, coverage assertion `len(seen)==11`.

### ATTACK 5 [PASS — LOW sub-finding] — Regression in deletion
8 legacy scripts mapped 1:1 to new hooks. `hook_common.py` (326 LOC) needs explicit reuse/rewrite verdict before Phase 3 deletion.

### ATTACK 6 [PASS] — Charter compliance
Composes cleanly with topology M1-M5. INV-HOOK-NOT-GATE extends INV-HELP-NOT-GATE. Cadence differs (monthly vs quarterly) but no conflict.

### ATTACK 7 [FAIL — HIGH] — Override expiry enforcement unspecified
Clock-start, replay protection, post-acceptance file lifecycle, `auto_expires_after: never` audit — all unspecified.

**Required fix:** dispatch.py `validate_override` spec: (a) clock-start = `git log -1 --format=%ct <evidence_file>`, (b) replay protection: `(override_id, evidence_file)` pair counts once toward `max_active_per_30d`, (c) `auto_expires_after: never` permitted only for REVIEW_SAFE_TAG + ISOLATED_WORKTREE with annual operator audit cited in M5 test.

### ATTACK 8 [FAIL — HIGH] — fail-open default conflates ADVISORY/BLOCKING
Plan: "exit 0 on hook error per Claude Code best-practice." This is graceful-degradation default, not a recommendation for security-class gates. For BLOCKING (secrets_scan, pre_merge_contamination, pre_checkout_uncommitted_overlap), fail-open on crash silently disables every BLOCKING gate.

**Required fix:** ADVISORY hooks fail-open on crash; BLOCKING hooks fail-closed (exit 2 + stderr "dispatch.py crash on `<hook_id>` — operator must `--no-verify` to proceed"). OD-HOOK-1.

### ATTACK 9 [FAIL — MEDIUM] — PR-monitor advisory feasibility
"Monitor armed" telemetry source unspecified. Advisory becomes structurally indistinguishable from a memory file the agent doesn't read.

**Required fix:** emit `MONITOR_ARM_REQUIRED:<pr-num>:<expiry>` sentinel; Stop hook checks session activated Monitor referencing `<pr-num>`. OR downgrade metric to "subsequent gh pr command observed within 10min" proxy.

### ATTACK 10 [PASS — LOW] — Pre-checkout overlap detector cost
Operations bounded (<1s on Zeus-sized repo). Specify 5s hard timeout on `git ls-tree`.

## Required fixes by phase

### Pre-Phase 1 (planner amendment, ≤30 min)
- Fix `evidence_file:` paths to `evidence/` prefix (OD-HOOK-3, MEDIUM)
- Remove orchestrator-delivery SKILL path or relocate (LOW)
- Update §1.1: 187 → 188 (LOW)

### In Phase 1 spec (executor brief tightening)
- ATTACK 4: tighten `test_hook_dispatch_smoke.py` spec (HIGH)
- ATTACK 7: define `validate_override` precisely (HIGH)
- ATTACK 8: ADVISORY fail-open + BLOCKING fail-closed (HIGH, OD-HOOK-1)

### In Phase 2 spec
- ATTACK 1: `OPERATOR_SIGNATURE_REQUIRED` for ARCHIVE/TRUTH_REWRITE classes (HIGH, OD-HOOK-2)
- ATTACK 2: `pre_edit_hooks_protected` hook covering `.claude/{settings.json,hooks/**}` (HIGH)
- ATTACK 9: Monitor-armed telemetry source (MEDIUM)
- ATTACK 5 sub-finding: `hook_common.py` reuse-or-rewrite inventory (LOW)

### In Phase 3 cutover gates
- ATTACK 10: 5s `git ls-tree` timeout (LOW)

## Summary
Plan is structurally sound. 3 HIGH findings + 4 MEDIUM addressable in executor brief tightening and Phase-2 deliverable expansion without re-planning. Phase 1 may proceed in parallel-install mode.

Regression baseline preserved: 98 passed / 5 skipped on charter+gates subset.

## Operator Decisions (resolved 2026-05-06 by coordinator architecture-homework)

**OD-HOOK-1: ADVISORY fail-open + BLOCKING fail-closed.**
Resolution: critic recommendation accepted. Architectural answer (security gates fail-closed is universally correct; conflating with advisory's graceful-degradation is a category error). No operator tradeoff.

**OD-HOOK-2: Operator-signed sentinel for ARCHIVE / TRUTH_REWRITE classes only.**
Resolution: split by `reversibility_class`. WORKING/ARCHIVE-class overrides accept `cat <<EOF` evidence (low blast radius). TRUTH_REWRITE/ON_CHAIN classes require `evidence/operator_signed/<override>.signed` sentinel — file written outside agent write surface (operator-only path documented in CHARTER). Mirrors topology redesign's reversibility-class pattern.

**OD-HOOK-3: `evidence/` (matches repo).**
Resolution: definitional fact. Repo has `evidence/`; PLAN's `docs/evidence/` was a typo. Amended.
