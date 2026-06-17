# verifier proof-of-done for coarse_global_removal
HEAD: 7017b398d752fe3fb9425b7d6aa1b09b6e737038
Verifier: verifier
Date: 2026-06-17

## Claim
Drop gfs_global (0.25°/25km) and gem_global (~15km) from live T2 multi-model fusion (DECORR_GLOBALS, NCEP_FAMILY, GEM_FAMILY, download extra-models), make 5-provider completeness contract domain-aware so non-CONUS/non-NA cities are COMPLETE without NCEP/CMC, and remove dead forward registry specs.

## Verdict
GREEN

---

## CHECK 1 [STATUS: VERIFIED]
Acceptance criteria — full contract suite green.

Command:
```
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/data/test_fusion_upgrade_trigger.py tests/architecture/ \
  tests/test_bayes_precision_fusion_model_selection_gate.py \
  tests/test_bayes_precision_fusion_candidate_accrual_models.py \
  tests/test_bayes_precision_fusion_arrival_guard.py \
  tests/test_bayes_precision_fusion_gem_current_value_previous_runs_fallback.py \
  tests/test_openmeteo_call_budget.py \
  tests/test_replacement_0_1_bayes_precision_fusion_materializer_wiring.py \
  tests/test_bayes_precision_fusion_download.py
```

Result: **108 passed in 2.04s** — zero failures.

Money-path / live-inference suite:
```
.venv/bin/python -m pytest -q -p no:cacheprovider tests/money_path/ tests/strategy/live_inference/
```
Result: **2 failed, 344 passed** — the 2 failures are exactly the pre-declared pre-existing ones:
- `test_mainstream_warm_cycle_uses_bounded_fresh_family_window`
- `test_market_discovery_constructs_public_clob_with_bounded_timeout`

No new failures introduced.

---

## CHECK 2 [STATUS: VERIFIED]
Regression baseline — no new money-path breaks.

Pre-declared baseline: exactly 2 pre-existing failures in tests/money_path/ + tests/strategy/live_inference/. Observed: same 2 failures, 344 passing. Delta = 0 regressions.

---

## CHECK 3 [STATUS: VERIFIED]
Artifact existence and shape — all 12 claimed files present with expected modification timestamps.

Production files (all modified 2026-06-17):
```
 429  src/forecast/model_selection.py           (22308 bytes)
 614  src/data/replacement_fusion_upgrade_trigger.py (29623 bytes)
2860  src/data/replacement_forecast_materializer.py (163380 bytes)
 829  src/data/forecast_source_registry.py      (31327 bytes)
1228  src/data/bayes_precision_fusion_download.py (64451 bytes)
 413  src/forecast/bayes_precision_fusion.py    (21069 bytes)
```

Test files (all modified 2026-06-17):
```
 318  tests/data/test_fusion_upgrade_trigger.py          (14768 bytes)
 199  tests/test_bayes_precision_fusion_candidate_accrual_models.py (10076 bytes)
 433  tests/test_bayes_precision_fusion_download.py      (20366 bytes)
 170  tests/test_bayes_precision_fusion_gem_current_value_previous_runs_fallback.py (8499 bytes)
 195  tests/test_bayes_precision_fusion_model_selection_gate.py (10646 bytes)
 502  tests/test_replacement_0_1_bayes_precision_fusion_materializer_wiring.py (28408 bytes)
```

git diff --stat against previous commit confirms exactly these 12 files: 320 insertions, 181 deletions.

---

## CHECK 4 [STATUS: VERIFIED]
Cross-module side effects.

(a) Removed forward specs `openmeteo_gfs_global` / `openmeteo_gem_global` from forecast_source_registry.py:
- `grep -rn "openmeteo_gfs_global|openmeteo_gem_global" src/` → only tombstone comments at lines 364-365 of forecast_source_registry.py. Zero live call sites.
- `grep -rn "openmeteo_gfs_global|openmeteo_gem_global" tests/` → NO HITS. No test consumers of the removed specs.

(b) DECORR_GLOBALS, NCEP_FAMILY, GEM_FAMILY changes in model_selection.py:
- Confirmed at runtime: `DECORR_GLOBALS = ('icon_global', 'jma_seamless', 'ukmo_global_deterministic_10km')` — gfs_global and gem_global absent.
- `NCEP_FAMILY = ('gfs_hrrr', 'ncep_nbm_conus')` — no gfs_global.
- `GEM_FAMILY = ('gem_hrdps_continental',)` — no gem_global.
- `BAYES_PRECISION_FUSION_EXTRA_MODELS` in bayes_precision_fusion_download.py contains neither gfs_global nor gem_global (confirmed at runtime).
- `GLOBAL_LIKELIHOOD_MODELS`: gfs_global absent, gem_global absent (confirmed at runtime).

