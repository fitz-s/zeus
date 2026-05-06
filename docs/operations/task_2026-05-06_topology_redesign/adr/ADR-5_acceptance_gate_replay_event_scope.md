---
adr_id: ADR-5
title: Acceptance Gate + Replay Event Scope
status: accepted
date: 2026-05-06
author: architect (drafted by p0-docs teammate)
operator_signature: "Fitz 2026-05-06 retroactive — OD-1 resolution per orchestrator-delivery skill flow correction"
sunset_date: 2027-05-06
authority_basis: ULTIMATE_DESIGN §0 rows 6, 8; IMPLEMENTATION_PLAN Phase 0.C
---

# ADR-5: Acceptance Gate + Replay Event Scope

## Decision (first paragraph)

**Recommend: Accept** — The Phase 0.H GO gate is a 20-hour replay of the Phase 0.A autonomous session (bccc8776, confirmed recoverable); if the original fixture is unavailable, a synthetic 5-task panel matching ≥80% topology-operations task-class distribution substitutes. The `src/state/chronicler.py` event log is confirmed sufficient as the replay seed; Phase 0.G builds against a fixed 7-day seed window to avoid R2 non-determinism. Phase 5 acceptance target: replay friction ≤2h measured by **token-budget pressure** (the dominant friction path identified in Phase 0.A), not invocation count.

## Context

Phase 0.A baseline finding: token-budget exhaustion — not topology_doctor invocation count — was the dominant friction source in the autonomous session. Measuring invocations at Phase 5 would miss the actual bottleneck. The redesign's primary value claim (bootstrap token cost drops from ~220k to ≤30k) directly predicts relief on this dimension; ADR-5 locks the measurement methodology to that prediction. The `chronicler.py` event log (existing, consumed by `scripts/replay_parity.py`) covers the event types needed for deterministic projection replay; no additional event sources were identified.

## Options considered

- A. **Invocation count as friction proxy** — Pros: simple. Cons: Phase 0.A shows invocation count is a lagging indicator; token budget exhausts before count reaches actionable thresholds.
- B. **Token-budget pressure as friction proxy (chosen)** — Pros: directly measures the Phase 0.A failure mode; aligns with the redesign's stated 220k→≤30k reduction claim. Cons: requires token-usage logging in Phase 0.A baseline (retroactively if not present).
- C. **Wall-clock time only** — Pros: simplest. Cons: conflates model latency, rate limits, and token pressure; cannot isolate topology overhead.

## Consequences

- Positive: Phase 5 acceptance is falsifiable on the design's primary claim; replay-correctness CI lane runs <60s on the fixed 7d seed window.
- Negative: Phase 0.A baseline must record token-budget checkpoints; if not already captured, Phase 0.A deliverable requires amendment before Phase 0.H.
- Reversibility: substitution policy (synthetic panel) is documented here; switching back to full-session replay requires only updating Phase 5 fixture path — no code change.

## Acceptance criteria

- Phase 5 day 80-83: replay friction ≤2h, measured as time-to-first-token-budget-exhaustion on the full 20-hour session (or synthetic panel).
- Replay-correctness CI lane completes in <60s on the 7-day seed window.
- Phase 0.A baseline file includes token-budget checkpoint data (or is amended to add it before Phase 0.H evaluation).
- If synthetic substitution invoked: task-class distribution ≥80% topology operations, documented in Phase 5 decision file.

## Risks attached

- R2: Replay-correctness gate non-determinism — mitigated by fixed 7-day seed window; non-deterministic event types listed as explicit exclusions in Phase 0.G.
- R7: 20-hour replay fixture cannot be reconstructed — mitigated by Phase 0.A confirming session bccc8776 is recoverable; synthetic substitution policy documented above.
