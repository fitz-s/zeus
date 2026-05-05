# Data Density Discount — Design Proposal

Created: 2026-05-02 evening CDT
Last revised: 2026-05-03 (SUPERSEDED by canonical reference)
Authority: operator directive 2026-05-02 — "把贫瘠度转化为一种数学折价";
  refined 2026-05-03 with three-failure-mode hardening (Boiled Frog, Noise vs
  Outage, Small-Sample Multiplier).
Status: SUPERSEDED — canonical specification is now
  `docs/reference/zeus_oracle_density_discount_reference.md` §6.
  This document retains the original flat-coverage proposal for historical
  context. **DO NOT IMPLEMENT THE FORMULAS BELOW** — they have known failure
  modes (boiled frog, noise over-reaction, missing small-sample multiplier).
  Use the reference document's §6 specification instead.

## Principle

The current oracle penalty system measures **Mismatch** between our oracle-time
WU snapshot and PM settlement value. This catches pipeline drift (snapshot vs PM)
but is **structurally blind to source thinness** because PM also reads WU — when
WU is thin, both sides agree on the thin number, and the bridge measures 0% error
even though the trade was made on incomplete data.

Operator named this "fake safety": `Mismatch == 0% does NOT mean Oracle is safe`.

The fix: introduce a parallel **Data Density Discount (DDD)** signal driven by
historical observation coverage, then take the max:

$$
\textbf{Total Oracle Risk} = \max(\text{Mismatch Rate},\ f(\text{Average Coverage Ratio}))
$$

For Lagos: Mismatch = 0% (verified bridge has no mismatch on the 5 historical
overlap days), but Average Coverage = 0.64 (vs 0.97-1.0 for healthy cities).
DDD discount fills the gap so Lagos doesn't trade at zero penalty.

## Definitions

- `coverage_ratio(city, date) = count(distinct utc_timestamp in observation_instants_v2
  where source = expected_source_for_city(city) and target_date = date) / 24`,
  capped at 1.0
- `Average Coverage Ratio = mean(coverage_ratio over last 90 days)` per city
- A "thin day" = `coverage_ratio < 22/24 ≈ 0.917`

## Proposed discount function f(coverage)

| Coverage range | DDD value | Kelly multiplier | Status |
|---|---|---|---|
| ≥ 0.95 | 0.00 | 1.00× | OK |
| 0.85 ≤ x < 0.95 | linear 0% → 2% | 0.98–1.00× | INCIDENTAL |
| 0.70 ≤ x < 0.85 | linear 2% → 5% | 0.95–0.98× | CAUTION |
| 0.55 ≤ x < 0.70 | linear 5% → 8% | 0.92–0.95× | CAUTION |
| < 0.55 | 9% (cap) | 0.91× | CAUTION ceiling |

Design rationale:
- DDD never exceeds 9% — it stays in CAUTION, never auto-BLACKLISTs. Per operator
  directive: thinness is priced as a discount, not a kill switch.
- Mismatch retains the existing thresholds (>10% → BLACKLIST). Pipeline drift
  IS a kill-switch trigger; thinness alone is NOT.
- Curve passes through (0.95, 0%) so healthy cities pay nothing, and (0.70, 5%)
  so notably-thin cities pay meaningful but not punitive size-down.

### Worked examples (real data)

| City | Mean Cov | DDD | Mismatch (current) | Total Oracle Risk | Kelly mult |
|---|---|---|---|---|---|
| Lagos | 0.64 | 7.5% | 0% | 7.5% | 0.925× |
| Denver | 0.89 | 1.2% | unknown (low data) | 1.2% | 0.99× |
| LA / Seattle | 0.92 | 0.6% | unknown | 0.6% | 0.99× |
| Dallas / SF | 0.95 | 0% | low | 0% | 1.00× |
| NYC / Tokyo / Singapore | ≥0.97 | 0% | low | 0% | 1.00× |

Lagos lands in clear CAUTION (~7.5% size-down) without blacklist — exactly the
operator-specified outcome.

### Coverage thresholds — sensitivity check

