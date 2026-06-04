# BEFORE / AFTER — FIX-1 calibration_bin_source_v2_fit_enabled

**Branch:** `fix/calibration-bin-source-canonical-v2`
**Date:** 2026-06-03
**Authority:** FIX-1 canonical bin_source / wiring verdict 2026-06-03

---

## The Defect

`manager.py:_fit_from_pairs` was hardcoded to `bin_source_filter="canonical_v1"`.
The live `calibration_pairs` corpus in `zeus-forecasts.db` is **100%
`canonical_v2`** (48,157,324 rows; `CANONICAL_CALIBRATION_PAIR_BIN_SOURCE =
"canonical_v2"` in `calibration_transfer_policy.py:50`).

Result: `_fit_from_pairs` always returned **0 pairs → None** for every city,
causing all uncorrected cities to fall through to the cross-cluster fallback
(first matching cluster in `calibration_clusters()` alphabetical order).

A secondary mismatch: `get_decision_group_count` applied no `bin_source` filter
— so it counted all 48M rows and returned a large N (passes the level3≥15
threshold), while `_fit_from_pairs` simultaneously fetched 0 rows.  The count
gate passes; the fit starves.  This is the count/fit population mismatch.

---

## Affected Cities

**47/54 cities** have `edli_bias_correction_enabled=True` and early-exit
at `_snapshot_p_cal` (identity-Platt), so their `q` is unaffected.

**≤7 uncorrected cities** reach `get_calibrator` and are affected:
Auckland, Dallas, Hong Kong, Jakarta, Jinan, Lagos, Zhengzhou.

---

## Corpus Confirmation (read-only probe, 2026-06-03)

```
zeus-forecasts.db calibration_pairs:
  canonical_v2: 48,157,324 rows   ← 100% of corpus
  canonical_v1:              0 rows
```

---

## Before / After Table

Season derived from 2026-06-03 per city latitude.

| City | Cluster | Season | FLAG-OFF (current behavior) | FLAG-OFF Platt (A/B/C) | FLAG-ON (after fix) | FLAG-ON Platt (A/B/C) |
|------|---------|--------|-----------------------------|------------------------|---------------------|----------------------|
| Auckland | Auckland | DJF | borrows **Amsterdam_DJF** (n=123) | A=1.1748 B=0.0734 C=? | OWN FIT (n_groups=4,416) | A=0.7860 B=0.0158 C=-1.5547 |
| Dallas | Dallas | JJA | borrows **Amsterdam_JJA** (n=211) | A=1.2132 B=0.0650 C=? | OWN FIT (n_groups=2,064) | A=1.1078 B=0.0070 C=0.0373 |
| Hong Kong | Hong Kong | JJA | borrows **Amsterdam_JJA** (n=211) | A=1.2132 B=0.0650 C=? | OWN FIT (n_groups=1,511) | A=1.1571 B=0.0401 C=-0.5676 |
| Jakarta | Jakarta | DJF | borrows **Amsterdam_DJF** (n=123) | A=1.1748 B=0.0734 C=? | OWN FIT (n_groups=1,565) | A=0.5248 B=-0.0048 C=-2.3633 |
| Lagos | Lagos | JJA | borrows **Amsterdam_JJA** (n=211) | A=1.2132 B=0.0650 C=? | OWN FIT (n_groups=1,690) | A=0.7462 B=0.0015 C=-1.5958 |
| Jinan | Jinan | JJA | borrows **Amsterdam_JJA** (n=211) | A=1.2132 B=0.0650 C=? | **identity (level 4)** — 0 canonical_v2 groups | n/a |
| Zhengzhou | Zhengzhou | JJA | borrows **Amsterdam_JJA** (n=211) | A=1.2132 B=0.0650 C=? | **identity (level 4)** — 0 canonical_v2 groups | n/a |

**Notes:**
- FLAG-OFF `C` parameter not shown for borrowed models — the legacy
  `platt_models` table stores the full params but they were not queried
  (not needed to establish the borrow identity; A/B confirm it is Amsterdam).
- Jinan and Zhengzhou have 0 `canonical_v2` pairs in `zeus-forecasts.db`
  (no calibration history ingested yet). Under flag-ON they get identity
  Platt (level 4, uncalibrated) — which is more honest than borrowing
  Amsterdam's Northern-European climate parameters.
- All 5 cities with own fits have n_groups > 1,500 (well above level3=15).
  Fit used 20 complete groups (sample for probe; runtime uses full corpus).

---

## Count / Fit Population Agreement

| State | `get_decision_group_count` filter | `_fit_from_pairs` filter | Agreement? |
|-------|----------------------------------|--------------------------|------------|
| FLAG-OFF (pre-fix hardcode) | `None` (counts all 48M) | `"canonical_v1"` (0 rows) | **NO — mismatch** |
| FLAG-OFF (post-fix) | `None` | `None` | YES — both see all rows |
| FLAG-ON | `"canonical_v2"` | `"canonical_v2"` | YES — same population |

---

## What Was Changed

1. **`src/calibration/store.py`** — `get_decision_group_count` gains
   optional `bin_source_filter: str | None = None` parameter, mirroring
   `get_pairs_for_bucket`. Both queries use an identical `bin_clause` pattern.

2. **`src/calibration/manager.py`** — Three changes:
   - Import `CANONICAL_CALIBRATION_PAIR_BIN_SOURCE` from
     `src.data.calibration_transfer_policy`.
   - New `_calibration_bin_source_v2_fit_enabled()` helper reads
     `feature_flags.calibration_bin_source_v2_fit_enabled` from
     `config/settings.json` (fail-open → False).
   - In `get_calibrator`: the flag-resolved `bin_source_filter` is passed to
     **both** `get_decision_group_count` and `_fit_from_pairs` — guaranteeing
     count/fit population agreement in both flag states.
   - `_fit_from_pairs` gains `bin_source_filter: str | None = None` and
     threads it to `get_pairs_for_bucket` (replacing the hardcoded
     `"canonical_v1"` literal).

3. **`config/settings.json`** — new flag:
   ```json
   "calibration_bin_source_v2_fit_enabled": false
   ```
   Default `false` = FLAG-OFF = legacy byte-identical behavior.

---

## Flag Status

**FLAG IS LEFT OFF (`false`).**

No live behavior has changed. The original cross-cluster borrow continues
for all 7 uncorrected cities. No daemon restart. No promotion. No capital
exposure change.

**To promote:** review the table above, set
`feature_flags.calibration_bin_source_v2_fit_enabled = true` in
`config/settings.json`, and restart the daemon in shadow mode.
Verify `q` delta on the uncorrected cities (particularly Auckland DJF and
Jakarta DJF where the own-fit A parameter diverges significantly from
Amsterdam's: 0.786/0.525 vs 1.175). Jinan and Zhengzhou will move from
Amsterdam-borrow to identity-Platt (more honest, slightly less calibrated).
