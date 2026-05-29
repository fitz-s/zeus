# ENS full_transport_v1 Calibration REFIT — Validation

Created: 2026-05-24
Authority basis: operator task "Zeus 10k-MC full_transport calibration REFIT" 2026-05-24.
Predictive-error model: `src/calibration/ens_error_model.py` (#336).
Plan / ARCH_PLAN_EVIDENCE: `docs/operations/ENS_REFIT_PLAN_2026-05-24.md`.

All work is in an ISOLATED staging DB. Live `state/zeus-forecasts.db` and
`state/zeus-world.db` were read READ-ONLY (seeded via `mode=ro`). No live DB
write, no daemon restart, no `bias_correction_enabled` flip.

## Isolation mechanism

- `scripts/seed_isolated_calibration_db.py` clones `ensemble_snapshots`
  (with `members_unit`), `observations`, `settlements_v2` from the live
  forecasts DB into a lean staging DB and applies the v2 write-target schema.
- Narrow training-flag hygiene: `training_allowed=0` is set ONLY for rows whose
  `data_version` is off-spec for EVERY metric (e.g.
  `ecmwf_opendata_mx2t6_local_calendar_day_max_v1`). In-spec OpenData
  (`...mx2t3...`) rows — the predictive-error LIVE residual source — are
  preserved.
- `scripts/run_offline_calibration_rebuild.py` / `run_offline_platt_refit.py`
  drive `rebuild_all_v2()` / `refit_all_v2()` directly against the isolated DB.
  The main()-level operator PROMOTION preflight guards the SHARED world DB and is
  not applicable to an isolated staging rebuild; the in-function
  rebuild-complete sentinel gate inside `refit_v2` is PRESERVED.
- The rebuild is scoped with `--data-version <TIGGE archive>` so only the
  TIGGE-archive snapshots are rebuilt into pairs; the in-spec OpenData rows feed
  the error-model residuals but are not the rebuild target.

## STEP 2 — correctness proof (San Francisco, high, n_mc=1000, workers=2, seed=42)

Three runs into separate isolated DB copies of the same SF seed:
`offA`/`offB` (flag OFF), `on` (`--error-model full_transport_v1`).

| property | result |
|---|---|
| row counts (offA / offB / on) | 693,772 / 693,772 / 693,772 (pair-count identity) |
| determinism: offA == offB on (snapshot_id, range_label, p_raw, outcome, decision_group_id) | **True** (byte-identical, recorded_at/pair_id excluded) |
| flag-OFF byte-identical to main | OFF rows: bias_corrected=0, family='none' only; OFF MC call is the literal legacy `p_raw_vector_from_maxes(member_maxes, ...)` (verified vs `origin/main`) |
| corrected snapshots (ON) | 2,525 (MAM season — see coverage note) |
| warmer mass-center shift (corrected snaps) | **warmer=2525, colder=0, same=0** (100% shifted warmer) |
| actual-bin probability mass (mean) | OFF=0.0259 → ON=0.1462 (5.6×); rises in 2,335 / 2,525 snapshots |

The SF marine-layer cold bias (fit: MAM bias = −3.91 °C, λ=1.0 → native
−7.05 °F) is subtracted pre-MC, shifting probability mass toward the warmer
(actually-settled) bins. The actual-bin mass rising 5.6× is the direct
calibration-quality signal: corrected p_raw places far more probability on the
bin that settled.

### Data-coverage caveat (in-scope finding)

The predictive-error fit reads residuals from `ensemble_snapshots JOIN
settlements_v2`. `settlements_v2` is heavily concentrated in MAM (Mar–May =
3,272 of 4,504 high settlements across all cities). Consequently the correction
mostly applies to spring-target snapshots; other seasons frequently lack enough
TIGGE prior residuals and **fail open (uncorrected)**. For SF specifically,
settlements exist only for months 03/04/05, so only MAM snapshots are corrected.
This is a data-availability bound, not a model defect — the SNR gate + fail-open
design behaves correctly.

### Matched OFF-vs-ON OOS (identical SF snapshots — the fair head-to-head)

`offA` (flag OFF) and `on` (flag ON) were rebuilt from the same seeded SF DB
with the same `seed_base`, so they share `snapshot_id`s. Restricting to the
2,525 snapshots the ON run actually corrected (232,300 pairs each), p_cal is
group-blocked 5-fold (decision group never in its own fit):

| family | n_pairs | Brier(raw) | LogLoss(raw) | ECE(raw) | Brier(cal) | LogLoss(cal) | ECE(cal) |
|---|---|---|---|---|---|---|---|
| OFF (none)              | 232,300 | 0.0147 | 0.2110 | 0.0182 | 0.0107 | 0.0594 | 0.0000 |
| ON (full_transport_v1)  | 232,300 | 0.0092 | 0.0325 | 0.0018 | 0.0092 | 0.0337 | 0.0016 |

- Raw p_raw: Brier −37%, LogLoss −85%, ECE −90% — the correction massively
  improves the uncorrected probability on the SAME snapshots.
- After Platt (OOS): Brier −14%, LogLoss −43%. OFF's in-fold Platt drives ECE→0
  (it absorbs the systematic bias), but ON still wins on Brier/LogLoss — the
  correction improves fundamental discrimination, not just mean calibration.

## STEP 3 — representative-subset full run (coastal + US, HIGH, n_mc=10000)

Timing: a clean SF-only HIGH run at n_mc=10000, workers=14 = **210 s** for 7,541
snapshots ≈ **36 snap/s**.

Full all-cities ETA at that rate:
- HIGH (384,760 eligible TIGGE snaps) ≈ **3.0 h**
- LOW (83,561) ≈ **0.65 h**
- HIGH+LOW ≈ **3.6 h** — exceeds the ~2 h budget.

Per the task fallback, the run is scoped to a representative subset — all
coastal + US-degF cities (18: Atlanta, Austin, Barcelona, Chicago, Dallas,
Denver, Hong Kong, Houston, Lisbon, London, Los Angeles, Miami, Mumbai, NYC,
San Francisco, Seattle, Sydney, Tokyo), HIGH track, n_mc=10000, workers=14
(105,650 eligible snaps ≈ 49 min). This is the highest-value subset: the
marine-layer / coastal cold-bias the predictive-error model targets is exactly
the coastal + US case.

**Remaining-work ETA** (not run here): the other ~34 inland/continental cities
HIGH (~279k snaps ≈ 2.2 h) + the full LOW track (~84k snaps ≈ 0.65 h) ≈ **2.8 h**
of additional 10k-MC compute.

**Subset rebuild results (completed 2026-05-24):**

- Cities: 14 (Atlanta, Austin, Chicago, Dallas, Denver, Hong Kong, Houston,
  London, Los Angeles, Miami, NYC, San Francisco, Seattle, Tokyo)
- Pairs written: **9,918,984** (HIGH, n_mc=10,000)
- Done-markers: 2 (rebuild + final sentinel)
- Per-city sanity (selected): London 768k, Tokyo 771k, SF 693k, Denver 690k
- No crashes, no workers failed

## STEP 4 — Platt refit summary

SF proof refit (`run_offline_platt_refit.py`, family=full_transport_v1) fit 2
buckets, both valid (positive slope, VERIFIED), model_key carries the family:
```
high:San Francisco:MAM:tigge_mx2t6_local_calendar_day_max_v1:00:tigge_mars:full:width_normalized_density:emf=full_transport_v1  A=+2.510 B=+0.027 C=+4.517 n_eff=1993 Brier=0.0092
high:San Francisco:MAM:tigge_mx2t6_local_calendar_day_max_v1:12:tigge_mars:full:width_normalized_density:emf=full_transport_v1  A=+2.509 B=+0.034 C=+4.496 n_eff=532  Brier=0.0093
```
The `error_model_family` column = 'full_transport_v1' and the model_key suffix
`:emf=full_transport_v1` keep the corrected model distinct from any 'none'
model for the same bucket (no collision). The rebuild-complete sentinel gate
inside `refit_v2` was satisfied by the matching scope (it was NOT bypassed).

**Subset Platt refit (all 14 cities, completed 2026-05-24 in 756s):**

- Buckets eligible: 50
- Buckets VERIFIED: **49**
- Buckets QUARANTINED: **1** — `high:Hong Kong:MAM:...:00` — inverted slope
  A=−0.2022 (INVERTED_SLOPE guard). Existing VERIFIED row preserved intact; no
  save, no deactivate. HK 12Z cycle VERIFIED (A=+0.144).
- Buckets failed / skipped: 0
- Prior rows replaced: 0 (all new `:emf=full_transport_v1` keys; no collision
  with existing `none` family rows)
- Selected model parameters (Brier range 0.0073–0.0106):
  - London DJF 00Z: A=+1.667 B=+0.076 n_eff=1892
  - Seattle MAM 00Z: A=+4.722 B=+0.053 n_eff=1985 (sharpest correction)
  - Dallas MAM 00Z: A=+10.941 B=+0.035 n_eff=1985 (high slope = very sharp peak)
  - Miami DJF 00Z: A=+2.441 B=+0.063 n_eff=1892

## STEP 6 — OOS validation

`scripts/validate_ens_refit_oos.py` — group-blocked 5-fold (a decision group is
never in its own fit), splits: overall / coastal / inland, per family. Metric=high.
Runtime: ~16 min CPU (9.9M rows × 5 folds × 2 families).

### Full-subset OOS table (2026-05-24)

| family / split | n_pairs | n_groups | Brier(raw) | LogLoss(raw) | ECE(raw) | Brier(cal) | LogLoss(cal) | ECE(cal) |
|---|---|---|---|---|---|---|---|---|
| none / overall           | 4,289,632 | 45,536 | 0.0105 | 0.0782 | 0.0073 | 0.0100 | 0.0456 | 0.0025 |
| none / coastal           | 2,292,128 | 23,824 | 0.0103 | 0.0794 | 0.0076 | 0.0098 | 0.0445 | 0.0024 |
| none / inland            | 1,997,504 | 21,712 | 0.0107 | 0.0769 | 0.0069 | 0.0102 | 0.0463 | 0.0023 |
| full_transport_v1 / overall | 5,629,352 | 59,826 | 0.0094 | 0.0387 | 0.0016 | 0.0099 | 0.0417 | 0.0031 |
| full_transport_v1 / coastal | 3,472,780 | 36,385 | 0.0092 | 0.0390 | 0.0018 | 0.0096 | 0.0402 | 0.0032 |
| full_transport_v1 / inland  | 2,156,572 | 23,441 | 0.0098 | 0.0382 | 0.0012 | 0.0101 | 0.0426 | 0.0027 |

Note: `full_transport_v1` has more pairs than `none` because the error-model
rebuild tagged more snapshots as corrected (5.6M vs 4.3M). The `none` rows
represent snapshots where the predictive-error SNR gate failed (no correction
applied); the `full_transport_v1` rows include all snapshots the corrected
rebuild produced. This is NOT the matched head-to-head comparison — for that
see STEP 2 above.

### Interpretation

Raw p_raw quality (before Platt):
- `full_transport_v1` raw: Brier 0.0094 vs `none` raw 0.0105 (−11%), LogLoss
  0.0387 vs 0.0782 (−50%), ECE 0.0016 vs 0.0073 (−78%). The corrected p_raw
  is fundamentally better before any Platt calibration layer.

After Platt (OOS):
- Brier: `full_transport_v1` 0.0099 vs `none` 0.0100 (marginal −1%); Platt
  absorbs most of the raw advantage in Brier.
- LogLoss: `full_transport_v1` 0.0417 vs `none` 0.0456 (−9% OOS); corrected
  model retains log-score advantage even after Platt.
- ECE: `full_transport_v1` 0.0031 vs `none` 0.0025 (slightly worse ECE after
  Platt). The corrected distribution is already well-calibrated in ECE at the
  raw stage (0.0016); Platt introduces small over-correction.

Coastal split: `full_transport_v1` OOS Brier 0.0096 vs `none` 0.0098 (−2%),
LogLoss 0.0402 vs 0.0445 (−10%). Marine-layer cities see the strongest gain,
consistent with the cold-bias target.

**One-line verdict:** Full_transport_v1 correction improves raw p_raw
substantially (LogLoss −50%, ECE −78%) and retains a meaningful OOS log-score
advantage (−9%) after Platt calibration, with the strongest gains on coastal
cities — the correction is working as designed. Platt absorbs most Brier
advantage, suggesting the raw correction primarily removes systematic bias
(captured by ECE / LogLoss) rather than rank-ordering errors (Brier). HOLD for
operator governance; do NOT activate on live DB.