If we tighten the curve so that Lagos lands at 8.5% (closer to BLACKLIST ceiling),
the kelly mult drops to 0.915 and the linear curve also pushes Denver/LA above
1% from current 0.6-1.2%. If we soften so Lagos lands at 5%, Denver/LA drop to
near zero, which loses the structural signal that they ARE notably thin. The
proposed curve is a middle ground; operator should pick the inflection point
based on risk-tolerance preference.

## Bridge integration: skip thin-coverage days from mismatch computation

Mandated by operator: "明确忽略 observation_instants_v2 覆盖时长不足多少小时的日期"
(explicitly ignore dates where observation_instants_v2 coverage is below threshold).

Modification to `scripts/bridge_oracle_to_calibration.py`:

```python
# Pseudocode addition before mismatch counting:
THIN_COVERAGE_THRESHOLD = 22 / 24  # 22 hours

for date_str, settlement in settlements.items():
    primary_src = expected_source_for_city(city)
    coverage = count_distinct_hours(city, date_str, primary_src) / 24
    has_verified_fallback = (
        # at least one allowed fallback source has 22+ distinct hours for this date
        any(count_distinct_hours(city, date_str, fb) >= 22
            for fb in allowed_sources_for_city(city) if fb != primary_src)
    )
    if coverage < THIN_COVERAGE_THRESHOLD and not has_verified_fallback:
        continue  # measuring this day's mismatch is measuring WU's downtime, not oracle
    # ... existing mismatch comparison ...
```

Rationale: if WU's data was thin on a given day AND no verified fallback exists,
including that day in the mismatch denominator measures vendor downtime, not
UMA accuracy. Operator's exact phrasing: "你测量的其实是 WU 的宕机时间，而不是 UMA
预言机的准确性".

## Day-0 dynamic check (live circuit breaker)

Mandated: historical mean isn't enough — Lagos has 121 gap-days, coverage variance
is huge. Need an intraday signal at decision time.

Proposed addition to evaluator (or risk layer) at trade-decision time:

```python
def today_coverage_so_far(city: str, now_utc: datetime) -> float:
    today_local = now_utc.astimezone(ZoneInfo(city_cfg.timezone)).date()
    distinct_hours = count_distinct_hours(city, today_local.isoformat(), primary_src)
    elapsed_hours = (now_utc - day_start_utc(today_local, city_cfg.timezone)).total_seconds() / 3600
    if elapsed_hours <= 0:
        return 1.0  # too early to judge
    return min(distinct_hours / elapsed_hours, 1.0)
```

Decision rule:
- If `today_coverage_so_far < 0.40` AND elapsed_hours >= 6 → reject the trade with
  `entries_blocked_reason="day0_observation_gap"`.
- Independent of historical DDD, independent of Mismatch — this is real-time
  circuit-breaking on TODAY's data quality.

Operator rationale: "如果系统在中午检查发现今天的 WU 观测点只有零星几个，即使你的物理预报
（TIGGE）给出了极高胜率，此时也必须动态缩减仓位甚至熔断当天的交易".

The trigger threshold (0.40 + ≥6 elapsed hours) is conservative — won't fire
before noon, won't fire if ≥40% of elapsed hours were captured. Open question:
should the threshold scale by city (Lagos tighter, NYC looser) or be a uniform
floor?

## Integration with existing oracle_penalty.py

Current file structure (oracle_penalty.py:_classify_rate):
```python
if rate > CAUTION_THRESHOLD:    # 0.10
    return BLACKLIST (kelly_mult = 0)
elif rate > INCIDENTAL_THRESHOLD:  # 0.03
    return CAUTION (kelly_mult = 1.0 - rate)
elif rate > 0:
    return INCIDENTAL (kelly_mult = 1.0)
else:
    return OK (kelly_mult = 1.0)
```

Required changes:

1. **Add a DDD computation module** (new file `src/strategy/data_density_discount.py`)
   that exposes `density_discount(city: str) -> float` returning the f(coverage) value.