(c) Deleted vestigial DECORR_GLOBALS duplicate in bayes_precision_fusion.py — grep confirms no importer of that symbol from that file.

---

## CHECK 5 [STATUS: VERIFIED]
Cold-start reproducibility — the diff is self-documenting.

All changed files carry the 2026-06-17 authority comment referencing the coarse-global removal operator law. The rationale ("no 25km model, don't download what you don't use") is captured in inline comments in model_selection.py (lines 74-76, 116-118, 123-125) and in the replacement_fusion_upgrade_trigger.py docstring. The domain-aware gate rationale (why _REGIONAL_DOMAIN_KEY not REGIONAL_MODELS) is documented at expected_provider_families_for_city lines 88-93. A fresh agent reading the diff + cited comments has full rationale; no tribal knowledge required.

---

## Deviation A verification [STATUS: SOUND]
`expected_provider_families_for_city` gates on `_REGIONAL_DOMAIN_KEY` membership, NOT `REGIONAL_MODELS`.

Confirmed correct: `ncep_nbm_conus` is CONUS-domain-gated (it IS in `_REGIONAL_DOMAIN_KEY`) but is NOT in `REGIONAL_MODELS` (which lists only nest models: icon_d2, meteofrance_arome_france_hd, ukmo_uk, gfs_hrrr, gem_hrdps_continental). A `REGIONAL_MODELS`-only test would treat `ncep_nbm_conus` as a pure global and wrongly expect NCEP everywhere.

Runtime evidence:
- Tokyo (35.5523, 139.7799): `expected_provider_families_for_city` → `frozenset({'DWD', 'JMA', 'UKMO'})` — NCEP absent. Correct.
- Chicago (41.9786, -87.9048): → `frozenset({'UKMO', 'DWD', 'NCEP', 'CMC', 'JMA'})` — NCEP and CMC present. Correct.

## Deviation B verification [STATUS: SOUND]
Only dead forward specs removed; `*_previous_runs` de-bias-history specs retained.

`grep -rn "openmeteo_gfs_global|openmeteo_gem_global" src/` → zero live consumers (only tombstone comments). `gfs_previous_runs` and `gem_previous_runs` specs remain in forecast_source_registry.py (same class as ecmwf_previous_runs anchor bridge), unconditionally retained.

---

## RED-on-revert proof

### (a) Domain gate — test_non_conus_city_excludes_absent_ncep_cmc_no_phantom_upgrade

Break applied: `capturable_expected = capturable` (removed `& expected` intersection).

Result:
```
FAILED tests/data/test_fusion_upgrade_trigger.py::test_non_conus_city_excludes_absent_ncep_cmc_no_phantom_upgrade
AssertionError: {'capturable_families': ['DWD', 'JMA', 'NCEP', 'UKMO'], 'is_upgrade': True, ...}
assert True is False
1 failed in 0.89s
```
Test went RED. Restore confirmed: `git diff --stat src/data/replacement_fusion_upgrade_trigger.py` shows the change (change is intentional, not residual break). Manually verified restore by reverting to `capturable & expected`.

### (b) Family removal — test_outside_conus_ncep_has_no_rep

Break applied: `"gfs_global"` added to `DECORR_GLOBALS` and to `NCEP_FAMILY`.

Result:
```
FAILED tests/test_bayes_precision_fusion_candidate_accrual_models.py::test_outside_conus_ncep_has_no_rep
AssertionError: (35.68, 139.69, (..., 'gfs_global'))
assert ['gfs_global'] == []
1 failed in 1.00s
```
Test went RED. Restore confirmed: both DECORR_GLOBALS and NCEP_FAMILY restored to exact pre-break state.

---

## Contract math spot-checks (all PASS)

```python
# Tokyo expected families
expected_provider_families_for_city(35.5523, 139.7799)
→ frozenset({'DWD', 'JMA', 'UKMO'})   # NCEP absent: VERIFIED

# Chicago expected families
expected_provider_families_for_city(41.9786, -87.9048)
→ frozenset({'UKMO', 'DWD', 'NCEP', 'CMC', 'JMA'})   # NCEP+CMC present: VERIFIED

# gfs_global absent from GLOBAL_LIKELIHOOD_MODELS: True
# gem_global absent from GLOBAL_LIKELIHOOD_MODELS: True
# gfs_global absent from BAYES_PRECISION_FUSION_EXTRA_MODELS: True (not in tuple)
# gem_global absent from BAYES_PRECISION_FUSION_EXTRA_MODELS: True (not in tuple)

# Check 5: Tokyo served {ecmwf_ifs, icon_global, jma_seamless, ukmo_global_deterministic_10km}
decorrelated_provider_families_of(served_models)
→ frozenset({'DWD', 'JMA', 'UKMO'})
served_families >= expected → True   # decorrelated_complete: VERIFIED
```
