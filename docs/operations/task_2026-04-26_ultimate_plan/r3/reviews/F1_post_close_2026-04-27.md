# F1 post-close third-party review record — 2026-04-27

Phase: F1 `Forecast source registry`
Branch: `plan-pre5`
Gate: Required post-close third-party critic + verifier before F2/F3 unfreeze.

## Initial post-close review

- Critic: Sagan — BLOCK (procedural/control-surface only).
  - F1 code slice passed scope review: forecast-source registry/provenance wiring only, no calibration retrain, no active TIGGE ingest, no settlement routing, and no live venue behavior.
  - Blocker 1: `docs/operations/current_state.md` still pointed at stale R3 phase control state instead of the F1 post-close blocker/remediation state.
  - Blocker 2: `r3/slice_cards/F1.yaml` listed a stale acceptance-test name rather than the implemented registry-boundary / `EnsembleSignal` bit-identity regression.
- Verifier: Anscombe — BLOCK (procedural/artifact only).
  - Technical verification remained green, but no F1 post-close artifact existed and `_phase_status.yaml` still recorded post-close review as pending.

## Remediation recorded before rerun

- `docs/operations/current_state.md` now names F1 as `COMPLETE / POST-CLOSE REVIEW BLOCKER REMEDIATION`, states F2/F3 remain frozen until post-close critic+verifier PASS, and preserves the M2/INV-29 and live-money freeze points.
- `r3/slice_cards/F1.yaml` now cites `tests/test_forecast_source_registry.py::test_ensemble_signal_math_bit_identical_with_registry_metadata_only`; the card explicitly keeps registry/source gating at data/evaluator fetch boundaries while `EnsembleSignal` math remains unchanged.
- `receipt.json` and this post-close record include the remediation trail so the next reviewer can verify the closeout state without relying on chat memory.

## Rerun evidence

- `python3 -m py_compile src/data/forecast_ingest_protocol.py src/data/forecast_source_registry.py src/data/forecasts_append.py src/data/ensemble_client.py src/data/hole_scanner.py src/state/db.py scripts/backfill_openmeteo_previous_runs.py` -> ok.
- `pytest -q -p no:cacheprovider tests/test_forecast_source_registry.py tests/test_forecasts_schema_alignment.py tests/test_backfill_openmeteo_previous_runs.py tests/test_ensemble_client.py tests/test_ensemble_signal.py tests/test_k2_live_ingestion_relationships.py::test_R2_forecasts_sources_match_registry tests/test_k2_live_ingestion_relationships.py::test_R11_forecasts_model_source_map_matches_backfill` -> `46 passed`.
- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase F1` -> `GREEN=20 YELLOW=0 RED=0`.
- `git diff --check` -> clean.
- `git diff -- src/signal/ensemble_signal.py` -> empty, confirming F1 still has no `EnsembleSignal` math diff.
- Full F1/data/signal regression suite (`tests/test_forecast_source_registry.py tests/test_forecasts_schema_alignment.py tests/test_k2_live_ingestion_relationships.py tests/test_ensemble_client.py tests/test_backfill_openmeteo_previous_runs.py tests/test_etl_forecasts_v2_from_legacy.py tests/test_ensemble_signal.py tests/test_digest_profile_matching.py`) -> `103 passed`.

## Final post-remediation review

- Critic rerun: Halley — PASS.
  - Confirmed `current_state.md` no longer points at stale U2/F1 state, F1 card acceptance tests are reconciled, post-close artifact exists, TIGGE remains dormant/gated, no calibration retrain or Platt refit, no settlement routing, no live venue behavior, and no `EnsembleSignal` diff.
  - Fresh critic evidence: py_compile ok, full F1/data/signal suite `103 passed`, F1 drift `GREEN=20 YELLOW=0 RED=0`, `git diff --check` clean.
- Verifier rerun: Schrodinger — PASS.
  - Independently reran py_compile, full F1/data/signal suite (`103 passed`), focused verifier subset (`46 passed`), F1 drift (`GREEN=20 YELLOW=0 RED=0`), `git diff --check`, `git diff -- src/signal/ensemble_signal.py`, and planning-lock.
  - Confirmed F2/F3 may be unfrozen independently of M2 once this PASS is recorded; M2 remains held behind the M1 `INV-29 amendment` gate.

## Verdict

PASS — F1 post-close third-party critic + verifier gate is complete. F2/F3 may be unfrozen; M2 remains frozen behind the M1 `INV-29 amendment` gate.
