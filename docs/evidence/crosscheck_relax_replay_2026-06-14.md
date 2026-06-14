# GFS Crosscheck Relax — Decision-Time-Price Settlement Replay

- Created: 2026-06-14
- Authority basis: crosscheck relax decision (phase-2 investigation worfmboc9)
- Mode: READ-ONLY (sqlite3 SELECT + code read). No edits, no writes.
- Author: scientist agent

---

## TL;DR Verdict

**INCONCLUSIVE-leaning-MOOT for the historical window; the relax is forward-looking only.**

Variants (a) GFS-fetch-threw, (b) GFS-unusable-ensemble, and (c) GFS-target-hours-missing —
the three the relax proposal would loosen — have **ZERO historical firings** in the entire
instrumented `no_trade_events` history (2026-05-20 → 2026-05-28). **100% of the 141 crosscheck
blocks are variant (d) `SOURCE_COMPARABILITY_FAILED`**, which the proposal does NOT touch.
So there is **no historical settlement evidence that bears on relaxing a/b/c at all** — the
blocked-candidate population for a/b/c is empty.

For completeness, I replayed the only crosscheck-blocked population that DOES exist (variant d,
97 probability traces). It does **not** support relaxing even variant d: of 75 settled blocked
candidates the ECMWF modal bin won only 17 (per-trace 22.7%; deduped 7/19 = 36.8%), and
decision-time market price is **absent (0.0) at the ECMWF modal bin for 76/97 traces (78%)**,
so a real economic q_lcb−price edge is **not reconstructable** for the bins that carried signal.
Where a real price did exist, the candidate either won at a near-saturated price (no edge left)
or lost. No blocked candidate exhibited "real price + positive edge + win".

---

## 1. Schema Reconnaissance

Code path verified at `src/engine/evaluator.py` inside `if not is_day0_mode:` (line 4889).
Four variants, exactly as briefed:

| Variant | evaluator.py | reason enum | availability_status | reason_detail signature |
|---|---|---|---|---|
| (a) GFS fetch threw | 4899 `except Exception` | CROSSCHECK_UNAVAILABLE | `_availability_status_for_error(e)` | `"…crosscheck unavailable: {e}"` (has `: ` + exc text) |
| (b) unusable ensemble | 4916 `is None / not validate` | CROSSCHECK_UNAVAILABLE | `DATA_UNAVAILABLE` | `"…crosscheck unavailable"` (no colon) |
| (c) target-hours missing | 4942 | **GFS_CROSSCHECK_UNAVAILABLE** | `DATA_UNAVAILABLE` | `"GFS crosscheck unavailable"` |
| (d) source-not-comparable | 4983 | CROSSCHECK_UNAVAILABLE | `DATA_UNAVAILABLE` | `"SOURCE_COMPARABILITY_FAILED:{json}"` |

All four emit `p_raw`, `p_cal`, `p_market` into the returned `EdgeDecision`. GFS is a pure
confirmation gate — the ECMWF posterior is fully computed before the gate. Confirmed.

### Where decision-time price lives

`probability_trace_fact` (zeus-world.db), keyed by `decision_id`, persists the decision-time
vectors for blocked candidates:
- `p_market_json` — per-bin decision-time market price (THE decision-time price)
- `p_raw_json` — per-bin ECMWF raw posterior
- `bin_labels_json` — per-bin question text
- `agreement` = `'CROSSCHECK_UNAVAILABLE'` for all four variants
- `rejection_stage` = `'SIGNAL_QUALITY'`, `availability_status`, `direction`
- `edge_bin_p_market`, `near_tail_p_market` — NULL on the blocked path (gate returns before
  edge-bin telemetry is populated; confirmed 0/97 populated)

`no_trade_events` (zeus-world.db), keyed by natural key
`(market_slug, temperature_metric, target_date, observation_time, decision_seq)` — carries
`reason` + `reason_detail` (the variant discriminator) but **NOT decision_id**, so the join to
`probability_trace_fact` is on (city, target_date, metric), not an FK.

`settlement_outcomes` (zeus-forecasts.db): `(city, target_date, temperature_metric)` →
`winning_bin`, `settlement_value`, `authority`. Settled truth.

`trade_decisions` (zeus_trades.db): executed `price`, `fill_price`, `edge`, `p_posterior`,
`ci_lower/upper` for TRADED candidates only.

