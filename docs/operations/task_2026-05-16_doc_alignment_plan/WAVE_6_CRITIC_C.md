# WAVE 6 Critic C — Docs + Hooks Scope

**VERDICT: REVISE**

**Reviewer**: fresh-context sonnet critic
**Date**: 2026-05-16
**Mode**: THOROUGH → ADVERSARIAL (escalated on P8 maturity-promotion finding)
**Authority basis**: PLAN.md §WAVE 3 + §WAVE 4; carry-forward exclusions from WAVE_3_CRITIC.md + WAVE_4_CRITIC.md

---

## Overall Assessment

The hook ecosystem (P1–P6) and JSON wiring (P3) are solid; BLOCKING tiers intact. The lore INDEX (P9) has a structural defect: all 3 cards reference a non-existent source file. The module_manifest.yaml maturity promotions (P8) cite test counts that the repo does not support with the directory structure asserted — execution/calibration/riskguard/contracts have 0 tests in module-named subdirectories; tests live flat in `tests/`. This is a documentation fidelity issue, not a runtime defect, but the promotion claims are falsifiable and wrong-as-written.

---

## Per-probe Disposition Table

| # | Probe | Result | Disposition |
| :-- | :-- | :-- | :-- |
| P1 | BLOCKING tier integrity | `registry.yaml:77,98` both `severity: BLOCKING`; no new BLOCKING; no demotions | PASS |
| P2 | Handler-source completeness (5 hooks) | All 5: `_run_advisory_check_pr_thread_reply_waste` (639), `session_start_visibility` (1018), `worktree_create_advisor` (1041), `worktree_remove_advisor` (1104), `maintenance_worker_dry_run_floor` (1154) | PASS |
| P3 | JSON validity | Both `.claude/settings.json` and `.codex/hooks.json` parse cleanly | PASS |
| P4 | Boot self-test | `_self_test.sh` does not exist — skip (no shell self-test shipped) | N/A |
| P5 | Codex mirror parity (6 entries) | 16 total entries in `.codex/hooks.json`; 5 of the 6 probed hooks present (`pr_thread_reply_waste`, `session_start_visibility`, `worktree_create_advisor`, `worktree_remove_advisor`, + pre-existing ones). `maintenance_worker_dry_run_floor` is NOT in codex mirror — registry has no `codex_mirror` field for any hook. Plan probe says "6 zeus → codex mirror entries match by name + tier"; Wave 4 Critic passed this on count=16. This hook is advisory-only, codex omission is low-risk. | MINOR |
| P6 | `maintenance_worker_dry_run_floor` advisory behavior | Confirmed advisory: never blocks, path-trigger only, `ZEUS_MW_DRY_RUN_VERIFIED` bypass documented. Does NOT read `install_metadata.json` — by design. | PASS (per Wave 4 Critic ruling, not re-litigated) |
| P7 | INDEX.md 156-row count + NO_DIR | `wc -l` = 156; `grep -c NO_DIR` = 0 | PASS |
| P8 | 6 semantic drift fixes | See findings below | MIXED — 1 MAJOR, 1 MINOR |
| P9 | `docs/lore/INDEX.json` schema + card validity | Schema valid; 3 cards all reference non-existent file | MAJOR |
| P10 | `.claude/CLAUDE.md` pointer | Points to `AGENTS.md` + `REVIEW.md`; correct | PASS |

---

## Critical Findings

None.

---

## Major Findings

**M1 — lore/INDEX.json cards reference a non-existent source file**

Evidence: all 3 cards in `docs/lore/INDEX.json` have `extracted_from`:
`docs/operations/task_2026-05-15_p8_authority_drift_3_blocking/POSTMORTEM.md:<anchor>`
That path does not exist in the worktree (`os.path.exists` = False for all 3). The lore directory contains only `INDEX.json` and `topology/` subdirectory. No lore markdown files were committed alongside the INDEX.

- Confidence: HIGH
- Why this matters: Any consumer of `INDEX.json` (topology_doctor, future lore tooling, agents following the index) will dereference dead paths. The schema is structurally valid but the content is broken — the INDEX references an artifact from a prior task's operations directory that was not carried forward or is on a different branch.
- Fix: Either (a) commit the POSTMORTEM.md source file into `docs/lore/topology/` and update `extracted_from` paths to point there, or (b) regenerate `INDEX.json` from lore files that actually exist in this branch.

**M2 — module_manifest.yaml maturity promotions cite subdirectory test counts that do not exist**

