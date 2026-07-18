# New-target serving latency: center/carrier vs. ENS shape — empirical proof

Created: 2026-07-18
Scope: READ-ONLY empirical measurement. No source code touched.

## Question under test

For a NEW target (the first time Zeus can serve a settlement probability for a
given city/target_date/temperature_metric), what is on the critical path of
serving latency — the center/carrier (OpenMeteo IFS9 deterministic anchor,
mu*) or the ENS shape (ensemble spread)?

A prior investigation claimed two numbers:
1. ~97.3% of new targets are CENTER-bound (not shape-bound) at first serve.
2. The OM anchor publishes AFTER the market entry in ~96.4% of traded cases.

This note reproduces (1) with real timestamps under two definitions and
**refutes (2) outright** — the data shows the opposite direction.

## Method

### Data sources (read-only, `?mode=ro`)
- `state/zeus-forecasts.db`: `forecast_posteriors`, `deterministic_forecast_anchors`.
- `state/zeus_trades.db`: `position_current`, `execution_fact` (ATTACHed to the
  forecasts connection for a single-connection cross-DB join).

### Grounding in code
`src/data/replacement_forecast_materializer.py`:
- `_posterior_source_available_at` (line 208) returns
  `max(baseline_b0 role possession, openmeteo_ifs9_anchor role possession)` —
  the posterior cannot exist before the slower of these two "carrier" roles.
- Lines 3584–3601: `available_at` is initialized to that carrier possession
  time, then — **only if** `bayes_precision_fusion_override.current_evidence_shape`
  is present (i.e. a causal ECMWF-ENS snapshot existed for this cell at
  materialization time) — it is widened to
  `max(carrier_avail, current_evidence_shape.source_available_at)`. This value
  is written verbatim to `forecast_posteriors.source_available_at`.
- Critically, `current_evidence_shape` is **optional** (`_read_current_evidence_shape`,
  line 1503, returns `None` on no matching `ensemble_snapshots` row) — a
  missing ENS shape does not block materialization; the row is still written
  as `FUSED_CENTER_ONLY_NORMAL` / `FUSED_NORMAL_PARTIAL`/`FULL` depending on
  what else fired. Shape is enrichment, not a gate. This structurally biases
  the population toward "center-bound," which the two classifications below
  make explicit rather than assume.

The provenance JSON of every posterior that used a current-evidence shape
embeds it verbatim at `provenance_json.bayes_precision_fusion.current_evidence_shape.source_available_at`
— this is the *exact* value the code compared against the carrier time (not a
re-derived approximation), so it was used directly instead of re-joining
`ensemble_snapshots` and risking picking a different row than the one the
fusion actually consumed.

### Definitions
- **First serve** per family (city, target_date, temperature_metric) =
  the `forecast_posteriors` row with `MIN(computed_at)` for that family.
- **Center avail** = `deterministic_forecast_anchors.source_available_at`
  reached via `forecast_posteriors.openmeteo_anchor_id`.
- **Shape avail** = `provenance_json.bayes_precision_fusion.current_evidence_shape.source_available_at`
  when present, else absent (no ENS shape entered this posterior at all).
- **Entry timestamp** per family = `MIN(execution_fact.filled_at)` for
  `order_role='entry'`, joined `execution_fact.position_id → position_current.position_id`
  to recover `(city, target_date, temperature_metric)` (`position_current`
  carries these columns directly — no market_id→city mapping needed).

### SQL / queries (representative; full logic in `latency_proof.py`, discarded scratch script)
```sql
-- first-serve row per family
SELECT p.* FROM forecast_posteriors p
JOIN (SELECT city, target_date, temperature_metric, MIN(computed_at) AS min_computed_at
      FROM forecast_posteriors GROUP BY city, target_date, temperature_metric) fm
  ON fm.city=p.city AND fm.target_date=p.target_date
 AND fm.temperature_metric=p.temperature_metric AND fm.min_computed_at=p.computed_at;

-- anchor availability
SELECT anchor_id, source_available_at FROM deterministic_forecast_anchors WHERE anchor_id IN (...);

-- first entry fill per family (cross-DB via ATTACH)
SELECT pc.city, pc.target_date, pc.temperature_metric, MIN(ef.filled_at)
FROM trades.execution_fact ef
JOIN trades.position_current pc ON pc.position_id = ef.position_id
WHERE ef.order_role='entry' AND ef.filled_at IS NOT NULL
GROUP BY pc.city, pc.target_date, pc.temperature_metric;
```

## Results

### Population
- 2,131 distinct (city, target_date, temperature_metric) families with a
  first-serve row in `forecast_posteriors` (all `runtime_layer='live'`;
  43,389 total posterior rows).
- Every first-serve row has a resolvable `openmeteo_anchor_id` → anchor row
  (0 NULL FKs, 0 dangling FKs) — center-avail coverage is 100%.
- Only **197/2,131 (9.2%)** of first-serve rows carry an embedded ENS shape
  (`current_evidence_shape` present) — i.e. in **90.8%** of cases the ENS
  shape was simply absent/uncausal at first-serve time and could not have
  gated anything.

### Headline 1 — center-bound %

Two honest ways to read "center-bound," because the shape is not always in
the race:

| Definition | n | center-bound | % | Wilson 95% CI |
|---|---|---|---|---|
| **Contest-only** (both center and shape data exist; which is *actually* later) | 197 | 171 | **86.8%** | 81.4% – 90.8% |
| **Broad** (shape-absent counts as trivially center-bound, since there is no competing input at all) | 2,131 | 2,105 | **98.8%** | 98.2% – 99.2% |
| Shape-bound (contest-only) | 197 | 26 | 13.2% | 9.2% – 18.6% |
| Ties | 197 | 0 | 0.0% | — |

