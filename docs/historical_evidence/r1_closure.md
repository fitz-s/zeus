# R1 Closure — Phase 3
# Created: 2026-05-06
# Authority basis: RISK_REGISTER R1; phase2_h_decision.md E-6; IMPLEMENTATION_PLAN §5 D-6

## R1 Denominator Declaration

R1 ("guarded writer coverage") closes at Phase 3 with the following declared denominator:

**Applicable surface:** capabilities with `.py` hard_kernel_paths = **14 of 16 capabilities**.

Breakdown:
- 16 total capabilities in `architecture/capabilities.yaml`
- 14 have `.py` hard_kernel_paths → 14/14 PASS the AST decorator lint (as of Phase 2 exit)
- 2 non-py-only (authority_doc_rewrite, archive_promotion) → SKIPPED with documented rationale (C-6 from phase1_h_decision.md; F-7 from phase2_h_decision.md)
- Phase 4 pre-registered paths (venue_adapter.py, live_executor.py) → pytest.skip with explicit reason, not vacuous pass — these files do not exist until Phase 4 creates them

**Coverage ratio: 14/14 = 100% on the applicable surface.**

## Non-py capabilities exemption

`authority_doc_rewrite` and `archive_promotion` have hard_kernel_paths that are all non-.py files (docs/, archive/). AST decorator lint is not applicable. These require Phase 4 Gate 3 diff-verifier extension (`git diff --name-only` path check) for enforcement. This is documented as a Phase 4 carry-forward in phase2_h_decision.md F-7.

## Phase 4 re-open condition

R1 re-opens at Phase 4 when `venue_adapter.py` and `live_executor.py` are created as Phase 4 deliverables (LiveAuthToken phantom + ABC split per ULTIMATE_DESIGN §5 Gate 2). Both files must receive `@capability("live_venue_submit")` and/or `@capability("on_chain_mutation")` decorators, and the pytest.skip markers in `test_capability_decorator_coverage.py` must be converted to active assertions.

## R1 Status

**CLOSED for Phase 3 scope.** Denominator = 14 capabilities with .py hard_kernel_paths; coverage = 14/14 = 100%. Re-opens at Phase 4 for the 2 pre-registered Phase 4 paths and for the non-py enforcement gap.
