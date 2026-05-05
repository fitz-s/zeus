# E8 Audit — Synthesis: Three-Layer Bulk-Regeneration

Created: 2026-05-03
Authority: synthesis of 01–04 read-only audits
Status: **NOT Tokyo-only. System-wide. Three stacked layers of provenance loss.
Live serving exposed.**

## TL;DR

The original RERUN_PLAN.md framed E8 as "Tokyo HIGH 682k rows recorded
2026-04-29 10:00–10:16, isolated". The audit shows that framing was wrong
in two important directions:

1. **It is not isolated.** It affects 100/102 (city, metric) bins of
   `calibration_pairs_v2` (97.9 % of rows) and 387/399 active Platt
   calibrators (97 %).
2. **The impact is not just the DDD plan.** The live trading engine
   reads the same bulk-refit calibrators at decision time without any
   snapshot freeze.

And the audit also surfaced a **third layer** that the original plan
never named: the **raw observation feed itself was wiped and reloaded
yesterday (2026-05-02)**. That destroys the time-anchor we were going
to use to do a leakage-safe rerun.

## The three layers

| Layer | Table | Affected | Bulk-write window | Detection |
|---|---|---|---|---|
| L1 raw obs | `observation_instants_v2` (`source = wu_icao_history`) | **47 / 47 cities, 100%, 943,265 rows** | **2026-05-02 14:44 → 16:38 UTC** (~2 h) | All `imported_at` collapse onto yesterday |
| L2 pairs | `calibration_pairs_v2` | **100 / 102 bins, 97.9% of 41.2M rows** | **2026-04-28 / 2026-04-29 / 2026-05-01** | Per-bin `recorded_at` span < 30 min, `target_date` span > 800 d |
| L3 calibrators | `platt_models_v2` (`is_active=1`) | **387 / 399, 97%** | **2026-04-29 21:36 → 22:12 UTC** (~36 min); HK on 2026-05-01 | All `fitted_at` collapse onto one window |

**Temporal sequence**:
1. `2026-04-28` → first wave of pair regen (~3.8M rows, 6 cities)
2. `2026-04-29 21:00 → 22:30` → main wave: 36.5M pair rows + 387 calibrator fits
3. `2026-05-01` → Hong Kong + Paris cleanup (~870k pair rows + 12 HK calibrators)
4. `2026-05-02 14:44 → 16:38` → **YESTERDAY**: full `wu_icao_history`
   wipe-and-reload, ALL cities

So the calibrators were fit on the old raw observations, and **the raw
observations were then completely overwritten the next day**. We cannot
verify whether the new raw obs match the labels the calibrators were
trained against without independently auditing the regen script.

## Why this matters for the DDD plan

RERUN_PLAN.md proposed using `recorded_at < 2026-04-28` as the time-window
filter to escape leakage. **That filter no longer exists** because:
- L1 (raw obs): every row's `imported_at` is now 2026-05-02. There is
  no row with `imported_at < 2026-04-28`. The provenance signal is gone.
- L2 (pairs): every row's `recorded_at` is between 2026-04-28 and
  2026-05-01. No row predates the regen window. Same gone.

The only fields whose timestamps were **not** rewritten by the regen are:
- `forecast_available_at` (claimed forecast time, comes from the
  upstream forecast payload, not the DB write time)
- `target_date`
- `local_timestamp` / `utc_timestamp` (claimed observation time)

If those are trustworthy (still TBD), then the proper time anchor for
rerun is `forecast_available_at` plus `target_date`, not anything
involving `recorded_at` / `imported_at` / `fitted_at`.

## Why this matters for live trading

Live serving path (audit-D, file:line evidence):

```
evaluate_candidate         src/engine/evaluator.py:1844
  → get_calibrator         src/calibration/manager.py:187
    → load_platt_model_v2  src/calibration/store.py:628
      SQL: SELECT param_A, param_B, param_C, …
           FROM platt_models_v2
           WHERE temperature_metric = ? AND cluster = ?
             AND season = ? AND data_version = ?
             AND input_space = ? AND is_active = 1
             AND authority = 'VERIFIED'
           ORDER BY fitted_at DESC LIMIT 1
```

**Three live exposures**:

1. **No `recorded_at <= frozen` clause.** Any future mass-refit under
   the same `data_version` becomes live the moment its rows are written.
   A regen script can silently flip live behavior city-wide in 36
   minutes.
