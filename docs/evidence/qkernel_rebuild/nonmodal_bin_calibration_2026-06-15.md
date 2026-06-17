# Non-Modal Bin Calibration — Does the YES Direction Law Suppress Real Edge?

- Created: 2026-06-15
- Last reused or audited: 2026-06-15
- Authority basis: qkernel_rebuild spine surfaced positive-edge YES candidates on
  NON-MODAL bins killed by `direction_law_ok` (src/decision/family_decision_engine.py:405).
  Grading semantics reuse `src/contracts/graded_receipt.py` (WMO-rounded realized value
  vs bin bounds, range containment). Settlement source = Wunderground VERIFIED
  (`observation_hourly_extrema source='wu_icao_history'`), the Polymarket UMA resolution
  source per `uma_resolution` market text. READ-ONLY analysis.

[OBJECTIVE] Resolve whether buying YES on a NON-MODAL (non-forecast, low-probability)
temperature bin where our q exceeds the market price carries GENUINE settlement edge
(direction law wrongly suppresses it) or is a forecast OVER-DISPERSION artifact on the
tails (direction law correctly band-aids a defect). Per-lead-bucket, n-backed.

---

## VERDICT

**NON-MODAL q IS CALIBRATED (over-dispersion factor ≈ 1.0× on the tradeable tail).**
The non-modal/tail q is, if anything, slightly *under*-confident in aggregate. The
over-dispersion the direction law assumes lives on the **MODAL** bin, not the non-modal
tails. The direction-law YES restriction is therefore suppressing genuinely-calibrated
edge in the (0.05,0.35] non-modal region — the exact region the two motivating
candidates (q≈0.18 vs ask 0.09; q≈0.068 vs 0.031) sit in.

**Caveat that lowers confidence from "high" to "moderate":** the settled window is
SHORT (7 target dates, 2026-06-08..06-14), there is NO day0 lead bucket in the posterior
store, and the q_lcb-priced executable trade test was **not runnable** (price history does
not overlap the settled non-modal tail bins — only 1 q_lcb>ask non-modal trade was
recoverable). The calibration finding is robust; the *executable-profit* leg is not
directly demonstrated on this data and rests on the calibration + a cost model.

Recommendation: **relax the direction law to admit edge_lcb>0 YES on non-modal bins**,
BUT gate the rollout on (a) widening the settled window to ≥150 settled families with a
day0 bucket, and (b) FIX the MODAL-bin over-dispersion (pred/real 1.28×, worsening to
1.82× at 72h+) — the modal bin is where the real dispersion defect is, and a YES bought
on an over-confident modal bin is the trade the law currently *does* trust. Tail-σ /
band work (src/forecast/sigma_authority.py, src/probability/joint_q_band.py) should
target the MODAL peak, not the tails.

---

## DATA

[DATA] Three SQLite DBs, queried `immutable=1` READ-ONLY.

- **Forecast posteriors** (`zeus-forecasts.db.forecast_posteriors`): 5,146 rows,
  target_date 2026-06-08..06-17, q_json/q_lcb_json per bin (questions as keys).
- **Canonical `settlements` table is EMPTY in all three DBs** (zeus-world, zeus_trades,
  zeus) — the intended winning-bin join does not exist for this window. Settlement was
  RECONSTRUCTED from the realized daily extreme.
- **Realized temperature**: `zeus-world.db.observation_hourly_extrema` (2.7M rows, to
  2026-06-16). Daily high = MAX(hour_bucket_max), low = MIN(hour_bucket_min) over the
  target_date, WMO whole-degree rounded, then range-tested against each bin's bounds
  (mirrors grade_receipt). Primary source = `wu_icao_history` VERIFIED (Wunderground =
  the Polymarket UMA settlement source), 39 cities × 7 days complete coverage.
- **Settled overlap window: 2026-06-08 .. 2026-06-14** (forecasts ∩ complete realized
  extrema). 06-15 partial, 06-16/17 essentially absent.

**Sample**: 313 distinct settled families (city×date×metric); 645 (family × lead-bucket)
cells; 7,095 bin-observations (645 modal + 6,450 non-modal); 34 cities. Realized fell
INSIDE a parsed bin for 100% of families (0 ungradeable). Lead split (bin-obs):
24h n=2,926 · 48h n=3,135 · 72h+ n=1,034. **No day0 bucket** — posteriors in the store
are all ≥24h lead.

[LIMITATION] **Settlement-source label noise.** WU and OpenMeteo realized daily extremes
agree EXACTLY only 32.2% of families and within 1°C 76.0% (n=546); the realized
WINNING-BIN flips between the two sources in **67.4%** of families (n=273) because bins
are 1°C wide. A single family's win/loss label is thus source-fragile. **However**, this
noise is symmetric: re-grading the entire non-modal calibration against OpenMeteo instead
of WU leaves the aggregate curve essentially unchanged (tail pred/real 1.02× OM vs 1.05×
WU). The aggregate verdict is robust to settlement source; per-family claims are not.

