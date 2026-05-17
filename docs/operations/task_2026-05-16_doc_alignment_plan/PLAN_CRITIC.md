# Plan Critic Verdict — Doc Alignment Plan v2 (2026-05-16)

Opus critic verdict captured from agent a4e08979c47227c4b on plan v2 of the post-PR-#119 大扫除 doc alignment work.

## Verdict: REVISE (incorporated into plan v3)

3 CRITICAL + 6 MAJOR findings; all amendments applied to `/Users/leofitz/.claude/plans/tingly-petting-crystal.md` v3.

## 3 CRITICAL Findings

1. **WAVE 1.5 handler architecture mismatch**: v2 invented `rules/handlers/<task>.py` with `enumerate()`/`apply()` methods that don't match `engine.py:268-337` signatures (`_enumerate_candidates(config) -> list[TaskSpec]`, `_apply_decisions(task, manifest, ...)`). Existing `maintenance_worker/rules/task_registry.py` already implements `TaskRegistry.get_tasks_for_schedule()`. **Resolution**: v3 wires `_enumerate_candidates` to TaskRegistry, adds dispatcher, per-task-id rule modules at `maintenance_worker/rules/<task_id>.py`.

2. **2 of 6 cited scout artifacts missing on disk**: `SCAFFOLD_GAPS_VERIFIED.md` and `WORKTREE_AUDIT.md` content existed only in conversation BATCH_DONE returns. **Resolution**: WAVE 0.2 writes both artifacts to disk before plan execution.

3. **WAVE 4.1 BLOCKING→ADVISORY reversal undoes deliberate decision**: Commit `342bd73ff2` (2026-05-09) deliberately elevated `pr_create_loc_accumulation` to BLOCKING with cost-curve-calibrated rationale; operator memory `feedback_pr_300_loc_threshold_with_education` enforces it. HOOKS_AUDIT observation was descriptive (notes tier mismatch with v2 protocol), NOT prescriptive. **Resolution**: WAVE 4.1 DELETED from plan; carve-out to WAVE 7 governance packet if v2 protocol amendment desired.

## 6 MAJOR Findings (applied to v3)

4. **WAVE 3.7 INV-27 edit governance violation**: `architecture/invariants.yaml` is canonical immutable LAW; editing INV-27 wording from "warnings do not block" → "ADVISORY warnings do not block; BLOCKING-tier signals do" introduces NEW semantic content that requires governance packet, not 1-line drift fix. **Resolution**: INV-27 row REMOVED from WAVE 3.7; carve-out to WAVE 7.

5. **WAVE 2.2 missing bare-file stub rule**: 23 of 46 OLD-archive entries are bare `.md` files; ARCHIVAL_RULES.md only defines stubs for directory-shaped packets. Some entries (e.g. `PROPOSALS_2026-05-04.md`) are already-deleted. **Resolution**: WAVE 2.3 adds bare-file rule + pre-deletion check.

6. **WAVE 6.4 missing pytest baseline capture**: Full pytest on Zeus repo is 15-30 min + flaky; plan needs pre-edit baseline to detect delta-direction per `feedback_critic_reproduces_regression_baseline`. **Resolution**: WAVE 0.7 captures baseline.

7. **WAVE 1.5 test LOC estimate undershoot**: 27 tests / 800 LOC = unrealistic; real estimate 50+ tests, 1500-2000 LOC. **Resolution**: WAVE 1.5.5 amended to ≥36 tests, 1500-2000 LOC.

8. **WAVE 6 opus critic stall risk**: 50% of opus revision dispatches in 2026-05-15 session stalled (per `feedback_long_opus_revision_briefs_timeout`). Final critic on 3500-5500 LOC is very likely to stall. **Resolution**: WAVE 6.5 decomposed to 3 parallel sonnet critics (handlers / migration / docs+hooks), ≤30 line briefs.

9. **Missing risks**: pytest flake masking real regressions; critic-of-critic recursion (~50% per memory); first-tick cascade across handlers; stash@{1} verification. **Resolution**: Risk register expanded.

## Probe Results (10 probes)

| Probe | Verdict |
|-------|---------|
| 1. Scope coherence | NEEDS_REVISE → ACCEPTED with caveat (大扫除 framing covers) |
| 2. WAVE 0 sequencing | FAIL → fixed with per-path verification |
| 3. WAVE 1.5 architecture premise | FAIL → rewritten against actual engine.py + TaskRegistry |
| 4. WAVE 2 stub path | PASS for dirs; needs amendment for bare files (MAJOR #5) |
| 5. WAVE 3.7 INV-27 governance | FAIL → removed (CRITICAL #4) |
| 6. WAVE 4 hook BLOCKING reversal | FAIL → removed (CRITICAL #3) |
| 7. WAVE 5 dry-run safety | PASS_WITH_CAVEAT → defense-in-depth top-of-function guard added |
| 8. Coverage of 大扫除 framing | NEEDS_REVISE → accepted (some 5-min items bundled but acceptable for spring-cleaning) |
| 9. Critic dispatch tier risk | NEEDS_REVISE → WAVE 6 decomposed to sonnet (MAJOR #8) |
| 10. Risk register completeness | FAIL → expanded with critic-flagged missing risks (MAJOR #9) |

## Disposition

Plan v3 incorporates ALL 3 CRITICAL fixes + ALL 6 MAJOR fixes. User approved v3 via ExitPlanMode 2026-05-16. Plan execution begins WAVE 0.

## Provenance

Plan critic dispatched 2026-05-16 by orchestrator-session 7f255122. Agent ID: a4e08979c47227c4b (opus, fresh-context, per-task probe contract). BATCH_DONE return contained 14 finding rows + verdict REVISE. This artifact preserves the critic's verdict for execution-phase reference.
