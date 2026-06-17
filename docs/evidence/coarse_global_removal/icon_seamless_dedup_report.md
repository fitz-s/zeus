# icon_seamless Alias-Dedup Removal Report

**Date:** 2026-06-17
**Commit:** 1d6c86c635
**Branch:** claude/agent-a62d5151be8421196

## Authority

icon_seamless is the Open-Meteo "ICON seamless" blend — bit-identical to icon_d2 inside the
German/EU nest (=icon_d2 100% of overlapping rows) and ≈icon_global elsewhere (≈79%). The
DWD/ICON family is already represented by icon_d2 (EU 2km), icon_eu (EU 7km), and icon_global
(worldwide). icon_seamless contributes no decorrelated information and was never a member of
DECORRELATED_PROVIDER_FAMILIES — it was only the alias-dedup probe. Operator + independent
statistical critic confirmed: drop it.

---

## Files Changed

### `src/forecast/model_selection.py`
- **Line 1-35 (header + module docstring):** Removed §3 reference to `DEDUP icon_seamless==icon_d2`; updated authority basis and docstring to reflect removal. Updated `# Last reused or audited: 2026-06-17`.
- **Lines ~137-139 (constants removed):** `ALIAS_CORR_THRESHOLD = 0.995` and `ALIAS_MEAN_ABS_DELTA_EPS = 0.05` module-level constants REMOVED (they existed only for the dedup probe; is_alias() still accepts these as kwargs with defaults).
- **Lines ~300 (SelectedModelSet docstring):** Updated `dropped_aliases` docstring — always empty now, retained for caller compat.
- **Lines ~324-437 (select_models function):** Removed the alias-dedup runtime block (5 lines that checked `"icon_seamless" in present` and conditionally appended to dropped_aliases). `alias_series` kwarg is still accepted (for caller compatibility) but is now ignored. `dropped_aliases` initializes as `[]` and is always empty.
- **Lines ~275-290 (is_alias function):** Updated docstring to note it's no longer called by select_models; retained as utility.

### `src/data/bayes_precision_fusion_capture.py`
- **Line 2 (header):** Updated `# Last reused or audited: 2026-06-17`.
- **Lines ~111-128 (OPENMETEO_MODEL_IDS):** Removed `"icon_seamless": "icon_seamless"` entry (with its comment). Updated module-level comment to note icon_seamless removal.
- **Line ~354 (candidate_models):** `+ ["icon_seamless"]` removed from candidate_models assembly. Now: `list(GLOBAL_LIKELIHOOD_MODELS) + list(REGIONAL_MODELS)`.
- **Lines ~428-433 (alias_series block):** Entire alias_series build block removed (`for m in ("icon_d2", "icon_seamless"): ...`). `select_models()` call no longer passes `alias_series`.
- **Line ~335 (docstring):** Updated to remove dedup reference.

### `src/data/bayes_precision_fusion_download.py`
- **Lines ~17-18 (module docstring):** Updated to include icon_seamless in the list of models dropped 2026-06-17.
- **Lines ~129 (OPENMETEO_PREVIOUS_RUNS_SOURCE_ID):** `"icon_seamless": "icon_d2_previous_runs"` RETAINED with a comment explaining it resolves product-identity of existing history rows as they age out under 180d retention. Not a forward-fetch surface.
- **Lines ~256-263 (BAYES_PRECISION_FUSION_EXTRA_MODELS):** Removed `+ ("icon_seamless",)`. Updated comment to note removal.

### `src/data/replacement_fusion_upgrade_trigger.py`
- **Lines ~52-55 (comment block):** Updated comment: "icon_seamless / the ECMWF anchor are intentionally NOT here" → updated to past tense ("icon_seamless was also NOT here and has since been removed from the candidate set entirely (2026-06-17)").
- **Line ~129 (decorrelated_provider_families_of docstring):** Updated to past tense for icon_seamless.
- **No logic changes.**

---

## dropped_aliases Consumer Trace

`dropped_aliases: tuple[str, ...]` is a field on `SelectedModelSet`. Consumers:

