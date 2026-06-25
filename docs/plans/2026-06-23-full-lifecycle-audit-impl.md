# Plan: Full-lifecycle audit implementation (grounded)
> Created: 2026-06-23 | Branch: claude/full-lifecycle-audit-impl (off 4e68ffbe) | Status: IN PROGRESS

## Goal
Close the verified live-money gaps from the external lifecycle audit, in mission-law order
(settlement-evidenced, forward real-chain, no shadow, no over-gate). The #1 verified leak —
live taker authorization on plain `q_lcb` instead of an execution-conditioned bound — is fixed
first; the rest is a sequenced, evidence-gated roadmap.

## Context
- The audit ran BLIND (GitHub raw reads on branch `claude/exit-q-cert`, never executed locally;
  operator flagged "会些许偏离"). All claims were ground-truthed against THIS HEAD by 4 agents.
- **Result: ~50% of the audit is already-fixed or wrong.** Build only the verified-real gaps.

## Grounded defect ledger (verdicts vs current code 4e68ffbe)

REAL — build / plan:
- **P0-1** taker authorized by `taker_all_in_cost <= q_lcb+1e-9` — no execution-conditioned bound exists.
  Seam: `src/strategy/live_inference/mode_consistent_ev.py:523-532` (FIX-B gate);
  submit/eval/quality-proof seams in `src/engine/event_reactor_adapter.py:4998 / 10453 / 5150,5193`.
  Zero `q_exec_lcb` in src/. **<- Phase 1, highest value.**
- **P0-2** `qlcb_reliability_guard.apply_guard` INERT branch (`qlcb_reliability_guard.py:356-370`) serves
  raw band q_lcb when artifact absent — but only modal direction-law trades pass; OOF NO-harvest needs
  `OOF_WILSON_95` (`family_decision_engine.py:1143-1150`). Mostly SUBSUMED by P0-1 for takers.
- **P0-3** Day0 not wired to spine: `_NoDay0Reader->None` (`qkernel_spine_bridge.py:760-769`), lead<24h
  hard-refused (`:956-962`). Machinery EXISTS in legacy/materializer path -> wiring job, not build. Phase 2.
- **P0-4** exit threshold/point-prob (`portfolio.py:1006 fresh_prob`); no unified q_exit utility.
  `HoldValue`+`_sell_value_exceeds_hold_value` exist (`portfolio.py:830,51`), used in SOME gates; top-level
  <1hr still blanket force-sell (`:1060`). Partial. Phase 3.
- **P0-6** attribution "never a calibration training input" (`settlement_attribution_schema.py:36`); no
  per-action online learner. `decision_certificates` (VERIFIED ActionableTradeCertificate) exists. Phase 4
  (Phase 1 substrate already starts this loop).
- **P1-2** market_coherence block-only at entry, never drives exit/profit-take. By design. Phase 3 (with P0-4).
- **P1-4** `CI_SEPARATED_EXIT_CONTEXT_INCOMPLETE_HOLD` silently holds when EV unprovable, resets neg_edge_count,
  no refresh/quarantine (`portfolio.py:1226-1233`). Small, real. Phase 1.5.

DROP — already-fixed or audit-wrong (do NOT build):
- **P0-5** FILL_UP/SHIFT_BIN — ALREADY first-class (`src/strategy/family_rebalance.py` + `*_wiring.py`,
  2026-06-22): residual `delta=target-live-pending`, close-before-open, family lease, pending-unknown
  fail-closed all present. Audit WRONG. -> Drop audit PR-6.
- **P1-1** NO-toxicity from uncapped q_ucb — direction INVERTED (2 agents independently): overcounted far-tail
  YES -> undercounted NO from complement -> MORE conservative, not toxic. Audit WRONG. -> Do not build.
- **P1-3** negrisk synthetic/arb routes disabled (`src/execution/negrisk_routes.py`; bridge
  `enable_negrisk_routes=False`) — intentional single-leg-submit safety. Low priority, needs multi-leg submit first.

