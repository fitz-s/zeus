# F1 work record — forecast source registry

Date: 2026-04-27
Branch: plan-pre5
Task: R3 F1 forecast source registry — source gating and forecast provenance wiring
Changed files:
- docs/AGENTS.md / docs/README.md / docs/operations/AGENTS.md / docs/operations/current_state.md / architecture/AGENTS.md / workspace_map.md / architecture/docs_registry.yaml / docs/reference/AGENTS.md
- architecture/topology.yaml / architecture/source_rationale.yaml / architecture/test_topology.yaml / architecture/module_manifest.yaml
- docs/reference/modules/data.md / docs/reference/modules/signal.md / docs/reference/modules/state.md
- src/data/AGENTS.md
- src/data/forecast_ingest_protocol.py / src/data/forecast_source_registry.py
- src/data/forecasts_append.py / src/data/ensemble_client.py / src/data/hole_scanner.py
- src/state/db.py
- scripts/AGENTS.md / architecture/script_manifest.yaml / scripts/backfill_openmeteo_previous_runs.py
- tests/test_forecast_source_registry.py / tests/test_forecasts_schema_alignment.py / tests/test_digest_profile_matching.py / tests/test_backfill_openmeteo_previous_runs.py
- docs/operations/task_2026-04-26_ultimate_plan/r3/boot/F1_codex_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml
- docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/F1_work_record_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/F1_pre_close_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/F1_post_close_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/F1.yaml
- docs/operations/task_2026-04-26_ultimate_plan/receipt.json

Summary:
Implemented F1 forecast-source plumbing. Added a typed K2 forecast ingest protocol, a registry with active-source gating, and a dormant TIGGE source that requires both operator evidence and `ZEUS_TIGGE_INGEST_ENABLED=1`. Existing forecast-history and live ensemble sources stay enabled by default. New `forecasts` writes now persist `source_id`, `raw_payload_hash`, `captured_at`, and `authority_tier`, with additive legacy-safe schema hooks. `ensemble_client.fetch_ensemble()` now returns registry provenance and fails closed before network for disabled/gated sources. `hole_scanner` forecast sources now derive from the registry. The previous-runs backfill script now writes the same F1 provenance fields as live append. `EnsembleSignal` math was intentionally unchanged.

Verification:
- `python3 -m py_compile src/data/forecast_ingest_protocol.py src/data/forecast_source_registry.py src/data/forecasts_append.py src/data/ensemble_client.py src/data/hole_scanner.py src/state/db.py` -> ok.
- `pytest -q -p no:cacheprovider tests/test_forecast_source_registry.py` -> 9 passed.
- `pytest -q -p no:cacheprovider tests/test_forecasts_schema_alignment.py tests/test_k2_live_ingestion_relationships.py::test_R2_forecasts_sources_match_registry tests/test_k2_live_ingestion_relationships.py::test_R11_forecasts_model_source_map_matches_backfill tests/test_ensemble_client.py tests/test_digest_profile_matching.py::test_r3_f1_forecast_source_registry_routes_to_f1_profile_not_heartbeat` -> 11 passed.
- Focused F1/data/signal regression suite: `tests/test_forecast_source_registry.py tests/test_forecasts_schema_alignment.py tests/test_k2_live_ingestion_relationships.py tests/test_ensemble_client.py tests/test_backfill_openmeteo_previous_runs.py tests/test_etl_forecasts_v2_from_legacy.py tests/test_ensemble_signal.py tests/test_digest_profile_matching.py` -> 103 passed.
- Post-backfill-alignment F1/data/signal regression suite -> 103 passed.
- Post-backfill-alignment py_compile affected source + backfill script -> ok.
- Post-backfill-alignment F1 drift -> GREEN; `git diff --check` -> clean.
- Pre-close critic Bohr -> PASS; verifier Sartre -> PASS.
- Post-close blocker remediation: current_state pointer and F1 slice card acceptance tests reconciled.
- F1 post-close review record created for Sagan/Anscombe procedural blockers.
- Post-close remediation rerun py_compile affected source + backfill script -> ok.
- Post-close remediation focused verifier subset -> 46 passed.
- Post-close remediation full F1/data/signal regression suite -> 103 passed.
- Post-close remediation F1 drift -> GREEN=20 YELLOW=0 RED=0; `git diff --check` clean; `git diff -- src/signal/ensemble_signal.py` empty.
- Post-close verifier Schrodinger -> PASS after independent reruns; F2/F3 may unfreeze while M2 remains held.
- Post-close critic Halley -> PASS after remediation.
- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase F1` -> GREEN.
- `git diff --check` -> clean.

Next:
F1 post-close critic+verifier PASS is recorded. F2/F3 are unfrozen; M2 remains held behind INV-29.
