# critic-dave Phase 9C Learnings — Cycle 1

**Duration:** ~12 min, ~12 tool uses (well under 20 min / 40 tool-use budget).
**Session:** lean re-spawn after predecessor stream-idle timeout.

---

## 1. Surgical revert beats deep reading for relationship-antibody audits

Priority B cost me 2 edits + 2 pytest invocations and conclusively separated "genuine antibody" (R-BZ.1) from "checkbox antibody" (R-CC.2). Reading both test files line-by-line would have taken 4x the tool budget and produced weaker evidence. **Future cycles: reach for surgical revert early whenever a contract claims "relationship tested".** The test that keeps passing after the relationship is severed is the test that was never testing the relationship.

## 2. READ-side fixes often have WRITE-side twins that get missed

The L3 CRITICAL fix is correctly described as a read-side repair: `get_calibrator` now reads v2 with metric filter. But the code path it feeds into — `_fit_from_pairs` → `save_platt_model` (legacy) — was not updated. An immune-system fix must close the matching write path, or the fix is asymmetric. **Heuristic: whenever you see a read-side fix, grep for the paired write call in the same module and verify it got the same treatment.** If pairs are shared (reads + writes on same table family), both surfaces must move together.

## 3. "Component correctness" and "relationship correctness" produce very different tests

R-CC.2 is structurally well-written AS a component test of `_read_v2_snapshot_metadata`. But it was labeled as the DT#7 gate antibody in the contract. Naming a test after the relationship it supposedly guards, while actually testing only the component, is the most dangerous flavor of coverage illusion because it encourages future readers to trust the gate. Fitz Constraint #1 in practice: **the antibody must invoke the exact code path that performs the relationship**, not the helpers it uses. End-to-end over unit.

## 4. Antibody-count drift is a cheap sanity check

Contract and commit message both claimed "13 antibodies"; the file has 12. This took one grep and caught a real discrepancy. Always validate numeric claims in commit messages when they're cheap to verify — they are the documentation layer most prone to copy-paste decay across session handoffs and the easiest to nail down.

## 5. Lean spawns work when the predecessor leaves a map

The onboarding brief (`critic_dave_onboarding_brief.md`) was essential — without it I would have spent 15+ tool calls re-exploring what critic-carol had already mapped. **Lean re-spawn protocol is viable when prior handoff artifacts are solid.** Reciprocal obligation: my cycle should leave an equally skimmable map for whichever critic runs P9C.1 review.

---

**Bottom line:** P9C is real structural progress but the main-line closure is premature by one iteration. Verdict ITERATE, not FAIL — the L3 fix itself is sound; the problems are at its boundaries (write-side twin, relationship-vs-component antibody for DT#7).