## Approach (Phase 1 design — the load-bearing decision)
`q_exec_lcb = min(q_decision_lcb, cell_LCB)` where `cell_LCB` is the Wilson lower bound of the realized
settled pay-rate `P(token pays | fill class)`, used ONLY when the cell has `n >= N_MIN` settled fills;
otherwise climb the hierarchy; if even the coarsest level is thin, `cell_LCB = +inf` (no extra constraint ->
reduces to today's `q_decision_lcb`).
- Cell hierarchy (coarse, thin-data-realistic): `side x action_mode x lead_bucket -> side x action_mode -> side -> +inf`.
- **Forward-only accrual** (operator NO-backtest law): artifact starts EMPTY, the Phase-1 receipt substrate tags
  every forward fill with its cell key + settlement outcome, nightly build grows the cohort. q_exec_lcb is a
  no-op today, tightens forward as cells fill. buy_no taker @~0.70 winning ~67% will bind once n>=N_MIN -> the
  documented leak self-closes on settlement evidence.
- Why this over the audit's "block on no data": no-shadow (it's live-direct, wired, governing), no over-gate
  (zero behavior swing today), settlement-evidenced (mission). Reversible: re-checkout the live files.

## Tasks

### Phase 1 — q_exec_lcb substrate + taker gate (P0-1)  [THIS SESSION]
- [ ] 1. Contract: add `q_exec_lcb`/cell-key fields (nullable) to candidate proof + basis enum
  - Files: `src/contracts/native_side_candidate.py`, new `src/contracts/probability_semantics.py`
- [ ] 2. Receipt substrate: record exec cell key (+ settlement outcome) per fill — forward cohort feed
  - Files: `src/events/live_profit_audit.py`, `src/state/schema/edli_live_profit_audit_schema.py` (additive cols)
- [ ] 3. Estimator + build script (Wilson LCB per cell, hierarchical shrink, N_MIN gate, +inf thin fallback)
  - Files: new `src/calibration/execution_conditioned_lcb.py`, new `scripts/build_q_exec_lcb_artifact.py`
- [ ] 4. Wire LIVE taker gate to q_exec_lcb (replace q_lcb comparison; q_lcb stays as q_decision_lcb input)
  - Files: `src/strategy/live_inference/mode_consistent_ev.py`, `src/engine/event_reactor_adapter.py` (seams 4998/10453/5150-5193)
- [ ] 5. Tests (TDD — write first): raw-q_lcb-positive-but-cell-LCB-below-cost -> no-trade; thin cell -> identity no-op;
  NO uses native NO cell not YES complement; artifact-absent -> no-op not crash
- [ ] 6. Verify + deploy live-direct (revertible: re-checkout live files + kickstart daemons)

### Phase 1.5 — P1-4 silent-hold fix  [THIS SESSION if Phase 1 clean]
- [ ] Replace `CI_SEPARATED_EXIT_CONTEXT_INCOMPLETE_HOLD` silent hold with REFRESH_EXIT_AUTHORITY /
  EXIT_QUARANTINE_PENDING_BOOK (refresh book/chain, don't reset neg_edge_count). File: `src/state/portfolio.py:1226-1233`.

### Phase 2 — Day0 spine wiring (P0-3)  [next, evidence-gated]
- `ReactorDay0Reader` feeding observed extreme + remaining-window into spine predictive builder; lift day0 hard-refuse
  only when receipt carries source/local-day/freshness/remaining-window lineage. Files: `qkernel_spine_bridge.py`
  (reuse `src/forecast/day0_conditioner.py`, `predictive_distribution_builder.py`).

### Phase 3 — exit q_exit utility + convergence (P0-4, P1-2)  [larger, evidence-gated]
- Unify exit into sell-now/hold/wait/shift utility scorer (q_hold/q_exit LCB + exit-bid depth) + market-convergence
  profit-take. Files: `src/state/portfolio.py`, `src/execution/exit_lifecycle.py`, new `src/decision/exit_utility.py`,
  `src/decision/market_convergence.py`.

### Phase 4 — decision-outcome learning loop (P0-6)  [largest, evidence-gated]
- Per-action/candidate outcome attribution feeding q_exec_lcb / fill artifacts via governed nightly builds (never
  live-DB training writes). Files: new `src/analysis/decision_outcome_attribution.py`, build script.

## Risks / Open Questions
- **Scope fork (operator):** audit ~50% stale -> literal "完整实现" of P0-5 (exists) / P1-1 (wrong) is impossible/wrong.
  Recommend: Phase 1 (+1.5) now, Phases 2-4 sequenced + forward-evidence-gated.
- q_exec_lcb terminal: chose min-with-evidence (no-op->tightens) over audit's block-on-no-data. Reversible.
- `action_mode` (taker/maker) + `lead_bucket` must be on the fill record for cell-tagging — verify
  `realized_fill`/`decision_certificates` carry them in task 2; if not, substrate adds them forward-only.
- N_MIN (start 30) and Wilson z set during TDD against accruing cohort, not historical backfill.
