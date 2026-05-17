# critic-opus re-verification of PR #72 cleanup-debt-2026-05-07
HEAD: c2a59f96c9fd4c28c79efb79838a34f6bc1f3c23
Reviewer: critic-opus
Date: 2026-05-07
Branch: cleanup-debt-2026-05-07
PR diff: 27 files, +5472 / -30 vs main

## Subject
Re-verification after round-3 fix commit `c2a59f96` claiming closure of 5 findings: F-K3-AUDIT-OVERADMIT (HIGH), F-K2-BATCH-CAP-COSMETIC (HIGH), F-AS06-PHANTOM-EMITTER (MED), F-AS05-LINE-DRIFT (MED), F-AGENTS-K3-INVISIBLE (LOW).

## Verdict
**APPROVED-WITH-CAVEATS** — all 5 prior findings credibly closed at file:line. Two new third-order defects found in citation rot of unrelated AS-rules and one half-wired emitter. Caveats are LOW-MEDIUM, do NOT block merge once acknowledged or queued.

## Provenance note on the prior critic doc
The brief cites `evidence/topology_v2_pr72_full_critic_opus.md`; that path does NOT exist. The 5 finding-IDs (F-K3-AUDIT-OVERADMIT etc.) appear nowhere in the repo. Closest committed doc is `evidence/topology_v2_critic_opus.md` — a Phase-1 plan-stage critic with different findings (Attack 8/9 HIGH on dispatch.py LOC + Phase 2 ordering). The round-2 critic the brief references must have lived in a session transcript never committed. I treated the round-3 commit message's F1-F5 as the authoritative closure list and grep-gated each.

## Closure verification

| Finding | Severity | Closure citation | Verdict |
|---|---|---|---|
| F-K3-AUDIT-OVERADMIT | HIGH | `topology_doctor_digest.py:1473-1571` rewrites `_apply_typed_intent_shortcut` to whitelist-driven; `admission_severity.yaml:46-69` declares `admits_path_globs` for `plan_only`/`audit`; test `test_typed_intent_enum.py:269` `test_all_blocked_paths_produce_advisory_only_not_admitted` exists | CLOSED |
| F-K2-BATCH-CAP-COSMETIC | HIGH | `topology_doctor_digest.py:1413-1453` actually gates admission (caps `newly_admitted` to first `batch_cap`, sets `status=advisory_only` + `blocked_by_batch_cap` + `next_action`); test `test_companion_loop_break.py:378` `test_batch_cap_actually_gates_admission_not_just_advisory` exists | CLOSED |
| F-AS06-PHANTOM-EMITTER | MED | `topology_doctor_test_checks.py:55` emits `test_topology_missing` via `_issue_with_admission_severity`; `admission_severity.yaml:202` code corrected from phantom `test_topology_test_unregistered` to `test_topology_missing`; `:203` emitter_path matches | CLOSED |
| F-AS05-LINE-DRIFT | MED | `admission_severity.yaml:189` cites `:451`; `topology_doctor_docs_checks.py:451` shows `_issue_with_admission_severity("operations_task_unregistered", ...)` (verified directly via sed) | CLOSED |
| F-AGENTS-K3-INVISIBLE | LOW | `AGENTS.md:269-281` adds 13-line K3 typed_intent shortcut summary with whitelist-driven note for `plan_only`/`audit`, K2 batch-cap reference, enum pointer to `admission_severity.yaml::typed_intent_enum` | CLOSED |

102 admission/severity tests pass (`test_typed_intent_enum.py` + `test_companion_loop_break.py` + `test_admission_severity_schema.py`). Round-3 fix commit message mappings match grep evidence.

## ATTACK 1 [VERDICT: FAIL] Citation rot — AS-01/02/03 emitter_path lines drifted
`architecture/admission_severity.yaml:120` claims "emitter_path:line references verified 2026-05-07 (grep-gate within 10 min per CLAUDE.md)." Grep at HEAD c2a59f96:
- AS-01 cites `topology_doctor.py:2053` → actual line 2053 is `unresolved_fields=(),` (struct field). Real `if admission_status == "scope_expansion_required"` is at **line 2114** (drift +61).
- AS-02 cites `topology_doctor.py:2883` → actual line 2883 is empty. Real `"scope_expansion_required": "navigation_scope_expansion_required"` mapping is at **line 2952** (drift +69).
- AS-03 cites `topology_doctor.py:2409` → actual line 2409 is `task=task,`. Real `f"profile_needs_typed_intent:{selected_by}"` is at **line 2470** (drift +61).

