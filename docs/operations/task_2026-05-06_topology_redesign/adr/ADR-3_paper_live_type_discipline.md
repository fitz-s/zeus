---
adr_id: ADR-3
title: Paper / Live Type Discipline — LiveAuthToken Phantom + Separate ABCs
status: accepted
date: 2026-05-06
author: architect (drafted by executor agent)
operator_signature: "Fitz 2026-05-06 retroactive — OD-1 resolution per orchestrator-delivery skill flow correction"
sunset_date: 2027-05-06
authority_basis: ULTIMATE_DESIGN §0 row 3; IMPLEMENTATION_PLAN Phase 0.C
---

# ADR-3: Paper / Live Type Discipline — LiveAuthToken Phantom + Separate ABCs

## Decision (first paragraph)

**Recommend: Accept** — `LiveExecutor` and `ShadowExecutor` shall be separate abstract base classes; `submit()` on `LiveExecutor` shall require a `LiveAuthToken` phantom-typed parameter that `ShadowExecutor` cannot construct. mypy/pyright in CI shall fail on any `submit()` call missing the token. A 30-day `@untyped_for_compat` escape hatch is available during Phase 4 rollout for legitimate in-progress refactors.

## Context

Operator decision #3 addresses the paper/live confusion failure category (briefing §8 #1). The current system uses a shared executor class with a runtime `is_live` flag — bypassable and invisible to the type checker. The researcher (§3.2 #1) identified Jane Street's phantom-type pattern as the highest-prevention structural control for this failure class. The researcher (§3.2 #4) identified QuantConnect/Lean's separate ABC hierarchy as the operational precedent showing the pattern scales. ULTIMATE_DESIGN §5 gate 2 encodes this as a blocking CI enforcement.

## Options considered

- A. **Runtime mode flag** — single executor class; `is_live: bool` at construction. Pros: minimal refactor. Cons: bypassable; paper tests can instantiate live mode accidentally; type system gives no signal.
- B. **Phantom type + separate ABCs (chosen)** — `LiveAuthToken` is a zero-value phantom; `ShadowExecutor` lacks `submit()`; `LiveExecutor.submit()` requires the token. Pros: wrong-executor bugs are unconstructable at type-check time. Cons: legitimate executor refactors must touch token + ABC together — deliberate friction (ULTIMATE_DESIGN §11 #3).
- C. **Separate modules, no phantom** — no shared interface. Pros: full separation. Cons: no type enforcement at submit boundary; live module still importable in paper context.

## Consequences

- Positive: CI red on any `submit()` call missing `LiveAuthToken`; `ShadowExecutor` cannot construct a token; briefing §8 failure category #1 becomes a type error, not a runtime bug.
- Negative: renaming `LiveExecutor` requires updating `LiveAuthToken` and the submit boundary together — by design, but felt as friction (ULTIMATE_DESIGN §11 #3).
- Reversibility: ABCs collapse to shared class via single-commit revert before Phase 4 ships; `@untyped_for_compat` provides 30-day per-site bypass during rollout.

## Acceptance criteria

- `mypy` / `pyright` fails on any `submit()` call without a `LiveAuthToken` argument.
- `ShadowExecutor` has no code path constructing `LiveAuthToken`.
- `live_venue_submit` capability decorated on `LiveExecutor.submit()` in `tests/test_capability_decorator_coverage.py`.
- Phase 4 rollout log shows zero breakage on `ShadowExecutor` call sites.

## Risks attached

- R3: Phantom type breaks existing imports — mitigated by separate ABCs (Shadow callers unaffected) and `@untyped_for_compat` escape hatch.
- R1: Decorator coverage must reach 100% on guarded writers before phantom rollout to the submit boundary (cross-risk R1 → R3, RISK_REGISTER §4).
