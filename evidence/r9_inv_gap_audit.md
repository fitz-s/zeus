# R9 INV-11 / INV-12 Gap Audit
# Created: 2026-05-06
# Authority basis: RISK_REGISTER R9; ULTIMATE_DESIGN §2.1; IMPLEMENTATION_PLAN Phase 1

## Question
Are INV-11 and INV-12 truly unused (safe to compact) or actively referenced?

## Verdict: ACTIVELY USED — DO NOT COMPACT

Both IDs are referenced across src/, tests/, and architecture/. They are not gaps in the
"reserved but unused" sense — they are de-facto invariants enforced by live code and tests
but never formally defined as entries in architecture/invariants.yaml.

This is the OPPOSITE of what R9 initially hypothesized. The gap is a DEFINITION gap,
not a reference gap.

## INV-11 Reference List (non-archive)

| File | Line(s) | Role |
|------|---------|------|
| architecture/test_topology.yaml | 877 | `reason: p10-infra verifier/loader skips cover INV-11 external assumption law` |
| src/contracts/AGENTS.md | 27 | `reality_contract.py: External assumption contracts (INV-11)` |
| tests/test_reality_contracts.py | 4, 33, 236 | 3 tests tagged INV-11: verify_all_blocking, tick_size, drift_detection |

**Semantic: INV-11 = External assumption contracts (RealityContractVerifier). All blocking
contracts must be verified before trade evaluation. p10-infra was never delivered; 10 tests
in test_reality_contracts.py runtime-skip.**

## INV-12 Reference List (non-archive)

| File | Line(s) | Role |
|------|---------|------|
| architecture/test_topology.yaml | 802 | `reason: Structural seam tests skip when target symbols/files are absent; these protect INV-12` |
| src/contracts/AGENTS.md | 19, 64 | `edge_context.py: INV-12 enforcement`; `bare floats → INV-12 violation` |
| src/contracts/execution_price.py | 11, 91, 126 | Module docstring + assert_kelly_safe + runtime violation label |
| src/strategy/AGENTS.md | 34 | `All probabilities at cross-layer seams must carry provenance (INV-12)` |
| tests/test_no_bare_float_seams.py | 7, 200, 204, 281 | `§P9.7, INV-12, D3`; assert_kelly_safe enforces D3/INV-12 |
| tests/test_reality_contracts.py | 5, 139 | INV-12/D3: fee_included_in_edge_calculation |

**Semantic: INV-12 = Bare-float seam elimination at Kelly/exit boundaries. ExecutionPrice
must carry provenance; passing raw floats across signal→strategy boundary is a violation.**

## Decision: Leave Gaps (per ULTIMATE_DESIGN §2.1 + RISK_REGISTER R9)

Per RISK_REGISTER R9 and ULTIMATE_DESIGN §2.1, the correct action is to leave the ID
gaps intact (do not compact INV-13..INV-36 down to fill 11/12). Reasoning:

1. Compacting would require renumbering 22+ entries and updating all references in
   src/, tests/, architecture/, docs/ — massive churn with no semantic gain.
2. INV-11 and INV-12 carry real enforcement intent; they should be DEFINED (added to
   invariants.yaml as formal entries) in a future Phase, not erased by renumbering.
3. ULTIMATE_DESIGN §2.1 states INV-XX entries are stable primitives — gap stability
   is a feature, not a flaw.

## Recommended Future Action (not Phase 1 scope)

Define INV-11 and INV-12 as formal invariants.yaml entries:
- INV-11: External assumption contracts (RealityContractVerifier) must be verified before
  trade evaluation. Delivery gate: p10-infra RealityContractVerifier.
- INV-12: Bare floats are forbidden at Kelly/exit seam boundaries; every probability
  crossing a module boundary must carry typed provenance (ExecutionPrice / EdgeContext).

This is tracked in docs/archives/packets/task_2026-05-02_review_crash_remediation/PLAN.md §F.