This is the SAME rot class round-3 corrected for AS-05 (`:431`→`:451`). The 2026-05-07 verification line covered AS-04..AS-09 but NOT AS-01..AS-03. Per `feedback_grep_gate_before_contract_lock.md` — line-refs rot fast; one bulk audit must touch every cited line. Three of nine AS-rules are still stale.

REQUIRED FIX (LOW; this PR or follow-up): update `admission_severity.yaml:130/147/161` to `:2114/:2952/:2470` OR re-anchor by symbol name (preferred — symbol survives line drift).

## ATTACK 2 [VERDICT: PASS] Premise mismatch
F1's claimed PLAN.md §6:693 reference for canonical scopes verifies — `admission_severity.yaml:46-69` declares `admits_path_globs` matching the description. F2's claim of "first batch_cap pairs admitted; remainder blocked" matches `topology_doctor_digest.py:1416-1418`. F3 phantom-code claim verifies — `test_topology_test_unregistered` returns zero hits in `topology_doctor_test_checks.py`; only `test_topology_missing` exists. All five round-3 premises hold against current source.

## ATTACK 3 [VERDICT: PASS] Test relationship coverage
Round-3 introduces three relationship tests that exercise the boundary admission→requested→admitted_files:
- `test_all_blocked_paths_produce_advisory_only_not_admitted` (typed_intent → status field)
- `test_batch_cap_actually_gates_admission_not_just_advisory` (101 files → first 50 admitted + 51 blocked)
- `test_typed_intent_status_advisory_only_when_paths_blocked` (mixed-paths invariant)

Cross-module: K2 (companion loop) → K3 (typed_intent) composition tested via `_apply_companion_loop_break` then `_apply_typed_intent_shortcut` order in `build_digest` (`topology_doctor_digest.py:1648-1650`). Antibody class is genuine — these tests fail on previous round-2 code.

## ATTACK 4 [VERDICT: PASS] Authority direction
DB > derived JSON > YAML > emitters direction respected. `admission_severity.yaml` is canonical; emitters resolve severity via `_load_admission_severity()` at runtime (`topology_doctor.py:758`); no inversion. Source-of-truth flow: YAML `code` field → `_issue_with_admission_severity(code, ...)` → emit. Run-time resolution is one-way.

## ATTACK 5 [VERDICT: FAIL] Negative-space audit — AS-09 emitter unwired
`admission_severity.yaml:250` cites `policy_checks.py:259` for AS-09 (`planning_lock_required`). Line 259 is the `planning_lock_trigger` *function definition* (the trigger detector), NOT the emit site. Actual emit is at `topology_doctor_policy_checks.py:312`:
```
issues.append(api._issue("planning_lock_required", path, ...))
```
Note `api._issue` not `api._issue_with_admission_severity`. So even if `admission_severity.yaml` ever marks `planning_lock_required` as ADVISORY, the emitter ignores it — severity tier silently drops to BLOCKING default. Same pattern as the F3 (AS-06) fix that just landed: round-3 wired `topology_doctor_test_checks.py:55` through `_issue_with_admission_severity` but missed `policy_checks.py:312`.

REQUIRED FIX (MEDIUM): change `policy_checks.py:312` and `:320` (`planning_lock_evidence_invalid`) from `api._issue(...)` to `api._issue_with_admission_severity(...)`. Otherwise AS-09 in YAML is a phantom-control: documented severity has no runtime effect.

This is a third-order defect: AS-06 was caught and fixed because round-2 critic flagged the *code* mismatch. AS-09's emitter is correct code (`planning_lock_required`) but uses the wrong helper. Round-2 critic didn't grep for `_issue_with_admission_severity` coverage — only 5/9 AS-rule emitters call it (`grep -c` confirms: docs_checks.py=1, test_checks.py=1, script_checks.py=3, topology_doctor.py=1; policy_checks.py=0).

## ATTACK 6 [VERDICT: PASS] Provenance chain
`admission_severity.yaml` declares `metadata.created_date: 2026-05-07`, `authority_basis`, schema version. Each rule carries `code` + `category` + `severity` + `emitter_path`. New tests carry the mandatory header block (`test_typed_intent_enum.py:1-3`, `test_companion_loop_break.py:1-3`, `test_admission_severity_schema.py:1-3`).

