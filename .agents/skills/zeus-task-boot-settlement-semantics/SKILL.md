---
name: zeus-task-boot-settlement-semantics
description: Boot profile for settlement/resolution/rounding/harvester/market-settles/oracle tasks. Auto-loads when working on market resolution, settlement values, harvester writes, or replay labels in Zeus. Replaces the settlement_semantics entry from architecture/task_boot_profiles.yaml (Tier 2 Phase 1 #12). Cross-link: src/contracts/settlement_semantics.py SettlementRoundingPolicy ABC + HKO_Truncation/WMO_HalfUp subclasses now type-encode the rounding-mismatch antibody.
model: inherit
---

# Zeus task boot — settlement_semantics

Source: round2_verdict.md §1.1 #8 (task_boot_profiles → 7 SKILLs). Replaces `settlement_semantics` profile in architecture/task_boot_profiles.yaml.

Trigger keywords: settlement, resolution, rounding, harvester, market settles, oracle.

## Required reads (in order)

1. AGENTS.md (root)
2. workspace_map.md
3. docs/reference/zeus_domain_model.md
4. docs/authority/zeus_current_architecture.md
5. docs/operations/current_source_validity.md
6. docs/operations/known_gaps.md
7. architecture/source_rationale.yaml
8. architecture/city_truth_contract.yaml
9. architecture/core_claims.yaml
10. architecture/fatal_misreads.yaml

## Current-fact surfaces

- docs/operations/current_source_validity.md

## Required proofs (answer BEFORE editing)

1. **settlement_value_path**: Which source, unit, and rounding law produce the settlement value for this market?
   - Evidence: docs/reference/zeus_domain_model.md, docs/operations/current_source_validity.md, architecture/city_truth_contract.yaml, architecture/core_claims.yaml
2. **dual_track_separation**: Does this high/low family preserve separate physical quantity and calibration identity?
   - Evidence: docs/authority/zeus_current_architecture.md

## Fatal misreads (load these antibodies)

- daily_day0_hourly_forecast_sources_are_not_interchangeable
- airport_station_not_city_settlement_station
- hong_kong_hko_explicit_caution_path

## Forbidden shortcuts

- Do NOT treat all cities as WU integer daily-high markets.
- Do NOT mix HIGH and LOW settlement, replay, or calibration identities.
- Do NOT bypass SettlementSemantics for DB/event settlement writes.
- Do NOT use Decimal ROUND_HALF_UP for asymmetric WMO half-up; the legacy `np.floor(x+0.5)` semantic at src/contracts/settlement_semantics.py:16-27 (and the new WMO_HalfUp.round_to_settlement) handles negative half-values correctly (-3.5 → -3, NOT -4). See SIDECAR-3 / batch_C_review §C4.

## Type-encoded antibody (SIDECAR-3 / BATCH C, 2026-04-28)

`src/contracts/settlement_semantics.py` defines `SettlementRoundingPolicy` ABC + `HKO_Truncation` + `WMO_HalfUp` subclasses + `settle_market(city, raw, policy)`. Mixing wrong (city, policy) raises TypeError. New code paths SHOULD use this; existing string-dispatch path remains for legacy compatibility.

## Code Review Graph use

- Stage: stage_2_after_semantic_boot
- Use for: callers, tests, blast radius
- NOT for: rounding law, city source truth

## Verification gates

```
python3 scripts/topology_doctor.py --core-claims --json
python3 scripts/topology_doctor.py --fatal-misreads --json
```
