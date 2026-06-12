# Forecast Freshness Truth — full evidence (2026-06-12)

**Status:** READ-ONLY investigation evidence. No code/config/live-DB changed.
**Authority basis:** operator directive 2026-06-12 ("新鲜度给我调查清楚，究竟每个forecast多久发布一次，
如果我们获取不到最新的准确forecast对于评估来说是一个衰减" — establish full freshness truth; quantify
staleness as evaluation decay).
**Data sources:** `state/zeus-forecasts.db` (mode=ro&immutable=1) — `raw_model_forecasts` (571,230 rows),
`forecast_posteriors` (2,406 rows), `settlement_outcomes` (7,282 rows, 6,909 VERIFIED).
Provider schedules: Open-Meteo Single Runs API doc, open-meteo/open-data README, ECMWF API doc.
**Code basis:** `src/forecast/bayes_precision_fusion.py`, `src/data/bayes_precision_fusion_download.py:659`,
`docs/authority/replacement_final_form_2026_06_09.md`.
**Probes (read-only, mode=ro):** `/tmp/q4budget.py`, `/tmp/samecycle.py` and the Q2/Q3 probes
(scripts under `scripts/probe_forecast_freshness_*.py` were swept by another session's cleanup mid-run;
their outputs are captured below and the probes are reproducible from the SQL in this doc).

---

## Q1 — Official publication cadence per provider in OUR stack

The replacement chain (`bayes_precision_fusion.py`) fuses: anchor `ecmwf_ifs` (prior) +
decorrelated globals `gfs_global, icon_global, gem_global, jma_seamless,
ukmo_global_deterministic_10km` + domain-gated regionals `icon_eu, icon_d2,
meteofrance_arome_france_hd, ukmo_uk_deterministic_2km, ncep_nbm_conus`. All are served via
the Open-Meteo Single Runs / Previous Runs API (provider=`open-meteo`).

| Model (our id)                  | Cycles/day (UTC)        | OM update interval | Real dissemination lag (run→available) | Conf |
|---------------------------------|-------------------------|--------------------|----------------------------------------|------|
| ecmwf_ifs (IFS 0.25 / HRES 9km) | 00/06/12/18 (4×)        | every 6 h          | ~4–6 h global; open-data IFS 0.25 +2 h | high |
| ecmwf_aifs                      | 00/06/12/18 (4×)        | every 6 h          | ~4–6 h                                 | high |
| gfs_global (NCEP GFS)           | 00/06/12/18 (4×)        | every 6 h          | ~4–6 h global                          | high |
| ncep_nbm_conus (NBM)            | hourly                  | every hour         | ~1.5–3.5 h CONUS                       | med  |
| icon_global (DWD 11km)          | 00/06/12/18 (4×)        | every 6 h          | ~3–5 h global                          | high |
| icon_eu (DWD 7km)               | 00/03/.../21 (8×)       | every 3 h          | ~2–4 h regional                        | high |
| icon_d2 (DWD 2km)               | 00/03/.../21 (8×)       | every 3 h          | ~1–3 h regional                        | high |
| ukmo_global_deterministic_10km  | 00/06/12/18 (4×)        | OM "every hour"    | ~4–6 h (UKMO often delayed)            | med  |
| ukmo_uk_deterministic_2km (UKV) | hourly-ish              | OM "every hour"    | ~2–4 h regional                        | low-med |
| meteofrance_arome_france_hd 1.3km | 00/03/.../21 (8×)     | every hour stitch  | ~1–3 h regional                        | med  |
| jma_seamless (GSM+MSM)          | GSM 4×, MSM 8×          | every 3 h          | ~3–5 h GSM, ~2–4 h MSM                  | med  |
| gem_global (CMC GDPS 15km)      | 00/12 (2×)              | every 6 h          | ~5–7 h global                          | med  |

Authoritative quote (Open-Meteo Single Runs API): *"After initialisation the model needs additional
computation time before results are distributed. This typically takes **4–6 hours for global models**
like ECMWF IFS and GFS and **1–3 hours for regional models**. A run initialised at 00 UTC is generally
accessible from around 04–06 UTC onwards."*

