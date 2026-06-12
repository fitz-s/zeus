# Exit-path replay: canonical+cost-aware vs legacy on REAL historical positions

Authority: Wave-2 item 3 flip verification, 2026-06-12. READ-ONLY replay over `state/zeus_trades.db` (mode=ro, immutable). Uses the REAL `HoldValue` contract functions (not a re-implementation), flag branch selected per leg.

## Flag taxonomy (what each flag actually gates)

- **CANONICAL_EXIT_PATH** (read `src/execution/harvester.py:2525`, also helper `:108`): selects `mark_settled()` vs `compute_settlement_close()` at SETTLEMENT close. `mark_settled` (`src/execution/exit_lifecycle.py:2529`) *calls* `compute_settlement_close` + one `logger.info`. Same settlement_price in -> same realized P&L + same state transition out. **It gates settlement bookkeeping/event routing, NOT an exit decision. Zero P&L / zero decision divergence by construction.**
- **HOLD_VALUE_EXIT_COSTS** (read `src/config.py:727`, used `src/state/portfolio.py` EV gates `_buy_yes_exit`/`_buy_no_exit`): swaps the EV-gate hold-value from `shares*prob` (legacy, fee=0/time=0) to `shares*prob - fee - time - crowding`. **Replayed below.**
- **exit_bias_family_unify_enabled** (read `src/engine/evaluator.py:3417`, `src/engine/monitor_refresh.py:636`): subtracts a per-city bias SHIFT from member_extrema BEFORE p_raw, then identity-Platt, on the EXIT/monitor side so exit belief matches entry belief. **Changes the monitor PROBABILITY upstream; not replayable from the stored already-computed `last_monitor_prob` — DATA GAP, see §4.**

## 1. Replay corpus

- MONITOR_REFRESHED events scanned: **18405**
- Usable refreshes (fresh prob + finite nonzero best_bid; the only refreshes on which the live EV gate actually runs): **3052**
- Distinct positions with >=1 usable refresh: **22** (replayable with known shares: **22**)

## 2. HOLD_VALUE_EXIT_COSTS — per-position legacy vs canonical EV-gate

`hold` = EV gate said HOLD (continue holding) at that refresh. `legacy_holds` / `canon_holds` count refreshes where each leg held; `divergent` counts refreshes where the legs DISAGREED (always canonical=sell-permit, legacy=hold, per the structural invariant).

| position | city | dir | entry_px | size$ | shares | settled | refreshes | legacy_holds | canon_holds | divergent |
|---|---|---|---|---|---|---|---|---|---|---|
| b5d966a9-990 | Seoul | buy_no | 0.630 | 4.41 | 7.00 | — | 132 | 131 | 96 | 35 |
| edlia8a015a5a7 | Paris | buy_yes | 0.120 | 1.08 | 9.00 | — | 445 | 439 | 420 | 19 |
| eae0a6bd-8a4 | Wellington | buy_yes | 0.360 | 1.80 | 5.00 | LOST | 325 | 75 | 57 | 18 |
| a4a2d274-897 | Beijing | buy_no | 0.730 | 3.65 | 5.00 | — | 55 | 55 | 55 | 0 |
| edli789b6f22c6 | Denver | buy_no | 0.600 | 10.23 | 17.05 | — | 28 | 28 | 28 | 0 |
| 8c723222-bd4 | Helsinki | buy_no | 0.690 | 3.45 | 5.00 | — | 163 | 163 | 163 | 0 |
| cd67c156-410 | Hong Kong | buy_no | 0.840 | 11.34 | 13.50 | WON | 163 | 163 | 163 | 0 |
| edlia3e4b65ac2 | Hong Kong | buy_no | 0.930 | 17.67 | 19.00 | — | 36 | 36 | 36 | 0 |
| fa24ae48-c5c | Istanbul | buy_no | 0.750 | 18.51 | 24.68 | LOST | 115 | 100 | 100 | 0 |
| 29c67699-36d | Karachi | buy_no | 0.810 | 17.01 | 21.00 | LOST | 164 | 163 | 163 | 0 |
| dab33b1f-0b1 | London | buy_no | 0.590 | 2.95 | 5.00 | WON | 162 | 162 | 162 | 0 |
| 1090d54b-c95 | Madrid | buy_no | 0.550 | 4.40 | 8.00 | WON | 164 | 163 | 163 | 0 |
| edlib7ce6b22f8 | Manila | buy_no | 0.610 | 3.05 | 5.00 | LOST | 36 | 34 | 34 | 0 |
| 99788511-f54 | Milan | buy_no | 0.620 | 8.06 | 13.00 | WON | 163 | 163 | 163 | 0 |
| edlibffd96a855 | Milan | buy_yes | 0.016 | 1.06 | 66.25 | — | 169 | 169 | 169 | 0 |
| 7d1fb48c-600 | San Franci | buy_no | 0.530 | 2.65 | 5.00 | WON | 109 | 109 | 109 | 0 |
| 08d9cf1e-cf9 | Tokyo | buy_no | 0.970 | 8.73 | 9.00 | WON | 142 | 142 | 142 | 0 |
| 942e9556-82c | Tokyo | buy_no | 0.690 | 3.45 | 5.00 | WON | 142 | 142 | 142 | 0 |
| edli00b7fc4387 | Tokyo | buy_no | 0.660 | 3.30 | 5.00 | LOST | 9 | 8 | 8 | 0 |
| 94714e5a-f37 | Warsaw | buy_no | 0.680 | 10.20 | 15.00 | WON | 163 | 163 | 163 | 0 |
| edlie660d819d2 | Warsaw | buy_no | 0.660 | 3.30 | 5.00 | WON | 25 | 25 | 25 | 0 |
| d71efb1a-6b9 | Wellington | buy_no | 0.520 | 2.60 | 5.00 | WON | 142 | 142 | 142 | 0 |

