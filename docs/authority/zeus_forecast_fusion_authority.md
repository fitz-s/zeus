# Zeus Forecast Fusion Authority

Status: active durable authority law  
Scope: forecast fusion source/model-selection authority, provider-family representative law, city fusion profiles, and regional domain gate semantics  
Machine authority: `architecture/forecast_fusion_manifest.yaml`  
Executable anchors: `src/forecast/model_selection.py`, `config/model_domain_polygons.yaml`, `src/forecast/bayes_precision_fusion.py`  
Freshness model: durable selection law. Current model rows, source cycles, provider health, and live completeness are current facts and must be proven from DB/runtime receipts.

---

## 1. Authority Boundary

Forecast fusion model selection is authority-bearing because it defines which forecast products may enter the probability distribution that later becomes q, q_lcb, edge, size, and live-money execution.

The authority order is:

1. executable source and tests;
2. `architecture/forecast_fusion_manifest.yaml`;
3. this authority file;
4. canonical references such as `docs/reference/zeus_forecast_source_and_regional_model_reference.md`;
5. dated reports/evidence/history.

No dated experiment, consult, PR review, report, or evidence note may add a live model, resurrect a removed model, or redefine a regional domain. It must first update code, manifest, tests, and this authority law.

---

## 2. Selection Law

Durable law:

- Every fusion has one anchor prior: `ecmwf_ifs`, when present at runtime.
- A physical provider family contributes at most one representative.
- Provider-family priority is machine-declared in `architecture/forecast_fusion_manifest.yaml`.
- Regional or domain-gated products may enter only when the city coordinate is inside the configured polygon and the lead is within the configured maximum.
- Missing current rows are runtime current facts. Docs may not invent substitute models.
- A live fusion row must still prove current capture/readiness/source-cycle validity before trading.

This means the manifest defines **eligible composition if rows are present**, not a guarantee that the current DB has all rows.

---

## 3. Provider Families

Machine authority: `architecture/forecast_fusion_manifest.yaml::provider_families`.

Current families:

| Family | Priority order | Meaning |
|---|---|---|
| ICON/DWD | `icon_d2`, `icon_eu`, `icon_global` | Most-specific eligible ICON product wins. |
| NCEP/NOAA | `gfs_hrrr`, `ncep_nbm_conus` | HRRR wins in its domain; NBM is only representative when HRRR is not eligible and NBM is eligible. |
| UKMO | `ukmo_uk_deterministic_2km`, `ukmo_global_deterministic_10km` | UKV wins in its domain/lead; otherwise UKMO global. |
| CMC/GEM | `gem_hrdps_continental` | HRDPS is the only current CMC representative. |

Removed/non-member products such as old coarse global variants, JMA, or alias probes cannot re-enter from docs prose.

---

## 4. City Profiles

Machine authority: `architecture/forecast_fusion_manifest.yaml::city_profile_groups`.

The profile groups cover every city currently configured in `config/cities.json`. A city missing from the manifest is a fail-closed docs/code drift until the manifest and tests are updated.

Profiles are lead-sensitive. A city in `icon_d2_arome` may use different eligible model composition at lead 0-1 than at lead 2-3 or lead 4+. Agents must read the profile definition, not just the group name.

---

## 5. Reference Boundary

`docs/reference/zeus_forecast_source_and_regional_model_reference.md` explains the concepts. It is not authority. If that reference and the manifest disagree, fix the reference or code/manifest through a governed change.

---

## 6. Required Change Protocol

Any change to forecast model composition, regional products, provider-family representative rules, or city assignment must update all relevant surfaces in the same patch:

1. executable code/config;
2. `architecture/forecast_fusion_manifest.yaml`;
3. this file if law changed;
4. `architecture/module_manifest.yaml` if routing changed;
5. `architecture/docs_registry.yaml` if docs changed;
6. tests proving model-selection behavior;
7. reference docs only after law is correct.

Do not update reference first and let it imply authority.
