---
adr_id: ADR-4
title: Anti-Drift Binding (M1-M5 + PLAN Self-Sunset)
status: accepted
date: 2026-05-06
author: architect (drafted by p0-docs teammate)
operator_signature: "Fitz 2026-05-06 retroactive — OD-1 resolution per orchestrator-delivery skill flow correction"
sunset_date: 2027-05-06
authority_basis: ULTIMATE_DESIGN §0 rows 5, 7; IMPLEMENTATION_PLAN Phase 0.C
---

# ADR-4: Anti-Drift Binding (M1-M5 + PLAN Self-Sunset)

## Decision (first paragraph)

**Recommend: Accept** — All five anti-drift mechanisms (M1-M5) defined in ANTI_DRIFT_CHARTER.md §1-§7 shall bind every artifact produced by the topology redesign, together or not at all. Simultaneously, every PLAN-class document carries a machine-readable `sunset_date` per the schedule in CHARTER §10, with `IMPLEMENTATION_PLAN.md` expiring 2026-08-06, `ULTIMATE_DESIGN.md` expiring 2027-05-06, `RISK_REGISTER.md` subject to quarterly review, `CUTOVER_RUNBOOK.md` flagged `revisit_on_cutover`, and `ANTI_DRIFT_CHARTER.md` carrying no sunset (meta-rule).

## Context

Two Zeus systems — `topology_doctor` and `zeus-ai-handoff` — drifted into 禁书 status despite inline anti-ritual warnings. The topology system accumulated 159 `[skip-invariant]` commits in 60 days (~2.6/day); zeus-ai-handoff drifted despite an explicit warning at SKILL.md line 71. Literary warnings fail. The five mechanisms are structural: M1 (telemetry-as-output), M2 (opt-in by default), M3 (sunset clock per artifact), M4 (original-intent contract), M5 (INV-HELP-NOT-GATE invariant). CHARTER §1 states partial adoption recreates the ratchet; partial binding is not an option.

## Options considered

- A. **Bind M1+M5 only (telemetry + invariant test)** — Pros: lighter initial surface. Cons: M2 opt-in default and M4 original-intent are the structural mechanisms that prevent stage-4 mandatory drift; omitting either reopens the ratchet. CHARTER §1 explicitly rejects partial sets.
- B. **Bind all five (chosen)** — Pros: closes every stage of the Help-Inflation Ratchet (CHARTER §2, 6 stages); self-enforcing through `test_help_not_gate.py` and schema validation on `sunset_date`. Cons: Phase 2 decorator rollout is only ~80% on day 1; the remaining 20% is caught on first touch.
- C. **Defer binding until Phase 3** — Pros: no up-front commitment. Cons: Phase 2 capability decorators would ship without M2/M4 frontmatter — every decorated helper risks immediate stage-3 hardening before the mechanism can demote it.

## Consequences

- Positive: Every new helper ships `mandatory: false` by default (M2); `original_intent` block required (M4); `sunset_date` required by schema (M3, enforced by `test_charter_sunset_required.py`).
- Negative: ~20% of source decorators ship without M4 original-intent blocks until first-touch; manual backfill required during Phase 2-3.
- Reversibility: M1-M5 are YAML frontmatter + test assertions; rolling back means deleting those fields and the three test files — a single commit, but it reopens drift exposure immediately.

## Acceptance criteria

- `tests/test_help_not_gate.py` passes green at Phase 5 cutover (three assertions: no helper blocks unrelated capability, every invocation emits ritual_signal, does_not_fit returns zero).
- `tests/test_charter_sunset_required.py` passes green: every YAML entry in capabilities + invariants carries `sunset_date`.
- `tests/test_charter_mandatory_evidence.py` passes green: no helper carries `mandatory: true` without all three M2 evidence keys.
- PLAN document sunset dates match CHARTER §10 table exactly.

## Risks attached

- R6: New design drifts back toward 禁书 within 6 months — M1-M5 binding is the primary structural mitigation; R6 sunset is `none` (reviewed at every charter version bump).
