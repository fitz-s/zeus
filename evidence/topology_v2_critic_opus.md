# critic-opus review of Navigation Topology v2 PLAN
HEAD: 9b677c28f418ce3d0abf90a7b1c30c0f4044ae63
Reviewer: critic-opus
Date: 2026-05-07
Worktree: /Users/leofitz/.openclaw/worktrees/zeus-cleanup-debt
Subject PLAN: docs/operations/task_2026-05-07_navigation_topology_v2/PLAN.md (929 LOC)

## Subject

Adversarial review of the K=3 structural-decision navigation/admission redesign PLAN, including 5 worktree-lifecycle capability additions and a per-worktree YAML sentinel.

## Verdict

**GO-WITH-CONDITIONS**

K=3 is approximately correct (with one defensible compression flag — F1 conflates two axes). All 18 cited paths resolve. Premise mismatch is bounded and recoverable. Three CONDITIONS must be met before Phase 1 ships:

- C1 (HIGH) — Phase ordering risk on Phase 1 vs Phase 2 must be addressed (Attack 9).
- C2 (HIGH) — `dispatch.py` lacks any `SessionStart` event handler today; the +30 LOC estimate in Phase 3 is unrealistic (Attack 8).
- C3 (MEDIUM) — typed_intent enum needs `hotfix` + explicit fall-through escape (Attack 3).

## Verification scoreboard

- 18 cited paths: **18/18 OK** (all resolve at HEAD 9b677c28).
- File:line citation rot: **GREEN** for 6/6 friction-pattern emitter citations re-grepped within 10 min.
  - `topology_doctor.py:2053` confirmed (`if admission_status == "scope_expansion_required"`).
  - `topology_doctor.py:2883` confirmed (`"scope_expansion_required": "navigation_scope_expansion_required"`).
  - `topology_doctor.py:2409` confirmed (`f"profile_needs_typed_intent:{selected_by}"`).
  - `topology_doctor_digest.py:639` confirms `_resolve_typed_intent` exists (PLAN cited line 1230 for caller; line 639 is the def site — both are accurate).
  - `topology_doctor_digest.py:1255-1261` `needs_typed_intent` set: confirmed.
  - `topology_doctor_policy_checks.py:259-261` `planning_lock_trigger`: confirmed.
  - `topology_doctor_script_checks.py:121-128` `script_long_lived_bad_name`: confirmed.
  - `topology_doctor_script_checks.py:267-274` `script_diagnostic_forbidden_write_target`: confirmed (line 281 actually).
  - `topology_doctor_docs_checks.py:431-457` `check_operations_task_folders` `operations_task_unregistered`: confirmed.
  - `tests/test_digest_admission_policy.py:122-131` `test_navigation_blocks_when_scope_expansion_required`: confirmed.
- Task folder count: **24** (PLAN claims 23 — off by 1; one folder created today in this same session probably). LOW.
- `task_2026` mentions in `docs/operations/AGENTS.md`: **45 confirmed** (matches PLAN).
- `pre_checkout_uncommitted_overlap` shipped: **CONFIRMED** at `.claude/hooks/registry.yaml:69-89` exactly as cited.
- `metadata.catalog_size: 16` confirmed in `architecture/capabilities.yaml:8`.
- `naming_conventions.yaml::file_naming.scripts.long_lived.allowed_prefixes`: confirmed at `architecture/naming_conventions.yaml:24` and consumed at `topology_doctor_script_checks.py:50`.

## ATTACK 1 [VERDICT: PASS-WITH-CAVEAT] K=3 compression check

The 5 frictions map onto 3 decisions as PLAN claims, but **F1 is a hybrid that hits both K1 (severity) and K3 (typed_intent), AND has a defensible third axis (allowlist semantics).** The PLAN itself acknowledges this: "F1 hits K1+K3" (line 60). That is honest, not compressed.

Strict structural reading:
- K1 owns: severity-tier on emitter side. Maps to F1, F4, F5.
- K2 owns: companion-loop-break for circular gates. Maps to F3, F4, F6.
- K3 owns: typed-intent admission semantic. Maps to F1, F2.

F4 hits K1+K2 (also acknowledged). F1 multi-hit is real and the PLAN does NOT hide it. K=3 is honestly K=3 even if some frictions touch multiple decisions — that's how structural decompositions work (frictions are observable symptoms; decisions are root causes; a friction can be a symptom of several roots simultaneously).