**Conclusion (Task 1):** decision-time price IS stored for blocked candidates via
`probability_trace_fact.p_market_json` — BUT see §3, it is 0.0 at the modal/signal bin in 78%
of cases, so it is present-but-unusable for the bins that matter.

---

## 2. Variant census — the decisive structural finding

```sql
SELECT reason, COUNT(*) n, MIN(observed_at), MAX(observed_at)
FROM no_trade_events
WHERE reason IN ('crosscheck_unavailable','gfs_crosscheck_unavailable')
GROUP BY reason;
```
```
crosscheck_unavailable | 141 | 2026-05-22T10:41:23 | 2026-05-28T06:10:11
(gfs_crosscheck_unavailable: 0 rows)
```

Variant discriminator (reason_detail):
```sql
SELECT CASE
  WHEN reason='gfs_crosscheck_unavailable' THEN 'c_target_hours_missing'
  WHEN reason_detail LIKE 'SOURCE_COMPARABILITY_FAILED%' THEN 'd_source_not_comparable'
  WHEN reason_detail LIKE '%crosscheck unavailable: %' THEN 'a_fetch_threw'
  WHEN reason_detail LIKE '%crosscheck unavailable' THEN 'b_unusable_ensemble'
  ELSE 'OTHER' END AS variant, COUNT(*) n
FROM no_trade_events
WHERE reason IN ('crosscheck_unavailable','gfs_crosscheck_unavailable')
GROUP BY variant;
```
```
d_source_not_comparable | 141
```

Triple-confirmed absence of a/b/c:
- `gfs_crosscheck_unavailable` rows = **0**  → variant (c) never fired
- traces with `availability_status != 'DATA_UNAVAILABLE'` = **0**  → variant (a) never fired
  (variant a is the only one using `_availability_status_for_error`)
- `crosscheck_unavailable` details containing `"unavailable: "` (exception text) = **0**
  → variant (a)/(b) exception path never produced a row

**Task 3 answer: variants a/b/c are dormant — exactly 0 firings.** The count of a/b/c-blocked
candidates in the data is **0**. The relax of a/b/c therefore has no historical settlement
footprint; its value is purely forward-looking (it only matters on future dates where the GFS
pipeline is genuinely absent, not merely incomparable).

The variant-d detail confirms the gate is doing real work, not a spurious data-absence block —
e.g. `{"comparable":false, "local_day_mapping_equal":false, "crosscheck_valid_window":
["2026-05-23T21:00","2026-05-24T20:00"], "crosscheck_source_id":"openmeteo_ensemble_gfs025"}`:
GFS and ECMWF resolved to **different local-day windows**. That is a genuine comparability
failure, categorically different from "GFS absent", and out of scope for the a/b/c relax.

### Instrumentation caveat (important)
`no_trade_events` spans only **2026-05-20 → 2026-05-28** (2952 rows total, latest row 17 days
stale as of 2026-06-14). The table is not currently being written. `probability_trace_fact` IS
current (latest 2026-06-14T05:37), but its 97 CROSSCHECK_UNAVAILABLE rows fall in the same
2026-05-22→05-28 window — no crosscheck blocks have been traced since. So the entire crosscheck
evidence base is one ~7-day window. Any verdict is sample-thin by construction.

---

## 3. Decision-time-price replay of the variant-d population (the only one that exists)

97 `probability_trace_fact` rows with `agreement='CROSSCHECK_UNAVAILABLE'`; all have
`p_market_json` and `p_raw_json` populated; all `availability_status='DATA_UNAVAILABLE'`,
`rejection_stage='SIGNAL_QUALITY'`, `trace_status='degraded_decision_context'`,
`direction='unknown'` (gate returns before direction is assigned).

### 3a. Price-at-signal-bin availability (reconstruction feasibility)
For each trace: modal bin = argmax(p_raw); price = p_market at that index (sqlite JSON1
`json_each`).
```
n_traces=97 | modal_bin_price_zero=76 (78%) | modal_bin_price_positive=21 (22%)
avg_modal_praw=0.66 | max_price_at_modal=0.984
```
The p_market vectors are systematically zero on the high bins and carry only tiny
floor-placeholder values (~0.001–0.05) on low bins — consistent with the "legacy fixed-p_market
bootstrap" fallback noted in evaluator.py comments. **For 78% of blocked candidates there is no
real decision-time price at the bin carrying the ECMWF signal**, so the conservative economic
edge `q_lcb − price` is not reconstructable there (q − 0 is mechanically "infinite" and
meaningless).

