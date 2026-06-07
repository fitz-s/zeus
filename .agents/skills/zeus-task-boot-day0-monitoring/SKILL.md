---
name: zeus-task-boot-day0-monitoring
description: Boot profile for Day0/day0/monitor/nowcast/observed-so-far/exit-trigger tasks. Auto-loads when working on same-day monitoring, Day0 nowcast, exits, or observed-so-far logic in Zeus. Replaces the day0_monitoring entry from architecture/task_boot_profiles.yaml (Tier 2 Phase 1 #12). Cross-link: INV-16 (causality_status reject axis) is now test-cited per BATCH D.
model: inherit
---

# Zeus task boot — day0_monitoring

Source: round2_verdict.md §1.1 #8 (task_boot_profiles → 7 SKILLs). Replaces `day0_monitoring` profile in architecture/task_boot_profiles.yaml.

Trigger keywords: Day0, day0, monitor, nowcast, observed so far, exit trigger.

## Required reads (in order)

1. AGENTS.md (root)
2. workspace_map.md
3. docs/reference/zeus_domain_model.md
4. docs/authority/zeus_current_architecture.md
5. docs/operations/current_source_validity.md
6. docs/operations/current_data_state.md
7. architecture/source_rationale.yaml
8. architecture/city_truth_contract.yaml
9. architecture/fatal_misreads.yaml

## Current-fact surfaces

- docs/operations/current_source_validity.md
- docs/operations/current_data_state.md

## Required proofs (answer BEFORE editing)

1. **day0_source_vs_settlement_source**: Is the live Day0 monitor source the same as settlement for this city/date, or explicitly different?
   - Evidence: docs/operations/current_source_validity.md, docs/operations/current_data_state.md, architecture/city_truth_contract.yaml
2. **high_low_day0_causality**: Does the task preserve high uses observed max floor and low uses observed min ceiling?
   - Evidence: docs/authority/zeus_current_architecture.md, docs/reference/zeus_domain_model.md

## Fatal misreads (load these antibodies)

- daily_day0_hourly_forecast_sources_are_not_interchangeable
- hourly_downsample_preserves_extrema
- code_review_graph_answers_where_not_what_settles

## Forbidden shortcuts

- Do NOT use settlement-daily truth as proof that live Day0 monitoring is fresh.
- Do NOT reuse high-temperature max logic for low-temperature min logic.
- Do NOT treat stale current_data_state as green for live monitoring.

## INV-16 enforcement (BATCH D 2026-04-28)

INV-16 (causality_status != 'OK' must reject from historical Platt lookup) is enforced by `tests/test_phase6_causality_status.py` (3 relationship tests, all PASS at HEAD). Day0 low-track edits MUST preserve the causality_status reject axis as distinct from OBSERVATION_UNAVAILABLE_LOW.

## Code Review Graph use

- Stage: stage_2_after_semantic_boot
- Use for: callers, tests, impacted monitor paths
- NOT for: live source validity, data freshness

## Verification gates

```
python3 scripts/topology_doctor.py --task-boot-profiles --json
python3 scripts/topology_doctor.py --fatal-misreads --json
```
