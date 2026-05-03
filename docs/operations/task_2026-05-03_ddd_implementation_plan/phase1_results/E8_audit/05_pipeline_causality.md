# E8.5 — Pipeline causality audit

Created: 2026-05-03
Authority: read-only code audit (haiku-E, content reconstructed by main session from agent return summary because the agent's Write call did not persist)

## Headline

- Op-1 (pair generation) causal-safe? **YES**
- Op-2 (calibrator fit) causal-safe? **YES**
- Train/test split logic: No explicit train/test split in fitting; uses all VERIFIED pairs for the bucket. Decision-group bootstrap for uncertainty.
- Any 2026-04-29 ad-hoc regen script found? Yes, `scripts/rebuild_calibration_pairs_v2.py` and `scripts/refit_platt_v2.py`.

## Op-1: Pair generation causality

### Call tree

INSERT calibration_pairs_v2 at `src/calibration/store.py:233`
  ← `add_calibration_pair_v2` @ `src/calibration/store.py:188`
    ← `harvest_settlement` @ `src/execution/harvester.py:1655`
      ← `process_settlement` @ `src/execution/harvester.py:615` (looping over learning_contexts)

For rebuilds:
  ← `add_calibration_pair_v2` @ `src/calibration/store.py:188`
    ← `_process_snapshot_v2` @ `scripts/rebuild_calibration_pairs_v2.py:359`
      ← `rebuild_v2` @ `scripts/rebuild_calibration_pairs_v2.py:490`

### `p_raw` source

File: `src/execution/harvester.py:1379`
```python
return {
    "p_raw_vector": json.loads(row["p_raw_json"]),
    "lead_days": float(row["lead_hours"]) / 24.0,
    "issue_time": issue_time,
    "available_at": row["available_at"],
```

`p_raw` is pulled from `ensemble_snapshots_v2` (or legacy `ensemble_snapshots`) using a `snapshot_id` resolved during settlement. The join is constrained by `expected_target_date` in `_snapshot_row_by_id`.

### `outcome` source

File: `scripts/rebuild_calibration_pairs_v2.py:358`
```python
outcome = 1 if b is winning_bin else 0
```

`winning_bin` is derived from `settlement_value`, which comes from `_fetch_verified_observation(conn, city.name, target_date, spec=spec)`.

File: `scripts/rebuild_calibration_pairs_v2.py:193`
```python
SELECT city, target_date, {obs_column} AS observed_value, unit, authority, source
FROM observations
WHERE city = ? AND target_date = ? AND authority = 'VERIFIED'
```

The observation lookup is strictly keyed to the `target_date`. There is no windowing that pulls data from `target_date + 1`.

## Op-2: Calibrator fit causality

### Call tree

INSERT platt_models_v2 at `src/calibration/store.py:502`
  ← `save_platt_model_v2` @ `src/calibration/store.py:474`
    ← `_fit_bucket` @ `scripts/refit_platt_v2.py:333`
      ← `refit_v2` @ `scripts/refit_platt_v2.py:461`

### Training-pair selection

File: `scripts/refit_platt_v2.py:241`
```python
return conn.execute("""
    SELECT p_raw, lead_days, outcome, range_label, decision_group_id
    FROM calibration_pairs_v2
    WHERE temperature_metric = ?
      AND training_allowed = 1
      AND authority = 'VERIFIED'
      AND cluster = ? AND season = ? AND data_version = ?
""")
```

The query pulls all verified pairs for the specific bucket. There is no `target_date` cutoff because the `calibration_pairs_v2` table itself is only populated when a target date has settled.

### Train/test split

`src/calibration/platt.py:88`:
```python
def fit(
    self,
    p_raw: np.ndarray,
    lead_days: np.ndarray,
    outcomes: np.ndarray,
    bin_widths: np.ndarray | None = None,
    decision_group_ids: np.ndarray | None = None,
    n_bootstrap: int | None = None,
    regularization_C: float = 1.0,
    rng: np.random.Generator | None = None,
) -> None:
```

The `fit` method uses the provided data directly for the primary fit and performs a decision-group bootstrap for uncertainty estimation. **It does not perform a temporal split.** This is the production design: the fit consumes everything settled.

## 2026-04-29 regen evidence

- Script: `scripts/rebuild_calibration_pairs_v2.py`
- Companion: `scripts/refit_platt_v2.py`
- Commit: `8d5b4147 Session 2026-04-28: TIGGE preflight + LOW backfill + obs provenance demolition`
- Preflight verifies `calibration_pairs_v2.causality_safe` at `scripts/verify_truth_surfaces.py:1525`

## Verdict

The pipeline is **causally clean**. Row generation strictly matches forecast-issue context with the specific `target_date` observation. Fitting uses all available settled pairs. The bootstrap respects decision-group boundaries.

**The timestamp collapse the operator noticed is cosmetic** — it tells us "the most recent regen was 2026-04-29", nothing more. The math result is independent of when the regen ran, because both row generation and calibrator fit are causally keyed to `target_date`.

**Caveat for §2.2 evaluation**: the production fit deliberately uses every settled pair. If the original §2.2 evaluation measured Brier on rows that were ALSO in the fit's training set, that's an in-sample evaluation artifact — but it is a property of the §2.2 evaluation script's design, NOT of the regen event. To get a clean §2.2 measurement, the evaluation must refit Platt on a held-out target_date split, or use cross-validation; the production calibrators themselves are correctly designed to absorb every settled pair.
