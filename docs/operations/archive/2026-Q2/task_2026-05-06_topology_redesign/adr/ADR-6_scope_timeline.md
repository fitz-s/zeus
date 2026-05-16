---
adr_id: ADR-6
title: Scope and Timeline (zeus-ai-handoff Parallel Rescoping + 90-Day Total)
status: accepted
date: 2026-05-06
author: architect (drafted by p0-docs teammate)
operator_signature: "Fitz 2026-05-06 retroactive — OD-1 resolution per orchestrator-delivery skill flow correction"
sunset_date: 2027-05-06
authority_basis: ULTIMATE_DESIGN §0 rows 9, 10; IMPLEMENTATION_PLAN Phase 0.C
---

# ADR-6: Scope and Timeline (zeus-ai-handoff Parallel Rescoping + 90-Day Total)

## Decision (first paragraph)

**Recommend: Accept** — The total implementation timeline is 90 days (Phase 0.A start 2026-05-06 → Phase 5 cutover ~2026-08-04), aligning `IMPLEMENTATION_PLAN.md` self-sunset with 2026-08-06. A parallel `zeus-ai-handoff` rescoping work stream (Phase 5 days 76-80, 3 days) applies M2 (`mandatory: false` default) and M4 (complete `original_intent` block with `intent_test`, `does_not_fit: refuse_with_advice`, `scope_keywords`, `out_of_scope_keywords`) to the skill's frontmatter; SKILL.md line 71 anti-ritual prose is proven insufficient (CHARTER §2) and is superseded by structural mechanism.

## Context

The Help-Inflation Ratchet (CHARTER §2, 6-stage progression) consumed zeus-ai-handoff despite explicit anti-ritual prose at SKILL.md line 71. Literary warnings fail — this is the repo's empirical record, not a hypothesis. M2 + M4 applied to zeus-ai-handoff frontmatter make the wrong usage structurally observable: `mandatory: false` means no session can depend on invocation; `original_intent.does_not_fit: refuse_with_advice` means the skill self-reports misuse at invocation time rather than silently inflating scope. The 90-day timeline matches the IMPLEMENTATION_PLAN sunset (2026-08-06), ensuring the planning artifact expires exactly when Phase 5 is complete.

## Options considered

- A. **Address zeus-ai-handoff post-cutover** — Pros: no Phase 5 scope creep. Cons: zeus-ai-handoff and the redesigned topology share the same agent surface; leaving it unrescoped during cutover means the new gates observe a live drifted helper on day 1. The ratchet restarts immediately.
- B. **Parallel rescoping at Phase 5 (chosen)** — Pros: structural M2+M4 applied before cutover day; rescoping is 3 days of frontmatter edits, not code; does not block Phase 5 critical path. Cons: Phase 5 owner must hold a 3-day parallel lane.
- C. **Full skill rewrite** — Pros: clean slate. Cons: disproportionate scope; M2+M4 frontmatter achieves the structural binding without rewriting proven skill logic.

## Consequences

- Positive: zeus-ai-handoff frontmatter post-Phase 5 is machine-checkable via `test_charter_mandatory_evidence.py`; drift detection activates immediately at cutover.
- Negative: Phase 5 must allocate 3 parallel days; any slip in Phase 4 compresses this window.
- Reversibility: Frontmatter changes are a single commit; reverting restores previous behavior. IMPLEMENTATION_PLAN sunset 2026-08-06 is an auto-demote, not a deletion; operator can re-affirm if Phase 5 slips.

## Acceptance criteria

- zeus-ai-handoff SKILL.md frontmatter post-Phase 5: `mandatory: false` present; `original_intent` block contains all four required keys (`intent_test`, `does_not_fit`, `scope_keywords`, `out_of_scope_keywords`).
- Phase 5 cutover completes on or before 2026-08-04.
- `IMPLEMENTATION_PLAN.md` sunset field reads `2026-08-06`; no Phase 0 work claimed before this date.

## Risks attached

- CHARTER §2 (Help-Inflation Ratchet, 6-stage): structural mitigation is M2+M4 applied to zeus-ai-handoff frontmatter; literary warning at SKILL.md line 71 is acknowledged insufficient and is not a substitute.