**Total divergent refreshes across all positions: 72.**

## 3. Divergent-refresh detail + P&L delta vs settled truth

Each row: canonical permitted a sell that legacy held. `pnl_delta_if_canon_executed` = (sell proceeds now `shares*bid`) − (settled value: shares if WON else 0). Negative = canonical would have SOLD a winner cheap (worse); positive = canonical would have escaped a loser (better). Conservative fill = the stored best_bid.

| position | city | dir | at | prob | bid | shares | nv_legacy | nv_canon | sell_val | settled | ΔP&L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T06:35 (41.4h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T06:39 (41.3h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T06:42 (41.3h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T06:43 (41.3h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T06:45 (41.2h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T06:47 (41.2h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T06:49 (41.2h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T06:51 (41.1h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T06:54 (41.1h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T06:57 (41.0h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T06:59 (41.0h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:02 (41.0h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:04 (40.9h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:06 (40.9h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:08 (40.9h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:10 (40.8h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:12 (40.8h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:14 (40.8h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:16 (40.7h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:18 (40.7h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:20 (40.7h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:22 (40.6h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:24 (40.6h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:26 (40.6h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:29 (40.5h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:31 (40.5h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:33 (40.4h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:35 (40.4h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:37 (40.4h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:39 (40.3h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:41 (40.3h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:43 (40.3h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:46 (40.2h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:48 (40.2h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| b5d966a9-990 | Seoul | buy_no | 2026-06-07T07:50 (40.2h) | 0.795 | 0.790 | 7.00 | 5.564 | 5.505 | 5.530 | — | n/a |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T03:31 (44.5h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T03:33 (44.4h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T03:35 (44.4h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T03:59 (44.0h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T04:01 (44.0h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T04:03 (43.9h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T04:05 (43.9h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T04:07 (43.9h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T04:09 (43.8h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T04:11 (43.8h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T04:25 (43.6h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T04:29 (43.5h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T04:31 (43.5h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T04:33 (43.4h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T06:05 (41.9h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T06:09 (41.8h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T06:13 (41.8h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| eae0a6bd-8a4 | Wellingto | buy_yes | 2026-06-08T06:17 (41.7h) | 0.231 | 0.230 | 5.00 | 1.155 | 1.110 | 1.150 | LOST | +1.15 |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:33 (34.4h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:35 (34.4h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:37 (34.4h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:39 (34.3h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:41 (34.3h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:43 (34.3h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:45 (34.2h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:47 (34.2h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:49 (34.2h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:51 (34.1h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:54 (34.1h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:56 (34.1h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T13:58 (34.0h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T14:01 (34.0h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T14:04 (33.9h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T14:06 (33.9h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T14:08 (33.9h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T14:10 (33.8h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |
| edlia8a015a5 | Paris | buy_yes | 2026-06-11T14:12 (33.8h) | 0.103 | 0.100 | 9.00 | 0.931 | 0.891 | 0.900 | — | n/a |

**Divergent-refresh outcome breakdown: WON sold=0 (harm), LOST escaped=18 (benefit), unsettled=54.**
**Net P&L delta if EVERY canonical divergence had executed (settled cases only): +20.70 USD.** NOTE: this is an UPPER BOUND on canonical's effect — a divergence at the EV gate only becomes a real sell if the upstream gates (consecutive-confirm count, CI-separation) ALSO permit exit on that cycle; the EV gate is the last layer, not the only one.

## 4. exit_bias_family_unify_enabled — data-availability gap + analytic direction

This flag changes the monitor PROBABILITY upstream (member_extrema bias shift). The MONITOR_REFRESHED payload stores only the already-computed `last_monitor_prob`; the raw member_extrema + `edli_per_city_v1` bias rows needed to recompute the shifted prob are not in the stored decision inputs. **Not replayable from this corpus without re-running the live monitor forecast pipeline against historical ensembles.**

Analytic direction (from the flag note + code): it subtracts the SAME per-city bias the LIVE ENTRY reactor already subtracts (`event_reactor_adapter._EDLI_BIAS_FAMILY`, 71 VERIFIED rows). Today entry is bias-corrected but exit/monitor is NOT (the legacy `full_transport_v1` family has 0 rows -> exit correction is permanently inert). So the flip removes an entry/exit ASYMMETRY: exit belief stops drifting from entry belief. FAIL-CLOSED: any missing row -> plain p_raw (today's behaviour), trading continues. Direction is toward CONSISTENCY, not toward more/less exiting per se; magnitude is the per-city bias_c (typically <1°C). This is the asymmetry implicated in the 2026-06-12 exit-blind losses and should be validated by the same settled-truth gate the note names (BEFORE_AFTER_bias_family_unify.md), not flipped blind.

## 5. Verdict

- **CANONICAL_EXIT_PATH: SAFE_TO_FLIP** (settlement-bookkeeping routing only).
- **HOLD_VALUE_EXIT_COSTS: SAFE_TO_FLIP (settled-truth strictly better).** 72 divergences, but 0 sold a winner and net settled ΔP&L = +20.70 USD (all benefit = loser-exits). Monotone (only ever permits MORE exits). CAVEAT: divergences sit on the belief≈bid knife-edge and the EV gate is the last exit layer, so the live effect is <= this upper bound.
- **exit_bias_family_unify_enabled: FLIP_WITH_CAVEATS.** Not replayable from stored decision inputs (§4). Direction is entry/exit belief CONSISTENCY (removes a known asymmetry), fail-closed. Gate on the per-city before/after belief-delta + settled-truth review named in its flag note, not on this replay.

## 6. Deletion scope — every consumer of the three flags

(grep `src/ tests/`, 2026-06-12)

**CANONICAL_EXIT_PATH:** read only at `src/execution/harvester.py` `_get_canonical_exit_flag()` (:108) used at :2334/:2525. Config: `config/settings.json:286`, `config/settings.example.json:160`. Tests pinning behaviour: `tests/test_exit_authority.py` (:91 default-False, :98/:103 True path) — these assert the flag READER, will survive a permanent-ON unless they assert default=False.
**HOLD_VALUE_EXIT_COSTS:** reader `src/config.py:720 hold_value_exit_costs_enabled()`; call sites `src/state/portfolio.py` :1247 :1326 :1416 :1504 (4 EV-gate seams). Config `:287`/example`:161`. Tests pinning OFF behaviour: `tests/test_hold_value_exit_costs.py` (:157 flag-OFF regression guard, :213/:245 patch return_value=False expecting NO `hold_value_exit_costs_enabled` breadcrumb) and `tests/test_live_safety_invariants.py:4491/:4534` (monkeypatch ->True). The OFF-pinning tests MUST be updated/removed when the legacy branch is deleted.
**exit_bias_family_unify_enabled:** readers `src/engine/evaluator.py:3417`, `src/engine/monitor_refresh.py:636`; emitted validations evaluator :4348/:4398, monitor :1033/:1161/:1231. Config `:284`/example(note only). Tests: `tests/test_bias_family_unify_d2.py` (:121 `_FF_ON`, :122 `_FF_OFF` — exercises BOTH legs; the `_FF_OFF` leg pins legacy behaviour and must be updated on deletion). Also `tests/test_k1_review_fixes.py:236` asserts `exit_bias_family_unify` NOT in applied (flag-OFF expectation).

