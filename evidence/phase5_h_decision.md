# Phase 5 Opus Critic — Full System Review

**Branch:** topology-redesign-2026-05-06 HEAD `6c267ae8`
**Reviewer:** code-reviewer (opus tier, Phase 5 K0 cutover gatekeeper)
**Date:** 2026-05-06
**Agent ID:** ae8409b090c90b1d8

## Final Verdict: **GO-WITH-CONDITIONS — cutover dispatch AUTHORIZED**

```
verdict: GO-WITH-CONDITIONS
critical: 0
high: 0  (1 accepted residual variant under K0-1b OD option C)
medium: 2  (P5-M1 settlement_commands gap; P5-M2 topology-ratio metric drift)
low: 2  (P5-L1 _HELPERS_WITHOUT_CAP_ID exemption; P5-L2 ≤1500 LOC budget scope)
cutover_sequence_safe: True
cutover_dispatch_authorized: True
operator_decisions_pending:
  - OD-R-3 settlement_write.blocked_when kill_switch_active inclusion (carry from Phase 4)
  - OD-LOC-BUDGET ≤1500 LOC scope definition (NEW LOW)
```

## Regression Baseline

- **Topology charter+gate sweep:** 142 passed / 5 skipped / 0 failed (combined: test_help_not_gate, test_capability_decorator_coverage, test_charter_sunset_required, test_charter_mandatory_evidence, test_ritual_signal_emission, test_ritual_signal_aggregate, test_route_card_token_budget, test_gate_edit_time, test_gate_commit_time, test_gate_runtime, test_gate2_live_auth_token, test_gate5_direct_caller_bypass, test_zeus_risk_halt_e2e, test_untyped_for_compat_expiry).
- **Full-suite pytest:** 5186 passed / 321 failed / 157 skipped / 2 errors (789s). 321 failures dominated by 114 pre-existing tests/test_topology_doctor.py stale tests (Phase 3 import-chain residue) + pre-existing failures elsewhere — none Phase 5-introduced. Failure rate ~5.8% vs Phase 4.A baseline 27% — improving.
- **Cap-id reference integrity:** 14/14 in-code @capability decorators match capabilities.yaml (2 docs-only skipped per spec).
- **Schema integrity:** capabilities.yaml schema_version=1, 16 entries; reversibility.yaml 4 classes; invariants.yaml 36 entries.
- **Ritual signal log:** 717 entries across 5 helpers; 0 schema-missing.
- **All 6 ADRs signed.** OD-2 charter override CLOSED 2026-05-06.

## 10 Adversarial Attack-Pattern Verdicts

1. **Forgery / privilege escalation** — PASS WITH CAVEAT. 7 attack vectors tested; K0-1, K0-1b, pickle, copy, __reduce__, __class__ swap, **NEW Attack 7 (subclass+setattr)** all blocked or accepted as residual under OD-K0-1b option C trust boundary.
2. **Bypass / Direct-import** — PASS. 4 executor.py entry points all gated. **NEW MEDIUM P5-M1**: settlement_commands.py::submit_redeem at line 308 calls adapter.redeem() without gate_runtime.check; settlement_commands.py not in any capability hard_kernel_paths. Effort: add to on_chain_mutation.hard_kernel_paths + gate at submit_redeem head. Post-cutover Step 5+.
3. **Integrity check** — PASS. All 4 stable-layer YAMLs verified post-5.A regression-fix.
4. **Test-hides-defect** — PASS WITH CAVEAT. _PHASE4D_DEFERRED_CAP_IDS purged. _HELPERS_WITHOUT_CAP_ID exemption (P5-L1) for 34 older-format entries. @untyped_for_compat 0 active call sites — prophylactic.
5. **Regression in deletion** — PASS. 114 stale-test failures NOT deletion-bug; clean delete.
6. **Charter compliance** — PASS. M1 (717 entries), M2 (forward guard), M3 (sunset_date), M4 (original_intent), M5 (3/3 PASS).
7. **§9 override expiries** — PASS. Only OD-2 override; CLOSED.
8. **Phase 5.C fixture validity** — PASS WITH MEDIUM CAVEAT. **P5-M2**: Phase 0.A (0.71%) vs Phase 5.C (21.42%) topology-ratio metric definitions diverge; phase5_replay_rerun.md transparently states Phase 0.A used wrong fixture. Cutover GO holds because absolute friction projection (0.40h scaled to 20h) is well within ≤2h target.
9. **Cutover sequence safety** — PASS. CUTOVER_RUNBOOK §2 walked; G5 live → G1 24h → G2 48h → G3 72h → G4 7d → delete legacy 14d. Monotonically increasing protection.
10. **R-3 settlement_write semantic gap** — MEDIUM (operator decision required). Spec ambiguity §2.2 vs §8 row 7. Replay rebuild covers any divergence; recommend defer to Step 5+.

