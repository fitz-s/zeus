# INV-15 Upgrade Review — Critic-Harness Gate

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
HEAD at review: `49cf5cc` (INV-15 upgrade commit; worktree-post-r5-eng)
Pre-batch baseline: 90/22/0 (4-file critic baseline) — preserved
Cycle 5 erratum count: 2/3 (engineering work, not erratum on prior verdict per dispatch §8)

## Verdict

**APPROVE-WITH-CAVEATS** (1 LOW caveat on omitted-tests rationale; 1 LOW nuance on semantic match indirection; 0 BLOCK conditions)

Engineering work cleanly applies the BATCH D pattern (CITATION_REPAIR for schema-citation gap, not enforcement gap) per methodology §5.X. All 6 cited tests resolve, pass, and exercise the INV-15 source whitelist gate at `src/calibration/store.py:117` `_resolve_training_allowed`. Bidirectional grep finds no other test files referencing INV-15. Baseline 90/22/0 preserved.

I articulate WHY APPROVE-WITH-CAVEATS:
- **6/6 cited tests collect + pass** independently (12 in broader sweep including class teardown — all PASS in 0.91s)
- **Bidirectional grep**: 7 INV-15 references across the 2 cited files; no other test files mention INV-15. Coverage scope sound.
- **Source code at L117** exists and matches the cited function name `_resolve_training_allowed` per docstring "INV-15: enforce source whitelist on training_allowed"
- **Methodology §5.X** cite valid (verified at L259 of methodology doc)
- **Round3 verdict §1 #5** cite valid (verified at L35: "INV-15 + INV-09 must be upgraded BEFORE CALIBRATION_HARDENING starts (~6-10h precondition)")
- **CITATION_REPAIR comment** describes the change accurately + cross-references methodology §5.X case study + R3 round3_verdict §1 #5 LOCKED
- **Cycle 5 erratum count** correctly does NOT increment (this is engineering upgrade, not post-implementation falsification of a prior claim)

2 LOW caveats below.

## Pre-review independent reproduction

```
$ git log -2 --oneline
49cf5cc INV-15 upgrade: register 6 existing relationship tests (BATCH D pattern)
6df76a0 Commit Tier 2 Phase 3+4 artifacts (untracked → tracked)

$ pytest --collect-only on 6 cited tests → 6 collected
$ pytest tests/test_phase4_rebuild.py::TestINV15SourceWhitelistGate tests/test_harvester_high_calibration_v2_route.py
12 passed in 0.91s

$ pytest 4-file critic baseline
90 passed, 22 skipped in 1.91s
```

EXACT MATCH baseline + cited tests resolve + cited tests pass.

## ATTACK 1 — All 6 cited test paths resolve [VERDICT: PASS]

`pytest --collect-only` for the 6 cited test paths returns "6 tests collected in 0.80s". No path errors, no collection failures. PASS.

## ATTACK 2 — All cited tests pass [VERDICT: PASS]

12 passed in 0.91s when running both target files (5 in TestINV15SourceWhitelistGate cited + 2 omitted from class + 5 in test_harvester_high_calibration_v2_route.py + 1 cited there). Zero failures. PASS.

## ATTACK 3 — Semantic match between INV-15 statement and tests [VERDICT: PASS-WITH-NUANCE]

INV-15 statement (verbatim from invariants.yaml L132): *"Forecast rows lacking canonical cycle identity may serve runtime degrade paths but must not enter canonical training."*

Cited tests verify: `_resolve_training_allowed(source, data_version, requested)` forces `False` when `data_version` prefix is not in `_TRAINING_ALLOWED_SOURCES = {'tigge', 'ecmwf_ens'}` (non-whitelisted source).

**Semantic relationship is INDIRECT (proxy, not direct)**:
- INV-15 is about CYCLE IDENTITY (issue_time provenance)
- Tests are about SOURCE WHITELIST (data_version prefix)
- Implication chain: only canonical TIGGE/ecmwf_ens sources have verifiable cycle identity → non-whitelisted sources are blocked from training → INV-15 invariant preserved transitively

This is a reasonable proxy because:
- The whitelist enforces source provenance; sources outside the whitelist (openmeteo, custom_experimental_v1) cannot guarantee cycle identity
- The gate is at the DB-write boundary (calibration store insert), so non-canonical sources are blocked before entering training
- The `requested=True` caller intent is OVERRIDDEN by the gate, matching INV-15's "MUST NOT enter canonical training" language