### 3b. Settlement replay (modal-bin would-have-been hit-rate)
Join to `settlement_outcomes` on (city, target_date, metric); metric inferred from candidate_id
(`highest…`→high). WIN = modal bin's question text contains the short `winning_bin` token
(tightened to `'be <winning_bin>'` to avoid e.g. "8" matching "38"):

| basis | settled | modal WIN | modal LOSS | hit-rate |
|---|---|---|---|---|
| per-trace (all 97) | 75 | 17 | 58 | **22.7%** |
| deduped (city/date/metric) | 19 | 7 | 12 | **36.8%** |

Per-trace is loss-weighted because losing candidates (Tokyo, Jeddah) re-fired many times.
Either basis is **at or below chance** and far below the prior probe's crude "traded 41%".

### 3c. The 21 positive-price candidates (economic detail)
```
city          tdate       q_modal  price   naive_edge  settled        hit
Lucknow       2026-05-29  0.87     0.984   -0.114      38°C or below  WIN  (negative edge)
Lucknow       2026-05-30  0.938    0.918    0.020      36°C or below  WIN  (~0 edge, saturated)
Seattle       2026-05-30  0.961    0.673    0.288      63°F or below  WIN
San Francisco 2026-05-30  0.577    0.022    0.555      64-65°F        LOSS (floor price)
Sao Paulo     2026-05-30  0.435    0.094    0.341      23°C           LOSS (floor price)
Tokyo         2026-05-25  0.514    0.003    0.510      25°C           LOSS (floor price)
…
```
The large "naive edges" are an artifact of floor-priced bins (price 0.003–0.09): q−price is big
only because there was effectively no market quote. The genuinely-priced winners (Lucknow 0.98,
0.92) leave **zero or negative edge** after the Polymarket taker fee
(`fee = 0.05·p·(1−p)`, `src/contracts/execution_price.py:278`). The floor-priced "edges" all
**lost**. **No candidate had real price + positive post-fee edge + win.**

### 3d. Matched traded-set comparison — NOT AVAILABLE
`trade_decisions` earliest row is **2026-06-05**; there are **0 trades in the
2026-05-22→05-28 crosscheck window** (the trades DB appears to have been reset on 2026-06-05).
So the prior probe's "traded 41%" cannot be reconstructed from the live trades DB for this
window, and no contemporaneous matched traded set exists. The comparison is empty.

---

## 4. Verdict (Task 4)

**Relaxing variants a/b/c: INCONCLUSIVE — and the historical settlement question is MOOT.**
a/b/c never fired (0 candidates), so no settlement evidence can confirm or refute "no win-rate
harm". The relax is a forward-looking change whose only effect is on future GFS-pipeline-gap
dates; the historical record is necessarily silent on it. Recommend treating it as a
forward-monitored change (re-instrument `no_trade_events`, which is currently stale, and watch
a/b/c firings + their settlement outcomes prospectively) rather than a settlement-backed one.

**Relaxing variant d (NOT proposed, but the only testable population): the evidence REFUTES it.**
The ECMWF modal bin won ≤37% of settled variant-d blocks, decision-time price is absent at the
signal bin 78% of the time, and where price existed there was no post-fee economic edge that won.
Variant d is catching genuine ECMWF/GFS local-day-window incomparability and the blocked ECMWF
posteriors were poorly calibrated in this window. Do not extend the relax to d.

### Caveats / limitations
- **Sample size**: one ~7-day window (2026-05-22→28), ≤19 deduped settled candidates. Thin.
- **Selection on settlement**: 13/32 candidates never settled (no VERIFIED truth) — possibly
  non-random (later target dates, smaller cities).
- **Modal-hit ≠ economic edge**: the report measures modal-bin settlement; a real economic
  replay needs a real decision-time price at the traded bin, which is missing for 78% here.
- **a/b/c absence is the dominant fact**: even a perfect replay can't speak to dormant variants.
- **no_trade_events staleness**: instrumentation stopped 2026-05-28; cannot see whether a/b/c
  fired more recently.

---

## Appendix — all queries are read-only (`?mode=ro`), cross-DB via ATTACH (no writes).
Primary tables: `probability_trace_fact` (zeus-world.db), `no_trade_events` (zeus-world.db),
`settlement_outcomes` (zeus-forecasts.db), `trade_decisions` (zeus_trades.db).
