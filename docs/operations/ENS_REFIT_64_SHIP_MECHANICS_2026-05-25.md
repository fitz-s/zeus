# #64 Ship Mechanics & Coverage Spec — full_transport → live (2026-05-25)

# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: live-DB inspection (state/zeus-world.db, state/zeus-forecasts.db), read of scripts/promote_platt.py, src/calibration/manager.py + store.py, and ENS_REFIT_MATH_ROI_2026-05-25.md (opus verdict).

## TL;DR — "replace the db" is not a DB copy
Shipping full_transport to live (#64) is a **code + data + config** change, not a table swap. Three hard facts, all verified against the live DBs and the sanctioned tooling:

1. **Live p_raw does NOT apply full_transport today.** The error-model layer is offline-only (no call in `src/forecast|oracle|strategy|signal`; confirmed in ENS_REFIT_MATH_ROI §findings). The 20h refit produced ft-corrected `calibration_pairs_v2` + ft-keyed `platt_models_v2` **offline**. Promoting the ft Platt models while live still emits *uncorrected* p_raw would feed uncorrected p_raw into Platt models *trained on ft-corrected* p_raw → train/serve mismatch → garbage. The ft correction must be **wired into the live p_raw path** first.

2. **Blanket promote would orphan 65–96% of live calibration coverage.** The ft Platt layer is partial; the sanctioned promote replaces *by data_version* (DELETE-all + INSERT-refit), so it would delete live models for buckets the refit never fit.

3. **HK HIGH is a real regression — must be carved out.** Per ENS_REFIT_MATH_ROI: HK HIGH ft is a genuine absolute calibration failure (PIT 96.9% in bin 0, +6.3°F over-warm at every lead incl. lead 0, ECE 15× global; root cause = wrong HK-HIGH posterior, not the architecture). There is **no live route-to-raw SNR gate** to mask it (λ is continuous, baked into p_raw; `full_transport_v1` IS the gated path). Miami HIGH is mostly a disjoint-date confound (conditional).

## Coverage facts (live vs refit staging DB `/private/tmp/ens_refit/full.db`)

`platt_models_v2`, VERIFIED+active buckets at (cluster × season) granularity:

| data_version | live buckets | refit buckets | live buckets orphaned if promoted |
|---|---|---|---|
| HIGH `tigge_mx2t6_local_calendar_day_max_v1` | 202 | 71 | **132 (65%)** |
| LOW `tigge_mn2t6_local_calendar_day_min_v1` | 198 | 8 | **190 (96%)** |
| LOW `tigge_mn2t6_local_calendar_day_min_contract_window_v2` | 175 | 8 | **167 (95%)** |

Refit staging totals: 160 platt models (136 HIGH + 16 LOW-v1 + 8 LOW-cw) vs live 1,406.

**Why partial despite a "complete" sentinel:** full.db `zeus_meta` carries `calibration_pairs_v2_rebuild_complete` for high+low with `{"completed": true, city=all, start=all, end=all}` — but that sentinel attests the **pairs rebuild** (the 10k-MC pairs, complete for all cities), NOT the **Platt fitting** (which only ran for 71 HIGH / 8 LOW buckets). Pairs-complete ≠ Platt-complete.

## Sanctioned tooling + gotchas
- `scripts/promote_platt.py` (platt → world.db) and `scripts/promote_calibration.py` (pairs → forecasts.db). Both: dry-run default, gzip backup + rollback, `--commit` to apply.
- **Replace semantics:** `cmd_promote` does `DELETE FROM platt_models_v2 WHERE data_version IN (...)` then INSERT stage rows. Filtered by **data_version**, which the refit shares with live → blanket promote orphans uncovered buckets (the table above).
- **Sentinel gate false-refuse:** `inspect`/`promote` report **NOT READY** for high/low/low_contract because of a writer↔reader schema mismatch — refit writes payload `{"completed": true}` + `data_version=all`; the script's `_sentinel_status_for_metrics` (`promote_platt.py:219-228`) requires payload `status=="complete"` AND the *specific* data_version. This is a likely real bug (refit_platt / rebuild writer vs promote reader), independent of the coverage issue. Do not paper over it with `--allow-incomplete`.
- **Empty live pin → silent-takeover risk:** `config/settings.json::calibration.pin` is currently `{frozen_as_of: null, model_keys: {}}`. The live loader defaults to "newest is_active=VERIFIED row wins" (`src/calibration/manager.py:47-52`). Inserting the refit's 158 VERIFIED models (fitted today) would let them auto-win for covered buckets with **no explicit blessing** — the exact silent takeover the pin was designed to prevent. The pin must be set explicitly as part of any ship.
- **model_bias is NOT the ship layer:** `model_bias` is 0 rows in the refit DB. full_transport is baked into `calibration_pairs_v2` (MC run with ft) and the Platt `model_key` suffix `:emf=full_transport_v1` (`src/calibration/store.py:617`). No model_bias migration needed.

## Recommended staged ship (copy-first; HK HIGH carved out)
1. **Wire ft into live p_raw** (code): call the predictive-error model in the live p_raw generation path so live emits ft-corrected p_raw matching the trained Platt input space. This is the substance of #64.
2. **Fix the sentinel writer↔reader mismatch** OR add an explicit blessing path; do not bypass the gate.
3. **Promote on a COPY of prod first**: clone world.db + forecasts.db, run `promote_calibration --commit` + `promote_platt --commit` against the copy, boot a daemon against the copy, confirm it reads ft + bins sane.
4. **Selective Platt (ECE-gated):** per ENS_REFIT_MATH_ROI §4.2, apply Platt only where per-cohort ECE>0.005; else p_raw-direct. So the partial Platt coverage is acceptable — most buckets ride the ft p_raw win, not Platt.
5. **Pin the 44 shipping cohorts** in `calibration.pin.model_keys`; bump `frozen_as_of`. **Carve out HK HIGH** (exclude from pin). Miami HIGH conditional (re-measure on overlapping dates first).
6. **Schema-bump + coordinated daemon restart** (per schema-migration discipline; daemon checks world schema, retries, else SystemExit).
7. **§4.3 bin comparison on fresh live traces** (the original goal): after restart, run `scripts/replay_probability_edge_bin_sanity.py` on post-migration `probability_trace_fact` — verify FP=0, Amsterdam-like rejected, bins fall in expected intervals.

## Open dependencies
- **HK HIGH posterior fix** — separate track; the ft HK-HIGH posterior is +6.3°F over-warm. Ship excludes HK HIGH until refit/fixed.
- **44-cohort pin map** — enumerate the exact `metric:cluster:season:cycle → model_key` entries from the refit's covered buckets, intersected with the ECE>0.005 Platt gate.
- **§4.3** — blocked until live migration produces fresh traces (full.db has empty trade tables).

## What did NOT happen (discipline)
No promote run, no `--allow-incomplete`, no 84GB copy made, no live pin edit. Execution held pending operator decision on ship path; this doc is the spec.