Evidence:
- `execution` promoted: `# promoted 2026-05-16: 15+ test files` — `find tests/execution/ -name 'test_*.py'` returns 0 (directory does not exist as a subdirectory)
- `calibration` promoted: `# promoted 2026-05-16: 19+ test files` — `find tests/calibration/ -name 'test_*.py'` returns 0
- `riskguard` promoted: `# promoted 2026-05-16: 5+ test files` — `find tests/riskguard/ -name 'test_*.py'` returns 0
- `contracts` promoted: `# promoted 2026-05-16: settlement_semantics + execution_price fully tested` — `find tests/contracts/ -name 'test_*.py'` returns 0

Actual layout: 448 test files live flat in `tests/` root (e.g., `tests/test_execution_harvester_paginator.py`). The claim format implies structured subdirectory layout that does not exist.

- Confidence: HIGH
- Why this matters: Fitz Constraint #4 (data provenance applies to code provenance): a comment asserting "15+ test files" that an agent can verify in 2 seconds and find false is worse than no comment — it trains agents to distrust manifest claims universally. The probe criterion in this task ("find tests/<module>/ -name 'test_*.py' | wc -l per module") was designed to catch exactly this. The promotions themselves may be substantively correct (the flat-layout tests do cover these modules extensively), but the evidence cited is wrong.
- Fix: Rewrite the promotion comments to cite the actual flat test naming pattern: `# promoted 2026-05-16: covered by tests/test_execution_*.py (15+ files in tests/ root)`

---

## Minor Findings

1. **`maintenance_worker_dry_run_floor` absent from `.codex/hooks.json` mirror** — registry has 17 hooks; codex has 16 entries. The missing entry is ADVISORY-only, fires on PreToolUse governance file edits, and Codex environments would benefit from the same checklist nudge. Low real-world impact since the hook is advisory. Fix: add `node .codex/hooks/zeus-router.mjs maintenance_worker_dry_run_floor` to the PreToolUse hook group in `.codex/hooks.json`.

2. **`data_rebuild_topology.yaml` still references `ensemble_snapshots` (bare) in prose** — the rename to `_v2` is present at line 135, but `architecture/digest_profiles.py` and `scripts/` reference bare `ensemble_snapshots` in SQL and comments extensively. These are not new callers introduced by WAVE 3/4, so this is carry-forward scope, not a WAVE regression. Noted for completeness.

3. **`lore/INDEX.json` has only 1 topic (`topology`) with 3 cards** — the schema is valid but sparse. If the plan intended a richer lore corpus this is under-delivery; if 3 cards was the target, it passes except for M1 above.

---

## What's Missing

- **Lore source files**: `docs/lore/topology/` subdirectory or equivalent lore markdown files that `INDEX.json` cards should point to. The index was generated but the source artifacts were not committed.
- **Maturity promotion rationale that survives a 5-second grep**: comments currently cite subdirectory counts; should cite flat-file naming patterns agents can verify.
- **`_self_test.sh`**: Plan probe P4 assumed it exists. It does not. If the executor skipped creating it, the boot self-test capability referenced in the `maintenance_worker_dry_run_floor` checklist message (`"Run boot self-test: python3 .claude/hooks/dispatch.py boot_self_test_only"`) may be the intended mechanism — verify that path works.

---

## Multi-perspective Notes

- **Executor**: P8 maturity-promotion comments will cause future agents to run the probe command, get 0, and flag drift. Fix the comment format before this merges.
- **Stakeholder**: The hook ecosystem (the highest-risk surface) is clean. The lore INDEX breakage and maturity comment mismatch are documentation fidelity issues only — no runtime path is affected.
- **Skeptic**: Wave 4 Critic passed P5 (codex mirror parity) based on count=16. The count is correct; `maintenance_worker_dry_run_floor` is entry #17 (new in WAVE 4) and genuinely absent from codex. Prior critic's pass was on count, not on completeness of the new hook. This is a real gap, albeit minor.

---

## Verdict Justification

REVISE — two MAJOR findings that survive self-audit and realist check:

1. M1 (lore INDEX dead paths): detected = immediately on first dereference; fix = commit source files or regenerate from existing lore. Not data-loss or security, but a broken artifact that merges as broken.
2. M2 (maturity comment mismatch): detected = 2-second grep in any future session; fix = rewrite 4 comment lines. If left unaddressed, future agents will flag false drift in module_manifest.yaml.

Realist check: neither finding affects any runtime execution path, settlement logic, or BLOCKING gate. Both are fixable in under 30 minutes. Escalated to ADVERSARIAL after M2 emerged; no additional structural issues found in the hook ecosystem under adversarial scrutiny.

---

## Open Questions (unscored)

- Did the POSTMORTEM.md source file exist on a sibling branch and was not cherry-picked? If so, the fix is a targeted cherry-pick, not a re-generation.
- Is `python3 .claude/hooks/dispatch.py boot_self_test_only` a valid invocation? The message in the hook references it but the self-test module may not be implemented.
