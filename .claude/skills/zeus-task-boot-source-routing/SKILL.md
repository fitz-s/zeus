---
name: zeus-task-boot-source-routing
description: Boot profile for source/routing/station/endpoint/WU/HKO/NOAA/Ogimet/city-source tasks. Auto-loads when editing ingest, client, backfill, or source-routing code in Zeus. Decides which source family, station, unit, and date-scoped evidence may be used for a city BEFORE editing. Replaces the source_routing entry from architecture/task_boot_profiles.yaml (Tier 2 Phase 1 #12).
model: inherit
---

# Zeus task boot — source_routing

Source: round2_verdict.md §1.1 #8 (task_boot_profiles → 7 SKILLs). Replaces `source_routing` profile in architecture/task_boot_profiles.yaml.

Trigger keywords: source, routing, station, endpoint, WU, HKO, NOAA, Ogimet, city source.

## Required reads (in order)

1. AGENTS.md (root)
2. workspace_map.md
3. docs/reference/zeus_domain_model.md
4. docs/operations/current_source_validity.md
5. docs/operations/current_data_state.md
6. docs/operations/known_gaps.md
7. config/cities.json
8. architecture/source_rationale.yaml
9. architecture/city_truth_contract.yaml
10. architecture/fatal_misreads.yaml

## Current-fact surfaces (re-read every session — they decay)

- docs/operations/current_source_validity.md
- docs/operations/current_data_state.md

## Required proofs (answer BEFORE editing)

1. **settlement_source_by_city_date**: Which source family settles this city/date, and is the evidence fresh enough?
   - Evidence: docs/operations/current_source_validity.md, config/cities.json, architecture/city_truth_contract.yaml
2. **source_family_not_endpoint_health**: Does the chosen source prove settlement correctness, not just HTTP availability?
   - Evidence: docs/operations/current_source_validity.md, docs/operations/known_gaps.md

## Fatal misreads (load these antibodies)

- api_returns_data_not_settlement_correct_source
- airport_station_not_city_settlement_station
- hong_kong_hko_explicit_caution_path
- code_review_graph_answers_where_not_what_settles

## Forbidden shortcuts

- Do NOT treat endpoint 200 / data presence as settlement-source proof.
- Do NOT infer city settlement source from airport code, station code, or config comments alone.
- Do NOT use fossil source routing as current routing without fresh audit evidence.

## Code Review Graph use

- Stage: stage_2_after_semantic_boot (graph is derived context, not authority)
- Use for: impacted files, callers, tests, blast radius
- NOT for: settlement truth, source validity, city/date authority

## Verification gates (before claiming done)

```
python3 scripts/topology_doctor.py --task-boot-profiles --json
python3 scripts/topology_doctor.py --fatal-misreads --json
```
