# F3 work record — TIGGE ingest stub

Date: 2026-04-27
Branch: plan-pre5
Task: R3 F3 TIGGE ingest stub — registered, operator-gated, dormant by default

Changed files:
- docs/AGENTS.md / docs/README.md / docs/operations/AGENTS.md / architecture/AGENTS.md / workspace_map.md / architecture/docs_registry.yaml / docs/reference/AGENTS.md
- architecture/topology.yaml / architecture/source_rationale.yaml / architecture/test_topology.yaml / architecture/module_manifest.yaml
- docs/reference/modules/data.md / src/data/AGENTS.md
- src/data/tigge_client.py / src/data/forecast_source_registry.py / src/data/ensemble_client.py
- tests/test_tigge_ingest.py / tests/test_forecast_source_registry.py / tests/test_digest_profile_matching.py
- docs/operations/current_state.md / docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml
- docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/F3.yaml
- docs/operations/task_2026-04-26_ultimate_plan/r3/boot/F3_codex_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/F3_work_record_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/F3_pre_close_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/F3_post_close_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/receipt.json

Summary:
Implemented the F3 dormant TIGGE ingest stub. `TIGGEIngest` implements the forecast ingest protocol shape, can be constructed with the gate closed, reports gate health without external I/O, and makes `fetch()` fail closed with `TIGGEIngestNotEnabled` before payload loading unless both the operator artifact and `ZEUS_TIGGE_INGEST_ENABLED=1` are present. The F1 registry records `ingest_class=TIGGEIngest` while preserving `enabled_by_default=False`, `requires_api_key=True`, and dual operator gates.

Switch-only follow-up:
To satisfy the "authorization should only be a switch" requirement without fabricating TIGGE source truth, open-gate TIGGE now reads an operator-approved local JSON payload from one of three reversible seams: constructor `payload_path`, `ZEUS_TIGGE_PAYLOAD_PATH`, or `payload_path:` / `tigge_payload_path:` in the latest `tigge_ingest_decision_*.md` artifact. Missing payload configuration fails closed with `TIGGEIngestFetchNotConfigured`. `ensemble_client.fetch_ensemble(..., model="tigge")` now routes through the registered `ForecastIngestProtocol` adapter and proves it does not call Open-Meteo HTTP. No real TIGGE archive HTTP/GRIB implementation was added; external archive access remains a later operator/data-source packet.

Verification:
- `python3 scripts/topology_doctor.py --navigation --task "R3 F3 ..." ...` -> navigation ok, profile `r3 tigge ingest stub implementation`.
- `python3 -m py_compile src/data/tigge_client.py src/data/forecast_source_registry.py src/data/forecast_ingest_protocol.py src/data/ensemble_client.py` -> ok.
- `pytest -q -p no:cacheprovider tests/test_tigge_ingest.py tests/test_forecast_source_registry.py tests/test_ensemble_client.py tests/test_digest_profile_matching.py::test_r3_f3_tigge_ingest_stub_routes_to_f3_profile_not_heartbeat` -> 17 passed.
- Follow-up: `python3 scripts/topology_doctor.py --navigation --task "R3 F3 TIGGE switch-only local operator payload wiring TIGGEIngest ZEUS_TIGGE_PAYLOAD_PATH registered ingest ensemble_client" ...` -> navigation ok, profile `r3 tigge ingest stub implementation`.
- Follow-up: `python3 -m py_compile src/data/tigge_client.py src/data/ensemble_client.py tests/test_tigge_ingest.py tests/test_forecast_source_registry.py` -> ok.
- Follow-up: `pytest -q -p no:cacheprovider tests/test_tigge_ingest.py tests/test_forecast_source_registry.py tests/test_ensemble_client.py` -> 20 passed.
- Follow-up: `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_f3_tigge_ingest_stub_routes_to_f3_profile_not_heartbeat` -> 1 passed.
- Follow-up: `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase F3` -> GREEN=12 YELLOW=0 RED=0, STATUS GREEN.
- Original closeout: `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase F3` -> GREEN=7 YELLOW=0 RED=0.
- `git diff --check` -> clean.
- `git diff -- src/signal/ensemble_signal.py src/calibration/platt.py src/calibration/manager.py src/calibration/store.py` -> empty.
- Freshness metadata: tests/test_tigge_ingest.py lifecycle header added.
- Pre-close blocker remediation: current_state phase-card pointer and F3 slice-card operator artifact path reconciled.
- F3 pre-close review record created for Hegel/Kierkegaard procedural blockers.
- Pre-close critic Jason -> PASS after artifact remediation.

Next:
F3 remains COMPLETE with post-close critic+verifier PASS for the original close. The follow-up local-payload switch wiring is ready for independent diff review and closeout evidence. Operator go-live still requires a real decision artifact, env flag, local JSON payload, and later calibration retrain authorization; external TIGGE archive HTTP/GRIB remains out of scope.

## Follow-up switch-only review — 2026-04-27

Independent critic/verifier review for the local-payload switch wiring completed after the follow-up changes.

- Critic Confucius the 2nd: APPROVE. Confirmed TIGGE remains experimental, disabled by default, dual-gated by artifact + `ZEUS_TIGGE_INGEST_ENABLED`, open-gate payload loading reads only constructor/env/artifact local JSON, `ensemble_client` routes model=`tigge` through the registered ingest adapter, and docs preserve no external HTTP/GRIB/no retrain/no live-deploy claims.
- Verifier Nietzsche the 2nd: PASS. Confirmed code inspection plus `py_compile`, `pytest` targeted suite, F3 drift check, scoped closeout with companion registries, and `git diff --check`. Noted the broader worktree/global topology health still has unrelated pre-existing blockers outside this F3 scope.