**Theoretical best-case freshness staircase (global anchor, 4×/day):** a 00Z cycle is fetchable
~05Z, 06Z→~11Z, 12Z→~17Z, 18Z→~23Z. At any wall-clock the freshest trustworthy global cycle is
**≥4–6 h old at best, up to ~10–12 h old just before the next cycle lands**. CMC gem (2×/day, 00/12Z)
is the coarsest single contributor: one cycle stays freshest for ~12 h.

---

## Q2 — Observed ingest + materialization latency

### Q2A — Ingest (raw_model_forecasts, live window from 2026-06-08T19Z)

**Critical provenance finding.** Zeus does NOT record real per-model dissemination times. It stamps a
synthetic `source_available_at = source_cycle_time + 14.00 h` for ALL models, hardcoded at
`src/data/bayes_precision_fusion_download.py:659` (`release_lag_hours: float = 14.0`). Verified:
`(source_available_at − source_cycle_time)` = **+14.00 h for all 12,053 live single_runs rows**, zero
spread. This is a uniform conservative assumption, ~8–10 h LONGER than real dissemination (Q1: 4–6 h).

True ingest promptness (the honest signal) = `captured_at − source_available_at`, live single_runs:
`n=12,053  min=−6.81  p50=+0.28 h  p90=+2.71 h  max=+7.08 h`.
**→ Once Zeus believes data is available, it captures within ~17 min median. Ingest is NOT the bottleneck.**

Capture lag `captured_at − source_cycle_time` (fresh single_runs): **p10≈8.1 h, p50≈14.3 h, p90≈16.7 h**
(the 14 h floor is the synthetic availability stamp + ~0.3 h real grab). `previous_runs` p90 reaching
~212 h is the de-bias HISTORY span (many old cycles), not a live-freshness signal — separated out.

**Cycle hours actually ingested (live):** 00Z dominates (666 model-endpoint groups), 06Z (108),
12Z (63), 18Z (40). Newest single_runs cycle captured each day = **06Z, never 12Z/18Z** (confirmed
06-10/11/12). Zeus effectively runs on **00Z and 06Z cycles only** — the 12Z/18Z cycles, though
published, are not the freshest cycle a posterior consumes in practice.

### Q2B — Materialization cadence (forecast_posteriors, live)

- 279 families (city,target,metric) live; 266 with ≥2 posteriors; per-family posterior count
  p10=2, p50=7, p90=14, max=24.
- **Refresh interval between consecutive posteriors of the same family:**
  `n=1,817  p10=0.08  p25=0.36  p50=3.00  p75=3.23  p90=8.29  max=14.55 h`.
  high-metric p50=3.0/p90=8.2; low-metric p50=3.0/p90=9.9.
  **→ The "~25 h families" anecdote is the overnight gap tail; the steady-state median refresh is 3 h,
  p90 ≈ 8–10 h. Worst per-family single gap caps at 14.6 h (p99), not 25 h.**
- Lag newest-consumed-cycle → computed_at: `p10=8.5  p50=15.4  p90=21.0 h` (≈ capture + ~1 h fanout).

### WHERE we lose time
1. **Download cadence vs publication:** synthetic 14 h availability gate is ~8–10 h more conservative
   than real dissemination (4–6 h). Posteriors could in principle be built on cycles ~8 h fresher.
2. **Cycle ceiling:** only 00Z/06Z cycles reach the posterior; 12Z/18Z published cycles unused.
3. **Born-stale materialization (the real loss):** at compute time, **14.2 % of posteriors consumed an
   anchor cycle that was ALREADY superseded** by a fresher ingested ecmwf_ifs cycle (p90 gap = 6.0 h =
   exactly one missed cycle; max = 12 h = two). Plus cross-cycle thrashing (below).

---

## Q3 — Staleness = evaluation decay, quantified

Scored 728 posterior×settlement records over 151 settled families (q_json keys are full market-question
strings; integer °C parsed from each and from `winning_bin`; multiclass Brier + winning-bin LogLoss + p(win)).

**[A] By posterior age vs the freshest posterior of the SAME settled family** (age 0 = latest belief held):

