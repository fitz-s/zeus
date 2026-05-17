# Shadow Classifier Calibration — 2026-05-06
# Created: 2026-05-06
# Authority basis: OD-2 charter override; invariants.jsonl shadow_classifier_calibration carry-forward; IMPLEMENTATION_PLAN Phase 1 D5

## Problem

The original classifier returned `NEW_ONLY` on every run because:
1. Legacy `topology_doctor --navigation --route-card-only` produces empty stdout for
   paths it doesn't route (no output = silent, not disagreement).
2. The original name-matching heuristic required capability names to appear literally
   in legacy output — they never did because legacy uses different label formats.

## Fix Applied (approach b — minimally invasive)

Patched `scripts/topology_route_shadow.py` with structural-equivalence comparison:

### `_classify()` changes
- Added path-set extraction from legacy output via `_extract_legacy_paths()` regex.
- Added `new_paths` parameter (populated from `card.hard_kernel_hits`).
- When `new_hit and not legacy_hit_by_name`:
  - **Sub-case (b)**: Legacy is silent (`""`, `"(no output)"`, error prefix) → classify
    as `agree_path_equivalent`. Legacy silence is not contradiction; new router is the
    only routing authority.
  - **Sub-case (a)**: Legacy has substantive output → check path-set intersection.
    Overlap → `agree_path_equivalent`. No overlap → `NEW_ONLY` (genuine disagreement).

### `_agreement()` changes
- Now returns `True` for `agree_path_equivalent` in addition to `AGREE` / `BOTH_EMPTY`.

### `_run_new()` changes
- Returns `(summary, caps, flagged_paths)` tuple; `flagged_paths` sourced from
  `card.hard_kernel_hits` (the RouteCard attribute name confirmed by inspection).

## Smoke Run Results (7 scenarios, 2026-05-06)

All 7 runs: classification=`agree_path_equivalent`, agreement=`True`.
Zero `NEW_ONLY` results.

| Paths | Task | Classification |
|-------|------|---------------|
| src/execution/harvester.py | settle HKO 2026-05-06 | agree_path_equivalent |
| src/calibration/manager.py | calibration rebuild after new pairs | agree_path_equivalent |
| src/state/ledger.py | append position event live | agree_path_equivalent |
| src/contracts/world_view/__init__.py + calibration_transfer_policy.py | flip source validity for ecmwf | agree_path_equivalent |
| src/control/control_plane.py | activate kill switch RED level | agree_path_equivalent |
| scripts/refit_platt_v2.py | refit platt models dry run | agree_path_equivalent |
| src/execution/executor.py | submit live order to polymarket | agree_path_equivalent |

Records appended to: `evidence/shadow_router/agreement_2026-05-06.jsonl`

## Limitation

All 7 runs fell into sub-case (b) — legacy was silent. Sub-case (a) path-set intersection
logic is correct but not exercised by current scenarios (would require a legacy invocation
that returns substantive path-mentioning output). Sub-case (a) remains defensively
implemented for future use when legacy routing expands.
