# Slice card aggregate

- total_cards: 23
- total_hours: 162.5

| Region | Cards | Hours |
|---|---|---|
| down | 7 | 16.5 |
| mid | 8 | 90 |
| up | 8 | 56 |

## Per-card risk + gate + dependencies

| ID | Risk | Hours | Gate | Depends on | Title |
|---|---|---|---|---|---|
| down-01 | ? | 4 | standard | down-02, down-03, up-04 | D1 unified-SDK drop-in swap (preserve signer.sign two-step seam) |
| down-02 | ? | 2 | standard | — | D0 operator-question pair (Q1-zeus-egress + Q-HB) + absorption-rule documentatio |
| down-03 | ? | 2 | standard | down-02 | V2 SDK unified-client antibody (skip-on-import-error tripwire) |
| down-04 | low | 0.5 | standard | — | Q1 acceptance amendment — "from Zeus daemon machine, with funder_address present |
| down-05 | low | 2 | critic-opus | down-02 | Packet status amendment — flip to dormant-tracker; impact_report rewrite gate at |
| down-06 | medium | ? | critic-opus | down-02 | D2.B apscheduler-job-4 V2-heartbeat — CONDITIONAL slot, gated on Q-HB cadence ci |
| down-07 | medium | 6 | critic-opus | down-01, down-02 | pUSD collateral branch — balanceOf() rewire + redemption path activation (ACTIVA |
| mid-01 | ? | 6 | standard | mid-02, mid-03 | A1 K4 RED → durable-cmd (new authority surface) |
| mid-02 | ? | 12 | standard | up-04 | A1.5 PAYLOAD_BIND — SIGNED_ORDER_PERSISTED event + signer interception (schema c |
| mid-03 | ? | 12 | standard | — | A4.5 STATE_GRAMMAR_AMEND — CANCEL_FAILED + PARTIAL_FILL payload + error-typing e |
| mid-04 | ? | 6 | standard | mid-03 | A4.5b PARTIAL_FILL_OBSERVED emission in recovery + RESTING via venue_status disc |
| mid-05 | ? | 14 | standard | mid-03 | EXCHANGE_RECONCILE_SWEEP — exchange-side journal-diff (F-006 closure) |
| mid-06 | ? | 8 | standard | mid-01, mid-02, mid-03, mid-04, mid-05 | §8.3 ↔ INV-29 compatibility map + C1.5 cross-module relationship tests (F-012) |
| mid-07 | medium | 14 | critic-opus | mid-03, mid-04, mid-05 | WS_OR_POLL_SUFFICIENCY — User WebSocket subscription OR documented polling with  |
| mid-08 | high | 18 | critic-opus | mid-03, mid-04, mid-06, mid-07 | FAILURE_INJECTION_SUITE — deterministic fake CLOB + failure-injection harness +  |
| up-01 | low | 4 | standard | — | NET_NEW polymarket_truth_contract.yaml (Layer 0 truth-contract manifest) |
| up-02 | low | 6 | standard | up-01 | NET_NEW src/contracts/order_semantics.py — OrderSemantics.for_market() dispatche |
| up-03 | medium | 8 | critic-opus | up-01 | NET_NEW ExecutableMarketSnapshot table + pre-trade-gate (Apr26 design language) |
| up-04 | medium | 6 | critic-opus | up-01, up-02, up-03, up-06 | EXTEND venue_commands schema — payload-residual columns post INV-28/INV-30 |
| up-05 | medium | 8 | critic-opus | up-04, up-07 | NET_NEW SignedExecutionEnvelope atom — chain-anchor lifecycle (post-D-phase-fina |
| up-06 | medium | 8 | critic-opus | — | NET_NEW UNVERIFIED rejection matrix — 7-consumer enforcement (lands BEFORE up-04 |
| up-07 | medium | 6 | critic-opus | up-03, up-04 | NET_NEW snapshot↔command freshness gate — Python single-insertion + semgrep |
| up-08 | medium | 10 | critic-opus | up-04, mid-02, mid-04, mid-01 | FROZEN_REPLAY_HARNESS — bit-identical replay of probability chain (P_raw → Size) |

## Critical path

- length: 62h
- path: up-01 → up-03 → up-04 → mid-02 → mid-01 → mid-06 → mid-08
