---
adr_id: ADR-1
title: Invariants + Capabilities as Dual Primitive
status: accepted
date: 2026-05-06
author: architect (drafted by executor agent)
operator_signature: "Fitz 2026-05-06 retroactive — OD-1 resolution per orchestrator-delivery skill flow correction"
sunset_date: 2027-05-06
authority_basis: ULTIMATE_DESIGN §0 row 1; IMPLEMENTATION_PLAN Phase 0.C
---

# ADR-1: Invariants + Capabilities as Dual Primitive

## Decision (first paragraph)

**Recommend: Accept** — The topology system's routing primitive shall be the conjunction of two YAML catalogs: `invariants.yaml` (34 existing entries, extended) expressing *what must hold* and `capabilities.yaml` (16 entries) expressing *who may write what*. Each capability entry names the invariants it protects, the hard kernel paths it guards, and the reversibility class that governs gate severity. The route function reads both catalogs at query time and returns a typed `RouteCard`.

## Context

The current system encodes routing knowledge as 60 named digest profiles (6,001 LOC in `digest_profiles.py`) plus ~12,290 LOC of `topology_doctor` scripts. Profile-based routing requires authoring a new profile for every task class and gives agents no principled way to extend it. The architect (§3.1 #1) identified that every routing failure traces to a missing or stale profile, not to a gap in the underlying invariant or capability catalog. The researcher (§3.2 #1) identified that capability-tagged phantom types at the write boundary have the highest prevention rate for the failure categories in §8.

## Options considered

- A. **File-path routing only** — route on which files a diff touches. Pros: simple. Cons: paths are cosmetic; same path carries different semantic weight depending on whether it is the write boundary or a test fixture. False-positive rate stays high.
- B. **Invariants + capabilities (chosen)** — dual YAML catalog; route function joins on `hard_kernel_paths` and `protects_invariants`. Pros: schema is the single source of truth; extends by appending entries, not modifying code; enables phantom-type gate (ADR-3). Cons: Phase 2 decorator rollout is ~80% on day 1 (ULTIMATE_DESIGN §11 #1; R1).
- C. **Relationship-only** — encode only inter-module dependencies. Pros: automatically derived from imports. Cons: does not capture write vs read intent; cannot assign reversibility class; cannot power the LiveAuthToken gate.

## Consequences

- Positive: `route_function.py` (≤500 LOC) replaces `digest_profiles.py` (6,001 LOC) + topology_doctor entry points; bootstrap token cost drops from ~220k to ≤30k.
- Negative: Phase 2 ships ~80% decorator coverage; the remaining 20% caught only when first touched (ULTIMATE_DESIGN §11 #1).
- Reversibility: git tag `pre-phase3` preserves the full profile catalog; restoring `digest_profiles.py` and the `topology.yaml :: digest_profiles` block is a single commit.

## Acceptance criteria

- `architecture/capabilities.yaml` has exactly 16 entries with `sunset_date: 2027-05-06` on each.
- `architecture/invariants.yaml` entries carry `capability_tags`, `relationship_tests`, and `sunset_date` keys.
- `src/architecture/route_function.py` exists and references both YAML catalogs.
- `tests/test_route_card_token_budget.py::test_route_card_t0_under_500_tokens` passes.

## Risks attached

- R1: Source-decorator coverage incomplete at Phase 2 ship — CI lint detects via `test_capability_decorator_coverage.py`.
- R4: Shadow router agreement rate <90% during Phase 0.F would defer cutover.
