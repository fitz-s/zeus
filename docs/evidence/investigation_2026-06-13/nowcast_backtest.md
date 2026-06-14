# Same-Day Nowcast Backtest — Does a Point-in-Time Nowcast Belief Beat the Market After Fee?

**Date:** 2026-06-14
**Mode:** READ-ONLY. DBs `?mode=ro`, timeout 25s, ISO-T. Unit = `(city, target_date, bin)` event.
**Charge (from `alpha_hunt.md` §"What would change the verdict"):** the full edge-hunt refuted the 1–2-day lane (adverse selection; peak-bin MAE ≈ 1.3 °C ≈ one bin-width) but flagged the **true same-day nowcast (≤6–12 h to local EOD)** as the ONE structure never tested, *only because no settled nowcast sample exists in the recorded decision data*. This backtest reconstructs that belief point-in-time, offline, with zero leakage, and asks: (a) does peak-bin MAE collapse? (b) does the nowcast beat the market price at settlement after the 1¢ fee?
**Belief logic reused (not reinvented):** `src/signal/day0_high_distribution.build_day0_high_distribution` physical law `sample = settle(max(observed_high_so_far, future_member_max))`; obs floor from `observation_instants` (settlement-faithful `wu_icao_history`); remaining-hours forecast from `day0_hourly_vectors` via `remaining_day_extremes_c` semantics (only hours ≥ decision instant). Scripts: `/tmp/nbk/run.py`, `/tmp/nbk/run2.py`, `/tmp/nbk/out2.json`.

---

## VERDICT

**UNTESTABLE OFFLINE for the market-beating / after-fee-edge question — and the precision "collapse" is a tautology, not alpha.** Two independent findings:

1. **The after-fee edge vs market is structurally untestable in the recorded data.** There is **zero** city-date with the triple overlap required to grade a nowcast trade: {VERIFIED settlement} × {high-res hourly forecast vectors} × {a captured market price inside the same-day afternoon nowcast window}. Specifically, across **110 US/regional-city conditions on the one date that carries a condition_id↔bin map (2026-06-13), 0/110 have ANY market snapshot at or after local-noon of the target day** — snapshot capture ceases ~11:29 UTC (target-day morning), 6–12 h *before* the nowcast decision instant. The market price simply isn't recorded during the window the nowcast would trade in.

2. **The peak-bin MAE collapse is real but tautological — it is "the day is mostly over," not forecasting skill.** Pooled peak-bin MAE does fall from ~1.0 °C at 12 h-to-EOD to **0.16 °C at 6 h** (exact-bin hit 33/39 = 84.6%, CP 95% CI [0.70, 0.94]). BUT at 6 h-to-EOD **the daily max has already been physically observed in 95% of cells** — the "belief" is just reading the running max. In the cells where the peak had NOT yet occurred (forecast genuinely required), precision is **no better than the refuted 1–2-day lane**: 9 h → MAE 0.91 °C, exact-hit 1/12; 12 h → MAE 1.17 °C, exact-hit 3/20 (15%). And the observed max-so-far is **public** — the market sees the same thermometer, so even the trivial-pin cells carry no informational edge.

**Confidence: HIGH** on (1) (0/110 is categorical), HIGH on the tautology in (2) (the obs-floor-already-≥-settlement decomposition is unambiguous). The honest conclusion: the same-day nowcast cannot be validated offline here, and the mechanism that makes it *look* precise (post-peak observation) is exactly the mechanism that denies it an edge over an equally-observing market. A live-shadow is required to test it properly, but the prior on finding edge is now LOWER, not higher.

---

## 1. DATA FEASIBILITY MAP (what exists, what overlaps)

| Substrate | Table | Coverage relevant to nowcast | Verdict |
|---|---|---|---|
| Obs floor (max-so-far) | `zeus-world::observation_instants` (`wu_icao_history`) | All 54 cities, hourly, `utc_timestamp`+temp; settlement-faithful (full-day max == VERIFIED settlement, 12/12 spot-checks) | **AVAILABLE** |
| Remaining-hours forecast | `zeus-forecasts::day0_hourly_vectors` | **17 cities** (US + W-Europe only — regional high-res model domain gate), target_dates **2026-06-11…06-14**, dense `captured_at` series (~30 min) | **AVAILABLE but narrow** |
| Settlement truth | `zeus-forecasts::settlement_outcomes` (VERIFIED) | 06-11/06-12 settled for the 17 vector cities; 06-13 VERIFIED cities are **Asia/Europe ONLY** (no vectors) | **partial** |
| Market price (point-in-time) | `zeus_trades::executable_market_snapshots` | 3.7 M rows, `captured_at`+`orderbook_top_bid/ask`, by `condition_id` | available, **but window-mismatched (§3)** |
| bin ↔ condition_id map | `zeus-world::probability_trace_fact.{bin_labels_json,condition_ids_json}` | **Only target_date 2026-06-13** has `condition_ids_json` backfilled | **single date only** |

**Triple-overlap = ∅.** The 17 vector cities are settled on 06-11/12 but those dates carry no `condition_ids_json` (so no bin↔market join). 06-13 carries the map but (a) its VERIFIED settlements are Asia cities with no vectors, and (b) the US vector cities on 06-13 are not yet settled. No single `(city, target_date, bin)` satisfies all of {settled} ∧ {vector} ∧ {market-mapped}.

## 2. PRECISION RESULT (testable; reconstructed point-in-time, no leakage)