Where K=3 IS slightly forced: the "allowlist semantics is K3" framing is correct, but F1's "scope_expansion_required" path could also be read as a 4th independent issue: *positive-list-only admission*. PLAN folds that into K3 by re-defining typed_intent to short-circuit the allowlist. That is a legitimate compression — it actually IS the same decision viewed from two angles ("how do we admit a new path that doesn't match an existing list" → "use typed-intent semantics not allowlist match"). Not artificially squashed.

CAVEAT (LOW): the framing "5 → 3" undersells that F4 and F1 are 2-hit and would be deeper research signals; a future reviewer might miss this nuance. Not a blocker.

## ATTACK 2 [VERDICT: PASS] Companion-loop-break covers 4 admission gates

PLAN §2.3 (line 246-254) `companion_auto_admits` enumerates exactly 4 companion patterns:
- `scripts/**` → `architecture/script_manifest.yaml`
- `tests/test_*.py` → `architecture/test_topology.yaml`
- `docs/operations/task_*/**` → `docs/operations/AGENTS.md`
- `src/**` → `architecture/source_rationale.yaml`

Verified registries exist:
- `architecture/script_manifest.yaml` exists; `topology_doctor_script_checks.py:246` emits `script_manifest_missing` against it.
- `architecture/test_topology.yaml` exists (1,272 LOC).
- `architecture/source_rationale.yaml` exists.

**No 5th hidden gate found** that maps to an emitter under typed_intent=create_new. `docs/artifacts/AGENTS.md` (mentioned in operator brief) — checked; not currently an emitter target in `topology_doctor_docs_checks.py`. The 4 companions are exhaustive for the friction class.

POSSIBLE-FUTURE-RISK (LOW): if `architecture/admission_severity.yaml` itself ever requires a manifest companion (recursive case), a 5th rule would be needed. Not a blocker today since the PLAN's own admission_severity.yaml is the schema for navigation, not a script/test/task/src-class file.

## ATTACK 3 [VERDICT: FAIL] typed_intent enum completeness

PLAN's enum: `{plan_only, create_new, modify_existing, refactor, audit, hygiene}`.

