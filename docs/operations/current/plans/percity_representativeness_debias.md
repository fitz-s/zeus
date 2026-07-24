# Plan — Per-City Representativeness De-Bias (law-8 foundation fix)

**Date:** 2026-06-14. Prepared in an isolated worktree; NOT deployed.

## Goal
Restore the per-city anchor center de-bias deleted at
`replacement_forecast_materializer.py:1455`, made SAFE on thin data, so the OpenMeteo IFS9 anchor
representativeness offset (per-city, two-sign, lead-stable — `cold_bias_metadata_root.md`) is
corrected without the overfit that refuted the naive version (`percity_corrected_oos.md`).

## Change set (cross-zone; > 4 files → planning lock)
- NEW `scripts/fit_anchor_representativeness_debias.py` — fit δ_city (EB-shrunk, activation-guarded,
  lead-pooled on the deep `previous_runs` history) + walk-forward do-no-harm report → artifact
  `state/anchor_representativeness_debias.json`.
- NEW `src/calibration/anchor_representativeness_debias.py` — fail-soft loader
  `get_city_debias_c(city, metric)` gated on metric=high + family do_no_harm + per-city activated.
- EDIT `src/data/replacement_forecast_materializer.py:1455` — `bias_shift_c` reads the loader
  (the only live-path edit). δ_city propagates into the fused μ\* via `anchor_value_corrected_c`.
- EDIT `src/state/db_writer_lock.py` — allowlist the new read-only fitter.
- EDIT `architecture/script_manifest.yaml`, `architecture/test_topology.yaml` — registry
  mesh-maintenance for the new script + test.
- NEW `tests/test_anchor_representativeness_debias.py` — RED-on-revert (correct well-sampled city;
  thin city not corrected; artifact round-trip; metric/do_no_harm gates; missing-artifact inert).

## Safety
- N_min = 30 activation guard (per-city mean SE < one bin); below → family-level fallback.
- EB shrink λ = τ²/(τ²+SE²) toward 0; thin gently, well-sampled fully.
- Family-level do-no-harm walk-forward gate; HIGH passes (corr MAE 1.6319 ≤ raw 1.7269), LOW gated off.
- ARTIFACT-GATED, no retired flag: artifact absent → loader None → byte-identical to today.

## Design / current-state detail
Full design, schema, file:line, test results, deploy procedure:
`docs/evidence/investigation_2026-06-13/percity_debias_impl.md`.
