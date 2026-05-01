# AGENTS.md "Risk levels" clarification proposal — DATA_DEGRADED semantics

**Status**: AWAITING-OPERATOR-RULING
**Filed**: 2026-05-01 by team-lead during ultrareview25_remediation P0-4 depth audit
**Related**: `docs/operations/repo_review_2026-05-01/SYNTHESIS.md` P0-4 reclassification

## TL;DR

Two reviewers (architect + critic-opus) flagged a "P0 fail-closed silently fails open" finding around DATA_DEGRADED. The depth audit shows the **code is correct by design**; the **AGENTS.md doc** is what's over-stated. This file proposes a one-paragraph AGENTS.md amendment to bring law into line with code, plus a relationship test that's already landed (`tests/test_dual_track_law_stubs.py::test_data_degraded_does_not_trigger_force_exit_sweep`) to lock the semantic.

## What two reviewers said

> AGENTS.md:81-83 mandates "Computation error or broken truth input → RED. Fail-closed." But `src/riskguard/riskguard.py:269-277` returns `RiskLevel.DATA_DEGRADED` when the trailing-loss reference is unavailable, and `risk_level.py:28` ranks DATA_DEGRADED (1) below YELLOW (2). `cycle_runner.py:570 red_risk_sweep = risk_level == RiskLevel.RED` — DATA_DEGRADED does not trigger the sweep. **Net effect**: a missing trailing-loss baseline silently disables the RED active-position sweep on what AGENTS.md calls a fail-closed surface.
> — critic-opus 2026-05-01

## What the code actually does (depth audit)

| Trigger | Returns | Behavior |
|---|---|---|
| Genuine compute error / unhandled exception | `RiskLevel.RED` (`riskguard.py:1058,1067,1074`) | Cancel pending + sweep active positions |
| Trailing-loss reference UNAVAILABLE | `RiskLevel.DATA_DEGRADED` (`riskguard.py:269-277`) | Block new entries (`cycle_runner.py:732`); preserve held positions; alert |
| Trailing-loss reference STALE, no breach detected | `RiskLevel.DATA_DEGRADED` (`riskguard.py:286-292`) | Same as above |
| Trailing-loss reference STALE, breach detected | `RiskLevel.RED` (`riskguard.py:288`) | Same as RED above |
| Portfolio loader degraded | `RiskLevel.DATA_DEGRADED` (`riskguard.py:1030`) | Same — entries blocked, no sweep |
| Loss above threshold | `RiskLevel.RED` (`riskguard.py:281-283`) | Sweep |

Code distinguishes two failure modes:
- **GENUINE compute error** → RED (truly fail-closed; pending cancelled, active swept)
- **MISSING / STALE truth input** → DATA_DEGRADED (block new entries, hold; alert; do NOT force-sell)

## Why the code design is correct (and stronger than RED-everywhere)

`src/riskguard/risk_level.py:17` LEVEL_ACTIONS comment makes the design explicit:

> DATA_DEGRADED = "Data degraded, acting with **YELLOW-equivalent safety without declaring loss boundary breach**"

Three reasons this beats "missing data → RED":

1. **Sweeping costs money**. Force-selling active positions at unfavorable prices on every transient data glitch (a missed snapshot, a 30-second WU outage, a stale reference) amplifies risk rather than reducing it. The right default for a transient glitch is "block new entries until we re-establish truth."
2. **Truth-input absence ≠ breach**. RED claims "we have evidence of a breach." DATA_DEGRADED claims "we cannot prove a breach but cannot disprove one either, so we hold." Conflating them deletes the operator's ability to distinguish "real loss" from "lost the ability to measure loss."
3. **Sweeping requires the data we just said is missing**. If the portfolio loader is degraded (`riskguard.py:1030`), we don't know what active positions to sweep. RED with a sweep that operates on incomplete portfolio state is worse than holding.

`src/riskguard/riskguard.py:286` documents the asymmetric direction: "Staleness degrades GREEN to DATA_DEGRADED, but **preserves RED**." Missing data doesn't escalate; doesn't deflate.

## What AGENTS.md says vs. what code says

**Current AGENTS.md (root, lines 81-83)**:

> Overall level = max of all individual levels. Computation error or broken truth input → RED. Fail-closed.

**Proposed amendment** (one paragraph, replaces the third sentence):

> Overall level = max of all individual levels. **Genuine computation error → RED, fail-closed (cancel pending, sweep active). Missing or stale truth input → DATA_DEGRADED, YELLOW-equivalent (block new entries, preserve held positions, alert). The distinction**: RED attests to a known boundary breach; DATA_DEGRADED attests that we cannot prove a breach but cannot disprove one either, so we hold rather than force-sell at unfavorable prices on a transient glitch. Both modes block new entries; only RED sweeps active positions.

## What's already landed (P0-4 fix as code)

Relationship test added 2026-05-01:
- `tests/test_dual_track_law_stubs.py::test_data_degraded_does_not_trigger_force_exit_sweep`

This test pins three properties that together lock the YELLOW-equivalent design:

1. `overall_level(YELLOW, DATA_DEGRADED) == YELLOW` (rank order)
2. `overall_level(RED, DATA_DEGRADED) == RED` (rank order)
3. `cycle_runner.py:570` uses strict `==` not `>=` (predicate shape — pinned via source-text grep so anyone "tightening" the gate must also update the test)

## Operator action requested

Choose one:

- **(a) Approve clarification**: amend AGENTS.md "Risk levels" paragraph as proposed above. The relationship test stays as the antibody.
- **(b) Reverse the design**: route MISSING/STALE truth input to RED and accept the sweep cost. Delete the relationship test, update LEVEL_ACTIONS comment, add `level >= RiskLevel.RED` predicate, write a new antibody for the new design.
- **(c) Defer**: file at `architecture/governance_queue/` like F12 did, leave code + test in place, AGENTS.md stays slightly over-stated until ruling.

The relationship test landed regardless — whichever direction you choose, future agents won't drift it silently.

## References

- `src/riskguard/risk_level.py:17` — design comment for DATA_DEGRADED
- `src/riskguard/riskguard.py:269-277` — DATA_DEGRADED return on missing reference
- `src/riskguard/riskguard.py:286-292` — staleness preservation logic
- `src/riskguard/riskguard.py:1030` — portfolio_loader_degraded → DATA_DEGRADED
- `src/riskguard/riskguard.py:1058,1067,1074` — genuine error → RED
- `src/engine/cycle_runner.py:570` — strict-equality sweep gate
- `src/engine/cycle_runner.py:732` — DATA_DEGRADED in entry-block tuple
- `tests/test_phase8_shadow_code.py:425+` — Phase 9A entry-block antibody
- `tests/test_dual_track_law_stubs.py::test_data_degraded_does_not_trigger_force_exit_sweep` — INV-19a sibling antibody (new 2026-05-01)
- `tests/test_dual_track_law_stubs.py::test_red_triggers_active_position_sweep` — INV-19 sweep-positive antibody
