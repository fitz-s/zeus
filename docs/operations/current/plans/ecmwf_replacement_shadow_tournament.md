# ECMWF Replacement Shadow Tournament Plan

Status: active
Created: 2026-06-05
Authority: user request 2026-06-05 + ECMWF 0.1/AIFS replacement assessment attachment

## Objective

Investigate and implement the first safe Zeus slice for ECMWF 0.1/AIFS replacement work: register replacement candidates as disabled/shadow-only forecast products and choose any future promotion candidate from empirical evidence, not preference.

## Scope

- Touch `src/data/forecast_source_registry.py` only for static product/source policy.
- Reuse `tests/test_forecast_source_registry.py` for relationship tests.
- Do not add ingest, schema migration, executable reader routing, calibration retrain, Kelly authority, or live submit authority in this slice.

## Required Safety

- B0 `ecmwf_open_data` remains the only current registered entry-primary ECMWF live channel.
- R1/R2/A1/C1 replacement candidates are disabled by default and diagnostic/shadow-only.
- Candidate products must not map to current OpenData/TIGGE Platt lookup authority.
- Empirical selection requires settled sample, anti-lookahead cleanliness, decision-time availability, q_lcb coverage, and positive after-cost economic evidence.

## Verification

- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/data/forecast_source_registry.py tests/test_forecast_source_registry.py --plan-evidence docs/operations/current/plans/ecmwf_replacement_shadow_tournament.md`
- `pytest -q tests/test_forecast_source_registry.py`
- `python3 -m py_compile src/data/forecast_source_registry.py`
- `git diff --check`
