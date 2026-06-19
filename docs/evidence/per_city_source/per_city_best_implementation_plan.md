# Per-city best near-airport source — complete upstream→midstream→downstream implementation plan

```
# Created: 2026-06-17
# Authority basis: operator law "每个城市都应该有最好的天气预报 / per-city best near-airport source;
#   fusion = per-city 最佳不同天气组合, NOT a blind fixed combination; aifs = fallback only;
#   最贴近机场METAR的来源, 最细到欧洲2km icon; fusion = add finer stations CLOSER to the airport".
#   Settlement-graded evidence in this directory (per_city_model_mae, midstream_center_map,
#   upstream_ingest_map, residual_legacy_sources). Authority registry:
#   docs/polyweather_city_source_overlay_verified.csv.
```

## Settlement-graded ground truth (this session)
- **No gaps.** Every city has a settlement-faithful best source: 48/51 ≤1.5°C MAE @ lead-1
  (unit-corrected, VERIFIED settlements). The earlier "dozens of gap cities (Tokyo/Shanghai/Seoul
  3–4°C)" was a LEAD-POOLING + °F/°C artifact. At the decision lead: Tokyo icon_seamless 0.74,
  Seoul 0.55, Karachi 0.64, Shanghai 0.98, Lucknow ecmwf_ifs 1.11 (gfs WORST 5.34 — the poison).
- **The cold-center mechanism (two paths, both lack per-city-best selection):**
  1. **Materializer center** (the live receipt q): consumes `select_models`, but it was POLYGON-only
     (covers EU/CONUS/UK/N-America) and **structurally excluded `icon_seamless`** — the per-city-best
     ICON for non-EU cities — using coarse `icon_global` (a near-universal −0.4…−2.3°C COLD bias).
  2. **Spine decision center** (`qkernel_spine_enabled=true`): BLIND fuse of EVERY model in
     `raw_model_forecasts` — admits the per-city-WORST (Lucknow gfs 5.34, Seoul gfs 3.13), equal-weights
     correlated families. `select_models` is NEVER applied (event_reactor `_spine_multimodel_members_for_event`).
  3. The 5 coarse globals (gfs/icon/gem/jma/ukmo) ride as a FIXED set in every non-regional fusion —
     the "blind fixed combination" the operator rejects; per-city they must be pruned by airport-proximity.
- **Residual cold sources still on HEAD** (cleanup unmerged): AIFS-soft-anchor materializer fallback;
  `ensemble_snapshots.members_json` cold-mx2t3 (day0 lane + the `_forecast_authority_payload` cert wire);
  inert bias-maze remnants. Worklist: `residual_legacy_sources.md` GATE 0/1/2.

## DONE this session (settlement-graded, corrected)
- **Validator artifacts** `per_city_model_mae.{md,json}` (UNPAIRED — coverage-confounded, see correction)
  and `paired_cold_source_corrected.md` (PAIRED, authoritative). The paired analysis is the ground truth.
- **M1a REVERTED.** I first swapped the ICON rep icon_global→icon_seamless on the unpaired ranking; a PAIRED
  check showed icon_seamless≈icon_global (mean Δ≈0 for non-EU) — a NO-OP. Reverted; `model_selection.py`==HEAD.
- **Real cold source identified (paired, n=330):** the fusion averages in the **coarse-far cold members
  jma_seamless (−1.25, offshore-snap) and gem_global (−0.74, 15km)**; the near-fine models (ecmwf −0.27,
  icon −0.25, gfs +0.01, ukmo +0.05) are near-calibrated. Pruning gem+jma warms the fused center −0.42→−0.17
  (+0.25°C, near-calibrated). This CONFIRMS the operator's cell-distance/data-precision thesis.

## Remaining work — the complete implementation

### UPSTREAM
- **U1 — per-city source registry.** Load `docs/polyweather_city_source_overlay_verified.csv` into a
  runtime registry: per city → {forecast stack, national service, settlement source}. Currently 0 code refs.
- **U3 — cell-distance recording (the PHYSICAL selection key).** Each model returns its NEAREST native
  grid cell; `raw_model_forecasts.cell_selection` records only the policy ("nearest"), NOT the returned
  cell coordinate or its distance to the airport. Capture the returned cell lat/lon at ingest, compute
  `dist_km(cell, airport)`, store it. This is the operator's KEY ("closest to the airport"); MAE is the validator.
- **U4 (Phase 2 completeness) — national-service ingest.** `raw_model_forecasts` is open-meteo ONLY.
  Ingest the national services the overlay lists (NWS/NDFD, MGM, HKO, CWA, …) for the cities where they
  are the near-airport authority. Larger build; the open-meteo multi-model already covers every city sub-1.8°C.

### MIDSTREAM
- **M1b — per-city near-airport member selection for ALL families.** Extend `select_models` to prune the
  coarse-far global members per city by cell-distance×resolution (U3), keeping the per-city near-airport
  subset — the operator's "best combination, not fixed combination". Validated by the MAE artifact.
  Non-conflicting (`model_selection.py`).
- **M2 — spine consumes the per-city selection.** `_spine_multimodel_members_for_event` must filter its
  `raw_model_forecasts` member set through `select_models` before fusing (kill the blind fuse), so the spine
  decision center == the materializer center (one selection). event_reactor (serialized with the cleanup agent).

### DOWNSTREAM
- **D1** — the corrected center flows through q → q_lcb → edge → Kelly → submit unchanged (no new code).
- **D2 — deploy + ARM + fills.** Coordinate the deploy with the operator's WIP (event_reactor/materializer
  uncommitted). ARM = settlement-graded calibration coverage + after-cost EV (no fixed % bar). Watch real fills.

### CLEANUP (parallel agent, running)
- Strip residual cold sources: bias-maze GATE 0, carrier-decouple GATE 1, day0 re-source GATE 2
  (`residual_legacy_sources.md`). One agent owns the event_reactor forecast-authority edits; worktree off HEAD.

## Conflict partition (the operator has heavy uncommitted WIP on event_reactor + materializer + lifecycle)
- **Orchestrator (non-conflicting):** `model_selection.py` (M1a✓, M1b), the U1 registry + U3 recorder (new files),
  validation, deploy/ARM judgment.
- **Cleanup agent (worktree, one owner of event_reactor forecast-authority):** GATE 0/1/2 + later M2 spine selection.
- **Operator's WIP merges last;** the orchestrator resolves event_reactor merge with git-master.

## Sequencing
1. (running) cleanup agent → reviewable branch.
2. U1 registry + U3 cell-distance recorder (non-conflicting, new files).
3. M1b per-city pruning (model_selection, validated vs settlement).
4. M2 spine selection (after operator WIP committed; serialized in the event_reactor worktree).
5. Deploy the coordinated set + ARM + watch fills.

## Acceptance (operator law — no fixed % bar)
Re-run `per_city_model_mae` + a PIT/center-vs-settlement check on the LIVE served center: served center tracks
settlement (top-decile PIT share → ~10%, mode-match rises, the buy_no-on-center flood stops), WITHOUT any
statistical de-bias. Then continuous settlement-graded POSITIVE-after-cost fills, calibration-coverage-validated.
