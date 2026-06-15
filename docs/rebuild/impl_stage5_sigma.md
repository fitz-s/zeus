# Stage 5 — Sigma Authority: Implementation Report

- Created: 2026-06-14
- Authority basis: `docs/rebuild/consult_build_spec.md` lines 369-430 (sigma_authority
  Create block) + lines 1109-1125 (Stage 5 block); reconciled against
  `docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md` (GREENFIELD — no live edits).
- Module: `stage5_sigma`

## What was built

The single predictive-σ authority for the q-kernel forecast spine. No live path may
serve a σ below the realized walk-forward settlement error of the cell, and no
soft-anchor path may serve member-vote q without a σ. This kills the ~47%
single-degree modal-bin spike caused by the live materializer's constant-1.0°C floor
(`replacement_forecast_materializer.py:1119`,
`predictive_sigma_c = max(1.0, sqrt(fused.sd² + σ_resid²))`) being the final authority on
under-dispersed raw-replacement / day0 cells.

The correction is the TRANSFORMATION, not a downstream gate: the served σ is produced
by `sigma = max(sigma_before_floor, floor.rmse_native, floor.mad_sigma_native)` as the
LAST operation. There is no code path that returns an unfloored `sigma_before_floor`,
so a sub-realized σ is mathematically unrepresentable — not detected-and-clamped. The
soft-anchor branch likewise cannot reach the served-q path with a width-less q: it
either returns `live_eligible=False` / `PREDICTIVE_SIGMA_AUTHORITY_MISSING`, or a
conservative sigma-bearing fallback `max(global_lead_bucket_floor, realized_floor)`
with a receipt.

## Files + symbols

### NEW `src/forecast/sigma_authority.py`

Dataclasses (EXACT spec field names, frozen):
- `SigmaFloorArtifact` — spec lines 373-389. Fields: `artifact_id`, `authority`
  (`Literal["SETTLEMENT_RESIDUAL_WALK_FORWARD_SIGMA_V1"]`), `city`, `station_id`,
  `metric`, `season`, `regime_key`, `lead_bucket`, `training_cutoff_utc`,
  `valid_until_utc`, `n`, `rmse_native`, `mad_sigma_native`, `crps_calibration_status`,
  `source_hash`.
- `SigmaComponents` — spec lines 391-401. Fields: `raw_member_spread_native`,
  `model_dispersion_native`, `center_parameter_se_native`,
  `station_representativeness_sigma_native`, `day0_remaining_process_sigma_native`,
  `realized_floor_native`, `sigma_before_floor_native`, `sigma_after_floor_native`,
  `artifact_id`.
- `SigmaDecision` — the served-σ decision wrapper: `sigma_native`, `components`,
  `floor_artifact`, `live_eligible`, `ineligibility_reason`, `receipt`.

Functions:
- `realized_sigma_floor(case) -> Optional[SigmaFloorArtifact]` — spec line 420. Sources
  the realized walk-forward floor from the live `settlement_sigma_floor(city, season,
  metric)` table (detrended trailing-window settlement std × `k_default`,
  `emos.py:175`). Returns `None` when the cell is absent (that absence drives the
  soft-anchor eligibility decision; it is NOT silently replaced by a constant).
- `model_dispersion_sigma`, `center_parameter_se_sigma`,
  `station_representativeness_sigma`, `day0_remaining_process_sigma` — the candidate
  components (spec lines 407-411). `day0_remaining_process_sigma` reproduces the
  materializer `max(1.0, sqrt(fused.sd² + σ_resid²))` construction as an INTERNAL
  candidate only.
- `global_lead_bucket_floor(case)`, `lead_bucket_for(case)`, `_c_to_native`,
  `_weighted_spread`, `_source_hash` — helpers.
- `build_sigma(case, models, *, fused_center_sd_native=None, sigma_resid_native=None,
  has_fusion_capture=True) -> SigmaDecision` — the σ algorithm (spec lines 403-430),
  verbatim RSS + realized-floor `max`, plus the soft-anchor eligibility branch.

### NEW `tests/forecast/test_sigma_authority.py`

- `test_sigma_never_below_realized_floor_on_emos_raw_replacement_day0` (spec RED-on-revert)
- `test_soft_anchor_without_sigma_is_not_live_eligible` (spec RED-on-revert)
- `test_soft_anchor_with_floor_serves_conservative_sigma_with_receipt` (companion — line 430 fallback)
- `test_predictive_sigma_is_composed_honest_width_without_realized_floor` (companion)

## Spec lines implemented