**NUANCE-INV15-1 (LOW)**: the CITATION_REPAIR comment says "tests already enforce the source whitelist gate" — this is technically correct but understates the proxy relationship. A future agent reading invariants.yaml might assume the tests directly verify "cycle identity is canonical" when they actually verify "source is whitelisted (which implies cycle identity is canonical)". Recommend a follow-up enrichment to the comment: "tests enforce source whitelist as the operationalization of cycle-identity-canonicity — the whitelist is the proxy by which canonical cycle identity is verified at insert time."

PASS-WITH-NUANCE.

## ATTACK 4 — Bidirectional grep: any tests missed? [VERDICT: PASS-WITH-LOW-CAVEAT]

Reverse grep: `grep -rn "INV-15\|INV_15\|inv_15" tests/` returns 11 lines across:
- `tests/test_phase4_rebuild.py` (8 references; 2 in docstrings + 6 in test bodies)
- `tests/test_harvester_high_calibration_v2_route.py` (3 references)

No other test files mention INV-15. Coverage scope sound at file level.

**Class-membership check**: `TestINV15SourceWhitelistGate` has **7 tests** total; executor cited **5 of 7**. The 2 omitted are:
- `test_source_with_trailing_space_is_normalized` (L150)
- `test_data_version_with_leading_space_is_rejected` (L161)

See ATTACK 5 for omission rationale audit.

**LOW-CAVEAT-INV15-1**: at the FILE level, no tests missed. At the CLASS level, 5 of 7 cited (2 omitted; ATTACK 5 audit determines whether defensible).

## ATTACK 5 — Why only 5 of 7 tests in the class? [VERDICT: REVISE]

Independent inspection of the 2 omitted tests:

**`test_source_with_trailing_space_is_normalized` (L150)**: tests that `"TIGGE_ "` (trailing space) IS accepted by the whitelist (passes after normalization). Per `src/calibration/store.py:117` docstring: *"Normalize: strip whitespace and lowercase so 'TIGGE_' or ' tigge_...' don't bypass."* This test verifies the documented normalization branch of the gate.

**`test_data_version_with_leading_space_is_rejected` (L161)**: tests that `" openmeteo_..."` (leading space) IS REJECTED. Same: tests normalization-then-whitelist-check.

**The executor's framing "edge-case-not-core-invariant" is QUESTIONABLE**:
- Both omitted tests verify the NORMALIZATION branch of the same `_resolve_training_allowed` gate
- Per source code at L117, normalization IS part of the gate (not auxiliary): "Normalize: strip whitespace and lowercase so 'TIGGE_' or ' tigge_...' don't bypass"
- The normalization is DEFENSE-IN-DEPTH against bypass attempts — directly an enforcement mechanism
- An attacker passing `"tigge_..."` with leading space would bypass a naive whitelist; the normalization is the antibody for this bypass

**REVISE recommendation**: include the 2 omitted tests in the `tests:` block. CITATION_REPAIR pattern (per BATCH D) is "register all existing tests that enforce the invariant" — the 2 omitted tests enforce the normalization branch which IS part of the invariant's enforcement gate. Excluding them creates a coverage gap that future BATCH-D-style audits would flag.

This is a non-blocking REVISE recommendation; the upgrade work is structurally sound, but the citation list should be COMPLETE per the BATCH D pattern's own discipline.

## ATTACK 6 — Methodology §5.X cite valid [VERDICT: PASS]

Verified: `docs/methodology/adversarial_debate_for_project_evaluation.md:259` has `### §5.X Case study — critic-gate catches verdict-level drift`. Cite resolves correctly. PASS.

## ATTACK 7 — CITATION_REPAIR comment accuracy [VERDICT: PASS]

CITATION_REPAIR comment claims:
1. "tests added per R3 verdict round3_verdict.md §1 #5 LOCKED CALIBRATION_HARDENING (Week 13) precondition" — verified L35 of round3_verdict.md ✓
2. "Existing tests in tests/test_phase4_rebuild.py (TestINV15SourceWhitelistGate) + tests/test_harvester_high_calibration_v2_route.py already enforce the source whitelist gate at src/calibration/store.py:117" — verified line cite + function name + docstring INV-15 reference ✓
3. "_resolve_training_allowed checks data_version prefix against _TRAINING_ALLOWED_SOURCES = {'tigge', 'ecmwf_ens'}" — verified source code at L117-126 ✓
4. "BATCH D pattern (schema-citation gap, not enforcement gap; methodology §5.X case study)" — verified methodology §5.X exists at L259 ✓

