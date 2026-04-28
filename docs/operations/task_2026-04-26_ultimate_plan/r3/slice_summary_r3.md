# R3 Slice card aggregate

- total_cards: 20
- total_hours: 312

| Phase | Cards | Hours |
|---|---|---|
| A | 2 | 52 |
| F | 3 | 32 |
| G | 1 | 14 |
| M | 5 | 70 |
| R | 1 | 12 |
| T | 1 | 22 |
| U | 2 | 42 |
| Z | 5 | 68 |

## Per-card risk + gate + dependencies

| ID | Phase | Risk | Hours | Gate | Depends on | Title |
|---|---|---|---|---|---|---|
| A1 | A | high | 30 | critic-opus | U1, U2, M1, M2, M3, M4, M5, R1, T1, F1 | StrategyBenchmarkSuite — alpha + execution metrics + replay/paper/live promotion gate |
| A2 | A | high | 22 | critic-opus | U1, U2, M1, M2, M3, M4, M5, R1, T1, F1, A1 | RiskAllocator + PortfolioGovernor — caps, drawdown governor, kill switch |
| F1 | F | medium | 12 | critic-opus | U1, U2 | Forecast pipeline plumbing — wired but operator-gated source switch |
| F2 | F | high | 14 | critic-opus | U1, U2, F1 | Calibration retrain loop wiring — operator-gated trigger + frozen-replay antibody |
| F3 | F | medium | 6 | critic-opus | F1 | TIGGE ingest stub — registered, gated, dormant by default |
| G1 | G | medium | 14 | critic-opus | U1, U2, M1, M2, M3, M4, M5, R1, T1, F1, F2, F3, A1, A2 | Live readiness gates — 17 CI gates + staged live-smoke verification |
| M1 | M | high | 14 | critic-opus | Z0, Z1, Z2, Z3, Z4, U1, U2 | Lifecycle grammar — venue_commands states + transitions + event types (cycle_runner-as-pro |
| M2 | M | high | 10 | critic-opus | Z0, Z1, Z2, Z3, Z4, U1, U2, M1 | SUBMIT_UNKNOWN_SIDE_EFFECT semantics — never treat unknown as rejected |
| M3 | M | high | 16 | critic-opus | Z0, Z1, Z2, Z3, Z4, U1, U2, M1, M2 | User WebSocket ingest + REST fallback (PolymarketUserChannelIngestor) |
| M4 | M | high | 12 | critic-opus | U1, U2, M1, M2, M3 | Cancel/replace + exit safety — mutex per (position, token) + typed cancel parser |
| M5 | M | high | 18 | critic-opus | U1, U2, M1, M2, M3, M4 | Exchange reconciliation sweep — bulk diff exchange truth vs journal |
| R1 | R | medium | 12 | critic-opus | U1, U2, M1, M2, M3, M4 | Settlement / redeem command ledger |
| T1 | T | high | 22 | critic-opus | U1, U2, M1, M2, M3, M4, M5, R1 | Paper/live parity FakePolymarketVenue — same adapter contract, simulated failure modes |
| U1 | U | medium | 14 | critic-opus | Z0, Z1, Z2, Z3, Z4 | ExecutableMarketSnapshotV2 — table + freshness gate + capture seam |
| U2 | U | high | 28 | critic-opus | Z0, Z1, Z2, Z3, Z4, U1 | Raw provenance schema — 5 distinct projections (commands / order-facts / trade-facts / pos |
| Z0 | Z | low | 4 | standard | — | Plan-lock + source-of-truth rewrite |
| Z1 | Z | high | 12 | critic-opus | Z0 | CutoverGuard — runtime state machine for V1→V2 cutover |
| Z2 | Z | high | 18 | critic-opus | Z0, Z1 | V2 strict venue adapter + VenueSubmissionEnvelope |
| Z3 | Z | high | 12 | critic-opus | Z0, Z1, Z2 | HeartbeatSupervisor — MANDATORY for live resting orders |
| Z4 | Z | high | 22 | critic-opus | Z0, Z1, Z2 | CollateralLedger — pUSD + CTF tokens + reservations + wrap/unwrap commands |

## Critical path

- length: 256h
- path: Z0 → Z1 → Z2 → Z4 → U1 → U2 → M1 → M2 → M3 → M4 → M5 → T1 → A1 → A2 → G1
