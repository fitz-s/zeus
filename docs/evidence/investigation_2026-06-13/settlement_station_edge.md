# Settlement-Station vs Retail-Forecast Edge — Decisive (Corrected, Non-Hindsight) Test

**Date:** 2026-06-14
**Mode:** READ-ONLY. DBs opened `?mode=ro`, `.timeout 25000`, ISO-T. No edits, no live touch.
Raw scripts `/tmp/stationedge/*.py`. Every number below re-measured this session at source.
**Charge:** Re-test the edge under the CORRECTED framing. The prior "market out-forecasts us"
verdicts (`edge_existence_decisive.md`, `corrected_belief_oos.md`) were marshalled-hindsight — they
compared the market's near-settlement price (outcome nearly observed) against an early forecast, or
compared OUR forecast directly to the SETTLED outcome. Both leak the outcome. The real thesis is the
**settlement-station vs retail-forecast gap**: retail prices off a CITY-CENTER forecast; the market
SETTLES on the ICAO AIRPORT station; the two differ systematically per city by the representativeness
offset δ_city (Tokyo −2.18 … Karachi +2.48, `cold_bias_metadata_root.md`). We forecast the airport
(correct settlement metadata). Trading that gap = the alleged edge. Test it cleanly: compare OUR
airport-forecast bin distribution vs the MARKET PRICE **at a fair pre-max decision time**, and let
settlement only GRADE — never enter the signal.

---

## VERDICT

**UNDERPOWERED — the corrected test CANNOT be run on current data.** It is not refuted and not
confirmed: the two data assets the test requires **do not overlap in time**, so the joinable panel
collapses to a near-empty intersection (1 cell with a usable multi-bin market distribution; ~17–25
degenerate single-bin events total). This is a *data-availability* verdict, measured cleanly, not a
statement about the edge itself.

**The decisive structural facts (this session, at source):**

1. **Forecast posteriors survive only for 2026-06-08 → 2026-06-16.** `forecast_posteriors` (high
   metric) holds 3,673 rows, MIN target_date **2026-06-08**, MAX 2026-06-16. Everything earlier was
   purged; no deeper bin-posterior history exists in any forecast table (`forecasts` empty;
   `raw_model_forecasts` has deep history 2025-12-01→ but holds **raw point forecasts, not bin
   posterior distributions** — it cannot supply the q over market bins the test needs without
   re-running the whole fusion/posterior pipeline against per-date market topology that is itself
   purged).

2. **Labeled multi-bin market prices survive only for late May.** `token_price_log` (the ONLY market
   source carrying BOTH price AND the bin label `range_label` == our q_json question key) is fresh to
   2026-06-14 but its **multi-bin coverage is concentrated 2026-05-25 → 2026-05-30** (41 of 48
   cells with ≥3 priced bins). After 2026-06-08 it has collapsed to **median 1, max 4 priced bins per
   (city,date)** — captures of the already-converged bin only. (`market_price_history` is worse:
   dead at 2026-05-28; `market_events`/bin-dictionary EMPTY in all three DBs.)

3. **The intersection is 1 cell.** Cells with a forecast posterior AND ≥3 priced market bins (the
   minimum to reconstruct a market bin distribution to disagree with): **exactly 1** (Tokyo
   2026-06-08). With ≥2 priced bins: **3**. With ≥1: **25**. The dense market window (May) has no
   surviving forecast; the forecast window (June) has a market log collapsed to one bin per cell.

4. **The dense market source is unlabelable by bin at acceptable cost.** `executable_market_snapshots`
   (zeus_trades, **3.77M rows**, fresh to 2026-06-14T16:12, all bins) is the only dense source, but it
   stores the bin only as per-token `clobTokenIds` in `token_map_json` — **no `range_label`, no bin
   range column**. Recovering bin labels requires a non-indexed JSON scan across 3.77M rows
   (DISTINCT scan timed out at 200s); and the only label dictionary to join against (`token_price_log`,
   2,371 tokens) is itself the sparse source. So the dense source cannot rescue the panel.

**Confidence: HIGH** that the test is unrunnable on current data (the overlap was enumerated exactly,
not estimated). The single-bin degenerate panel that *does* exist (17 events) yields wild,
uninterpretable swings (buy_no +0.64 on n=14 — the base-rate-favorite artifact the prior docs warned
of; buy_yes −0.18 on n=3), which only re-confirms it is hopelessly underpowered, not a result.

---

## WHAT THE CORRECTED TEST REQUIRES (and why each piece is missing)

| Ingredient | Source | Status |
|---|---|---|
| Market bin **distribution** at a pre-max decision time (≥3 priced bins/cell) | `token_price_log.range_label`+`price`/`bid`/`ask`/`timestamp` | Present ONLY late-May; ≤1–4 bins/cell in June |
| OUR airport-forecast bin distribution at that same decision time | `forecast_posteriors.q_json` (keys == market questions) | Present ONLY 2026-06-08→06-16 |
| Settlement grade (airport station actual max) | `settlement_outcomes` (VERIFIED, °C) | **Healthy** — 621 high-C cells ≥05-25, ~48/day |
| Per-city representativeness offset δ_city (for the concentration cut) | `state/anchor_representativeness_debias.json` (worktree, fitted on deep `previous_runs`) | **Available** — 51 cities, Jakarta −2.81 … Qingdao +1.15 |

