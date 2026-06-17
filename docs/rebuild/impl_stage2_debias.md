# Stage 2 — DebiasAuthority implementation report

**Module:** `stage2_debias`
**Date:** 2026-06-14
**Worktree:** `/Users/leofitz/zeus/.claude/worktrees/qkernel-rebuild` (isolated; live daemon runs a different tree)
**Authority basis:** `docs/rebuild/consult_build_spec.md` DebiasAuthority Create block (lines 135-218) + Stage 2 block (lines 1053-1070); reconciled against `docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md`.

---

## 1. What was built

A single de-bias authority that decides whether — and by exactly how much — the forecast member center may be shifted toward settlement truth, replacing the scattered parallel mean-correction surfaces (live EDLI `effective_bias_c` subtraction, EMOS μ-offset, grid-representativeness row, raw-replacement correction).

### Files (NEW only — no live-file edits)

| Path | Symbols |
|---|---|
| `src/forecast/debias_authority.py` | `BiasArtifact` (frozen dataclass, 24 fields), `AppliedDebias` (frozen dataclass, 7 fields), `DebiasAuthority` (sole public method `apply`), constants `N_SIGMA_BIAS=2.0`, `SIGMA_FLOOR_EPSILON`, `CRPS_TOLERANCE`, `MIN_N`, `FRESHNESS_DAYS`, `CORRECTION_BASIS_PRIORITY`, helpers `min_n`, `crps_tolerance`, `_member_native_values`, `_station_matches`, `_product_matches`, `_source_mapping_matches`, `_correction_basis` |
| `tests/forecast/test_debias_authority.py` | the 3 spec-named RED-on-revert tests + fixtures |
| `tests/forecast/__init__.py` | package marker (the `tests/forecast/` dir did not exist before) |

`src/forecast/types.py` (`ForecastCase` / `RawModelMember` / `FreshModelSet`) and `src/probability/event_resolution.py` (`EventResolution`) already existed (Stage 1) and were **imported and used, not redefined**.

---

## 2. Spec lines implemented (exact)

- **139-164 `BiasArtifact`** — all 24 fields verbatim, in order, frozen. Verified by `dataclasses.fields`.
- **166-184 `AppliedDebias`** — all 7 fields verbatim, including the exact 8-member `activation_status` Literal (`APPLIED`, `NO_ARTIFACT`, `STALE_REFUSED`, `PRODUCT_MISMATCH_REFUSED`, `STATION_MISMATCH_REFUSED`, `OOS_HARM_REFUSED`, `MAGNITUDE_REFUSED`, `LOW_N_REFUSED`), frozen.
- **189-191** — `DebiasAuthority.apply(self, case, models) -> tuple[np.ndarray, AppliedDebias]` is the only public method.
- **195** — all raw members normalized to the settlement unit before any comparison; no artifact applies if its product/station/source mapping differs from the member (`_product_matches`, `_station_matches`, `_source_mapping_matches`).
- **202-210 activation rule** — implemented EXACTLY: `fresh` (training cutoff ≥ issue − 3d), `right_station`, `right_product` (`product_set_hash == models.model_set_hash` OR `model_id in member.model_id`), `enough_n` (n ≥ `min_n(case)`), `no_harm` (`oos_crps_after ≤ oos_crps_before + crps_tolerance(case)`), `magnitude_ok` (`abs(proposed_shift_native − residual_mean_native) ≤ N_SIGMA_BIAS · max(residual_std_native, sigma_floor_epsilon)`).
- **207-212** — `N_SIGMA_BIAS = 2.0` for live activation; the magnitude rule is a **model-validity REFUSAL inside the estimator**, not a downstream cap. Tokyo −4.847°C against a ≈−0.33°C trailing residual band fails the band → `MAGNITUDE_REFUSED`.
- **213-218 de-bias happens once** — `apply` selects EXACTLY ONE correction basis via the deterministic priority order `per_model_station_walk_forward > model_family_station_walk_forward > city_station_representativeness > no_debias`, applies it once, returns the chosen artifact id first in `artifact_ids` and marks all rejected artifacts in telemetry (never independently applied).

---

## 3. The corrected TRANSFORMATION (why the bad output is mathematically impossible, not gated)

Per operator law, the fix is the transform itself, not a detector that catches a bad value and leaves the broken transform in place. Two structural properties:

1. **The served shift IS the realized residual mean, never the artifact's claim.** When an artifact is admitted, `_apply_chosen` subtracts `chosen.residual_mean_native` (the realized trailing-band center) from the members — NOT `proposed_shift_native`. The artifact's claimed shift only gates admissibility via the magnitude band; it is never the value applied. So the served correction is bounded by realized residuals by construction (the Stage-2 live signal: "applied bias histogram bounded by realized residuals"). The −4.847 value is never multiplied into the members on any code path: a disagreeing artifact is refused (`MAGNITUDE_REFUSED`, no shift), and an agreeing artifact serves its realized band center.

2. **One shift, one basis — a second center-shift is unrepresentable.** There is no second independent center-shift surface in this module. `apply` chooses exactly one basis by priority and applies a single per-member shift vector. A second temperature-mean shift is not a value to detect; it has no construction site. (The live `_assert_single_temperature_mean_correction` antibody at `event_reactor_adapter.py:11498` was a *detector* over two parallel transforms; this module removes the parallel transforms instead.)

---

## 4. Drift resolved (spec ↔ live)

| Item | Spec | Live / resolution |
|---|---|---|
| Dependency types | spec lists `ForecastCase`/`RawModelMember`/`FreshModelSet` inline (build-spec lines 96-133) | These ALREADY EXIST in `src/forecast/types.py` (Stage 1). **Imported and used, not redefined.** Field names match the spec block exactly. |
| `member.model_id` (spec line 204 `artifact.model_id in member.model_id`) | reads as membership in a single member | Resolved toward the live `RawModelMember.model_id` (a `str`). Implemented as: artifact `model_id` ∈ the SET of member `model_id`s (`_product_matches`). This is the only coherent reading against the real type and matches the "per-model match" intent. |
| Settlement unit source | spec line 195 "normalized to the settlement unit" | `FreshModelSet.member_values_native` already carries values in the settlement native unit (per the Stage-1 `types.py` docstring); the settlement unit identity is `case.resolution.measurement_unit` (live `EventResolution`). Normalization point is `_member_native_values`; a `_c_to_native` helper is provided for any future °C-keyed artifact field, though current member values are already native. |
| Live artifact superseded | spec: "current EDLI branch reads `effective_bias_c` … then subtracts it from members; that behavior becomes illegal outside DebiasAuthority" | Read-only audit of `event_reactor_adapter.py:11406-11609`: the live path reads a single per-city `effective_bias_c` (degC, ×1.8 for °F-settled cities) keyed on season/metric/data_version/authority/error_model_family with **no settlement-station identity check and no realized-residual band check**, and subtracts it unconditionally when the flag is on. DebiasAuthority supersedes this with station+product+mapping identity, freshness, n, OOS no-harm, and the magnitude model-validity refusal. **No edit to `event_reactor_adapter.py`** — the live call sites `:11084` and `:12594` are neutralized later at Stage 11, per the drift ledger. |
| `tests/forecast/` dir | spec mandates `tests/forecast/test_debias_authority.py` | The directory did not exist (forecast tests live directly under `tests/`). Created `tests/forecast/` with `__init__.py` to honor the spec-named path. |

**Cotenancy note:** this is a shared worktree (task #100 bundles Stage 0 + Stage 2). `git status` shows a concurrent modification to `src/state/schema/no_trade_events_schema.py` and a new `src/decision/` dir — these are the Stage 0 sibling's work, **NOT mine**. My self-merge stages only my three paths by explicit pathspec.

---

## 5. Test results

`/Users/leofitz/zeus/.venv/bin/python -m pytest -q tests/forecast/test_debias_authority.py` → **3 passed in 0.80s**.

RED-on-revert verified by three independent revert-probes (each restored after):

| Revert probe | Target test | Result |
|---|---|---|
| Bypass station/product/fresh gates (force every artifact applicable) | `test_bias_row_must_be_fresh_product_matched_station_matched` | FAILED (`STATION_MISMATCH_REFUSED` → `APPLIED`) ✓ |
| Remove the magnitude gate + serve `proposed_shift_native` (the unconditional live subtraction) | `test_tokyo_minus_4847_bias_refused_against_realized_residual_band` | FAILED (`MAGNITUDE_REFUSED` → `APPLIED`) ✓ |
| Replace single-basis choice with summing all applicable shifts (parallel corrections) | `test_only_one_temperature_mean_shift_can_apply` | FAILED (chosen `art_per_model` → `art_family`) ✓ |

Each test fails iff the corrected transformation is reverted to the broken behavior the spec replaces.