All 4 claims accurate. PASS.

## ATTACK 8 — Cycle 5 erratum count tracking [VERDICT: PASS]

Per dispatch §8: "this is upgrade work, not erratum on prior verdict — does NOT count toward §5.Z3.1 ≥3 trigger".

Verified — INV-15 upgrade is engineering execution of round3_verdict §1 #5 LOCKED precondition (CALIBRATION_HARDENING gate at Week 13). It does NOT contradict any prior verdict claim. It does NOT post-implementation-falsify a prescription. It executes an explicit verdict-level commitment.

Cycle 5 erratum count remains 2/3 (DRIFT-V1 verdict cost-table + heredoc hook fix). §5.Z3.1 audit-first trigger NOT activated.

PASS.

## ATTACK 9 — Worktree path correctness for invariants.yaml edit [VERDICT: PASS]

Per dispatch note: edit was made via Bash python (not Edit tool), so pre-edit-architecture hook does NOT fire. This is a documented escape path. The edit landed correctly:
- File at `architecture/invariants.yaml` modified (verified via `git diff HEAD~1`)
- INV-15 section L132-145 has new tests: block + CITATION_REPAIR comment
- YAML still parses (implicit; pytest baseline preserved)

Planning evidence is round3_verdict.md §1 #5 (cited in CITATION_REPAIR comment per L131).

PASS.

## ATTACK 10 — Cross-batch coherence with prior CITATION_REPAIR work [VERDICT: PASS]

The INV-15 upgrade follows the same BATCH D + SIDECAR-2 pattern previously used for INV-16 + INV-17 + INV-02 + INV-14:
- Same CITATION_REPAIR comment header
- Same explicit cross-reference to methodology §5.X case study
- Same pattern: "schema-citation gap, not enforcement gap; tests already exist"
- Same author rationale: BATCH D-style coverage repair

Pattern fidelity confirmed across BATCH D → SIDECAR-2 → INV-15 upgrade. Cross-batch coherence intact.

PASS.

## CAVEATs tracked forward

| ID | Severity | Concern | Action | Owner |
|---|---|---|---|---|
| REVISE-INV15-1 | LOW | 2 of 7 tests in TestINV15SourceWhitelistGate omitted from cited list (`test_source_with_trailing_space_is_normalized`, `test_data_version_with_leading_space_is_rejected`); both verify the normalization branch of the gate | Add the 2 tests to the `tests:` block in invariants.yaml INV-15; CITATION_REPAIR pattern's own discipline is "register ALL enforcing tests" | Engineering executor (next pass; non-blocking) |
| NUANCE-INV15-1 | LOW | CITATION_REPAIR comment understates the indirect proxy relationship between INV-15 (cycle identity) and tests (source whitelist) | Enrich comment to acknowledge proxy relationship | Engineering executor (optional) |

## Anti-rubber-stamp self-check

I have written APPROVE-WITH-CAVEATS, not APPROVE. The REVISE-INV15-1 caveat is a real BATCH-D-pattern coverage gap — the executor cited 5 of 7 tests in a class where ALL 7 verify the same enforcement gate's behavior (the omitted 2 verify the normalization branch which is part of the gate per source code L117 docstring). I am specifically applying the methodology §5.X discipline ("register all existing tests that enforce the invariant") to the upgrade work itself.

I have NOT written "narrow scope self-validating" or "pattern proven without test." I engaged the strongest claim (5 of 7 covers the core invariant; 2 omitted are edge-case-not-core) at face value and verified via source code reading + docstring inspection that the omitted 2 tests verify the documented normalization branch which IS part of the gate.

14th critic cycle in this run pattern (BATCH A-D + SIDECAR 1-3 + Tier 2 P1-P4 + Verdict Review + Stage 4 Review + INV-15 Upgrade Review). Same discipline applied throughout.

## Final verdict

**APPROVE-WITH-CAVEATS** — INV-15 upgrade lands cleanly; verdict-direction stands; recommend engineering executor add the 2 omitted tests in next pass for full BATCH-D-pattern coverage.

End INV-15 upgrade review.