Sample = VERIFIED-settled vector-city cells on 2026-06-11 + 2026-06-12, metric=high (n=113 cell·horizons over 39/39/35 cells at 6/9/12 h). Belief = `settle(max(obs_floor_≤dec, remaining_member_max_≥dec))`. No-leakage verified end-to-end (Chicago 06-12 hb=6: 5 later obs + 14 later vectors correctly excluded; obs-floor already 81 °F = settlement).

| hours-to-EOD | n | peak-bin MAE (°C) | median | exact-bin hit | **peak already observed (obs ≥ settle)** |
|---:|---:|---:|---:|---:|---:|
| 6 | 39 | **0.160** | 0.00 | 33/39 = 0.846 | **37/39 = 95%** |
| 9 | 39 | 0.597 | 0.50 | 20/39 = 0.513 | 27/39 = 69% |
| 12 | 35 | 0.994 | 1.00 | 13/35 = 0.371 | 15/35 = 43% |

hb=6 exact-hit Clopper-Pearson 95% CI **[0.695, 0.941]**. vs the 1–2-day lane's 1.30 °C / 24% (`edge_existence_decisive.md` §2).

**The collapse is post-peak observation, not skill.** Decomposing by whether the day's peak had already physically occurred at the decision instant:

| hours-to-EOD | cells where peak NOT yet observed (forecast genuinely needed) | MAE (°C) | exact-bin hit |
|---:|---:|---:|---:|
| 6 | 2 | 0.75 | 0/2 |
| 9 | 12 | 0.91 | 1/12 = 0.08 |
| 12 | 20 | 1.17 | 3/20 = 0.15 |

When the forecast must actually predict an unobserved peak, MAE is **0.9–1.2 °C and exact-hit 8–15% — statistically indistinguishable from the refuted 1–2-day lane.** The headline 0.16 °C / 84.6% at hb=6 is the model reporting an already-recorded thermometer reading.

## 3. WHY THE AFTER-FEE EDGE IS UNTESTABLE (the integrity crux)

The decisive, no-leakage market-window probe (`/tmp/nbk` systematic scan, 11 US vector cities, 06-13):

```
conditions checked = 110
with ANY market snapshot at/after local-noon of the target day = 0   (0/110)
last-snapshot times across all conditions: 2026-06-13T11:28Z .. 2026-06-13T11:29Z
```

For these cities local EOD is ~04:00–06:00 UTC next day, so the nowcast decision instants (EOD−6/9/12 h) are **17:00–23:00 UTC** — every one is **6–12 h after the final captured snapshot**. The live system snapshots a market on its 1–2-day-ahead decision cadence and stops once that interest passes (morning of the target day); it does **not** capture the order book during the same-day afternoon, which is exactly when a nowcast would trade. A leaked backtest (grading a nowcast trade against a morning price, or against settlement without a contemporaneous price) would be worse than none, so it is not produced.

Secondary blocker even if the window matched: the belief is a near–point estimate (only **1–2** in-domain regional models per city → `n_members` ∈ {1,2}; `q_lcb` width is not meaningful), so the q_lcb>cost selection rule the live lane uses cannot be exercised faithfully offline.

## 4. MINIMAL LIVE-SHADOW TO TEST IT (since offline is impossible)

To settle the nowcast edge question, the system must record, point-in-time, the inputs it does not record today:

- **Snapshot the order book through the same-day afternoon.** Extend `executable_market_snapshots` capture for day0/same-day temperature markets to continue at ≥30-min cadence from local-noon to market close (currently stops ~target-day morning). This is the single missing datum.
- **Persist the nowcast belief at each same-day decision instant** (obs_floor, remaining-member vector, q-vector, q_lcb, chosen bin) alongside the contemporaneous market price and the decision `captured_at` — i.e. emit `probability_trace_fact`-class rows from the nowcast lane, not just the 1–2-day lane.
- **Backfill `condition_ids_json` ↔ `bin_labels_json`** for every traded date (today only 2026-06-13 carries it) so bin↔price joins are possible historically.
- **Power:** target ~150 settled same-day events with a *genuine forecast component* (peak NOT yet observed at the decision instant) — at the empirical ~30–55% per-day post-noon rate that is **≈ 8–12 cities × 2 metrics over ~15–20 trading days**. Pre-register the rule (buy where nowcast `q_lcb` > contemporaneous ask, after 1¢ fee), grade with Clopper-Pearson / bootstrap CI, and require a held-out OOS split (the 1–2-day winner died OOS — do not repeat that).
- **Expectation to disconfirm:** because the obs-floor that drives the nowcast is public, the live-shadow is most likely to show the market already prices it (no edge); the test's value is to *prove* that rather than assume it. The pinnable-bin cells are precisely the cells where the market is also pinned.

---

## RAW (deciding numbers)
- Triple-overlap {settled}∧{vector}∧{market-mapped} city-dates: **0**.
- Market price in nowcast window (US vector cities, 06-13): **0 / 110 conditions**; last snapshot ~11:29 UTC, nowcast instants 17:00–23:00 UTC.
- Peak-bin MAE: 12 h **0.99 °C** → 6 h **0.16 °C**; but **95% of 6 h cells have the peak already observed** (tautology).
- Forecast-genuinely-needed subset: MAE 0.9–1.2 °C, exact-hit 8–15% — **= the refuted 1–2-day lane**.
- `n_members` ∈ {1,2} (1–2 regional models/city) → q_lcb width not meaningful offline.

*End nowcast backtest. Read-only. No leakage (obs `utc_timestamp` ≤ dec, vectors `captured_at` ≤ dec and hours ≥ dec; verified end-to-end). Scripts: `/tmp/nbk/run.py`, `/tmp/nbk/run2.py`, `/tmp/nbk/probe_map.py`, `/tmp/nbk/out2.json`.*
