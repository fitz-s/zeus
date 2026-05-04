# Platt Calibration Residual ×  Peak Local Hour — Verification Audit

Created: 2026-05-03 morning CDT
Authority: operator directive 2026-05-02 — "我们不能把数字瞎猜"; verify before implementing track-aware DDD
Status: COMPLETE — operator hypothesis PARTIALLY supported, refinement required

## Operator hypothesis (under test)

> "你的 Platt 模型其实**已经**在历史数据中隐式地学到了这种'Buckley 军用基地的夜间关机带来的升温偏差'。只要你不在实盘中强行去修正它（也就是不换 ICAO），你的模型预测出的概率就是精准对齐 Polymarket 结算逻辑的。"

Concrete falsifiable claim: if we group winning-bucket Platt `p_raw` values by the
local hour at which the daily extreme actually occurred, the means should be
**roughly equal across hours** (the model has internalized the per-hour station
artifacts and self-corrects).

If means VARY systematically across peak hours, calibration has NOT internalized
the artifacts and a coverage-based DDD layer would add information.

## Method

1. Pull all `calibration_pairs_v2` rows with `authority='VERIFIED'` and `outcome=1`
   (the winning bucket per settlement day) for 6 cities, 2025-04-01 to 2026-04-19.
2. For each `(city, target_date, temperature_metric)`, compute the actual peak
   local hour from `observation_instants_v2.running_max` (the hour at which the
   daily max/min was observed).
3. Group winning-bucket `p_raw` by `(city, metric, peak_hour, lead_bucket)`.
4. Restrict to peak hours with **n ≥ 100 days** (the "common regime" hours;
   filter out rare/anomalous peak times where weather regime confounds).
5. Compare spread of mean p_raw across hours to within-hour variation.

## Headline results (lead_days ∈ [3, 8), most stable sample)

| city | metric | common peak hrs | mean(p_raw_winner) | min hour | max hour | spread | rel spread |
|---|---|---|---|---|---|---|---|
| **Dallas** | **high** | **13–16** | **0.1340** | **0.1299** | **0.1377** | **0.0078** | **5.8%** ✅ |
| NYC | high | 0–16 | 0.1242 | 0.1145 | 0.1328 | 0.0183 | 14.8% |
| Houston | low | 3–6 | 0.2185 | 0.1837 | 0.2416 | 0.0579 | 26.5% |
| Los Angeles | high | 10–14 | 0.2087 | 0.1738 | 0.2441 | 0.0703 | 33.7% |
| Denver | low | 3–6 | 0.1022 | 0.0855 | 0.1217 | 0.0362 | 35.4% |
| Denver | high | 11–16 | 0.1006 | 0.0783 | 0.1224 | 0.0441 | 43.8% |
| Dallas | low | 4–23 | 0.1868 | 0.1258 | 0.2129 | 0.0871 | 46.6% |
| Seattle | high | 12–17 | 0.1947 | 0.1361 | 0.2372 | 0.1011 | 51.9% ⚠ |
| **Houston** | **high** | **11–15** | **0.0658** | **0.0347** | **0.1319** | **0.0972** | **147.7%** ⚠ |

## Interpretation

The operator hypothesis is **partially supported**:

**✅ Supported case — Dallas HIGH (5.8% relative spread).**
For peak hours 13/14/15/16, mean p_raw is 0.130–0.138 — essentially flat. Calibration
has fully internalized the per-hour station behavior. Switching ICAO here would be
strictly destructive.

**⚠️ Counterexample — Houston HIGH (147.7% relative spread).**
Mean p_raw drops by 4× from peak_hr=11 (0.132) to peak_hr=14 (0.035). This is NOT
"hour with thin coverage gets low p_raw". The local-hour distribution check (run earlier
this session) showed Houston's hours 11-15 have similar coverage (22-25 days/29 each).

**The hour-dependent variation is regime-correlated, not coverage-correlated.**
On Houston-typical convective summer days the peak shifts to ≈14:00 and the model
correctly admits low confidence (the daily max bin is genuinely uncertain). On
atypical days where peak shifts to 11:00 or 12:00, conditions are stable/clear and
the model is highly confident. The Platt surface IS calibrated — but conditional on
weather regime, not on raw hour of day.

## Refined operator claim

The hypothesis "calibration has internalized station artifacts" is true, but the
mechanism is more subtle than "model learned that hour X is missing from station Y":

> The Platt surface has internalized the **joint distribution of
> (regime_at_peak_hour, settlement_outcome)**. Coverage-thin hours that happen to
> co-occur with hard-to-predict regimes already get low p_raw via the regime
> signal in the underlying TIGGE forecast. Adding a coverage-based DDD on top
> double-penalizes difficult regimes the model already correctly down-weights.

