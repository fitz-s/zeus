---
name: zeus-task-boot-calibration
description: Boot profile for calibration/Platt/replay/forecast-skill/training/backtest tasks. Auto-loads when editing calibration, replay, Platt fitting, or forecast skill in Zeus. Preserves source-family + HIGH/LOW separation. Replaces the calibration entry from architecture/task_boot_profiles.yaml (Tier 2 Phase 1 #12).
model: inherit
---

# Zeus task boot — calibration

Source: round2_verdict.md §1.1 #8 (task_boot_profiles → 7 SKILLs). Replaces `calibration` profile in architecture/task_boot_profiles.yaml.

Trigger keywords: calibration, Platt, replay, forecast skill, training, backtest.

## Required reads (in order)

1. AGENTS.md (root)
2. workspace_map.md
3. docs/reference/zeus_domain_model.md
4. docs/authority/zeus_current_architecture.md
5. docs/operations/current_data_state.md
6. docs/operations/current_source_validity.md
7. architecture/source_rationale.yaml
8. architecture/city_truth_contract.yaml
9. architecture/core_claims.yaml
10. architecture/fatal_misreads.yaml

## Current-fact surfaces

- docs/operations/current_data_state.md
- docs/operations/current_source_validity.md

## Required proofs (answer BEFORE editing)

1. **training_source_identity**: Which historical observation and settlement source trains this calibration family?
   - Evidence: docs/operations/current_data_state.md, docs/operations/current_source_validity.md, architecture/city_truth_contract.yaml
2. **strategy_key_and_dual_track**: Does the change preserve strategy_key and HIGH/LOW calibration separation?
   - Evidence: docs/authority/zeus_current_architecture.md, architecture/source_rationale.yaml

## Fatal misreads (load these antibodies)

- daily_day0_hourly_forecast_sources_are_not_interchangeable
- wu_website_daily_summary_not_wu_api_hourly_max
- code_review_graph_answers_where_not_what_settles

## Forbidden shortcuts

- Do NOT train from an observation source just because it is populated.
- Do NOT mix HIGH/LOW rows in Platt fitting, replay bins, or settlement rebuild identity.
- Do NOT treat forecast_skill_source as settlement_daily_source.

## Code Review Graph use

- Stage: stage_2_after_semantic_boot
- Use for: calibration callers, replay/test blast radius
- NOT for: training truth, settlement truth

## Verification gates

```
python3 scripts/topology_doctor.py --core-claims --json
python3 scripts/topology_doctor.py --fatal-misreads --json
```
