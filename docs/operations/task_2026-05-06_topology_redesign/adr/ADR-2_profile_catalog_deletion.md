---
adr_id: ADR-2
title: Profile-Catalog Deletion — 26× / 19× Reduction Target
status: accepted
date: 2026-05-06
author: architect (drafted by executor agent)
operator_signature: "Fitz 2026-05-06 retroactive — OD-1 resolution per orchestrator-delivery skill flow correction"
sunset_date: 2027-05-06
authority_basis: ULTIMATE_DESIGN §0 rows 2, 4; IMPLEMENTATION_PLAN Phase 0.C
---

# ADR-2: Profile-Catalog Deletion — 26× / 19× Reduction Target

## Decision (first paragraph)

**Recommend: Accept** — `architecture/digest_profiles.py` (6,001 LOC) and all 60 digest profiles in `topology.yaml :: digest_profiles:` shall be structurally deleted and replaced by the dual-primitive system (ADR-1). The gate metric is an absolute ceiling: topology infrastructure ≤1,500 LOC. Two LOC baselines are stated transparently: 29,290 verified-floor (named files only) → ≥19× reduction; 39,800 briefing-framed (including adjacent test/helper files) → ≥26×. Neither ratio is the success gate; the absolute ceiling is.

## Context

Operator decisions #2 and #4 collapse here. Decision #2 authorizes structural deletion of `digest_profiles.py` and the 60-profile catalog. Decision #4 sets the reduction target and requires both baselines to be stated. The briefing §2 figure (39,800) includes files not enumerated in the verified floor; ULTIMATE_DESIGN §9.4 documents the discrepancy. R11 flags the ratio gap as a communication risk.

## Options considered

- A. **Incremental profile reduction** — retire fossil profiles phase by phase; keep the file. Pros: lower per-change risk. Cons: file remains an authority; false-block problem persists.
- B. **Structural deletion (chosen)** — delete in Phase 3 after shadow router proves ≥90% agreement over ≥7 days. Pros: eliminates ambiguity surface; 1,440 LOC replacement is 19–26× smaller. Cons: Phase 0.D warm-up required; gated on Phase 0.H GO.
- C. **Freeze and shadow indefinitely** — keep profiles read-only; shadow runs in parallel forever. Pros: zero deletion risk. Cons: bootstrap token cost stays ~220k; anti-drift risk (R6) unchanged.

## Consequences

- Positive: bootstrap token cost drops ≥7× (220k → ≤30k); profile count drops 60 → 0; topology infrastructure ≤1,500 LOC.
- Negative: LOC ratio (26× vs 19×) must always be cited with both baselines (ULTIMATE_DESIGN §11 #6).
- Reversibility: git tag `pre-phase3` preserves full catalog; `topology.yaml :: digest_profiles` block restorable via single commit per CUTOVER_RUNBOOK §4.2.

## Acceptance criteria

- Post-Phase 3: `architecture/digest_profiles.py` absent; `topology.yaml :: digest_profiles:` block absent.
- Topology infrastructure LOC ≤1,500 by `wc -l src/architecture/*.py`.
- Bootstrap token cost ≤30,000 per Phase 0.A baseline re-run.
- Phase 0.F shadow agreement ≥90% before deletion.

## Risks attached

- R11: Ratio ambiguity (26× vs 19×) — cite both numbers in all communications.
- R4: Shadow agreement <90% defers deletion; Phase 0.F is the floor gate.
- R10: Bypass culture floor — <2 `[skip-invariant]` commits/week for 30 days pre-GO.