1. **`SelectedModelSet.used_models` property** — excludes models in dropped_aliases from the returned tuple. With dropped_aliases always empty, behavior is unchanged (no model was being excluded that wasn't already absent from GLOBAL_LIKELIHOOD_MODELS/REGIONAL_MODELS).

2. **`bayes_precision_fusion_capture.capture_bayes_precision_instruments()`** — receives the SelectedModelSet as `selection` and returns it in `BayesPrecisionFusionCaptureResult.selection`. It never iterates dropped_aliases itself.

3. **`replacement_forecast_materializer`** — serializes `selection.dropped_aliases` into `provenance_json["bayes_precision_fusion"]["dropped_aliases"]`. This field will now always be an empty list `[]` in new posteriors. Old posteriors with a non-empty dropped_aliases are historical reads — no consumer gates on it (it is provenance, not a decision gate).

4. **Tests** — the old tests that asserted `"icon_seamless" in dropped_aliases` have been replaced with RED-on-revert antibodies (see below).

**Decision:** Keep `dropped_aliases` field (always empty). This preserves the provenance schema for old posterior rows and avoids breaking callers that deserialize the field.

---

## Test Changes (RED-on-Revert)

### `tests/test_bayes_precision_fusion_model_selection_gate.py`

**Removed:** `test_select_models_dedups_icon_seamless_against_icon_d2` — asserted icon_seamless appeared in `sel.dropped_aliases`.

**Added:** `test_icon_seamless_never_in_candidate_set` — RED-on-revert antibody:
- Asserts `"icon_seamless" not in GLOBAL_LIKELIHOOD_MODELS`
- Asserts `"icon_seamless" not in REGIONAL_MODELS`
- Asserts `"icon_seamless" not in BAYES_PRECISION_FUSION_EXTRA_MODELS`
- Asserts that even if a stray icon_seamless value appears in `present_models`, `select_models()` never emits it in `used_models`, `likelihood_globals`, or `regional_experts`.

Re-adding icon_seamless to any candidate set flips this RED.

### `tests/test_replacement_0_1_bayes_precision_fusion_materializer_wiring.py`

**Removed:** `test_flag_on_dedup_drops_icon_seamless` — asserted icon_seamless appeared in `prov["dropped_aliases"]` and not in `prov["used_models"]`.

**Added:** `test_flag_on_icon_seamless_never_in_used_models` — RED-on-revert antibody:
- Asserts `"icon_seamless" not in prov["used_models"]`
- Asserts `"icon_seamless" not in prov.get("dropped_aliases", [])`
- Asserts `"icon_d2" in prov["used_models"]` (in-domain regional still enters)

### `tests/data/test_fusion_upgrade_trigger.py`

**Changed fixture (line ~100):** `used_models=["ecmwf_ifs", _NCEP, _DWD, "icon_seamless"]` → `["ecmwf_ifs", _NCEP, _DWD]` (icon_seamless was a stray non-family model in the fixture; removing it doesn't change the test logic since decorrelated_provider_families_of ignores it either way).

**Updated test `test_provider_family_mapping_excludes_anchor_alias_and_dropped_jma`** → renamed `test_provider_family_mapping_excludes_anchor_and_dropped_models`: added explicit assertion that `decorrelated_provider_families_of({"icon_seamless"}) == frozenset()` (stray rows in provenance must never inflate family count).

---

## Test Run Output

```
tests/test_bayes_precision_fusion_model_selection_gate.py    PASSED (all tests)
tests/test_bayes_precision_fusion_candidate_accrual_models.py PASSED (all tests)
tests/data/test_fusion_upgrade_trigger.py                    PASSED (all tests)
tests/test_replacement_0_1_bayes_precision_fusion_materializer_wiring.py PASSED (all tests)
```

**Result: 44 passed, 0 failed** (target test suite).

Money-path smoke (`tests/calibration/ + tests/decision/test_live_receipt_contract.py`):
- 128 passed, 5 failed
- The 5 failures are in `tests/calibration/test_emos_serve.py` (TestEmosPredictive, TestEmosHookMemberSource, TestEmosMetricGating) — confirmed pre-existing in this worktree's lineage by stash-verify (same 5 fail on `HEAD~1` before any of these changes). Unrelated to icon_seamless.

---

## Notes

- `OPENMETEO_PREVIOUS_RUNS_SOURCE_ID["icon_seamless"]` retained: historical `raw_model_forecasts` rows with `model="icon_seamless"` age out under 180d retention. The entry ensures they resolve their product-identity correctly during that window.
- `is_alias()` function retained as a utility (no callers now, but keeps the module API stable for any external tooling referencing it).
- The integration test `tests/integration/test_qkernel_spine_sources_multimodel.py` has `icon_seamless` in its `MULTIMODEL_C` fixture — this is intentional: it tests the qkernel spine DB accessor against historical raw_model_forecasts rows (not the fusion candidate set). That test is unaffected and continues to pass.
