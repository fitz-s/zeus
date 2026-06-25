# Zeus Forecast Fusion Authority

Status: active durable authority law  
Scope: forecast fusion source/model-selection authority, provider-family representative law, stable city×metric profiles, release timing, fallback, and regional domain gate semantics  
Machine authority: `architecture/forecast_fusion_stable_profile_manifest.yaml`, `architecture/forecast_source_acquisition_manifest.yaml`, `architecture/forecast_fusion_manifest.yaml`  
Executable anchors: `src/forecast/model_selection.py`, `config/model_domain_polygons.yaml`, `src/forecast/bayes_precision_fusion.py`  
Freshness model: durable selection law. Current model rows, source cycles, provider health, and live completeness are current facts and must be proven from DB/runtime receipts.

---

## 1. Authority Boundary

Forecast fusion model selection is authority-bearing because it defines which forecast products may enter the probability distribution that later becomes q, q_lcb, edge, size, and live-money execution.

Authority order:

1. executable source and tests;
2. `architecture/forecast_fusion_stable_profile_manifest.yaml` for production city×metric membership;
3. `architecture/forecast_source_acquisition_manifest.yaml` for endpoint/cadence/release semantics;
4. `architecture/forecast_fusion_manifest.yaml` for older profile/domain context while migration remains active;
5. this authority file;
6. canonical references;
7. dated evidence/history.

No dated experiment, consult, PR review, report, or evidence note may add a live model, resurrect a removed model, or redefine a regional domain. It must first update code, manifest, tests, and this authority law.

---

## 2. Stable Membership Law

Production source membership is keyed by:

```text
city + metric
```

It is not keyed by:

```text
city + metric + lead
```

Lead may change only:

- source availability gate;
- run age penalty;
- weights;
- predictive sigma / q-band width;
- fallback activation.

Lead must not select a different primary source membership. A lead-specific source set is diagnostic evidence, not production law.

---

## 3. Release Timing Law

A model run may enter live fusion only after:

```text
safe_available_at = last_run_availability_time + 10 minutes
```

The `run` time is the model initialization time, not public availability time. If a source has not reached `safe_available_at` by decision time, it is absent for that decision. Docs may not invent a row or use a future run.

Different source cadences are expected. Faster regional sources may be available before slower global sources; this affects fallback and source-age weighting, not the stable primary membership key.

---

## 4. Fallback Law

Fallback order:

1. complete stable primary combo;
2. complete fallback combo for the same city×metric from the stable manifest;
3. permitted available subset only when the profile policy allows it and provenance records dropped sources;
4. current profile;
5. no live q.

Fallback is triggered by runtime availability, freshness, domain, horizon, or source-role failure. Fallback is not a second strategy and does not change durable city×metric membership.

---

## 5. Provider And Resolution Law

- A physical provider family contributes at most one representative to one fusion.
- Regional or domain-gated products may enter only when the city coordinate is inside the configured polygon and lead/horizon is valid.
- Primary finer sources must be nominal `<=10km` unless a coarser source has an explicit coarse/station-alignment exception in the manifest and settlement-grade evidence justifies it.
- Missing current rows are operations facts. Durable docs do not fabricate substitute models.

---

## 6. Required Change Protocol

Any change to forecast model composition, regional products, provider-family representative rules, source cadence, fallback law, or city assignment must update all relevant surfaces in the same patch:

1. executable code/config;
2. `architecture/forecast_fusion_stable_profile_manifest.yaml`;
3. `architecture/forecast_source_acquisition_manifest.yaml` if acquisition/cadence changed;
4. this file if law changed;
5. `architecture/module_manifest.yaml` if routing changed;
6. `architecture/docs_registry.yaml` if docs changed;
7. tests proving stable membership, release timing, step alignment, and fallback behavior;
8. reference docs only after law is correct.

Do not update reference first and let it imply authority.
