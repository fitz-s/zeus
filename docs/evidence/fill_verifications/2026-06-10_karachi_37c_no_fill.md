# Fill e2e verification — Karachi 37°C NO (2026-06-10 22:19Z)
# Created: 2026-06-10
# Authority basis: operator Phase-2 goal (2 verified good-trade fills); corrected-math
# verification procedure as ratified for the HK fill; mainstream = post-fill
# observability tripwire ONLY (operator law, never admission).

## Order
- command c200af0ede1f4868 → venue 0x5b32c96f...f88de, FOK taker BUY NO
- Market: "Will the highest temperature in Karachi be 37°C on June 12?" (gamma 2488700,
  slug highest-temperature-in-karachi-on-june-12-2026, cond 0x9dbced69...6d70, neg_risk)
- Price 0.66 × 12.5 shares = $8.25 cost; matched on-chain tx 0x800787fe...0d46ae
- Filled 2026-06-10T22:19:17Z; status MATCHED, remaining 0

## Decision receipt (re-probed from DBs, not memory)
- Posterior: forecast_posteriors id=1456 (zeus-forecasts), Karachi/2026-06-12/high,
  00Z cycle, computed 21:02Z, certified bounds present.
- Tradeable-latest semantics PROVEN IN PRODUCTION: a newer 06Z row (id=1404) exists with
  q_lcb_json NULL (fusion instruments not landed) and was correctly skipped; the certified
  00Z row was served. The clobber category stayed dead.
- Distribution (q / lcb / ucb): 39°C 0.257/0.062/0.263 (MODAL); 38°C 0.240/0.061/0.263;
  40°C 0.177/0.023/0.262; **37°C 0.145/0.016/0.260**; 41+ 0.107/0.005/0.523;
  36°C 0.057/0.003/0.240. σ_pred=1.487, floor 1.3343 applied, basis
  fused_center_residual_std.

## Verification checks
1. DIRECTION LAW ✓ — buy_no ⟺ bin≠forecast: bin 37°C vs modal 39°C, |Δ|=2.0°C >
   tolerance (σ_pred 1.49 / floor 1.33). Same shape as the HK check (2.0 > 1.33).
2. CERTIFIED EDGE ✓ — no_lcb = 1 − q_ucb_yes = 1 − 0.2604 = 0.7396 vs entry 0.66
   → +7.96¢ certified (worst-case-bound) edge. Complement here is the LAWFUL
   certified-bound form (no_lcb = 1−q_ucb), not the banned belief complement.
3. AFTER-COST ✓ — harshest fee reading (fee_rate_bps=1000 on winnings): EV/share at
   the certified bound = 0.7396×(0.34×0.9) − 0.2604×0.66 = +5.4¢ > 0. At point
   q_no=0.855: +16.6¢/share.
4. SIZING ✓ — $8.25 within the $5-15 operator envelope (single-Kelly equity-base stack).
5. PRICE CLASS ✓ — YES-side 0.34 (book 0.35/0.41) inside the 0.2-0.6 "probably good
   trade" heuristic band; not a micro order.
6. MAINSTREAM TRIPWIRE (post-fill observability ONLY) ✓ — open-meteo multi-model at
   airport coords (24.91, 67.16): ECMWF 39.4, GFS 36.1, ICON 38.0, UKMO 40.3, GEM 38.1
   → consensus ≈38.4°C, agrees with fused modal 39. No Paris-class divergence. (A
   city-center "best_match" single-model probe returned 34.5°C — grid/model artifact;
   multi-model at station coords is the meaningful reference.) Every model says high
   ≠ 37, so the NO direction is robust even across the model spread.
7. MARKET CONTEXT — market priced YES(37°C exact bin) at 0.34-0.41, an outlier vs both
   our fused distribution and the mainstream ensemble; this is the favorite-longshot/
   mispriced-bin class the strategy exists to harvest.

## Verdict
GOOD TRADE — e2e verified. This is good-fill 2/2 for the operator's Phase-2 goal
(HK 30°C NO = 1/2 on 2026-06-10 ~19:09Z; Karachi 37°C NO = 2/2 at 22:19Z).
Settlement (the only truth) lands after 2026-06-12 12:00Z market end.

## Open observability note (for K5.4 organ)
trade_decisions table did NOT carry this edli-lane fill (legacy rows only); receipt had
to be reconstructed from venue_commands + venue_order_facts + forecast_posteriors +
daemon log. The K6.10/K5.4 receipt-completeness items in the consolidated overhaul cover
exactly this gap.
