# Phase 6 Micro-Plan — Landing Sequence Recommendation

**Status**: consultant recommendation, NOT final. Team-lead rules before exec dispatch.
**Issued**: 2026-04-18 planner. **Basis**: `phase6_contract.md` + operating-contract P2.

## Ground truth (physical tree)

1. `day0_extrema.py` **already** defines `RemainingMemberExtrema` (frozen, both-None guard, `for_metric`). R-BF partially pre-landed.
2. `day0_window.py` exports `remaining_member_extrema_for_day0`; legacy `remaining_member_maxes_for_day0` is a **backward-compat shim**.
3. `evaluator.py:784` + `monitor_refresh.py:294` call the legacy name and double-pass one bare array into BOTH `member_maxes_remaining` AND `member_mins_remaining` (silent-corruption trap).
4. `day0_signal.py:85-91` LOW guard intact.

User's 3-step ordering is **physically feasible**. Step-1 = 2-callsite migration + shim removal, not greenfield.

## Recommendation: **Strategy A (single commit, internal R-progression)**

**Top reason**: P2 is binding post-P5. Strategy B collapses because contract §"Hard constraints" forbids decoupling guard-removal from callsite fixes — 6A would commit code the contract bans. B violates P2 as written.

**Top risk**: Step 3 (DT#6+B055) is most decoupled from math split, most likely to surface regression at critic time. Mitigation: land Step 3 as pure read-path branch on `PortfolioState.authority=UNVERIFIED` (5A seam exists); if it blows, exec cheap-reverts and re-issues as contract-amendment ruling (P7 deferral).

## Internal R-letter progression (Strategy A)

| M | GREEN | Guard | Meaning |
|---|-------|-------|---------|
| M1 Pipes | R-BF | IN | Extrema dataclass flows; HIGH byte-identical; LOW still raises |
| M2 Router | R-BA, R-BB, R-BD, R-BE | OUT | Router dispatches; HIGH≥obs_high; LOW≤obs_low; causal-reject; no cross-import |
| M3 Fail-closed | R-BC | — | LOW missing `low_so_far` raises clean; no silent high-fallback |
| M4 Resilience | R-BG | — | DT#6 authority-loss → monitor read-only; B055 absorbed |

Commit candidate = all four in tree + `pytest tests/test_phase6_day0_split.py` GREEN + regression ≤ 137 fail baseline.

## Three load-bearing verification gates

1. **M1→M2 HIGH equivalence**. Before removing LOW guard, run HIGH regressions on M1 tree. HIGH must be byte-identical (only change: `bare_array → extrema.maxes`). Drift ⇒ dataclass introduced silent normalization (dtype widening). Antibody: boundary dtype assert.
2. **M2 R-BE AST no-cross-import**. Silent-corruption category = LOW nowcast transitively importing HIGH helpers, re-entering HIGH math with LOW-labelled data. **Static AST, not runtime** — else mid-P7 refactor re-introduces import; tests pass; P&L silently wrong.
3. **M3→M4 DT#6 read-only**. Under `authority=UNVERIFIED`, monitor lane must not write. Gate: mocked authority-loss proves `.p_posterior` read-but-never-reassigned; zero `EdgeDecision(True)` reaches portfolio. Weak gate ⇒ "log then proceed" — worst of both.

## Per-step risk + mitigation

- **Step 1 Pipes** — shim deletion surfaces a 3rd caller. Mitigation: exec's first action is `grep -rn "remaining_member_maxes_for_day0" src/ tests/`; if > 2, escalate P2 scope ruling.
- **Step 2 Math split** — `Day0LowNowcastSignal` inherits observation-dominance threshold tuned for HIGH. Mitigation: R-BB asserts samples ≤ `obs_low_so_far` under adversarial all-warm ENS; no module-level knob sharing.
- **Step 3 Resilience** — B055 2h staleness × DT#6 authority timestamp produces non-monotone degradation. Mitigation: R-BG extended case — authority lost **AND** trailing-loss stale ⇒ single degraded-state transition, not two paths.

## P2 compliance

One contract, one commit, one critic review. M1–M4 are exec's local discipline, not scope adjustments — invisible to team-lead mid-phase.

## Planner does NOT decide

- Whether scout-gary re-scans for a 3rd shim caller before exec dispatch.
- Whether DT#6 read-only exemption extends to reconciliation-write paths (logged open question).