## Per-Phase Delta Summary

| Phase | Net delta | Status |
|---|---|---|
| 0.A baseline | 3 evidence files; 90.74h fixture (later corrected codex/PR67) | CLOSED |
| 0.B catalog | capabilities.yaml 16 entries | CLOSED |
| 0.C ADRs | 6 signed retroactively (OD-1) | CLOSED |
| 0.D fossil retire | -12,237 LOC | CLOSED |
| 0.E+0.F decorator+shadow | shadow agreement smoke | CLOSED |
| 0.G replay scaffold | replay_correctness_gate.py + CI | CLOSED |
| 1 stable layer | reversibility.yaml; invariants extended | CLOSED |
| 2 decorator rollout | 12 writer fns @capability/@protects | CLOSED |
| 3 generative+delete | route_function.py 201 LOC; -16,479 LOC | CLOSED (R12 partial → 5.B) |
| 4 enforcement (5 gates) | gate_edit_time + gate_commit_time + gate_runtime + replay CI + Gate 2 phantom | CLOSED via OD-K0-1b option C |
| 5.A telemetry | aggregate.py; INV-HELP-NOT-GATE 3/3; capabilities 6→16 fix | CLOSED |
| 5.B carry-forwards | R12 closed (topology_schema.yaml DELETED); L-1; L-3 | CLOSED |
| 5.C 20h replay re-run | codex/PR67 fixture; -91% delta; cutover GO | CLOSED |

## Net-LOC Delivered vs Target

- Target (briefing): 29,290 → ≤1,500 stable layer (≥19× reduction)
- Delivered NEW: 1,898 LOC (capabilities/reversibility/decorators/route_function/5 gates/live_executor/venue_adapter/shadow_executor)
- Net delete: -29,000 LOC
- Net result: ~-27,100 LOC reduction; well above 19× ratio
- **P5-L2** caveat: ≤1,500 LOC scope ambiguous (NEW infra 1,898 over; YAML-only 538 under). Operator clarification needed pre-Step-5.

## M1-M5 Production Readiness: GO

All 5 mechanisms LIVE. 717 ritual_signal entries; 100% decorator coverage on .py paths; all 16 capabilities original_intent-complete; 3/3 INV-HELP-NOT-GATE assertions PASS.

## 12-Risk-Register Final Disposition

R1-R12 all closed/resolved/tracked. None NEW unresolved. R5 (lease service) deferred per operator carry-forward (cutover does not depend).

## 6 ADR Ratification Status

All 6 ADRs signed: `Fitz 2026-05-06 retroactive — OD-1 resolution`. **ACCEPTED.**

## Carry-Forward Debt (Step 5+ Stabilization Scope)

1. OD-R-3 settlement_write.blocked_when semantic — operator decision (genuine business tradeoff)
2. P5-M1 settlement_commands.py gating gap — code patch (~5 LOC + capabilities.yaml extension)
3. L-1 _assert_risk_level_allows() dead code removal (partial via test_zeus_risk_halt_e2e.py)
4. L-3 @untyped_for_compat 30d expiry deadline 2026-06-05; CI guard live
5. P5-L1 _HELPERS_WITHOUT_CAP_ID exemption cleanup
6. P5-L2 ≤1,500 LOC budget scope clarification
7. P5-M2 Phase 0.A vs 5.C metric definition footnote in cutover communications
8. R5 lease service (deferred)
9. tests/test_topology_doctor.py 114 stale failures — rewrite or deprecate

## Operator Decisions Surfaced

1. **OD-R-3** (deferred from Phase 4): Should `settlement_write.blocked_when` include `kill_switch_active`? Spec ambiguous; replay rebuild covers any divergence. Recommend defer to Step 5+.
2. **OD-LOC-BUDGET** (NEW LOW): ≤1,500 LOC scope — does it include enforcement-layer modules (live_executor/venue_adapter/shadow_executor)? Recommend operator clarify pre-Step-5.

## Cutover Authorization

**Cutover dispatch is AUTHORIZED.** The two operator decisions are post-cutover stabilization scope; neither blocks Phase 5.D dispatch.
