# P2 — W-EDGEPROOF: Settlement-Graded Edge-Existence Gate

**Date:** 2026-06-14
**Mode:** PLAN-MAKING (read-only; no production edits, no deploy, no live touch).
**Workstream:** W-EDGEPROOF — the settlement-graded gate that licenses any live q_lcb flip. Sibling of W-QLCB (the causal fix) and W-EDGE-LOCATE (the location harness). This document owns the **proof standard**: exactly what evidence, which queries, which acceptance numbers, and which walk-forward/embargo protocol constitute a settlement-proven claim that the q_lcb fix produced REAL correct-bin edge and did not re-enable base-rate favorite-buying as "alpha."
**Authority spine:** `P1_strategy_of_record.md` (Thrust 6 / the DONE criterion), `P2_W-EDGE-LOCATE.md` (E2 harness, INV-CAL contracts), `P2_W-QLCB.md` (the fix under test), `P2_sequence_and_critical_path.md` (Gate ladder G1–G5), operator contract laws 1–8. Every empirical claim below is cited to the source plan or evidence doc that establishes it.

---

## 0. THE PROBLEM THIS GATE SOLVES

The q_lcb fix (W-QLCB Thrust 3) has a failure mode that is indistinguishable from success on a fill-count or revenue basis: it could raise q_lcb on cheap bins that are NOT ring-bin correct-bin edge (population C: model honestly below market; population A: structural far-tail zero) and produce admitted trades that are base-rate favorites re-labelled as alpha. The operator's contract (law 4/5/8) forbids this. The DONE criterion (law 1) demands settlement proof, not fills.

W-EDGEPROOF is the instrument that makes the distinction measurable, prior to any live promotion:

