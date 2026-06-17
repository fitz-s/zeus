# cell_selection=land — the forecast cold-drag math fix

Created: 2026-06-17
Authority basis: operator "fix the math, not a hardcoded value" (2026-06-17). The representativeness
cold drag is a DATA-QUERY bug, not something a de-bias should paper over.

## Root cause (sharpened 2026-06-17 — a deliberate wrong override, not a missing default)

Open-Meteo's DEFAULT cell selection is **land** (probed directly: for Tokyo ecmwf_ifs, no-param and
`cell_selection=land` both return the inland land cell (35.606,139.743); only `cell_selection=nearest`
returns the offshore Tokyo-Bay cell (35.536,139.795), ~2.7°C colder). Zeus's download EXPLICITLY set
`BAYES_PRECISION_FUSION_CELL_SELECTION = "nearest"`, **overriding the correct default** and forcing the
offshore sea cell for every coastal airport → the model returned SEA-surface temperature (cold by day)
instead of the airport's land surface. Recorded as `raw_model_forecasts.cell_selection='nearest'` on
10,603/10,603 recent rows. The fix RESTORES open-meteo's own default by setting `cell_selection=land`.

Corollary — the grid-representativeness table (`build_grid_representativeness.py`) used the forecast
endpoint with NO cell_selection, i.e. the LAND default, so its d_eff/Δz were ALWAYS measured to the land
cell (rebuilding it with explicit `cell_selection=land` changed 0/54 cells). That is why the σ_repr
variance was settlement-neutral on BOTH the "nearest" and "land" grids: σ_repr penalizes by DISTANCE, and
the correct land cell is the FAR one (Tokyo's anchor land cell is ~7.5km out) — distance is not
unrepresentativeness, so the variance was mis-penalizing the good cell. Only correcting the live VALUE
(the wrongly-forced `nearest` fetch → `land`) fixes the cold drag.

## The fix (data precision, NOT a de-bias / fitted offset)

`cell_selection=land` → OM picks the nearest LAND gridpoint (>50%-land cell), i.e. the model's value AT
the airport, not over water. This is "finer data closer to the airport" — the model's own field read at
the right place, no fitted correction term.

## Settlement-graded proof (ecmwf_ifs, all cities, high+low, last 10 settled days)

| metric | nearest | land |
|---|---:|---:|
| pooled MAE (n=452) | 1.121 | **0.996** (−0.125, **−11%**) |
| pooled bias | −0.595 | **−0.423** |

Per-city (high, 7d): Tokyo **−4.09 → −1.34** (offshore Tokyo-Bay snap), San Francisco −5.06 → +1.27
(Pacific snap; magnitude 5°C→1.3°C), Singapore −1.30 → −0.66. Inland airports (Wuhan, Seoul, Dallas)
**identical** — nearest IS land there, so no harm. (For contrast, the σ_repr variance flag was settlement
-NEUTRAL, ΔMAE +0.002 — a direction-blind variance cannot move a directional mean error; land does it at
the source.)

## What was wired

- `bayes_precision_fusion_download.py`: `BAYES_PRECISION_FUSION_CELL_SELECTION = "nearest" → "land"`.
- `bayes_precision_fusion_capture.py::_default_live_fetch` — instruments single-runs URL now sends `cell_selection=land`.
- `openmeteo_ecmwf_ifs9_anchor.py::params()` — the 9km ecmwf_ifs ANCHOR (prior, the cold culprit) now sends `cell_selection=land`.
- `bayes_precision_fusion_download.py::_default_previous_runs_fetch` — the de-bias HISTORY trains on the same land cell.
- Tests: `test_raw_model_forecasts_does_not_ignore_changed_cell_selection` made default-agnostic (nearest pinned explicitly).

`cell_selection` is part of the BLOCKER-4 product identity, so land captures accrue their OWN de-bias
history — never mixed with the legacy nearest residuals.

## Activation

Code-complete + validated; 0 new money-path failures. Goes LIVE on the next download cycle / daemon
restart (operator deploys): the land raw-value win is IMMEDIATE on first fetch; the per-model walk-forward
de-bias re-accumulates on land over a ~MIN_TRAIN-day warm-up (during which the un-de-biased land value
already beats the nearest value).

The σ_repr variance flag (`replacement_0_1_grid_representativeness_enabled`) stays OFF — SUPERSEDED by
this fix (correcting the value beats down-weighting a wrong one).