| Spec | Implemented as |
|---|---|
| 373-389 `SigmaFloorArtifact` | `SigmaFloorArtifact` dataclass, exact fields |
| 391-401 `SigmaComponents` | `SigmaComponents` dataclass, exact fields |
| 407-411 candidate components | `model_dispersion_sigma` / `center_parameter_se_sigma` / `station_representativeness_sigma` / `day0_remaining_process_sigma` |
| 413-418 `sigma_before_floor = sqrt(model²+param²+station²+day0²)` | RSS in `build_sigma` |
| 420 `floor = realized_sigma_floor(case)` | `realized_sigma_floor` |
| 421 `sigma = max(sigma_before_floor, floor.rmse_native, floor.mad_sigma_native)` | the served-σ `max` in `build_sigma` |
| 423 constant 1.0 is an internal candidate, never final | folded into `day0_remaining_process_sigma` only; realized-floor max dominates |
| 426-428 soft-anchor → `live_eligible=False`, `PREDICTIVE_SIGMA_AUTHORITY_MISSING` | `build_sigma` `has_fusion_capture=False` + no floor branch |
| 430 conservative fallback `max(global_lead_bucket_floor, realized_floor)` + receipt | `build_sigma` soft-anchor + floor branch |
| 1112 "No sub-realized σ" live signal | enforced by the `max` being the last op (no unfloored return path) |

## Drift resolved

- **GREENFIELD honored** — only the two new files were created; no live file was
  touched. The module is wired into the reactor later (integration), not now.
- **State files absent in worktree** — `state/settlement_sigma_floor.json` and
  `state/sigma_scale_fit.json` do not exist in this isolated worktree (live tree only).
  Resolved by COMPOSING the live read-side functions (`settlement_sigma_floor`,
  `emos_sigma_model`) rather than re-reading the JSON, matching the live type/return
  shape (`Optional[float]`, °C-native). Tests monkeypatch these read functions to
  inject realized-floor / no-floor scenarios, so the transformation (the `max` and the
  eligibility branch) is exercised independently of the on-disk table.
- **Realized-floor MAD source** — the spec's `SigmaFloorArtifact` carries both
  `rmse_native` and `mad_sigma_native`, but the live `settlement_sigma_floor` exposes a
  single realized trailing-window settlement std magnitude (no separate MAD series at
  this read seam). Resolved toward the live type: both `rmse_native` and
  `mad_sigma_native` are set to that one realized magnitude (converted to the
  settlement native unit). Both are realized, neither is a constant; the downstream
  `max(rmse, mad)` is well-defined and honest. A separately-fitted MAD series can be
  threaded later without changing the algorithm shape.
- **Unit normalization** — `settlement_sigma_floor` / `emos_sigma_model` are °C-native;
  σ magnitudes are converted to the settlement native unit via `× 9/5` (no offset) so
  the RSS and the floor `max` compare like with like for °F cities.
- **`n` field on the floor artifact** — the live `settlement_sigma_floor` table does not
  expose the realized residual sample count at the read seam, so the artifact records
  `n=0` and `crps_calibration_status="SETTLEMENT_FLOOR_TABLE"` to mark its provenance
  honestly (the magnitude is the realized std the table already validated; the count is
  not re-exposed here). This is provenance honesty, not a value the transform depends on.

## Constraint compliance (operator law)

- The corrected transform makes the bad output mathematically impossible: the served σ
  is `max(...)` as the last operation, so no detector/clamp/shadow-flag is used to catch
  a sub-floor value — the value cannot be produced. The soft-anchor branch returns
  before any served-q path when no σ authority exists.
- NEW FILES ONLY; no live file modified.
- Not committed / not git-added (orchestrator commits).

## Test results

### `tests/forecast/test_sigma_authority.py` (pytest tail)

```
....                                                                     [100%]
4 passed in 0.80s
```

### RED-on-revert verification

- REVERT 1 (drop the realized-floor `max`, serve `sigma_before_floor`):
  `test_sigma_never_below_realized_floor_on_emos_raw_replacement_day0` FAILS
  (AssertionError at the `sigma_native >= realized_floor_native` invariant). Restored → passes.
- REVERT 2 (soft-anchor serves member-vote q at the raw spread, `live_eligible=True`):
  `test_soft_anchor_without_sigma_is_not_live_eligible` FAILS (AssertionError on the
  `live_eligible is False` assertion). Restored → passes.

### Money path (`tests/money_path tests/strategy/live_inference`) pytch tail

```
........................................................................ [ 87%]
...........................................                              [100%]
331 passed in 4.57s
```

Money path unaffected (331 passed) — the module is new and not yet wired into any live
path.