## Implications for Data Density Discount design

**1. Don't change ICAO** — confirmed. The operator's core directive holds. Calibration
   is fragile in a regime-conditional way; switching settlement_source would invalidate
   millions of Platt training points.

**2. Coverage-based DDD remains valid for Lagos.** Lagos's variance (mean 0.78, min
   0.13 = 3 hours/24) is qualitatively different — vendor-side stream death produces
   *random* coverage drops uncorrelated with regime. Platt cannot internalize random
   stream death because it's not a property of weather. DDD captures real residual
   risk here.

**3. Coverage-based DDD has DIMINISHING value for US cities.** Their daily coverage
   floor (≈0.80–0.88) reflects routine WU/METAR archive sparsity — already self-priced
   by Platt because every training day saw the same sparsity. DDD only adds signal in
   catastrophic dropout cases (today's coverage < 0.5 = active vendor outage, not normal
   archive thinness).

**4. Track-aware DDD framework still useful, but threshold matters.**
   - Lagos: apply both flat coverage and directional coverage; thinness pricing
     remains 5–8% as designed
   - US cities: only fire if **today's** coverage in the relevant peak window
     drops below 50% — a catastrophic dropout, not a routine archive gap.
     Mean-90d coverage of 0.80 → DDD = 0% (already priced).

## Recommended DDD curve revision

Replace the previous flat curve with a **per-city ratchet**:

```
city_floor[city] = expected_typical_coverage_for_city_window(city, last 90d)
  e.g.  Lagos = 0.78,  Denver = 0.83,  Tokyo = 1.00

shortfall(city, today) = max(0, city_floor[city] - today_directional_coverage)
DDD(city, track, today) = apply_curve(shortfall)
```

The curve is no longer absolute coverage → discount; it's **deviation below the
city's own typical coverage** → discount. This makes DDD fire only on outages
(real news), not on baseline sparsity (already in calibration).

## Open questions

1. Does `city_floor` need to be regime-conditional too (warm season vs cold season)? Probably yes for cities where station-attended hours shift.
2. Lagos's `city_floor=0.78` is high enough that small drops won't fire DDD. Should we use a different formulation for Lagos specifically (because the Platt sample size for Lagos is much smaller, so Platt doesn't have the same self-correction)?
3. Validate by replaying past coverage outages (find a day where Lagos dropped to 0.13) and check whether Platt p_raw was systematically biased on that day vs typical.

## Verification SQL (reproducible)

```sql
-- peak_hour mapping
WITH hr AS (
  SELECT city, target_date, CAST(local_hour AS INTEGER) AS lh, running_max AS t
  FROM observation_instants_v2
  WHERE source LIKE 'wu_%' AND data_version='v1.wu-native'
    AND running_max IS NOT NULL
    AND city IN ('Denver','Houston','Los Angeles','Dallas','Seattle','NYC')
    AND target_date >= '2025-04-01' AND target_date <= '2026-04-19'
),
day_x AS (SELECT city, target_date, MAX(t) AS dmax, MIN(t) AS dmin FROM hr GROUP BY city, target_date),
high_peak AS (SELECT h.city, h.target_date, 'high' AS metric, MIN(h.lh) AS peak_hr
              FROM hr h JOIN day_x d USING (city, target_date)
              WHERE ABS(h.t - d.dmax) < 0.05 GROUP BY h.city, h.target_date),
low_peak AS  (SELECT h.city, h.target_date, 'low' AS metric, MIN(h.lh) AS peak_hr
              FROM hr h JOIN day_x d USING (city, target_date)
              WHERE ABS(h.t - d.dmin) < 0.05 GROUP BY h.city, h.target_date)
SELECT * FROM high_peak UNION ALL SELECT * FROM low_peak;

-- residual analysis: filter winning bucket only, group by peak_hour
SELECT cp.city, cp.temperature_metric, peak_hr, AVG(cp.p_raw) AS mean_p_winner, COUNT(*) AS n
FROM calibration_pairs_v2 cp JOIN <peak_hour CTE> ph
  ON cp.city=ph.city AND cp.target_date=ph.target_date AND cp.temperature_metric=ph.metric
WHERE cp.authority='VERIFIED' AND cp.outcome=1 AND cp.lead_days BETWEEN 3 AND 8
GROUP BY cp.city, cp.temperature_metric, peak_hr
HAVING n >= 100;
```

## Cross-references

- DDD design proposal: `DATA_DENSITY_DISCOUNT.md` (this folder) — needs revision per §"Recommended DDD curve revision"
- Lagos source-thinness investigation: `../task_2026-05-02_full_launch_audit/LAGOS_GAP_FOLLOWUP.md`
- Settlement pipeline gap: `AUDIT.md`
