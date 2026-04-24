# 15 Not Now

The following work should not be done before data-source truth is hardened:

- Do not optimize strategy/model execution on this DB as if it were canonical.
- Do not tune calibration models using current labels.
- Do not add complex model features sourced from legacy hourly tables.
- Do not rebuild large calibration tables until source-role/provenance/causality gates exist.
- Do not overwrite settlement v1 rows in place; migrate to v2 with evidence links.
- Do not treat Open-Meteo/Meteostat fallback as a shortcut for settlement truth.
- Do not rely on docs or graph summaries as proof of ingestion correctness.
- Do not run destructive repair scripts without dry-run manifests and rollback plan.