- It grades the SHADOW ring cohort (the would-be ring admissions W-QLCB's UP arm computes in shadow) against **settlement**, not against fills.
- It verifies the q_lcb fix raised q_lcb on the **right** bin class (near-center `exact` ring where q_point is honest and above market, not cheap-tail favorites).
- It requires the graded cohort to clear a **statistical power floor** (N_eff ≥ 200–300 distinct events, see §3) before any live promotion is considered.
- It licenses a band only if a **proper scoring rule** (multiclass log-score + RPS, not raw win-rate) AND a **vs-market benchmark** (Brier < market-Brier) AND an **after-cost positive EV** all clear together.

Without this gate, every q_lcb fix in the prior 100 patches was promoted on "it raised q_lcb" or "an order filled" — neither of which is settlement evidence for correct-bin alpha. This gate is the antibody.

---

## 1. THE UNIT OF ANALYSIS

**Unit: (city, target_date, metric, bin_label) — one SETTLEMENT EVENT.**

Not a contract. Not a cycle snapshot. Not a receipt row.

### 1.1 Why event-level, not contract-level

W-EDGE-LOCATE §1.2 established with data that the 28-raw-row Taipei 2026-06-05 28°C market contributes **1 distinct event** (the city-date-bin triple), regardless of how many reactor cycles wrote a regret row against it. Treating each cycle snapshot as a separate observation inflated n=5 events to n=48 raw rows — a 40× phantom-precision multiplier that would license "alpha" on two coin flips. The event-level dedup is INV-CAL-1 (five INV-CAL contracts, `P2_W-EDGE-LOCATE.md §4.2`).

**The grading rule:**

```sql
SELECT city, target_date, metric, bin_label, direction,
       MAX(would_have_won) AS won,        -- 1 if the bin settled in the winner, 0 otherwise (idempotent across cycle rows)
       AVG(c_cost_95pct)   AS cost,       -- the market-implied price at observation time
       AVG(q_lcb_5pct_shadow) AS q_lcb_shadow,  -- the W-QLCB shadow q_lcb (the thing under test)
       AVG(q_live)          AS q_point
FROM no_trade_regret_events_shadow         -- shadow q_lcb column; populated by W-QLCB N7-shadow
WHERE would_have_won IS NOT NULL
  AND target_date < :as_of_date           -- INV-CAL-3: walk-forward only
GROUP BY city, target_date, metric, bin_label, direction
```

This produces one graded row per event. Every downstream score, power calc, and acceptance test operates on this de-duplicated event table. Any report that operates on raw row counts (before the GROUP BY) is wrong and will be rejected.

### 1.2 The bin population scope (ring only; tail excluded by construction)

The gate applies ONLY to the **near-center `exact` ring** bin class — the fix target (W-QLCB §0.3, population B). The scope filter:

```sql
WHERE bin_kind = 'exact'                  -- excludes or_higher / or_below open-ended tail bins
  AND bin_forecast_distance(bin_low, bin_high, mu, step) <= 2   -- dist-0 and dist-1 ring
  AND q_point > 0.05                      -- excludes structural-zero far bins (population A)
  AND direction = 'buy_yes'               -- buy_no base-rate lane is excluded from this gate (law 4)
```

`bin_forecast_distance` is the LIVE primitive from `direction_law.py` — the SAME function the reactor uses. No second distance authority.

**Why buy_no is excluded from this gate:** buy_no at ~90% win-rate is base-rate (law 4; W-EDGE-LOCATE §1.2: net edge −0.007 after cost). A gate that proves buy_yes ring edge is not a license for buy_no. If buy_no ever needs a separate edge claim it requires its own gate with its own settlement evidence; that is out of scope here.

---

## 2. THE PROPER SCORE: MULTICLASS LOG-SCORE + RPS

Win-rate alone is insufficient: a model that puts 0.001 on the eventual winner and collects 0.001 × fee = "won" has positive win-rate but negative EV. The proper scoring rules penalize miscalibrated confidence across the full bin distribution.

### 2.1 Multiclass log-score (the calibration instrument)

For each settled event, the model held a distribution `q_vec = [q(bin_1), …, q(bin_K)]` over all K bins of the family (K = number of settlement bins for that city-date-metric family, typically 5–12). The realized outcome is bin `b*` (the bin that settled). The log-score for this event:

```
LS(event) = log q(b*)
```

where `q(b*)` is the **shadow q_lcb_corrected** value on the winning bin (the thing under test, not the point q). This is the proper scoring rule for the class the model claims to be confident in.

**Aggregate:** mean log-score over the de-duplicated event table, stratified by ring-distance bucket and lead bucket.

**Benchmark:** the market-implied log-score `log(market_price(b*))` on the SAME events. Edge exists iff `mean_LS_model > mean_LS_market`.

**Why log-score:** it is strictly proper (rewards the model that reports its true beliefs), sensitive to miscalibration in the tails (if the model claims 0.12 on a bin that wins 11% of the time, the log-score correctly penalizes even a slightly over-claiming model), and it is the standard information-theoretic ground truth for probability forecast quality (Gneiting & Raftery 2007). Win-rate alone does not distinguish a well-calibrated 0.12 from an over-claiming 0.20.

### 2.2 RPS (Ranked Probability Score) — the ordinal cross-check

Temperature outcomes are ordered: a forecast that places mass on adjacent bins incurs less error than one that completely misses. The RPS measures this:

```
RPS(event) = (1/(K-1)) × Σ_{k=1}^{K-1} (CDF_forecast(k) - CDF_observed(k))^2
```

where `CDF_forecast(k) = Σ_{j≤k} q(bin_j)` (cumulative predicted) and `CDF_observed(k) = 1[b* ≤ bin_k]` (cumulative observed).

**Lower RPS is better.** The model's RPS must beat the market-implied RPS on the SAME events. This cross-checks that the ring-bin mass improvement (from W-QLCB) did not come at the cost of worsening the adjacent-bin calibration (e.g. by collapsing tail mass too aggressively).

**Both scores are required to clear.** A model that passes log-score but fails RPS has a calibration problem at the distributional level (the ring bin won but the adjacent bins are worse than the market). Both must beat the market benchmark on the same settled events.

---

## 3. THE BENCHMARK SET

Three benchmarks, in priority order. All are evaluated on the **same de-duplicated event table** as the model, with the same walk-forward embargo.

### 3.1 Market-implied price (the primary benchmark, law 4)

`market_price(b*)` is the market's probability estimate for the winning bin, approximated by `c_cost_95pct` (the fee-adjusted taker price at observation time) from the regret row. This is the baseline the model must beat on BOTH log-score and RPS for any band to claim edge (INV-CAL-4: "a tie with the market is no-edge regardless of realized rate").

**Why this is the right primary benchmark:** it encodes market efficiency directly. If the model's `q_lcb_shadow` is merely tracking the market price (or is worse), the fix added no information — it just loosened the admission criterion. The log-score gap `mean_LS_model − mean_LS_market` is the information value the model adds beyond the market; it must be positive.

### 3.2 Walk-forward best single model (the anti-overfitting benchmark)

The σ_center fit and the bidirectional isotonic calibration (W-QLCB N5/N7) are fit on history. A model that over-fits the calibration map will beat the market on in-sample data and collapse on holdout. The walk-forward best-single-model benchmark is the **same model evaluated on data it was NOT fit on**: for each event `e` with `target_date = d`, the model's calibration map must have been fit on events with `target_date < d` only (INV-CAL-3; no look-ahead).

Operationally: the isotonic map `_isotonic_realized_rate` is fit once on `[2024-01-01, embargo_cutoff)` and evaluated on `[embargo_cutoff, now]`. The embargo cutoff is never earlier than 60 days before the evaluation window's start (see §4).

**This benchmark detects in-sample overfit:** if the model beats the market on the full historical window but loses to the walk-forward best-single-model on holdout, the calibration is over-fit and the fix must not be promoted. This is the `_meta.promotion` forward-fill validation from `sigma_scale_fit.json` pattern (P1 §3 T4), applied here to the q_lcb calibration.

### 3.3 Climatology (the floor benchmark)

The climatology benchmark is the unconditional frequency of each bin settling, computed from the full settled history without any conditioning on the weather model's output. On the ring bin for a given city-month, this is approximately the 30-year climatological probability that the actual temperature falls in a given degree bin.

Climatology serves as a floor: the model should beat climatology on log-score and RPS by construction (the weather model has real forecast skill). If the corrected q_lcb does NOT beat climatology on any ring band, the calibration has gone pathological (e.g. collapsing all mass onto the ring bin regardless of the day's μ*). This is a sanity check, not a primary gate.

**Climatology query:**

```sql
SELECT city, metric, bin_label, CAST(strftime('%m', target_date) AS INT) AS month,
       AVG(CAST(bin_label = settled_bin AS REAL)) AS clim_freq
FROM settlement_outcomes
WHERE authority = 'VERIFIED'
  AND target_date < :embargo_cutoff
GROUP BY city, metric, bin_label, month
```

The model's mean log-score on a ring band must exceed `log(clim_freq)` or the band is INSUFFICIENT regardless of the market comparison.

---

## 4. THE WALK-FORWARD / EMBARGO PROTOCOL

**Non-negotiable structure:** ALL calibration fits are trained on a period that ends strictly before the evaluation period begins. No look-ahead. No re-fit in the evaluation window.

### 4.1 The protocol

```
Training / fit window:   [2024-01-01 → embargo_cutoff)
Evaluation window:       [embargo_cutoff → now]
```

**embargo_cutoff = 2025-10-01** (90 days before the ~2026-01 period when W-QLCB's shadow would have first been active, rounded to a season boundary to avoid seasonal-boundary bias). This leaves ~21 months of fit history (7009 VERIFIED settlements are spread 2024-01-01 → 2026-06-13, confirmed `P2_sequence_and_critical_path.md §0`).

Every element that is fit on data — the isotonic map, σ_center, σ-shape — is fit on `target_date < 2025-10-01` only. Evaluation on `target_date ≥ 2025-10-01`.

### 4.2 Within-evaluation walk-forward discipline

Within the evaluation window, each day's verdict uses only events settled before that day (`target_date < :as_of_date`, INV-CAL-3). The isotonic map is NOT re-fit within the evaluation window — it was fit once on the training window. A re-fit within the evaluation window would be a form of look-ahead (the re-fit sees which bands had good/bad realized rates in the evaluation window before issuing verdicts).

**Exception:** if the shadow system accumulates enough NEW distinct events in the evaluation window (n≥30 in a single band), the E2 harness is allowed to issue a SUPPLEMENTAL verdict that uses ONLY those new events (walk-forward, none from the fit window). This supplemental verdict is the PRIMARY gate for live promotion (not the backfill-history verdict) because it represents genuinely out-of-sample ring alpha at the time of the promotion decision.

### 4.3 The backfill lever and its limit

`P2_sequence_and_critical_path.md §5.3` identified the key acceleration: 7009 VERIFIED settlements already exist and can be used to fit the isotonic map immediately (not waiting for new settlements). This is correct for the FIT side. For the EVALUATION side, only events in `[embargo_cutoff, now]` (the holdout) count toward the G4 LICENSE decision. The backfill accelerates the fit; it does not manufacture holdout evidence.

**Honest arithmetic (the current binding constraint):** W-EDGE-LOCATE §1.2 found ~5 distinct ring events in the regret substrate. Even after fitting on 7009 historical settlements and back-filling the isotonic calibration map, the **evaluation window ring cohort** is approximately 5 events (the ring market opportunities that arose during the evaluation window and were captured in `no_trade_regret_events`). The N_eff gate (§3 below) requires ≥200–300. **The gap is real and is the rate-limiter for any live promotion.** The gate is designed to wait honestly for this cohort to grow.

---

## 5. THE N_EFF EVENT-LEVEL POWER THRESHOLD

### 5.1 Why ~200–300 events for 5 cents/share edge

The target edge is ~1.5–3pp of honest market under-pricing on the ring bin (P1 §7). After the 1¢ fee on a ~9¢ bin, the net edge is:

```
net_edge = realized_wr − market_price − fee
         ≈ 0.108 − 0.091 − 0.01 = 0.007 per share (at the low end)
```

To detect a 0.7% net edge at 80% power against a one-sided null (H0: net_edge ≤ 0), using the Wilson/Jeffreys normal approximation for a binary outcome:

```
n_eff = (z_{alpha} + z_{beta})^2 × p(1-p) / delta^2
      ≈ (1.645 + 0.842)^2 × 0.1 × 0.9 / 0.007^2
      ≈ 6.18 × 0.09 / 0.000049
      ≈ 11,355
```

That is impractical. At a more realistic edge of 3pp (at the high end):

```
net_edge = 0.108 − 0.091 − 0.01 = 0.007 (still applies; the edge depends on the exact bin)
```

Re-parameterizing for a **5¢/share edge** (a decision threshold at which the operator would consider the lane non-trivial):

```
delta = 0.05, p = 0.10
n_eff = (1.645 + 0.842)^2 × 0.09 / 0.0025
      ≈ 6.18 × 36 ≈ 222 events
```

**The N_eff floor is 200 distinct (city, target_date, bin) events per band for a 5¢/share edge detection at 80% power.** For a 3¢/share bar (the minimum worth trading given friction):

```
delta = 0.03
n_eff = 6.18 × 0.09 / 0.0009 ≈ 618 events
```

The 200-event floor is therefore the **aggressive lower bound**, appropriate only if the edge is genuinely ~5¢ after the fee. If the model's point estimate of ring edge is 1–2pp (the low end), the correct N_eff is >1000 and the realistic verdict is that the lane will be INSUFFICIENT for many months of accrual.

**The gate uses N_eff ≥ 200 as the minimum activation threshold for any LICENSED verdict.** Below 200 → INSUFFICIENT_DATA regardless of the realized win-rate. This is a HARDER floor than W-EDGE-LOCATE's `N_MIN_EVENTS = 30` (which governs the initial band-verdict for the coverage/calibration seam). The EDGEPROOF gate's 200-event floor governs the live-promotion LICENSE decision.

### 5.2 Why the P2_W-EDGE-LOCATE N_MIN=30 is not sufficient for live promotion

W-EDGE-LOCATE §4.3 sets `N_MIN_EVENTS = 30` for the E2 `INSUFFICIENT_DATA` floor. That 30-event floor governs the isotonic calibration verdict — whether the coverage map has enough data to be reliable. It does NOT govern the statistical power to detect the edge itself. A 30-event ring cohort at 0.9¢ edge (after the fee) gives roughly 5% power — a coin flip. The live-promotion gate requires 200 events minimum (5¢ bar) precisely because the coverage calibration is a prerequisite, not proof of tradeable alpha.

**The two N thresholds have distinct functions:**
- `N_MIN=30` (W-EDGE-LOCATE E2): the calibration map is reliable enough to issue a coverage verdict.
- `N_EFF≥200` (W-EDGEPROOF): there is enough statistical power to claim tradeable after-cost edge. This is the live-promotion gate.

Both must pass for a LICENSED→live decision.

---

## 6. TRADED VS COUNTERFACTUAL AFTER-COST EV

### 6.1 The current situation: zero traded ring events

As of the plan date, the ring cohort in `no_trade_regret_events` has n≈5 distinct events, ALL counterfactual (the q_lcb fix was never live; these are shadow would-be admissions). There are no traded ring fills to grade (W-EDGE-LOCATE §1.2). Therefore **all current edge evidence is counterfactual**. The gate must handle both the counterfactual (pre-live-promotion) and post-live-fill phases.

### 6.2 Counterfactual after-cost EV (the pre-live phase)

In the shadow/counterfactual phase, the after-cost EV for a band is:

```
EV_shadow(band) = (realized_wr − cost − fee_per_share) × n_events
```

where:
- `realized_wr` = fraction of distinct ring events where `won=1` (de-duplicated, INV-CAL-1).
- `cost` = average market price at observation time (`c_cost_95pct`).
- `fee_per_share` = 0.01 (the Kalshi standard maker/taker fee, confirmed from task #66 and P1 §5).
- `n_events` = distinct events in the band.

A band passes the counterfactual EV check iff `EV_shadow > 0` with the Jeffreys/Wilson lower 95% CI on `realized_wr − cost` strictly positive (`realized_minus_price_lo95 > 0`, INV-CAL-4 from W-EDGE-LOCATE).

**The counterfactual gate cannot be the live-promotion gate.** Counterfactual edge is necessary but not sufficient: the counterfactual assumes a fill at the observed market price, which may not have been achievable at that size/time. It also does not account for adverse selection (the market may move against the entry immediately). The live-promotion gate (G5) requires ACTUAL fills to settle profitably.

### 6.3 Traded after-cost EV (the post-live-promotion phase, G5)

Once W-QLCB is promoted to live and ring fills accrue, the grading shifts from counterfactual to traded:

```
EV_traded(band) = (fills_won / fills_total) − mean_fill_price − fee_per_share
```

where `fills_won / fills_total` is the event-level (not contract-level) settlement win-rate on ACTUAL fills from `zeus_trades.db`. This is the G5 criterion: the band must clear **>51% after-cost at n≥30 forward fills**, event-level de-duplicated (one fill per city-date-bin, not one fill per contract), model-Brier < market-Brier on the same events.

**Why 51%, not 50%?** After the 1¢ fee on a ~9¢ bin, break-even is approximately `cost + fee = 0.09 + 0.01 = 0.10`. The model's q_lcb_shadow on admitted ring bins averages ~0.10–0.13 (W-QLCB §0.3 population B). At 51% win-rate the EV is `0.51 − 0.10 = 0.41 per contract × contract_size` — a thin but positive margin. Requiring >51% (not merely >50%) provides a margin above the statistical noise floor at small n.

---

## 7. THE EXACT QUERIES AND ARTIFACTS THAT CONSTITUTE PASS

### 7.1 Query EP-1: The ring cohort backtest (the G1 / D2 gate)

**Purpose:** Decide whether the UP arm ships at all (P1 D2; W-QLCB §4.1). Run BEFORE N7 is built.

```sql
-- Attach both DBs (INV-37: single connection, ATTACH)
ATTACH DATABASE 'state/zeus-forecasts.db' AS fcast;

-- Step 1: event-level dedup on the ring sub-population
WITH ring_events AS (
  SELECT
    r.city, r.target_date, r.metric, r.bin_label, r.direction,
    MAX(r.would_have_won) AS won,
    AVG(r.c_cost_95pct)   AS cost,
    AVG(r.q_lcb_5pct)     AS q_lcb_raw,   -- pre-fix raw value
    AVG(r.q_live)         AS q_point
  FROM no_trade_regret_events r
  WHERE r.would_have_won IS NOT NULL
    AND r.direction = 'buy_yes'
    AND r.q_live > 0.05                    -- exclude structural-zero bins (population A)
    AND r.target_date < '2025-10-01'       -- training window only (for the calibration backtest)
  GROUP BY r.city, r.target_date, r.metric, r.bin_label, r.direction
),
-- Step 2: bucket by claimed q_lcb band
bucketed AS (
  SELECT
    CASE
      WHEN q_lcb_raw < 0.01 THEN 'band_0_to_1pct'
      WHEN q_lcb_raw < 0.05 THEN 'band_1_to_5pct'
      WHEN q_lcb_raw < 0.10 THEN 'band_5_to_10pct'
      WHEN q_lcb_raw < 0.20 THEN 'band_10_to_20pct'
      ELSE                       'band_over_20pct'
    END AS lcb_band,
    won, cost, q_lcb_raw, q_point
  FROM ring_events
)
SELECT
  lcb_band,
  COUNT(*)          AS n_events,
  SUM(won)          AS wins,
  AVG(won)          AS realized_wr,
  AVG(q_lcb_raw)    AS mean_q_lcb,
  AVG(q_point)      AS mean_q_point,
  AVG(won) / NULLIF(AVG(q_lcb_raw), 0) AS R_over_E_ratio   -- the key diagnostic
FROM bucketed
GROUP BY lcb_band
ORDER BY lcb_band;
```

**Pass criterion (G1):** If `R_over_E_ratio ≥ 2.0` persists on the `band_0_to_1pct` AND `band_1_to_5pct` rows (the bands where q_lcb is near-zero and the crush is expected) → the UP arm is warranted; proceed to N7. If R/E collapses to ~1.0 across all bands when restricted to `q_point > 0.05` → the under-coverage is population-A-driven, not ring-bin crushing; reduce to N5 (producer fix) only with no UP arm; emit dated "market efficient on ring" verdict (law-1 DONE).

**Acceptance number:** R/E ≥ 2.0 on the near-zero bands with n ≥ 20 events in those bands.

### 7.2 Query EP-2: The shadow q_lcb calibration check (validates N7 is lifting the right bins)

**Purpose:** After N7's shadow is running, verify the UP arm raised q_lcb on RING bins (population B) and NOT on far-tail bins (population A) or below-market bins (population C).

```sql
-- Compare shadow q_lcb to raw q_lcb by bin class
WITH shadow_events AS (
  SELECT
    r.city, r.target_date, r.metric, r.bin_label, r.direction,
    MAX(r.would_have_won)              AS won,
    AVG(r.q_lcb_5pct)                 AS q_lcb_raw,
    AVG(r.q_lcb_5pct_shadow)          AS q_lcb_shadow,   -- the corrected value (N7 output)
    AVG(r.q_live)                     AS q_point,
    AVG(r.c_cost_95pct)               AS cost,
    r.bin_kind,
    bin_forecast_distance(r.bin_low, r.bin_high, r.mu_anchor, r.settlement_step) AS ring_dist
  FROM no_trade_regret_events r
  WHERE r.would_have_won IS NOT NULL
    AND r.direction = 'buy_yes'
    AND r.target_date >= '2025-10-01'  -- evaluation window only
    AND r.target_date < :as_of_date
  GROUP BY r.city, r.target_date, r.metric, r.bin_label, r.direction, r.bin_kind
)
SELECT
  bin_kind,
  CASE WHEN ring_dist <= 1 THEN 'ring_0_1'
       WHEN ring_dist <= 3 THEN 'ring_2_3'
       ELSE 'tail' END               AS dist_bucket,
  COUNT(*)                           AS n_events,
  AVG(q_lcb_raw)                     AS mean_q_lcb_raw,
  AVG(q_lcb_shadow)                  AS mean_q_lcb_shadow,
  AVG(q_lcb_shadow - q_lcb_raw)      AS mean_lift,        -- should be positive for ring, ~0 for tail
  AVG(won)                           AS realized_wr,
  AVG(cost)                          AS mean_price,
  AVG(won) - AVG(cost) - 0.01       AS net_edge_after_fee
FROM shadow_events
GROUP BY bin_kind, dist_bucket
ORDER BY bin_kind, dist_bucket;
```

**Pass criterion (EP-2):**
- `mean_lift > 0` for `ring_0_1` (the fix lifted ring bins).
- `mean_lift ≈ 0` for `tail` and `or_higher`/`or_below` bins (far-tail bins must NOT be lifted — population A stays at ≈0).
- `mean_q_lcb_shadow ≤ mean_q_point` for ALL rows (the `min(target, q_point)` clamp holds — law 8 / Hidden #2).
- `mean_q_lcb_shadow ≤ mean_price` for `dist_bucket = 'tail'` rows (far-tail stays rejected by `capital_efficiency`).

**FAIL = manufacturing far-tail alpha.** If the tail rows show positive lift, the UP arm is mis-scoped and N7 must NOT be promoted.

### 7.3 Query EP-3: The proper-score settlement check (the G4 / E2-LICENSED gate)

**Purpose:** Verify model beats market on multiclass log-score AND RPS in the evaluation window before issuing any LICENSED verdict. This is the correct-bin alpha proof.

```sql
ATTACH DATABASE 'state/zeus-forecasts.db' AS fcast;

WITH eval_events AS (
  SELECT
    r.city, r.target_date, r.metric, r.bin_label,
    MAX(r.would_have_won)      AS won,
    AVG(r.q_lcb_5pct_shadow)   AS q_shadow,   -- model probability (corrected)
    AVG(r.c_cost_95pct)        AS market_price,
    r.ring_dist_bucket
  FROM no_trade_regret_events r
  WHERE r.direction = 'buy_yes'
    AND r.bin_kind = 'exact'
    AND r.ring_dist_bucket IN ('ring_0_1', 'ring_2_3')
    AND r.target_date >= '2025-10-01'
    AND r.target_date < :as_of_date
    AND r.would_have_won IS NOT NULL
  GROUP BY r.city, r.target_date, r.metric, r.bin_label, r.ring_dist_bucket
),
scored AS (
  SELECT
    ring_dist_bucket,
    COUNT(*)                        AS n_events,
    -- Multiclass log-score: log of the claimed probability on the WINNING bin
    AVG(CASE WHEN won=1 THEN LOG(MAX(q_shadow, 0.001)) ELSE 0.0 END)
                                    AS model_log_score,
    AVG(CASE WHEN won=1 THEN LOG(MAX(market_price, 0.001)) ELSE 0.0 END)
                                    AS market_log_score,
    -- Brier score on the binary won/lost (proxy for RPS on the admitted bin)
    AVG((q_shadow - won) * (q_shadow - won))   AS model_brier,
    AVG((market_price - won) * (market_price - won)) AS market_brier,
    -- After-cost EV
    AVG(won) - AVG(market_price) - 0.01        AS net_edge_after_fee,
    -- Lower 95% CI on (realized_wr - cost): Wilson approximation
    AVG(won) - AVG(market_price)
      - 1.645 * SQRT(AVG(won)*(1-AVG(won))/COUNT(*)) AS edge_lo95
  FROM eval_events
  GROUP BY ring_dist_bucket
)
SELECT
  ring_dist_bucket,
  n_events,
  model_log_score, market_log_score,
  model_log_score - market_log_score   AS log_score_advantage,
  model_brier,     market_brier,
  market_brier - model_brier           AS brier_advantage,
  net_edge_after_fee,
  edge_lo95,
  CASE
    WHEN n_events >= 200
      AND model_log_score > market_log_score
      AND model_brier < market_brier
      AND edge_lo95 > 0.0
    THEN 'LICENSED'
    WHEN n_events >= 30
      AND (model_log_score <= market_log_score OR model_brier >= market_brier OR edge_lo95 <= 0.0)
    THEN 'NO_EDGE'
    ELSE 'INSUFFICIENT_DATA'
  END AS verdict
FROM scored;
```

**Acceptance numbers — LICENSED requires ALL of:**
1. `n_events ≥ 200` (N_eff power floor, §5).
2. `model_log_score > market_log_score` (model beats market on log-score).
3. `model_brier < market_brier` (model beats market on Brier / RPS proxy).
4. `edge_lo95 > 0.0` (lower 95% CI on realized_wr − cost is positive, after-cost positive EV).

**NO_EDGE:** `n_events ≥ 30` (enough data for calibration) AND any of 2/3/4 fails → the market is efficient on this band → stand down (do NOT loosen `capital_efficiency`).

**INSUFFICIENT_DATA:** `n_events < 30` → continue accruing shadow events; do not promote.

### 7.4 Query EP-4: The RPS full-distribution check (the distributional cross-check)

**Purpose:** Verify the ring-bin lift did not come at the cost of worsening adjacent-bin calibration. Requires the full bin distribution per event (not just the admitted bin).

```sql
-- For each settled family (city-date-metric), compute RPS across all bins
WITH family_forecast AS (
  SELECT
    fp.city, fp.target_date, fp.metric,
    fp.bin_label,
    fp.q_shadow_value  AS q_shadow,   -- the corrected q_lcb per bin from the shadow posterior
    fp.market_price,                  -- market-implied P per bin
    so.settled_bin
  FROM forecast_posteriors_shadow fp   -- shadow materialization (N7 output, all bins)
  JOIN fcast.settlement_outcomes so
    ON fp.city = so.city
    AND fp.target_date = so.target_date
    AND fp.metric = so.metric
    AND so.authority = 'VERIFIED'
  WHERE fp.target_date >= '2025-10-01'
    AND fp.target_date < :as_of_date
),
rps_by_event AS (
  SELECT city, target_date, metric, settled_bin,
    -- Compute RPS: sum of (cumulative_F - cumulative_1)^2 across all bin thresholds
    SUM(
      POWER(
        (SELECT SUM(q_shadow) FROM family_forecast f2
         WHERE f2.city = f.city AND f2.target_date = f.target_date
           AND f2.metric = f.metric AND f2.bin_label <= f.bin_label)
        - CAST(f.bin_label <= f.settled_bin AS REAL),
        2
      )
    ) / (COUNT(*) - 1) AS model_rps,
    SUM(
      POWER(
        (SELECT SUM(market_price) FROM family_forecast f2
         WHERE f2.city = f.city AND f2.target_date = f.target_date
           AND f2.metric = f.metric AND f2.bin_label <= f.bin_label)
        - CAST(f.bin_label <= f.settled_bin AS REAL),
        2
      )
    ) / (COUNT(*) - 1) AS market_rps
  FROM family_forecast f
  GROUP BY city, target_date, metric, settled_bin
)
SELECT
  COUNT(*)           AS n_families,
  AVG(model_rps)     AS mean_model_rps,
  AVG(market_rps)    AS mean_market_rps,
  AVG(market_rps - model_rps) AS rps_advantage,   -- positive = model better
  CASE WHEN AVG(model_rps) < AVG(market_rps) THEN 'PASS' ELSE 'FAIL' END AS rps_verdict
FROM rps_by_event;
```

**Pass criterion:** `mean_model_rps < mean_market_rps` (positive `rps_advantage`). A FAIL here with a passing EP-3 indicates the ring-bin lift came at the cost of worsening adjacent bins — the fix needs the point-q σ-shape fix (T4/N6) to redistribute tail mass correctly rather than only correcting the lower bound.

### 7.5 The artifacts that constitute a complete PASS

The following five artifacts, dated and signed with their as-of date and DB hash, constitute the gate passing. All five must be present and pass:

| Artifact | Query | Pass criterion | Evidence file |
|---|---|---|---|
| **EP-1 ring backtest** | §7.1 | R/E ≥ 2.0 on near-zero bands | `docs/evidence/edgeproof/EP1_ring_backtest_YYYYMMDD.json` |
| **EP-2 lift taxonomy** | §7.2 | Ring lift > 0; tail lift ≈ 0; shadow ≤ q_point | `docs/evidence/edgeproof/EP2_shadow_lift_YYYYMMDD.json` |
| **EP-3 proper-score gate** | §7.3 | n≥200, log-score+Brier beat market, edge_lo95>0 → LICENSED | `docs/evidence/edgeproof/EP3_proper_score_YYYYMMDD.json` |
| **EP-4 RPS distribution** | §7.4 | model_rps < market_rps | `docs/evidence/edgeproof/EP4_rps_distribution_YYYYMMDD.json` |
| **EP-5 traded settlement** | G5 criterion (W-EDGE-LOCATE §7.5) | n≥30 traded fills, >51% after-cost, event-level | `docs/evidence/edgeproof/EP5_traded_settlement_YYYYMMDD.json` |

**EP-1 through EP-4 are the PRE-LIVE-PROMOTION gate.** They must all pass before N7 is promoted from shadow to live (Wave 3 in the critical path). EP-5 is the POST-LIVE gate — it accumulates AFTER live fills and constitutes the repeating settlement evidence that defines DONE.

---

## 8. HOW THIS GATE PROVES THE Q_LCB FIX PRODUCED REAL CORRECT-BIN EDGE

The gate design directly addresses the specific failure mode it is guarding against: q_lcb being raised on the wrong population (base-rate favorites, cheap-tail dead-bins, or below-market-model bins) and that raise being mistaken for correct-bin alpha.

### 8.1 Population B isolation (why q_lcb was raised on the right bins)

EP-2 (§7.2) is the direct proof. It shows, for each bin class separately, whether the UP arm raised q_lcb. The structural guards in W-QLCB's fix:
- The `min(target, q_point)` clamp means the UP arm can NEVER raise q_lcb above the model's own point belief — so if q_point is below the market price (population C), the calibrated q_lcb stays below the market price and is still rejected by `capital_efficiency`. EP-2's `mean_q_lcb_shadow ≤ mean_q_point` column verifies this clamp is holding.
- Population A (far-tail) has a realized rate of ≈0 in its band → the isotonic map assigns target≈0 → no lift → still rejected. EP-2's tail row shows `mean_lift ≈ 0` for these bins.
- Only population B (ring bins where q_point ≈ 0.10 but q_lcb_raw ≈ 0) gets lifted, because only population B has: (a) a non-zero realized rate in its band, (b) a non-zero q_point ceiling, and (c) a raw q_lcb below the realized rate.

### 8.2 Settlement truth, not model assertion (why it is real edge, not manufactured)

EP-3 requires `model_log_score > market_log_score` and `model_brier < market_brier` on SETTLED events (INV-CAL-2: grade through `settlement_outcomes(VERIFIED)` only). These are **after-the-fact settlement outcomes** — not the model's own predictions about itself. The model cannot game these scores: a bin either settled as the winner or it did not. If the model consistently places more probability on the eventual winner than the market does (positive log-score gap), it has information the market lacks — correct-bin edge by definition.

The vs-market benchmark (benchmark §3.1) is the test: if the corrected q_lcb merely tracks the market price (or moves in the wrong direction), the log-score gap is zero or negative and the verdict is NO_EDGE regardless of win-rate.

### 8.3 Walk-forward proof, not in-sample overfit (why it survives out-of-sample)

The 90-day embargo (§4.1) and the INV-CAL-3 walk-forward discipline ensure the isotonic calibration map cannot "memorize" the evaluation window. The evaluation-window LICENSED verdict (EP-3) is proof that the calibration generalizes to events the fit never saw. This directly addresses the replacement-form §4 finding that in-sample EV inflated to +1.2¢ while holdout collapsed to −2.7¢ (P1 keep-list). A fix that passes EP-3 on the holdout window has NOT repeated that failure mode.

### 8.4 The q_lcb-fix-specific check (not just general model quality)

The critical comparison in EP-3 is `q_lcb_shadow` (the N7-corrected bound) vs the market. It is NOT comparing q_point to the market — the point q was already confirmed honest (Σq=winners, S1; P1 §1.3). What EP-3 tests is whether the LOWER BOUND correction — specifically, the UP arm moving q_lcb from ≈0 toward the settlement-calibrated target — results in a lower bound that accurately reflects where the model has real settlement-backed advantage. A fix that merely inflates q_lcb without settlement backing would show `realized_wr ≈ q_lcb_shadow` (well-calibrated at the lower bound) but NOT `model_log_score > market_log_score` (because the whole-distribution information gain is what the log-score measures). EP-3 catches this because the log-score is computed on the WINNING bin's shadow q — if the UP arm lifted the wrong bin's q_lcb, the winning bin's log-score contribution is unchanged.

---

## 9. THE TWO-SIDED HONEST VERDICT

This gate is explicitly designed to emit two first-class outcomes, neither of which is a failure:

**Outcome 1 — LICENSED (the ring edge is real):** EP-1 shows R/E ≥ 2.0 on the near-zero band; EP-2 shows ring lift > 0 and tail ≈ 0; EP-3 shows log-score + Brier beat market at n≥200 with positive after-cost EV lower CI; EP-4 shows RPS advantage. The band is promoted. G5 (EP-5) then proves it in traded settlement at n≥30 fills, >51% after-cost. **This is the genuine first proven correct-bin alpha fill.**

**Outcome 2 — NO_EDGE (the market is efficient on the ring):** EP-1 shows R/E collapses to ~1.0 when population A is excluded (the q_lcb crush was population-A-driven, not ring-bin suppression); or EP-3 shows the corrected q_lcb does not beat the market on log-score/Brier even at n≥30; or the after-cost EV lower CI never clears zero. **This is the honest law-1 verdict: the ring edge does not exist at tradeable size after the 1¢ fee. DONE = stand down on the lane, not engineer around it.**

Both are dated, numeric, settlement-grounded verdicts with specific query outputs. Neither is declared — both are proven. The gate's value is that it makes "the market is efficient" as legitimate a DONE as "the edge is real and trading."

---

## 10. RISKS AND ANTIBODIES

| Risk | Guard | Detection |
|---|---|---|
| UP arm lifts population C (below-market model) | `min(target, q_point)` clamp; EP-2 `shadow ≤ q_point` check | `test_qlcb_never_exceeds_point` RED-on-revert (W-QLCB §4.2 test 3) |
| Gate licenses on noise at n<200 | N_eff floor ≥ 200 hard-coded in EP-3 LICENSED predicate | EP-3 `n_events < 200 → INSUFFICIENT_DATA` regardless of score |
| In-sample overfit (evaluation sees fit data) | 90-day embargo; fit on `target_date < 2025-10-01` strictly | the embargo cutoff is a constant; any PR that changes it is a red flag |
| Row-count inflation (40× Taipei pattern) | INV-CAL-1 dedup GROUP BY in every query | `test_no_event_double_counts` (W-EDGE-LOCATE §7 test 1) |
| Settlement mis-grading (wrong bin-match / preimage) | Grade through `grade_receipt` (Alt B, §3.3 W-EDGE-LOCATE); EP-2 `would_have_won` vs spine cross-check | `test_grade_matches_spine` (W-EDGE-LOCATE §7 test 2) |
| buy_no base-rate re-labelled as alpha | buy_no excluded from ring gate scope; EP-3 vs-market Brier benchmark | `test_base_rate_buy_no_is_no_edge` (W-EDGE-LOCATE §4.2 INV-CAL-4 test) |
| Arm fires on buy_no favorites re-entering | `direction = 'buy_yes'` scope filter in every EP query | test asserts no buy_no row ever reaches LICENSED in EP-3 |

---

## 11. INTEGRATION WITH THE CRITICAL PATH

W-EDGEPROOF's queries are the GATING LAYER in the critical path (`P2_sequence_and_critical_path.md §4.1`):

- **EP-1** is Gate G1 (the §4.1 sub-population backtest) — run before N7 is built (Wave 2). It decides whether the UP arm ships at all.
- **EP-2** runs continuously from N7's first shadow cycle (Wave 2 onward) — it validates the fix is lifting the right bins.
- **EP-3 + EP-4** are Gate G4 (the E2 LICENSE verdict) — they determine when a ring band transitions from `INSUFFICIENT_DATA` to `LICENSED` (Wave 3). They gate N7 shadow→live and ARM.
- **EP-5** is Gate G5 — the traded-settlement final confirmation that constitutes DONE.

This workstream does NOT add new gates. It SPECIFIES the exact queries and acceptance numbers that existing gates (G1, G4, G5) require to fire. The gates were already named in the strategy-of-record (Thrust 6) and the critical-path plan; W-EDGEPROOF is the operationalization that makes them testable rather than declared.

---

## 12. SELF-CHECK

This gate is systematically correct by the operator's law-2 test (not a 1-order hack) because it deliberately refuses to license anything on the current n≈5 ring cohort and explicitly holds INSUFFICIENT_DATA as the first output. It manufactures no edge: the UP arm's structural clamp (`min(target, q_point)`) prevents q_lcb from ever exceeding the model's honest point belief, and EP-2 verifies the clamp is holding on every run. It closes the architectural blind spot (the "everything only lowers" disease, W-QLCB §0.4) by adding a measurement instrument that can detect when the corrected bound is STILL too low relative to settlement — not by loosening `capital_efficiency` but by requiring the bound to earn a LICENSED verdict through settlement evidence.

The one honest weakness: the entire gate's value depends on the ring distinct-event cohort growing from ~5 to ≥200. If the market stops offering ring mispricings, or if W-QLCB's shadow accrues events and they all lose (model not better than market on the ring), EP-3 will correctly emit NO_EDGE. That is not a defect of the instrument — it is the instrument doing its job and telling the operator, with 200 settled events and a dated JSON, that the suppressed-alpha pool the system chased for 100 patches does not exist at tradeable size after friction. The gate is designed to be right regardless of which direction the evidence points.

*End of P2 W-EDGEPROOF. Read-only plan; no production code or daemon changed. Every empirical claim cited to file:line, artifact, or query+counts from the sibling planning documents established this session.*
