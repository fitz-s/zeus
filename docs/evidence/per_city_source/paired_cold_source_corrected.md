# CORRECTED cold-center source — PAIRED settlement analysis (supersedes the unpaired MAE table)

```
# Created: 2026-06-17
# Authority basis: operator law (data precision / cell-distance-from-airport, not fusion-math, not
#   de-bias, not the ICON rep). "make it RUN and measure" discipline. Read-only, VERIFIED settlements.
```

## Why this corrects an earlier mistake
`per_city_model_mae.{md,json}` ranked each model on its OWN settled-row set (UNPAIRED). Different models
have different coverage, so the ranking was a COVERAGE artifact, not per-row skill. It wrongly concluded
"icon_seamless is the per-city best" and motivated swapping the ICON-family rep icon_global→icon_seamless
(M1a). A PAIRED check killed that: per (city,target_date,cycle) icon_seamless vs icon_global differ only as
per-cycle noise, **mean Δ ≈ +0.000 for non-EU cities** — the same coarse grid on average. **M1a was a no-op
and has been reverted.**

## Paired ground truth (common-support, n=330 rows, lead-1, °C, VERIFIED settlement)
Per-model BIAS (negative = cold) on rows where the core models are all present:

| model | bias °C | MAE °C | note |
|---|--:|--:|---|
| gfs_global | **+0.01** | 1.49 | near-calibrated |
| ukmo_global_deterministic_10km | **+0.05** | 1.32 | near-calibrated |
| icon_seamless | −0.25 | 1.13 | ≈ icon_global (the swap is a no-op) |
| ecmwf_ifs (9km anchor) | −0.27 | 1.43 | near-calibrated |
| gem_global (~15km) | **−0.74** | 1.40 | COLD — coarse cell |
| jma_seamless | **−1.25** | 1.96 | COLDEST — offshore-snap / coarse-far cell |

## The real lever (operator's cell-distance thesis, confirmed)
The served center is cold because the fusion averages in the **coarse-far cold members gem_global and
especially jma_seamless** (the offshore-snap for coastal airports — exactly "a coarse cell far from the
settlement station reads cold"). Equal-weight fused-center test (n=274):

- ALL-6 globals fused: bias **−0.42** °C
- drop gem+jma+gfs, keep ecmwf+icon_seamless+ukmo (the near-fine set): bias **−0.17** °C (near-calibrated)

→ pruning the cold-far coarse members warms the center **+0.25 °C** toward settlement, at equal MAE. This is
the operator's design ("add finer stations CLOSER to the airport, not farther"; "per-city best combination,
not a blind fixed combination") — NOT the ICON rep, NOT a de-bias.

## What the fix must be (and why it needs the physical key)
**Per-city** pruning of the cold-far coarse members: drop jma_seamless where its cell snaps offshore/far from
THAT airport (coastal cities), drop gem_global where 15km is far — but KEEP them where their cell is genuinely
near the airport (jma's MSM 5km nest over inland Japan is good). A pooled "always drop jma" would wrongly
drop it where it is the best near-airport source. So the principled selection key is **per-model cell-distance
to the airport** (U3) — not a pooled settlement-bias drop (that would be fitting to settlement, which the
operator rejected as the KEY; settlement-bias is the VALIDATOR only).

## Status
- M1a (icon_seamless ICON rep) — REVERTED (no-op; model_selection.py == HEAD).
- Real fix = U3 (record per-model cell-distance-to-airport) → M1b (per-city prune cold-far members) +
  M2 (spine consumes the same selection). See `per_city_best_implementation_plan.md`.
- The σ being too tight (PIT 42% top-decile) is a SEPARATE cold-tail lever (distribution width, not center) —
  noted, out of this center-fix scope.