2. **Modify `oracle_penalty._load`** to combine signals:
   ```python
   for (city, metric), info_from_bridge in bridge_results.items():
       ddd = density_discount(city)
       effective_rate = max(info_from_bridge.error_rate, ddd)
       result[(city, metric)] = _classify_rate(effective_rate)
   ```

3. **Bridge writes BOTH numbers** to `data/oracle_error_rates.json` for transparency:
   ```json
   {
     "Lagos": {
       "high": {
         "mismatch_rate": 0.0,
         "density_discount": 0.075,
         "oracle_error_rate": 0.075,
         "status": "CAUTION",
         "coverage_mean_90d": 0.64
       }
     }
   }
   ```
   The combined `oracle_error_rate` is what the consumer reads; the components are
   for audit / debugging only.

## Lagos / Africa / Underdeveloped-station clause

Add an explicit note to the bridge logic + `oracle_penalty.py` docstring:

> **Fake-safety guard**: Mismatch == 0% does NOT mean Oracle is safe.
> A city with thin source coverage may have mismatch=0% only because both the
> snapshot and PM read the same incomplete source. Always evaluate Total Oracle
> Risk = max(mismatch, density_discount). Cities flagged for special handling
> (currently: Lagos, future: any city with `coverage_mean_90d < 0.85`) should
> never have their oracle_error_rate computed from mismatch alone.

## Test plan

Each piece needs an antibody:

1. `tests/test_data_density_discount.py`:
   - `density_discount("Lagos")` returns ~0.075 ± tolerance given current DB
   - `density_discount("NYC")` returns 0
   - Curve continuity at boundaries (0.95, 0.85, 0.70, 0.55)
   - Returns 0.09 (cap) when coverage < 0.55
2. `tests/test_oracle_penalty_density_combined.py`:
   - Lagos: mismatch=0, ddd=0.075, effective=0.075, status=CAUTION, kelly=0.925
   - NYC: mismatch=0.01, ddd=0, effective=0.01, status=INCIDENTAL, kelly=1.0
   - "Fake safety" antibody: mock-construct a city with mismatch=0 + low coverage,
     assert effective rate > 0 (i.e., DDD activated)
3. `tests/test_bridge_thin_coverage_skip.py`:
   - Construct a fixture day with 5/24 hours WU coverage; assert bridge skips it
   - With 22/24 hours, assert bridge counts it
4. `tests/test_day0_observation_gap_blocker.py`:
   - With elapsed_hours=8 and observed_hours=2 (ratio=0.25), assert reject
   - With elapsed_hours=8 and observed_hours=4 (ratio=0.50), assert allow

## Implementation order

1. Operator approves the curve numbers in this doc.
2. Implement `src/strategy/data_density_discount.py` + tests.
3. Implement bridge thin-coverage skip + tests.
4. Wire `data_density_discount` into `oracle_penalty._load` + tests.
5. Run bridge to populate `data/oracle_error_rates.json`.
6. Day-0 circuit breaker is a separate slice (touches evaluator/cycle_runtime —
   coordinate with operator's in-flight TIGGE PR to avoid merge conflict).

## Open questions for operator

1. Approve the DDD curve as proposed, or pick different inflection points?
   (Lagos at 7.5%, Denver at 1.2% are the load-bearing examples.)
2. Day-0 circuit breaker threshold: uniform 0.40 floor, or per-city scaled?
3. The `THIN_COVERAGE_THRESHOLD = 22/24` for the bridge skip rule — keep at 22h,
   or different (e.g., 20h ≈ 0.83 to be more permissive)?
4. Should `data/oracle_error_rates.json` continue to be written by the bridge as
   sole writer, or should the DDD computation be a co-tenant writer? (Currently
   the design has the bridge write both signals together.)

## Cross-references

- Settlement pipeline status: `AUDIT.md` (this folder)
- Lagos source-thinness investigation: `../task_2026-05-02_full_launch_audit/LAGOS_GAP_FOLLOWUP.md`
- Existing penalty code: `src/strategy/oracle_penalty.py`
- Bridge writer (sole writer to oracle_error_rates.json): `scripts/bridge_oracle_to_calibration.py`
