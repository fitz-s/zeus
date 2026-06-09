---
name: zeus-task-boot-hourly-observation-ingest
description: Boot profile for hourly/observation_instants/instants/backfill/Ogimet/historical-hourly tasks. Auto-loads when editing hourly/instant observation clients, writers, backfill, or audit lanes in Zeus. Preserves extrema, provenance, source family, and current data posture. Replaces the hourly_observation_ingest entry from architecture/task_boot_profiles.yaml (Tier 2 Phase 1 #12).
model: inherit
---

# Zeus task boot — hourly_observation_ingest

Source: round2_verdict.md §1.1 #8 (task_boot_profiles → 7 SKILLs). Replaces `hourly_observation_ingest` profile in architecture/task_boot_profiles.yaml.

Trigger keywords: hourly, observation_instants, instants, backfill, Ogimet, historical hourly.

## Required reads (in order)

1. AGENTS.md (root)
2. workspace_map.md
3. docs/operations/current_data_state.md
4. docs/operations/current_source_validity.md
5. docs/operations/known_gaps.md
6. architecture/source_rationale.yaml
7. architecture/city_truth_contract.yaml
8. architecture/fatal_misreads.yaml

## Current-fact surfaces

- docs/operations/current_data_state.md
- docs/operations/current_source_validity.md

## Required proofs (answer BEFORE editing)

1. **hourly_source_and_extrema**: Which hourly source is valid for this city/date, and does aggregation preserve maxima/minima?
   - Evidence: docs/operations/current_data_state.md, docs/operations/current_source_validity.md, architecture/city_truth_contract.yaml, architecture/source_rationale.yaml
2. **writer_provenance_gate**: Does the write path stamp non-default authority, data_version, and provenance?
   - Evidence: architecture/source_rationale.yaml

## Fatal misreads (load these antibodies)

- hourly_downsample_preserves_extrema
- daily_day0_hourly_forecast_sources_are_not_interchangeable
- hong_kong_hko_explicit_caution_path

## Forbidden shortcuts

- Do NOT average, first-sample, or last-sample sub-hourly readings when extrema are required.
- Do NOT use Open-Meteo grid-snap as a settlement-source escape hatch.
- Do NOT infer v2 readiness from legacy observation_instants coverage.

## Code Review Graph use

- Stage: stage_2_after_semantic_boot
- Use for: writer/client/backfill callers, tests
- NOT for: source family truth, data freshness

## Verification gates

```
python3 scripts/topology_doctor.py --source --json
python3 scripts/topology_doctor.py --fatal-misreads --json
```