2. **Currently in production**: 387 of 399 calibrators were refit on
   2026-04-29. Live trading from 2026-04-29 22:00 onward has been using
   these calibrators. They were fit on the *pre-2026-05-02* raw obs.
   The 2026-05-02 reload may have changed `outcome` labels for past
   target_dates (we don't know yet — would need diff against a backup).
   If labels changed, the live calibrators are mis-fit relative to the
   current truth.
3. **`snapshot_id` is stored on `calibration_pairs_v2`** (`store.py:210`)
   **but NOT used in `load_platt_model_v2`'s WHERE clause**
   (`store.py:628`). The snapshot mechanism exists in schema but isn't
   wired into the read path, so it provides zero protection.

## Concrete numbers (per audit-A and audit-B)

- Bulk-regenerated calibration_pairs_v2 bins: **100 of 102** (Paris HIGH
  and Paris LOW are the only "partial" — 2 data_versions, ~10%
  VERIFIED, 58.5 h spread)
- Mass-refit Platt calibrators on 2026-04-29: **387 fits in ~36 minutes**
- Total contaminated calibration rows: **40,373,380 of 41.2M (97.9%)**
- Cities NOT mass-refit: **Hong Kong** (refit 2026-05-01 instead)
- Cities partially clean: **Paris** (10% verified, two data_versions)

## What we still don't know (the critical follow-up questions)

The audit can't answer these from DB introspection alone. Each requires
reading the regen script(s) and / or filesystem-level forensics.

1. **Was the 2026-04-29 regen LEAKAGE-SAFE?**
   The bulk pattern looks alarming, but a *correct* full regen would
   look like this too. The decisive question is: when the script
   regenerated row `(city=Tokyo, target_date=2025-06-01, lead_days=3)`,
   did it use only forecast and observation data dated ≤ 2025-06-01,
   or did it accidentally pull post-2025-06-01 labels? If the former,
   bulk-regen is just provenance loss, not leakage. If the latter, the
   contamination is real.
   - Investigation: locate the regen script, audit its as-of
     constraints.

2. **Did the 2026-05-02 raw-obs reload change labels?**
   `outcome` in `calibration_pairs_v2` is computed from raw obs. If the
   wu_icao_history reload introduced different temperature values for
   past target_dates, then the 2026-04-29 calibrators were fit on
   different labels than the live engine now sees.
   - Investigation: compare a sample of post-reload `temp_current` /
     `running_max` / `running_min` against any backup of pre-reload
     state, or against an independent oracle source (NOAA / KNMI raw).

3. **Is `forecast_available_at` actually trustworthy?**
   We need this field as the time anchor for any leakage-safe rerun.
   It claims to come from the upstream forecast payload, but if the
   regen script silently overwrote it (e.g. set it = `target_date` for
   all rows), it's also lost.
   - Investigation: spot-check `forecast_available_at` distribution per
     `(city, target_date)` — should show natural multi-day spread per
     target_date (one row per lead_day). If all rows for a target_date
     have identical `forecast_available_at`, it's been collapsed.

4. **Is there a pre-2026-05-02 backup anywhere?**
   - Investigation: `state/zeus-world.db` history (git? snapshot dir?
     `.bak` files?), and any `state/snapshots/` or rollback directory.

## Proposed E8 follow-up (still all read-only)

| ID | Question | Method | Cost |
|---|---|---|---|
| E8.5 | Regen script leakage-safety | `grep` for the regen script, read it, audit time-cutoff logic | 1 haiku, ~15 min |
| E8.6 | `forecast_available_at` integrity | SQL: per (city, target_date), count(distinct forecast_available_at), distribution of (target_date - forecast_available_at) | 1 haiku, ~15 min |
| E8.7 | Pre-reload backup search | filesystem scan: `find state/ -name "*.bak" -or -name "*backup*"`, `find . -name "zeus-world.db.*"`, check git LFS history | 1 haiku, ~10 min |
| E8.8 | Label-stability spot check | for 5 (city, target_date) pairs, query current `temp_current` and compare with NOAA / KNMI external | 1 haiku, ~30 min, requires network |

E8.5 and E8.6 are the most decisive. E8.7 is high-value-if-positive,
low-cost-if-negative. E8.8 is real validation but more expensive and
needs an external API budget.

## Implications for RERUN_PLAN.md

The current RERUN_PLAN.md needs structural revision. The leakage-safe
filter was going to be `recorded_at < 2026-04-28`, which no longer
works (no rows survive that filter). Revisions needed:

- **Phase A** (reproducibility): unchanged
- **Phase D** (Platt time-window-isolated refit): the filter must
  switch from `recorded_at` to `forecast_available_at`, AND we need
  E8.6 to confirm forecast_available_at is trustworthy
- **Phase E–H** (DST, peak window, §2.1 / §2.3 / §2.4 reruns): all
  unaffected by the bulk-regen IF the regen was leakage-safe (E8.5);
  affected significantly otherwise
- **NEW Phase 0**: pre-rerun integrity check answering E8.5–E8.8
  before any other phase begins
- **NEW Phase L+1**: live serving hardening — wire `snapshot_id` into
  `load_platt_model_v2` WHERE clause, OR add a `recorded_at <= frozen`
  filter, OR move calibrator selection to a config-pinned `model_key`

## Files

- `01_calibration_pairs_provenance.md` — audit-A
- `02_platt_calibrator_fits.md` — audit-B
- `03_observation_provenance.md` — audit-C
- `04_live_serving_data_path.md` — audit-D
- `E8_AUDIT_SYNTHESIS.md` — this document