**Missing categories with empirical evidence in this very repo:**
- `hotfix` — emergency direct-to-main fix (recovery PR pattern). Different from `modify_existing` because it bypasses normal companion loops. Not in enum.
- `rebase_keepup` — branch keep-up flow (operator's draft §D matrix). Different from `refactor` because no source change. Not in enum.
- `cotenant_shim` — observed in `evidence/cotenant_shims/`. Different from all 6 above.

PLAN §2.3 line 277 says "Free-form `--intent` strings still work — they fall through to today's resolution path." This is the escape hatch. **But §1 §2 §3 of `architecture/admission_severity.yaml` (PLAN line 165-171) makes `profile_needs_typed_intent` ADVISORY-only, not blocking.** So the escape is graceful — agents using `hotfix` or `rebase_keepup` get an advisory warning, not a denial.

**FAIL because:** PLAN's H-R3 risk note (line 807) acknowledges enum mismatch as "M/M" risk but does NOT enumerate which mismatches (no inventory of likely real-world inputs). The 30-day adoption target is `≥80% canonical` — but if 20%+ of valid use cases (hotfix/keepup/cotenant) ARE legitimately not in the enum, that adoption ratio cannot be reached. Goal is structurally inconsistent.

REQUIRED FIX (Phase 1): add `hotfix` and `rebase_keepup` to the enum, OR add an explicit `other` enum value documented as "use when none of the canonical 6 fit; emits ritual_signal `enum_extension_candidate`." Per Fitz Constraint #1 — "make the category impossible, not the instance." Enum without explicit-other forces silent fall-through to advisory-only when an honest agent could have declared the case.

## ATTACK 4 [VERDICT: PASS-WITH-CAVEAT] 5 worktree capabilities — overlap or gap?

Goals → owning capability mapping:
- (a) starting parallel work → `worktree_create` (PASS)
- (b) cross-agent collision detection → `cross_worktree_visibility` (advisory only — PASS)
- (c) PR-open accumulation → already advisory in `pr_create_loc_accumulation` hook (PASS, NOT in this PLAN's scope, correctly delegated)
- (d) keep-up-with-main decision → `worktree_branch_keepup` (PASS)
- (e) post-merge cleanup → `worktree_post_merge_cleanup` capability + existing hook (PASS)
- (f) backup/draft retention → `workspace_hygiene_audit` (PASS, advisory-only is correct)
- (g) abandoned worktree detection → **NO OWNING CAPABILITY** (CAVEAT)

CAVEAT (MEDIUM): "abandoned worktree" detection (worktree where last_commit_ts > 30d AND sentinel.intent unfulfilled) is an observable safety signal. PLAN's `cross_worktree_visibility` covers status display but does not own the staleness assertion. Operator's draft §D mentions this implicitly. Not a blocker for K=3 architecture but is a goal-coverage gap.

REQUIRED FIX (Phase 3, scope-add not blocker): extend `cross_worktree_visibility.intent` to include "stale worktree advisory (last_commit_ts > N days emits advisory)" OR add 6th capability `worktree_staleness_audit`. Either is a legitimate append.

## ATTACK 5 [VERDICT: PASS] Per-worktree YAML sentinel vs lock service — race

PLAN §2.7 line 511 explicitly states: "NOT a mutable lock service. NOT polled by other agents."

The race scenario: two agents simultaneously creating the SAME worktree path is **impossible at git's level** — `git worktree add` itself fails atomically if the path exists. Verified semantics in `git-worktree(1)` manpage (PLAN source citation 6).

The ALTERNATIVE race (two agents writing different sentinels to the same path within milliseconds of each other) — this is a write race on the YAML file itself, not on the worktree. The sentinel is created by the worktree creation flow. Since `git worktree add` succeeds for only one of the two agents, only one sentinel is written. PASS.

NIT (LOW): PLAN should explicitly state in §2.7 that sentinel write happens AFTER `git worktree add` returns success, so the race is delegated to git's atomicity guarantee. Not a blocker; the architecture is correct, the documentation could be tighter.

## ATTACK 6 [VERDICT: PASS-WITH-CAVEAT] Auto-registration breaks ratchet?

PLAN §2.4 line 297-301: "Companion auto-admit only when the companion path is ALSO in the diff." This is the structural defense — agent must INCLUDE the manifest entry in their `--files`, not just the new script.

So: agent adds `scripts/foo.py` AND `architecture/script_manifest.yaml` (with the foo.py entry). Both admitted. The "1000 stub scripts" attack requires the agent to also write 1000 manifest entries. Manifest entries carry mandatory `lifecycle`, `authority_scope`, `write_targets` per `topology_doctor_script_checks.py:268-272`. PLAN's H-R6 catches this ("Phase 1 schema test asserts unique `id` field; no `hard_kernel_paths` duplicate").

CAVEAT (MEDIUM): no rate limit on Phase 2 K2 admissions. An adversarial agent could include hundreds of paths in one `--files` argument. PLAN does not address `len(requested) > N` limit. Not strictly necessary because manifest writes are committed atomically and reviewable in the diff, but a `len(requested) > 50` advisory would be cheap insurance.

## ATTACK 7 [VERDICT: PASS] Severity demotion attack — slow-leak

PLAN's M1 (telemetry-as-output, line 822) emits `severity_demoted: true` for every K1 demotion. PLAN §6.3 sets day-30 target at "<30% of navigation events." This IS the metric.

However: the metric measures DEMOTION rate, not poor-quality-name accumulation rate. The threat model is: agents accumulate scripts named `low_high_alignment_report.py`, `_my_temp_thing.py`, etc. — each individually "advisory" but compounding. PASS because:
- `tests/test_help_not_gate.py` (existing, 408 LOC) M5 binding (PLAN §5 row 5) extends to assert that admission_severity does not block.
- The cross-check is the inverse: `naming_conventions.yaml::exceptions` field already has 30+ documented exceptions; agents adding 30 more would be visible in PR diff review. Manual gate at PR review remains; advisory does not eliminate it.

NIT: PLAN does not propose a Day-90 metric for "naming exceptions added per month" as a slow-leak counter. Recommend adding to §6.5 day-90 review.

## ATTACK 8 [VERDICT: FAIL] dispatch.py fork or extension?

**CRITICAL.** Verified `.claude/hooks/dispatch.py` (1,363 LOC). Searched for `SessionStart`, `WorktreeCreate`, `WorktreeRemove`:
```
grep -n "WorktreeCreate\|WorktreeRemove\|SessionStart" .claude/hooks/dispatch.py
[no matches]
```

dispatch.py currently handles `PreToolUse` (line 256, 261, 741) and `PostToolUse` (line 571) as JSON envelopes; other events fall to `exit 2` (line 270). **No SessionStart handler exists today.**

PLAN §2.9 (line 644-645) says "`dispatch.py` ports the SessionStart event; calls `python3 scripts/worktree_doctor.py advisory` and emits `additionalContext`." Phase 3 budget says "+30 LOC" for this. **30 LOC is unrealistic** because it requires:
- New event-routing branch in dispatch's main entry (the existing PreToolUse/PostToolUse switch).
- New `_emit_advisory(...)` call site (this exists at line 274 but is generic — must be wired to SessionStart).
- New invocation pattern: `subprocess.run(["python3", "scripts/worktree_doctor.py", "advisory"])` with timeout, error capture, fall-open-on-error contract.
- New telemetry emission (`ritual_signal_emitted: true`).
- Tests for SessionStart event handling that did not exist before.

Realistic estimate: **80-120 LOC** for the dispatch.py extension alone (not counting the test file).

REQUIRED FIX (Phase 3 LOC re-estimate): bump `dispatch.py` budget from `+30 LOC` to `+80-120 LOC`. Total Phase 3 LOC moves from ~430 to ~480-520. Still well under the 1500 LOC ceiling, but the planning math is wrong as stated. PLAN §8 phase summary (line 913) line "dispatch.py extension (+30 LOC)" must be corrected.

This is a **HIGH severity finding** because optimistic LOC estimates lead to under-scoping that surfaces as Phase 3 overrun, then pressure to skimp on the test file (the ADVISORY contract validation tests). PLAN already cites `evidence/hook_redesign_critic_opus_final_v2.md` ATTACK 8 as the precedent for advisory-doesn't-override-blocking — that critic's whole point was that advisory fall-open-on-error MUST be tested explicitly.

## ATTACK 9 [VERDICT: FAIL] 3-phase ordering — gap-window risk

**CRITICAL.** Phase 1 ships `architecture/admission_severity.yaml` with severity defaulting to ADVISORY. Phase 2 ships the decision-layer wiring that consumes that severity. Phase 1 → commit → maybe several days/weeks → Phase 2.

PLAN §3 Phase 1 exit criteria line 728: "No decision-layer changes yet — `topology_doctor*.py` still emits issues with uniform severity. Parallel install. Zero behavioral change for agents."

This is correct as written — Phase 1 has zero behavioral effect. So far so good.

**But Phase 2's ordering is the real gap.** Phase 2 ships severity demotion (K1) AND companion-loop-break (K2) in the same commit. PLAN §6.2 cutover sequence (line 846-853) is:
- Day 1: Phase 1 ships
- Day 2: Phase 2 ships in shadow mode
- Day 2-9: 7-day shadow window
- Day 9-14: Phase 3 ships

Inside Phase 2, K1 and K2 ship simultaneously. **What if K1 lands first (severity registry consumed) but K2 (companion-loop-break) has a bug?** Then naming gates are weakened (BLOCKING → ADVISORY for `script_long_lived_bad_name`, etc.) but the circular admission gate (F3, F4) is still enforced (just with a broken loop-break). Result: weaker gates AND still-circular admission = strictly worse than starting state.

PLAN §6.4 rollback offers `ZEUS_ADMISSION_SEVERITY=off` as the K1 rollback and `ZEUS_COMPANION_LOOP_BREAK=off` as the K2 rollback. So per-decision rollback IS available. But the rollback is reactive, not preventive.

REQUIRED FIX: split Phase 2 into 2A (K1 severity registry consumption only, K2 disabled by default with `ZEUS_COMPANION_LOOP_BREAK=on` opt-in) and 2B (K2 enabled by default). Then 14-day shadow window applies to K1 alone before K2 ships. Otherwise the audit ordering is "ship K1+K2 → discover K2 bug → realize K1 already weakened gates → operator must run rollback for K1 too even though K1 is correct." That's the worst kind of operator burden.

This is a **HIGH severity finding** but a SOLVABLE planning split, not an architectural defect.

## ATTACK 10 [VERDICT: PASS] Anti-drift to topology v1 charter

`tests/test_help_not_gate.py` exists (408 LOC, header verified: "INV-HELP-NOT-GATE mid-drift check").

The three assertions in that test are:
1. `test_no_helper_blocks_unrelated_capability` — no helper's forbidden_files crosses scope_capabilities.
2. `test_every_invocation_emits_ritual_signal` — schema-required fields present.
3. `test_does_not_fit_returns_zero` — does_not_fit policies don't carry forbidden_files.

PLAN §5 row 5 (line 826) extends this test with: (a) admission_severity.yaml entries do not block out-of-scope diffs; (b) typed-intent mismatch returns advisory not denial; (c) per-worktree sentinel absence does not block admission.

Demoting `naming_violation` to ADVISORY does NOT break these assertions. The assertions are about helpers not blocking out-of-scope payloads — they are agnostic to severity tier. Demotion strengthens them (more cases that emit-without-block).

Verified `naming_violation` is NOT in test_help_not_gate.py's payload set (no match for the term). The test concerns capabilities and skills surface, not topology_doctor's internal severity. PASS.

CAVEAT (LOW): PLAN's M5 extension is described as "two new test functions" (line 826). Realistic for the three sub-assertions in row 5. Not a defect.

## Severity rollup

- **CRITICAL: 0**
- **HIGH: 2** (Attack 8, Attack 9)
- **MEDIUM: 2** (Attack 3, Attack 4 caveat)
- **LOW: 4** (Attack 1 framing, Attack 5 nit, Attack 6 rate-limit, Attack 7 metric add)

## Required fixes (Phase 1 entry conditions)

C1 — Attack 9 — split Phase 2 into 2A/2B with K2 opt-in default. **PLAN §3 line 733** rewrite + **§6.2 line 846** cutover sequence rewrite.
C2 — Attack 8 — re-estimate dispatch.py extension from `+30 LOC` to `+80-120 LOC`. **PLAN §3 Phase 3 line 776** + **§8 line 913** numbers updated. Add explicit test for SessionStart fall-open-on-error contract per hook_redesign_critic_opus_final_v2.md ATTACK 8 precedent.
C3 — Attack 3 — add `hotfix` AND `rebase_keepup` (or explicit `other` escape) to `typed_intent_enum` in **PLAN §2.3 line 222**. Update `tests/test_typed_intent_enum.py` Phase 1 deliverable.

## Minor fixes (non-blocker; address during Phase 1 or 3)

- M1 — Attack 1 — add a footnote to PLAN §1.3 line 60 stating F1 and F4 are 2-hit and the multi-mapping is intentional (anti-rubber-stamp future-reviewer cue).
- M2 — Attack 4 — extend `cross_worktree_visibility.intent` (PLAN §2.6 line 467) OR add 6th capability `worktree_staleness_audit`.
- M3 — Attack 5 — add note to PLAN §2.7 that sentinel write is post-`git worktree add` success (race delegated to git atomicity).
- M4 — Attack 6 — add `len(requested) > 50` advisory in `_apply_companion_loop_break`.
- M5 — Attack 7 — add Day-90 "naming exceptions added per month" metric to §6.5.
- M6 — task-folder count: PLAN line 28 says "23 task folders"; today is 24. Trivial off-by-one (one folder created today). Update or note.

## Operator decisions pending

- D1 — confirm enum should include `hotfix` / `rebase_keepup` vs explicit `other` (Attack 3).
- D2 — confirm Phase 2A/2B split is acceptable budget overhead (Attack 9).
- D3 — confirm Phase 3 LOC bump from 430 to 480-520 is acceptable (Attack 8).

## Verdict block

```
verdict: GO-WITH-CONDITIONS
critical: 0
high: 2
medium: 2
low: 4
proceed_to_phase_1: True
operator_decisions_pending: [D1, D2, D3]
```

Phase 1 is READY to proceed. The Phase 1 deliverables (admission_severity.yaml schema + capabilities.yaml extension + 3 test files) are not affected by C1, C2, or C3 — those conditions affect Phase 2 and Phase 3 ordering and LOC. C3 (typed_intent enum extension) gets resolved IN Phase 1 since the enum lives in `admission_severity.yaml`. Operator decisions D1-D3 should be answered before Phase 1 commit so the schema is final.

proceed_to_phase_1: True