Settlement and δ_city are healthy. The two *belief-vs-price-at-decision-time* legs do not co-occur,
so the comparison the thesis demands (where does our airport distribution DISAGREE with the market's
city-center-based price, on the same cell, before the max) has **1 qualifying cell**.

## METHOD (leakage-free; the framing the charge specified)

- **Decision time** = latest market quote with `timestamp ≤ target_date 06:00:00` (morning-of, before
  the ~13–15h local daily max is set). Relaxing to ≤11:00 or ≤23:59 did **not** grow the panel (still
  17) — the binding constraint is forecast-posterior absence, not the cut. This is the explicit
  KEY CONTROL: the market price is read at a fair pre-max time; settlement is used ONLY to grade.
- **Our belief** = latest `forecast_posteriors.q_json` with `computed_at ≤` the market quote timestamp
  (strictly prior → no leakage), q for the exact bin question == `token_price_log.range_label`.
- **Grade** = VERIFIED `settlement_outcomes.winning_bin` (airport station max, after the 0% fee). The
  de-bias δ_city was fit OOS on deep `previous_runs` history (the OOS-safe version per
  `percity_debias_impl.md`), never on the graded cell.
- **Bin-label join verified clean** where both exist: `token_price_log` questions are byte-identical to
  q_json keys (e.g. `'Will the highest temperature in Tokyo be 20°C on May 25?'`). The attrition is
  pure missing-forecast, not a formatting mismatch (diagnosed: the dense-market May cells return
  `NO forecast posterior for this cell`).

## WHY THIS IS NOT A REPEAT OF THE HINDSIGHT ERROR

The prior flawed priors compared OUR forecast to the SETTLED outcome, or the market's
near-settlement price to an early forecast. This test never does that: the signal is strictly
`forecast_q(decision_time)` vs `market_price(decision_time)` on the same bin, with settlement
entering only as the grader. The framing is correct; it is the **data that is absent**, and that
absence is itself the clean, honest finding — not a re-run of the leak.

## WHAT IS NEEDED TO ACTUALLY RUN IT (the UNDERPOWERED remedy)

1. **Co-temporal capture.** Persist a **dense, multi-bin orderbook snapshot** (all bins, with the bin
   `range_label` resolved at capture) on the SAME target_dates the live `forecast_posteriors` cover.
   Either (a) backfill `range_label` onto `executable_market_snapshots` by materializing the
   condition_id→bin-range dictionary (`market_events` is empty — populate it from the gamma
   question text at scan time), or (b) extend `token_price_log` to log ALL bins per event, not just
   the converged one, at a fixed pre-max cadence (e.g. each target morning 02:00–06:00 local).
2. **Retain forecast posteriors** ≥30 days so the forecast window overlaps a month of dense capture.
3. **Re-grade** once ≥~200 (city,date,bin) cells exist where a pre-max **multi-bin** market
   distribution and a prior airport-forecast distribution co-occur — then the where-we-disagree
   bins can be bet, graded at settlement, and the result cut by |δ_city| to test the concentration
   claim (London-style: retail prices off city, airport caps lower). Until that co-temporal panel
   exists, "trade the airport-vs-market gap" remains **unproven by absence of overlapping data**,
   not by a measured null.

---

## RAW (deciding numbers)

- `forecast_posteriors` high: MIN target_date **2026-06-08**, MAX 2026-06-16, n=3,673. No earlier
  posterior history anywhere (`forecasts` empty; `raw_model_forecasts` = raw point values, deep
  2025-12-01→ but NOT bin distributions).
- `token_price_log`: fresh to 2026-06-14T15:01, 80,937 rows / 2,371 tokens / 52 cities; but cells with
  ≥3 priced bins by date — 05-25:5, 05-26:6, 05-29:10, 05-30:26, **06-08:1, none after**.
- **Overlap (forecast ∧ priced market bins, same city·date): ≥3 bins → 1 (Tokyo 06-08); ≥2 → 3;
  ≥1 → 25.** Single-bin decision events buildable end-to-end (pre-max quote + prior forecast q +
  settled): **17**.
- `executable_market_snapshots`: 3.77M rows, fresh, all bins — but no bin-range column; label recovery
  via 3.77M-row JSON DISTINCT scan timed out (>200s). Unusable as a bin-labeled source.
- Settlement universe (healthy): 621 VERIFIED high-°C cells ≥2026-05-25, ~48/day.
- δ_city (fitted, OOS-safe, worktree artifact): 51 cities, Jakarta −2.81, KL −1.68, Singapore −1.39,
  HK −1.29, Taipei −1.26, Seoul −1.19, Tokyo −0.69 … Qingdao +1.15; τ=0.72, walk-forward do_no_harm=True.
- Degenerate single-bin panel (NOT a verdict): buy_no +0.636 (n=14, base-rate-favorite artifact),
  buy_yes −0.178 (n=3). Uninterpretable at this n.

*End. Read-only. DBs `?mode=ro`. Scripts: /tmp/stationedge/{panel,build_events,build_events2,diag_labels,overlap,final_numbers,label_map}.py. The test framing is correct and hindsight-free; the verdict is UNDERPOWERED due to non-overlapping forecast-posterior and multi-bin-market-price windows.*
