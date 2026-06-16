# Unknown-unknown timing-invariant sweep — confirmed findings (2026-06-15)

```
Created: 2026-06-15
Method: mechanically generate timing invariants (ordering / format-consistency / tz-presence / doc-claims)
over every POPULATED table across the 3 live DBs, MINE each for violations on live data. No named questions.
Source: workflow w7q7w72zj (5 agents, 850k tokens). zeus-world.db sweep below is verbatim-confirmed; the
zeus-forecasts.db + zeus_trades.db sweeps + the ranked synthesis remain in the workflow output file
(tasks/w7q7w72zj.output) — the haiku extractor failed before writing them here; re-extract if needed.
```

## The dominant unknown-unknown: system-wide naive-timestamp FORMAT corruption (P0/P1)

`recorded_at` / `ingested_at` columns are written **naive space-separated** (SQLite `CURRENT_TIMESTAMP`
default, no TZ) while event-time columns use ISO `+00:00`/`Z`. Because `'T'`(84) > `' '`(32) in ASCII, every
**cross-column string comparison** between a naive `recorded_at`/`ingested_at` and an ISO event-time column
is corrupted (the ISO column always sorts higher regardless of real time). Confirmed across many tables:

| table | rows affected | columns | sev |
|---|---|---|---|
| `observation_revisions` | 134,250 (all) | utc_timestamp(ISO) vs recorded_at(naive); 2168 raw "inversions" all format artifacts | P0 |
| `venue_order_facts` | 4 (all) | observed_at/venue_timestamp(ISO+Z) vs ingested_at(naive); `CURRENT_TIMESTAMP` default | P0 |
| `source_run_coverage` | 1,087 (all) | computed_at/expires_at(ISO) vs recorded_at(naive) — affects source-run staleness | P1 |
| `readiness_state` | 577 (all) | computed_at(ISO) vs recorded_at(naive) | P1 |
| `daily_observation_revisions` | 3,129 (all) | recorded_at naive | P1 |
| `platt_models` | 981 (all) | fitted_at(ISO) vs recorded_at(naive); sub-second delta (no causal violation, format-incompatible) | P2 |
| `wrap_unwrap_events`, `zeus_meta` | all | naive recorded_at/updated_at | P3 |

- `observation_instants` (2.77M): **498 rows use `Z` suffix vs 2,776,825 use `+00:00`** → `'Z'`(90) > `'+'`(43)
  string-sorts those 498 after same-instant `+00:00` rows → silently mis-ranked. **+1 genuine inversion:**
  `utc_timestamp=2026-06-15T23:00:00+00:00 > imported_at=2026-06-15T22:57:32` by 147s (clock drift / backdated obs).

## Two staleness-propagation bugs at scale (P1)

- **`decision_certificates`: 315,470 / 1,265,824 = 24.9%** have `max_parent_source_available_at > source_available_at`
  — a quarter of all decisions used STALER source data than their own dependency parents. Avg lag 48h, max 30 days.
  Not lookahead (that PASSes) — a silent staleness regression in the decision lineage.
- **`opportunity_events`: 777,757 / 6,989,888 = 11.1%** have `available_at > received_at` — data marked "available"
  up to **46 min (avg 12 min) after** it was received. `available_at` = Zeus wallclock of observation; `received_at`
  = venue receipt. The pipeline does not mark data usable until Zeus processes it, ~12 min late on average.

## Dead TTLs (staleness undetectable)

- `readiness_state.expires_at` = NULL in all 577 rows → readiness state **can never expire**. (Same shape as
  `data_coverage.expected_at` NULL and the earlier dead-instrument findings.)

## Honest PASSes (confirmed-correct)

- `decision_certificates`: `source_available_at <= decision_time` (NO future-data lookahead) — PASS, 0/200k.
- `decision_certificates`: `persisted_at <= created_at` — PASS (created_at ~125s after persisted, normal pipeline latency).

## The pattern

Two systemic root-shapes, each at hundreds-of-thousands-of-rows scale:
1. **Format incoherence** — naive `CURRENT_TIMESTAMP` write-side columns vs ISO event-time columns → every
   string comparison between them is wrong. Fix = a single canonical timestamp format + a normalization helper
   at every comparison site (NOT a blind write-format flip — existing naive rows make it a migration).
2. **"Available" lags reality** — `available_at`/`source_available_at` set to Zeus's processing wallclock rather
   than the true receipt/dissemination time, so freshness/staleness is computed against a late, wrong anchor.