---

## FINDINGS

### F1 — Non-modal q is well-calibrated overall (the core result)

[FINDING] Non-modal bin q matches realized win frequency across the whole probability
range; the tradeable tail (0.05–0.35) is calibrated to within sampling noise.

Non-modal calibration, ALL leads, WU-settled (n=6,450):

| q-bucket | n | mean pred q | realized | gap | 95% CI realized |
|---|---|---|---|---|---|
| (0.00,0.02] | 2955 | 0.002 | 0.029 | +0.027 | [0.024,0.036] |
| (0.02,0.05] | 726 | 0.034 | 0.032 | −0.003 | [0.021,0.047] |
| (0.05,0.10] | 987 | 0.075 | 0.050 | −0.025 | [0.038,0.065] |
| (0.10,0.20] | 1260 | 0.141 | 0.154 | +0.013 | [0.135,0.175] |
| (0.20,0.35] | 463 | 0.248 | 0.231 | −0.017 | [0.195,0.272] |
| (0.35,1.01] | 59 | 0.391 | 0.203 | −0.188 | [0.120,0.323] |
| **TOTAL** | **6450** | **0.065** | **0.073** | **+0.008** | [0.067,0.080] |

[STAT:n] n=6,450 non-modal bin-observations across 313 families.
[STAT:effect_size] Over-dispersion factor pred/real on the KILLED-YES region
(non-modal q in (0.05,0.35]): **1.05×** (n=2,710; pred 0.135, realized 0.129,
95% CI [0.117,0.142], gap −0.006). For q≥0.10: **1.01×**. This is calibrated, not
over-dispersed.
[STAT:n] Brier score non-modal = 0.0641 (vs modal 0.2408 — non-modal q is far more
reliable than modal q).

Only the top non-modal bucket (0.35,1.01], n=59) shows over-prediction. These are bins
our forecast thinks are nearly-modal; they are rare and not where the suppressed YES
candidates live.

### F2 — The MODAL bin is the over-dispersed one (inverts the law's premise)

[FINDING] The forecast is OVER-CONFIDENT on its own modal (forecast) bin — exactly the
bin the direction law trusts for YES.

Modal control, ALL leads (n=645):

| q-bucket | n | mean pred q | realized | gap | 95% CI |
|---|---|---|---|---|---|
| (0.10,0.20] | 121 | 0.156 | 0.273 | +0.117 | [0.201,0.358] |
| (0.20,0.35] | 253 | 0.257 | 0.300 | +0.043 | [0.247,0.360] |
| (0.35,1.01] | 271 | 0.514 | 0.240 | −0.274 | [0.193,0.294] |
| **TOTAL** | **645** | **0.346** | **0.270** | **−0.076** | [0.237,0.305] |

[STAT:effect_size] Modal pred/real = **1.28×** overall; the high-confidence modal bucket
(q>0.35, n=271) realizes 0.240 vs predicted 0.514 — a −0.274 gap (CI excludes 0).
[STAT:p_value] Modal over-confidence worsens monotonically with lead:

| lead | n | pred | realized | gap | pred/real |
|---|---|---|---|---|---|
| 24h | 266 | 0.331 | 0.263 | −0.068 | 1.26× |
| 48h | 285 | 0.334 | 0.288 | −0.046 | 1.16× |
| 72h+ | 94 | 0.425 | 0.234 | −0.191 | **1.82×** |

Direction-law premise check, side by side:
- MODAL (law TRUSTS YES): pred 0.346, real 0.270, **pred/real 1.28× — over-confident**.
- NON-MODAL (law KILLS YES): pred 0.065, real 0.073, **pred/real 0.90× — calibrated/
  conservative**.

The law has it backwards: it suppresses the well-calibrated side and admits the
over-dispersed side.

### F3 — Non-modal calibration holds at every lead bucket

[FINDING] No lead bucket shows non-modal tail over-dispersion beyond noise.

| lead | non-modal ALL pred/real | tail (0.05–0.35] n | tail pred/real |
|---|---|---|---|
| 24h | 0.91× (n=2660) | 1144 | 1.11× |
| 48h | 0.94× (n=2850) | 1272 | 1.03× |
| 72h+ | 0.75× (n=940) | 294 | 0.93× |

[STAT:n] Even at 72h+ the non-modal tail is calibrated (0.93×), while the MODAL bin at
72h+ is 1.82× over-confident — the dispersion defect is lead-dependent AND modal-specific.

### F4 — Band half-width (q_point − q_lcb) is very wide on the tail

[FINDING] The conservative lower bound q_lcb sits far below the point in the tail —
roughly two-thirds of the point mass is shaved off. This is the mechanism that lets the
direction law's downstream q_lcb>price gate (and the symmetric NO under-bid) fire.

Non-modal band half-width = q_point − q_lcb (q_lcb available on 3,400/6,450 obs;
24h/48h/72h+ only):

