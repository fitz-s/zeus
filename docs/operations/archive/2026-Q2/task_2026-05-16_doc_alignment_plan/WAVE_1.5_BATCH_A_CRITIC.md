# WAVE 1.5 Batch A Critic Verdict — REVISE (2026-05-16)

Opus critic (fresh-context, agent a4fd45ebf64d38822) review of WAVE 1.5 Batch A commits b1eea1aacc..4db6d1a75a. 10 probes; 2 CRITICAL + 3 MAJOR + 3 MINOR.

## Verdict: REVISE

**Composition-defect pattern**: 832/832 tests pass, individual handlers well-authored, but engine→handler wiring broken at architectural seam. Same shape as PR #119 F1 (unit-tested pieces, broken integration).

## 2 CRITICAL Findings (block Batch B)

### C1: Engine never calls `handler.enumerate()`
**File**: `maintenance_worker/core/engine.py:209-222` + dispatcher
- `_enumerate_candidates` returns `TaskCatalogEntry` list directly.
- DRY_RUN_PROPOSAL emits empty `ProposalManifest`.
- APPLY_DECISIONS calls `_dispatch_by_task_id(task.task_id, "apply", proposal, ctx)` — only `"apply"` method dispatched.
- The 3 new handler `enumerate()` functions are unreachable from engine. Tests call them directly.
**Impact**: Wave 1.5's stated goal ("wire enumerate + 3 handlers") NOT MET. Real tick discovers 0 archive candidates / 0 stale worktrees because engine never asks handlers what they found.
**Fix**: In ENUMERATE_CANDIDATES phase, iterate `TaskCatalogEntry` list, call `_dispatch_by_task_id(task_id, "enumerate", entry, ctx)` for each, collect `list[Candidate]` per task. DRY_RUN_PROPOSAL builds real manifest from those Candidates.

### C2: `apply()` receives `ProposalManifest` where handlers expect `Candidate`
**File**: `engine.py:394-396` vs `closed_packet_archive_proposal.apply()` / etc.
- Engine passes `proposal: ProposalManifest`; handlers use `decision.path`, `decision.evidence` (Candidate API).
- `Any` typing on dispatcher hides contract violation.
- Empirical: `_mock_diff` produces garbage like `# would execute: git mv ProposalManifest(task_id='...', proposed_moves=(), ...) docs/operations/archive/...`.
- Today hidden by `dry_run_only=True` + `live_default=False` everywhere — short-circuits before disk. But Batch B activating live = crash (`AttributeError`) or silent malformed data.
**Impact**: Worker's OQ1 confirmed empirically. Per Fitz Constraint #2 (translation loss): design intent survived in handler signatures but NOT in engine invocation.
**Fix**: Pick ONE shape and propagate with typed parameter (not `Any`):
- Option A: engine iterates `candidates: list[Candidate]` per task, calls `apply(candidate, ctx)` per candidate
- Option B: handler `apply()` signature changes to `apply(proposal: ProposalManifest, candidates: list[Candidate], ctx)`

## 3 MAJOR Findings

### M1: Check #6 safety direction inverted in `closed_packet_archive_proposal`
**File**: `closed_packet_archive_proposal.py:378-386`
- `checks_passed += 1  # Count as passed (conservative: if PR existed, packet was recently active)`
- Spec (ARCHIVAL_RULES.md:70-72): "if any open PR touches a file inside P/ → LOAD_BEARING_DESPITE_AGE"
- Worker's reasoning misfires: stale-but-PR'd packet (e.g., feature branch resurrected after months) slips through.
- Realist-downgraded from CRITICAL: `apply()` hardcoded dry_run_only=True + catalog `pr_merge_by_agent: forbidden` + human review gate.
**Fix**: When `gh` unavailable, mark `SKIPPED_NEEDS_HUMAN_REVIEW` and classify packet `LOAD_BEARING_DESPITE_AGE` with reason "Check #6 unverified — fail closed per spec". Fail closed, not open.

### M2: Same Check #6 inversion in `stale_worktree_quarantine`
**File**: `stale_worktree_quarantine.py:138-144`
- Only logs debug, proceeds to mtime-only classification.
- Catalog (`TASK_CATALOG.yaml:88-91`) explicitly lists `any_worktree_whose_branch_appears_in_open_pr` as forbidden.
- Same realist downgrade.
**Fix**: Mirror M1 fix. Add `SKIP_PR_CHECK_UNVERIFIED` verdict; fall through to STAY when gh unavailable.

### M3: Hardcoded `"daily"` schedule drops `authority_drift_surface`
**File**: `engine.py:313` — `return registry.get_tasks_for_schedule("daily")`
- Real catalog has `authority_drift_surface` at `schedule: weekly`.
- Today's engine NEVER schedules it.
- Worker disclosed (D3) + left TODO at line 312.
**Fix**: Parameterize `_enumerate_candidates(config, schedule="daily")`. Scheduler binding layer (or cron call site) decides which schedule to dispatch. Pin schedule contract in Batch B brief.

## 3 MINOR Findings

- M-minor-1: Wave family ATOMIC per-member+override vs spec "union of paths" — functionally equivalent today (all 9 exemption checks are per-packet-name), but diverges if any future check examines union content.
- M-minor-2: Dead code in `closed_packet_archive_proposal.apply()` lines 205-212 (unreachable "If somehow live_default is True" branch).
- M-minor-3: `_mock_diff` calls `getattr(decision, "path", decision)` — falls through to str(decision) on ProposalManifest; cosmetic until C2 fixed.

## What's Missing

- No integration test exercising engine→real-handler end-to-end. Dispatcher test uses fake module.
- `_dispatch_by_task_id` declares `"enumerate"` method support (docstring line 323) but no engine call site.
- `enumerate()` handler signature `(entry, ctx)` not pinned by typed protocol; future drift risk.
- Performance: `enumerate()` walks `docs/operations` synchronously calling `git grep` per packet. ~100 packets = O(seconds)/tick. No per-packet timeout.

## Per-Probe Disposition

| # | Probe | Verdict |
|---|-------|---------|
| 1 | TaskCatalogEntry vs TaskSpec | PASS (advisor-rec design decision documented) |
| 2 | Check #6 safety direction | FAIL → M1+M2 |
| 3 | Wave family ATOMIC | PASS (minor divergence noted) |
| 4 | Dispatcher signature consistency | FAIL → C2 |
| 5 | Forbidden-path enforcement | PASS |
| 6 | dry_run guard at handler top | PASS (all 3 handlers) |
| 7 | Test coverage actual | PASS (18 tests substantive) |
| 8 | No INV-## / hooks edits | PASS |
| 9 | Hardcoded "daily" breaks weekly | FAIL → M3 |
| 10 | 8 exemption checks completeness | PASS for Check #0; #1-#5+#7-#8 implemented; #6 stub → M1+M2 |

## To Upgrade to ACCEPT

1. C1+C2 fixed: engine calls `handler.enumerate(entry, ctx)`; `apply()` receives Candidate.
2. M1+M2 fixed: Check #6 fails closed when gh unavailable.
3. M3 may stay deferred IF Batch B brief opens with parameterized-schedule contract.

## Provenance

WAVE 1.5 Batch A critic dispatched 2026-05-16 by orchestrator session 7f255122 (agent a4fd45ebf64d38822, opus, fresh-context). Per `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi`: 100% SEV-1 catch rate on architectural — confirmed (2 SEV-1 caught). Realist Check downgraded Check-#6 findings to MAJOR per real-world mitigation (dry_run+human review). Critic stayed in THOROUGH initially, escalated to ADVERSARIAL after 2 CRITICAL surfaced.