The prior claim of **~97.3%** sits between these two framings — closest to
the **broad** definition (98.8%), which is the more natural reading of "what
gates the FIRST SERVE" given that shape is optional enrichment, not a
required input. Under the strict "when both inputs are actually contesting"
framing the number is materially lower (86.8%), because in the 9.2% of cases
where an ENS shape *did* enter, it was late relative to the anchor 13.2% of
the time. **Verdict: CONFIRMED (order of magnitude), with a refinement** —
center dominance is closer to ~87–99% depending on whether "shape never
showed up" counts as center-bound; it is not uniformly ~97.3% once shape is
actually present.

`center_avail − shape_avail` latency (hours, n=197, contest-only):
mean=10.9h, median=10.1h, p10=−1.7h, p90=11.2h, min=−2.6h, max=98.1h.
(Negative = shape arrived later than center — the 26 shape-bound cases.)

Two rows (2/197) show `final_avail` earlier than `max(center_avail,
shape_avail)` recomputed here — consistent with the `baseline_b0` role
(not modeled in this proof; the code's carrier time is
`max(baseline_b0, openmeteo)`, and this analysis only pulled the OpenMeteo
anchor half) occasionally being controlling instead. This affects 2 of 197
rows (1.0%) and does not change either headline conclusion.

### Headline 2 — anchor-after-entry % — **REFUTED**

| | n | anchor AFTER entry | % | Wilson 95% CI |
|---|---|---|---|---|
| Traded families matched | 170 | **0** | **0.0%** | 0% – 2.2% |

Of 209 distinct traded families in `zeus_trades.db`, 170 (81.3%) joined to a
first-serve record with a resolvable center-avail timestamp. In **zero** of
170 matched cases did the OM anchor become available after the family's
first entry fill. The direction is the opposite of the ~96.4% claim: the
anchor is available **before** entry in 100% of matched cases, with a
substantial lead:

`anchor_avail − entry_fill` (hours, n=170, negative = anchor before entry):
mean=−108.9h, median=−25.1h, p10=−478.0h, p90=−6.5h, min=−637.9h, max=−0.09h.

Spot-checked examples (center_avail, first-serve computed_at, entry fill):
```
Ankara      2026-07-01 high: center=2026-06-29T04:37:54Z  serve=2026-06-29T04:41:14Z  entry=2026-06-29T22:04:00Z
Ankara      2026-07-13 high: center=2026-07-11T08:52:21Z  serve=2026-07-11T08:54:42Z  entry=2026-07-13T22:02:18Z
Beijing     2026-06-14 high: center=2026-06-12T08:19:52Z  serve=2026-06-12T09:09:44Z  entry=2026-06-12T11:44:09Z
BuenosAires 2026-07-02 high: center=2026-07-01T02:10:09Z  serve=2026-07-01T02:27:51Z  entry=2026-07-01T22:17:15Z
```
In every sample, `serve` follows `center` by minutes (confirming center gates
the *first serve*), while `entry` follows `serve` by hours-to-days
(confirming entry is gated by something else entirely — edge/price
thresholds, not forecast-input readiness).

### Join coverage / honesty notes
- 1,961/2,131 first-serve families (92.0%) were never traded at all (or their
  trade rows didn't survive to `position_current`) — these contribute nothing
  to headline 2 and are excluded, not imputed.
- 39/209 traded families (18.7%) didn't match a first-serve record with a
  resolvable center_avail — likely city-name or dedup edge cases in the
  first-serve join; excluded, not imputed.
- The "entry timestamp" used is Zeus's own **order fill time**
  (`execution_fact.filled_at`), per the task's specified definition — not the
  market's own open/creation time. If the original 96.4% claim intended
  "market becomes tradeable" rather than "Zeus's own fill," that is a
  different, unmeasured quantity; this proof does not speak to it.

## VERDICT

**Headline 1 (center-bound %) — CONFIRMED IN MAGNITUDE, REFINED.** Center
dominance at first serve is real: 98.8% (95% CI 98.2–99.2%) under the natural
"what gates a first serve at all" framing, or 86.8% (95% CI 81.4–90.8%)
restricted to the 9.2% of cases where an ENS shape actually contested. The
~97.3% prior figure is closest to the broad framing and is a reasonable
description of the population; it materially overstates center dominance in
the minority of cases where shape data exists.

**Headline 2 (anchor-after-entry %) — REFUTED.** Measured 0.0% (0/170, 95%
CI 0–2.2%), the opposite direction of the claimed ~96.4%. The OM anchor is
available a median of ~25 hours (up to several weeks) **before** the first
trade entry in every matched case. This means the OM-anchor publication path
is **not** the latency lever gating entry timing — by the time any trade is
entered, the anchor has long since arrived. The real gate between first-serve
and entry is downstream (edge/price attractiveness thresholds), not
center/carrier possession.

**Combined implication:** the center/carrier *is* the critical path for how
fast a NEW target can be FIRST SERVED (headline 1 holds), but it is *not*
the critical path for how fast that target can be TRADED (headline 2 is
refuted) — those are two different latencies, and the original claim
conflated them. Any latency-lever work aimed at speeding up trade ENTRY
should not target OM-anchor possession; it should look at what happens in
the hours-to-days between first serve and the entry decision.