| age bucket | n   | Brier | Brier 95% CI    | LogLoss | p(win) |
|------------|-----|-------|-----------------|---------|--------|
| <3 h       | 172 | 0.889 | [0.832, 0.946]  | 2.95    | 0.230  |
| 3–6 h      | 48  | 0.825 | [0.711, 0.939]  | 2.08    | 0.264  |
| 6–12 h     | 109 | 0.949 | [0.872, 1.025]  | 3.78    | 0.204  |
| 12–24 h    | 228 | 0.918 | [0.871, 0.965]  | 2.47    | 0.207  |
| >24 h      | 171 | 0.905 | [0.850, 0.959]  | 2.98    | 0.216  |

Wall-clock age alone is **NOT strongly monotonic** here (CIs overlap; n modest). Staleness decay does not
read cleanly off a wall clock — consistent with the design hypothesis that the cost is per-missed-cycle.

**[B] By age of the CONSUMED model cycle at compute time** (computed_at − source_cycle_time):

| cycle-age bucket | n   | Brier | LogLoss | p(win) |
|------------------|-----|-------|---------|--------|
| <12 h            | 81  | 1.091 | 6.03    | 0.152  |
| 12–18 h          | 209 | 0.860 | 2.29    | 0.221  |
| 18–24 h          | 338 | 0.896 | 2.40    | 0.227  |
| 24–36 h          | 100 | 0.889 | 3.15    | 0.234  |

The <12 h bucket is WORSE — the lead-0 same-day overconfidence the final-form spec already flags
(σ_pred floor 1.0 °C). Not a freshness win; a separate calibration caveat.

**[C] By forecast lead (the dominant skill axis, for context):** Brier 0.840 (<0.5 d) → 0.919 (~1 d) →
0.989 (~2 d); p(win) 0.254 → 0.209 → 0.184. **Lead dominates calibration far more than refresh-age.**

**Information-arrival rate — belief drift vs time separation Δt (same family):**

| Δt band | nPairs | TV mean | TV p90 | Δμ °C mean | Δμ °C p90 |
|---------|--------|---------|--------|------------|-----------|
| 0.5–2 h | 619    | 0.316   | 0.660  | 0.731      | 2.173     |
| 2–4 h   | 1134   | 0.177   | 0.527  | 0.418      | 1.295     |
| 4–8 h   | 1748   | 0.338   | 0.625  | 0.715      | 1.727     |
| 8–16 h  | 1921   | 0.311   | 0.630  | 0.800      | 1.926     |
| 16–28 h | 879    | 0.263   | 0.548  | 0.578      | 1.438     |

(TV = total-variation distance 0..1; Δμ = shift in expected °C.)

**THE STRUCTURAL RESULT — drift is driven by NEW CYCLES, not by the clock:**

| pairing                       | nPairs | TV mean | TV p90 |
|-------------------------------|--------|---------|--------|
| consumed cycle UNCHANGED      | 1,593  | 0.197   | 0.553  |
| consumed cycle NEW (fresher)  | 4,708  | 0.319   | 0.627  |

A new-cycle ingest moves the distribution **1.6× more** (TV 0.319 vs 0.197) and shifts the center
~0.7 °C (p90 ~1.9 °C). With 1 °C bins, a 0.7 °C center move reallocates ~0.5–1 bin of probability mass
across the winning bin → that IS the quantified evaluation decay of holding an old (pre-new-cycle) posterior.

**Caveat (honesty):** the "same-cycle TV ≈ 0.20" is partly an aggregation artifact. A per-family trace
(Shanghai 2026-06-12 high) shows q_mean is essentially CONSTANT when the consumed cycle is identical
(Δμ = 0.00 between same-cycle recomputes) and jumps ±2.5 °C only when the consumed cycle changes. The
aggregate same-cycle TV is inflated by cross-cycle THRASHING within a family (next section).

---

## Q3b — Cycle thrashing (a second decay source the investigation surfaced)

