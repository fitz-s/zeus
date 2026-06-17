# Regime-B (post-peak near-arb) settlement-graded verdict
Created: 2026-06-15
Last reused or audited: 2026-06-15
Authority basis: settlement-truth grading (task brief); E1 theorem (settlement_value=max over day >= running_max(h)); INV source-binding

## VERDICT: EDGE_REAL (broad edge SMALL; fat hits RARE + concentrated)

### Source-binding (load-bearing correctness), full HIGH universe n=5793
- exact |cm-sv|<0.5: 99.40% (5758)
- obs UNDER settle (safe/conservative for E1): 0.43% (25)
- obs OVER settle (the ONLY E1-breaker): 0.17% (10) — 9x +1unit, 1x +2unit (Shenzhen)
- partial-day excluded (mostly 2026-05-02 data gaps): 205
- => risk-free guarantee fails ~1/580 markets, always by 1-2 units, station-concentrated
   (Shenzhen/Miami/Seoul = coastal/revision-prone). 2-unit elimination margin neutralizes 9/10.

### running_max column semantics (CRITICAL)
observation_instants.running_max = HOURLY high (per-hour reading), NOT cumulative.
running_min goes back UP across hours (verified Tokyo 06-08: 21->22 at h16). True cumulative
daily max = MAX(running_max for hours 0..h). Naive direct read un-eliminates bins => WRONG.

### E1 on SAME-DAY prices (true regime-B substrate, not day-ahead): n=1355 fires
- elimination accuracy 1355/1355 = 100.0% (incl. 637 fragile 1-unit-margin cases: all lost)
- residual is BIMODAL:
  - deep-tail YES<=0.10: n=144, mean 0.6c, EV ~+0.001..+0.095/opp (34 distinct city-date-bin)
  - fat-residual YES>0.10: n=1211, mean 97c, 100% lost — BUT only 9 (city,date) markets
    [Denver/Lucknow/Madrid/Miami/Milan/SF/Tokyo/Warsaw/Wuhan], market's favorite superseded late
- HEADLINE (honest): broad repeatable edge = a few cents/opp; rare fat hits = ~0.97/opp x ~9/window

### Price data reality
token_price_log lag(target - snapshot): lag=2d 51429, lag=1d 21437, SAME-DAY(0d) 8118, <0 ~210.
Backtest headline EV (2-9c) computed mostly vs DAY-AHEAD snapshots. Same-day-only re-grade above.

### Latency (obs availability)
wu_icao_history lands ~hourly + ~15min past hour (lag~0.26h good case), BURSTY (batch gaps up to
3.3h, hour caught up with later hour). Adequate for event-driven per-obs E1; NOT sub-minute race.
The live gap was PRESENCE (regime B = 0 trades), not speed.

### Dead dependency confirmed
day0_horizon_platt_fits = 0 rows; day0_nowcast_runs = 0 rows. Entire Day0/post-peak nowcast lane
is schema-only, never run. This is WHY TRADE_SCORE has no E1 signal.