## ATTACK 7 [VERDICT: PASS] Mode mismatch
PR touches `src/execution/live_executor.py` (Tier 0) removing `_assert_risk_level_allows()`. Verified: `_assert_risk_level_allows()` was a documented no-op; removal is safe because `gate_runtime.check("live_venue_submit")` at `src/architecture/gate_runtime.py:98` explicitly lists `risk_level_halt` in the gate set. No callers remain (`grep -rn _assert_risk_level_allows src/ tests/` returns zero). `tests/test_zeus_risk_halt_e2e.py` still passes (asserts gate path not method path).

NOTE: this src-side change is NOT in the round-3 closure list and is NOT mentioned in the PR title/description. It came from earlier commit `e704b1af` ("L-1 dead code"). Operator should be aware that the PR description focuses on topology v2 but ALSO ships a Tier-0 src removal. Defensible (no-op confirmed) but the PR scope drift is real.

## ATTACK 8 [VERDICT: PASS] Type-encodable category errors
F1 closure replaces an "if intent is plan_only or audit, admit everything" path-string check with a whitelist set + glob matcher, AND maintains the `_WHITELIST_DRIVEN_INTENTS` set as the type-discriminator (`topology_doctor_digest.py:82-87`). Per Fitz Constraint #1: this is a step toward "make the category impossible" — non-whitelist-intents structurally cannot reach the wrong code path because the set lookup precedes admission logic. Could be tighter (an enum class would be ideal), but the current set-based dispatch is acceptable.

## ATTACK 9 [VERDICT: PASS] Compaction survival
`AGENTS.md:269-281` makes the K3 contract discoverable to any future agent reading the entry-point doc. `admission_severity.yaml:42-69` declares the canonical enum + globs in one block — survives intent translation. Round-3 commit message names the closure file:line for each finding. Future agent with only diff + commit message + AGENTS.md can reconstruct the contract.

## ATTACK 10 [VERDICT: PASS] Rollback path
F1 + F2 are isolated to `topology_doctor_digest.py` (one file) + `admission_severity.yaml` (one file). F3 is one line in `topology_doctor_test_checks.py` + one block in YAML. F4 is one line in YAML. F5 is one block in AGENTS.md. Single-commit revert of `c2a59f96` cleanly removes round-3 closures. The earlier round-2 commit `2d02c765` covers blocked-path advisory_only logic separately. Revert chain is two commits max — clean.

## Severity rollup
- CRITICAL: 0
- HIGH: 0
- MEDIUM: 1 (Attack 5 — AS-09 emitter unwired)
- LOW: 1 (Attack 1 — AS-01/02/03 line drift)

Both are NEW third-order defects, not regressions of the 5 closed findings.

## Required fixes
- **MED — AS-09 unwired emitter**: `scripts/topology_doctor_policy_checks.py:312` and `:320` change `api._issue(...)` → `api._issue_with_admission_severity(...)`. Without this, AS-09's documented severity in YAML is unenforced. Can ship in this PR or as a follow-up if operator chooses to scope-cap.
- **LOW — AS-01/02/03 citation rot**: `architecture/admission_severity.yaml:130/147/161` update from `:2053/:2883/:2409` → `:2114/:2952/:2470`. Prefer symbol-anchor refactor (e.g., `topology_doctor.py::_route_card_next_action_hint` rather than line). This is the same rot class round-3 just fixed for AS-05; closing AS-01/02/03 makes it permanent.

## Carry-forward findings (not blocker)
- Tier-0 `src/execution/live_executor.py` change is part of this PR but absent from PR title/description. Suggest adding one line to PR description acknowledging the Phase-4 L-1 cleanup ride-along.
- The brief-cited critic doc `evidence/topology_v2_pr72_full_critic_opus.md` doesn't exist on disk. If a future re-verify is run, name `evidence/topology_v2_pr72_full_critic_opus_v2.md` (this doc) plus the round-3 commit message as authority — there is no v1 critic doc to load.

## Anti-rubber-stamp self-check
- Attack 1, 5 are FAIL with file:line evidence. Not pattern-stamping.
- Attacks 5/8/9 (the most-rubber-stamped per template) were re-read; Attack 5 caught the AS-09 unwired emitter, NOT a phantom complaint.
- Attack 2 PASS based on three independent file:line confirmations, not blanket trust.
- Attack 7 PASS based on `gate_runtime.py:98` literal gate-set inspection, not "no-op claim is plausible."

## Verdict block
```
verdict: APPROVED-WITH-CAVEATS
critical: 0
high: 0
medium: 1
low: 1
prior_findings_closed: 5/5
new_third_order_defects: 2
mergeable: True (caveats can be queued; AS-09 SHOULD be addressed before next live-launch milestone)
```

pr72_complete: True
mergeable_recommendation: True
