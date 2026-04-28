# Trading-Correctness Review — Zeus Ultimate Plan 2026-04-26

Reviewer: scientist agent. Anchor: ULTIMATE_PLAN.md HEAD `874e00c`; Apr26 forensic tribunal (1544 lines). Frame: does the 20-card / 120.5h plan let Zeus "dominate live market"?

## EDGE_PRESERVATION

The plan is overwhelmingly **leak-closing**, not edge-improving:

- Edge-improving: **0 cards**. Nothing touches Extended Platt (A/B/C), Monte Carlo noise, ENS member ingestion, market-fusion α, double-bootstrap CI, or fractional-Kelly fraction.
- Leak-closing: **17 cards** (up-01..07 source/identity/raw payloads; mid-01..06 command/state/reconciliation; down-01/03/06/07 transport/pUSD/heartbeat).
- Pure-transport / docs: 3 cards (down-02/04/05).

Ratio is defensible — Apr26 verdict was S0-execution-blocker, not edge-decay; closing leaks first is correct. **But** the plan's claim "Zeus is unblocked to dominate live market after 4-6 weeks" overclaims: 17 leak-closes give Zeus the **right to trade**, not measurable edge. Zero new alpha, zero alpha-monitoring. Re-label as "live-readiness gate," not "dominance."

## MONEY_PATH_GAPS

| Step | Plan coverage |
|------|---------------|
| contract semantics | up-01/02 — STRONG |
| source truth | up-03/04/06/07 — STRONG |
| forecast signal | NOT TOUCHED |
| calibration | NOT TOUCHED |
| edge | NOT TOUCHED (no DBS-CI hardening, no edge-decay tracker) |
| execution | mid-01..05 + down-01 — STRONG |
| monitoring | mid-06 — PARTIAL (no live-edge KPI) |
| settlement | down-07 only — PARTIAL (Apr26 §11 corpus deferred) |
| learning | NOT TOUCHED (no alpha-decay loop, no attribution-drift detector) |

Forecast/calibration/edge/learning are entirely absent. Apr26 §16 defers them; plan inherits that gap without flagging it as residual. Apr26 Phase 4 (settlement corpus, high/low split, DST resolved fixtures) silently dropped. §1 dedupe collapses Apr26 §11.1-11.4 into "data-readiness reroute" without confirming that packet exists or has owners.

## PROBABILITY_CHAIN_RISKS

Chain `51 ENS → daily max → MC → P_raw → Platt → P_cal → α-fusion → P_posterior → Edge & DBS-CI → Kelly → Size`. Plan touches **only the tail** (Size → Order). Chain is structurally intact (no module modified). Risk vectors:

1. **Schema risk.** up-04 15-col ALTER + INV-29 amendment touch `venue_commands`. If cascades into `position_events`/`execution_fact` semantics, replay-based calibration backtests become version-fragile. "Grammar-additive" claim has no antibody asserting calibration tooling reads only schema-versioned columns.
2. **Authority direction risk.** mid-01 reframes `cycle_runner` as RED→durable-cmd proxy. If RED propagation latency increases by one cycle, fractional-Kelly during DATA_DEGRADED becomes too aggressive. NC-NEW-D is shape-only, no latency assertion.
3. **Snapshot freshness.** up-03 max-age unspecified numerically. Mis-tuned gate silently locks out opening-inertia / shoulder-bin entry windows (decay-fast strategies).

Net: chain integrity preserved by omission, not by proof. No mid-06 test demonstrates `position_size(t)` is bit-identical pre- vs post-plan on a fixture portfolio.

## LIVE_MONEY_SAFETY_GAPS

X2 claims F-012 closed with "0 violators." Not true for the prompt's edge cases:

1. **Rapid sequential partial fills.** mid-04 adds `PARTIALLY_FILLED` as payload-discrim. No test for 3 fills <1s with overlapping `get_trades`; trade_id de-dup unspecified.
2. **Network jitter during heartbeat windows.** down-06 is Q-HB-gated. Independent of mandate, WS-reconnect jitter is the live failure mode — no WS-resubscribe correctness test, no missed-trade recovery via `get_trades` since-cursor.
3. **Oracle conflicts during settlement.** down-07 covers collateral-token PnL classification only. Apr26 §11 #3 (exchange resolution snapshot preservation, UMA dispute-window) rerouted to data-readiness — **no slice card owns it**.
4. **Cancel-failure during RED sweep.** Apr26 F-010 says runtime RED does not implement immediate cancel-sweep. mid-01 covers RED-as-authority but not RED-as-action; no behavioral test.
5. **Duplicate-submit under timeout-retry.** mid-02 adds signed-order persistence, but reconciler-first retry policy is unspecified at cycle-runner level. Apr26 P0 `test_duplicate_submit_idempotency.py` has no slice owner.

X2's "0 violators" holds only for cards in scope. Apr26 §13 lists 17 P0 tests — at least 5 are not cross-walked into the 20 cards.

## VERDICT

Plan is correct on what it covers; **under-claims its scope**. It is a live-readiness gate, not a live-market-dominance plan. Three additions needed to honestly hit the prompt:

1. **EDGE_OBSERVATION_SLICE** — alpha-decay tracker per `strategy_key` (settlement-capture / shoulder-bin / center-bin / opening-inertia) with weekly drift assertion. Apr26 §1.5 strategy-family table is the contract; no card enforces it.
2. **PROBABILITY_CHAIN_FROZEN_REPLAY** — bit-identical replay (P_raw → Size) before/after Wave A+B. Antibody against silent calibration-schema drift from up-04 ALTER.
3. **P0_TEST_OWNERSHIP_LEDGER** — cross-walk Apr26 §13 P0 list to slice cards; 5 unmapped tests (duplicate-submit, rapid partial-fill, RED cancel-all behavioral, market-close-while-resting, WS-resubscribe-recovery) need owners or explicit defer-with-risk.

Without these, post-plan Zeus passes the **safety bar** (no silent S0 losses) but not the **dominance bar** (no proof edge survives deploy, no alpha monitoring). Trading-correctness verdict: **CONDITIONAL — ship Wave A+B, add the three gaps above before declaring dominance.**