Per-family trace, Shanghai 2026-06-12 high, consumed-cycle column over time:
`06-09T12Z(μ30.67) → 06-10T00Z(28.18) → 06-09T12Z(30.67, OLDER) → 06-10T00Z(31.00) → … →
06-10T06Z(28.67) → 06-10T00Z(31.04, OLDER) → 06-10T06Z(30.21) → 06-11T06Z(30.88)`.

The materializer repeatedly steps BACKWARD to a staler cycle (06-10T00Z, then 06-09T12Z) between
forward steps, swinging q_mean by ±2.5 °C (2–3 bins). This is the L1 "coverage ≠ currency" category
resurfacing in the materialization ROTATION: ~half the refreshes do not monotonically advance the
consumed cycle. Each backward step is a self-inflicted staleness event independent of provider cadence.

---

## Q4 — Derived freshness budget + re-materialization design inputs (DATA, not build)

### (a) Cadence-derived staleness budget per family class

Refresh-interval distribution is the family's own clock. Budget = the family's p90 refresh interval +
dissemination slack:

```
staleness_budget(family) = p90(refresh_interval_family) + dissemination_slack
```

Fitted numbers (live):
- Global-only families (high & low): p90 refresh ≈ **8.3 h** → budget ≈ 8.3 + (real lag 6 h − we already
  paid it) ≈ **~8–10 h** before a held posterior is presumed decayed.
- Worst-case single gap (p99) = **14.6 h** → hard ceiling; any family quieter than this is starved.
- Regional families inherit the 3-hourly cycle, so their natural budget is tighter (~6 h) but our
  ingest still gates them on the 00Z/06Z anchor rhythm, so effective budget ≈ same 8–10 h.

Recommended trigger threshold for "presumed decayed" = **p90 family refresh ≈ 8 h**, alarm at the
**14.6 h p99 ceiling** (starvation).

### (b) When a held-position family should trigger targeted re-materialization

**Tie to Q1's staircase, NOT a wall clock** (Q3 proves drift is cycle-driven). Re-materialize a held
family's posterior **iff a newer provider cycle has been ingested than the cycle the posterior consumed**:

```
re_materialize(family) WHEN
   max(source_cycle_time of any in-universe model ingested into raw_model_forecasts
       for this (city,target,metric) since the posterior's computed_at)
   >  posterior.consumed_cycle   (per-model, anchor first)
```

Evidence this is the right trigger and that it fires meaningfully: **14.2 % of posteriors were born with a
fresher anchor cycle already ingested** (p90 missed gap = one full 6 h cycle). A cycle-aware trigger would
both (i) eliminate born-stale posteriors and (ii) stop the backward thrashing (Q3b) by enforcing
monotone consumed-cycle advance (never re-materialize onto an OLDER cycle than already consumed).

### (c) Decay-rate table = cost of NOT refreshing (from Q3)

| condition                              | belief move (TV) | center move Δμ | calibration cost                         |
|----------------------------------------|------------------|----------------|------------------------------------------|
| hold across a same-cycle window        | ~0.00–0.20       | ~0.0 °C        | negligible (posterior ~deterministic in-cycle) |
| hold across ONE missed new cycle       | **0.32 mean / 0.63 p90** | **0.7 °C mean / 1.9 °C p90** | ~0.5–1 bin of mass off the winning bin |
| +1 day of forecast lead (context)      | —                | —              | Brier +0.08, p(win) −0.045 per lead-day  |

**Interpretation for the build:** the cost function of staleness is a STEP function keyed on missed
cycles, not a smooth function of hours. Holding a posterior is ~free until a new cycle lands; the moment
a fresher cycle is ingested, expected calibration loss jumps by ~TV 0.32 / Δμ 0.7 °C. That is the precise
quantity the re-materialization trigger must protect against.

### (d) Provenance antibody recommendation (flagged, not built)

`source_available_at` is a synthetic `cycle + 14 h` constant (Q2A), ~8–10 h more conservative than real
dissemination. It is fine as a download GATE (fail-safe-late) but must NOT be mistaken for a real
freshness measurement. Any future freshness logic should key off `source_cycle_time` (the real cycle
identity) and `captured_at` (the real ingest time), never `source_available_at`.