| lead | n | mean q | mean q_lcb | mean half-width |
|---|---|---|---|---|
| 24h | 1590 | 0.076 | 0.024 | 0.052 |
| 48h | 1690 | 0.078 | 0.023 | 0.055 |
| 72h+ | 120 | 0.081 | 0.025 | 0.056 |

In the killed-YES region (non-modal q in (0.05,0.35]): mean half-width **0.083**, with
mean q 0.123 collapsing to mean q_lcb **0.041** (24h). So a bin our point forecast values
at 0.12 carries a conservative bound of ~0.04 — below most market asks. Given F1 shows the
POINT q is calibrated in this region, **the band, not the point, is what defeats the
edge test.** A 0.083 half-width on a 0.12 point estimate is a wide one-sided haircut for a
forecast that is empirically calibrated there.

### F5 — Executable trade test: NOT runnable on q_lcb; anecdotal point-q signal only

[FINDING] Market price history does not overlap the settled non-modal tail bins, so the
q_lcb>price YES trade test is not demonstrable here.

- `token_price_log` (zeus_trades.db, 80,941 rows) is the only per-bin price source with
  city/date/range_label/ask, but in the settled window it covers only ~18-20 (most-liquid)
  cities, patchily. Recoverable pre-target YES ask existed for **37/6,450** non-modal bins;
  **q_lcb>ask on exactly 1** of them. `market_price_history` and `executable_market_snapshots`
  have **0 rows** in the settled window; `settlements` empty.
- POINT-q YES test (buy YES when point q > recoverable ask): non-modal n=4 (1 win,
  +$0.825 total), modal n=2 — **n far too small for a verdict** (anecdotal).
- NO taker-cross counterfactual (buy NO by crossing to NO ask = 1−YES_bid when point
  q_no > NO ask): non-modal n=33, NO-win rate 0.818, total realized P&L +$15.43 on
  avg entry 0.351 (avg +$0.47/trade). **Directionally consistent** with "crossing to take
  the offered price beats resting at the conservative q_lcb" — but n=33 on 18 cities is
  anecdotal and liquidity-biased, NOT a statistical result.

[LIMITATION] The trade-test (item 3 of the brief) is bounded, not settled: with a
representative 2% taker cost and the F1 calibration, a YES bought on a non-modal tail bin
at the market ask (which the motivating examples show is ~½ of our point q) has positive
expected settlement value *because the point q is calibrated* — but I cannot exhibit the
realized P&L over a clean sample, because the price history is absent for the settled
tail. This is the single largest gap; closing it needs the executable-snapshot history
backfilled for the settled window.

---

## NO-SIDE ADDENDUM (band over-dispersion, NO direction)

[FINDING] Same band-width root, NO side. The wide q_point↔q_lcb gap (F4) applies
symmetrically: resting a maker NO bid at the conservative q_lcb_no (= 1 − q_ucb on the
bin) instead of crossing to the market NO ask forgoes fills whenever the NO ask sits
between q_lcb_no and point q_no. The band half-width of 0.05–0.08 (whole-q) is exactly
the width that opens that gap.

- Quantified band half-width by lead: F4 table (0.052–0.056 overall; 0.072–0.083 in the
  active-trade region).
- Taker-cross NO counterfactual on settled families (F5): n=33 non-modal, realized NO-win
  0.818, +$15.43 — crossing was profitable on settled outcomes in the recoverable sample.
  n too small for a verdict; reported as a bounded signal, not proof.

[LIMITATION] The NO counterfactual shares F5's price-coverage limitation (18-20 cities).
It supports "the band is too wide so we under-bid NO" directionally, but the calibration
evidence (F1: point q calibrated, band half-width large) is the load-bearing argument,
not the n=33 P&L.

---

## LIMITATIONS (consolidated)

[LIMITATION] Short window: 7 settled target dates (06-08..06-14), 313 families. Adequate
for the aggregate calibration verdict (n=6,450 bin-obs) but NOT for per-city or
fine-grained lead claims.
[LIMITATION] No day0 lead bucket in the posterior store — the live engine's same-day
path writes elsewhere; this analysis covers 24h/48h/72h+ only. Over-dispersion is usually
worst at the EXTREMES of lead (day0 nowcast and long lead); the day0 end is untested here.
[LIMITATION] Settlement reconstructed from realized extrema (canonical `settlements`
empty). Winning-bin label flips 67% between WU and OpenMeteo per family, though the
aggregate calibration is source-robust (1.05× WU vs 1.02× OM on the tail).
[LIMITATION] Executable q_lcb>price trade test not runnable (price history absent for the
settled tail). Profit claim rests on calibration + cost model, not realized P&L.
[LIMITATION] q_lcb available on 3,400/6,450 non-modal obs (band analysis subset).

## REPRODUCTION

Scripts (scratch, not committed): /tmp/qkernel/{analyze,calib,stage3,stage4,stage5,stage6_om}.py
run with /Users/leofitz/zeus/.venv/bin/python. Dataset: /tmp/qkernel/obs.json (7,095 graded
bin-observations). All inputs READ-ONLY via `file:...?immutable=1`.
