# Object Invariance Wave 37 — Calibration Weighting LAW Antibody Triage

Status: APPROVED
Date: 2026-05-08
Scope: `s4` calibration weighting LAW antibody gap triage.

## Invariant

Calibration training evidence must not silently change meaning across the
snapshot -> calibration-pair -> Platt/refit boundary. A row that is physically
causally impossible may be excluded. A row whose only issue is continuous
precision degradation must either carry explicit continuous precision weight or
remain blocked from promotion-grade claims until the data layer supports that
object.

## First-Principles Finding

The `s4` backlog says "add 11 antibody tests", but the repo currently has a
deeper law-vs-code conflict:

- `ensemble_snapshots_v2` has `training_allowed`, `boundary_ambiguous`, and
  `ambiguous_member_count`, but no `precision_weight` authority field.
- `snapshot_ingest_contract` still maps LOW boundary ambiguity to
  `training_allowed=False`.
- `rebuild_calibration_pairs_v2.py` and `refit_platt_v2.py` still select
  `training_allowed = 1` rows.

Therefore the core LAW 1 tests cannot be made passing without schema,
ingest, rebuild, refit, historical-row cohorting, and promotion-policy work.
That is not a small test-only repair and must not be hidden behind green tests.

## Allowed In This Wave

- Read-only triage of all 11 specified antibodies.
- Active tests only for sub-laws that current code can satisfy without schema
  migration, data rebuild, relabeling, or promotion.
- Known-gap detail for sub-laws that require a future data-layer packet.

## Not Authorized

- No canonical DB writes.
- No schema migration execution.
- No refetch, rebuild, relabel, refit, or calibration promotion.
- No `climate_zone` taxonomy write; operator review is required before s3/s6.
- No active LAW 1 test that fails CI without an approved implementation packet.

## Triage Matrix

| Antibody | Initial verdict | Reason |
|---|---|---|
| `test_calibration_weight_continuity` | DATA_LAYER_BLOCKED | Needs `precision_weight` schema/read/write contract and cohorting for legacy binary rows. |
| `test_per_city_weighting_eligibility` | PATCHED_SAFE_CONFIG_TEST | `weighted_low_calibration_eligible` is explicit in `config/cities.json`; opt-out list follows LAW2 PoC-v5 authority. |
| `test_no_temp_delta_weight_in_production` | PATCHED_SAFE_STATIC_TEST | Targeted production calibration/strategy sources are statically checked. |
| `test_weight_floor_nonzero_for_ambig_only` | DATA_LAYER_BLOCKED | Needs `precision_weight` computation and persisted rows. |
| `test_high_track_unaffected_by_low_law` | DATA_LAYER_BLOCKED | Needs `precision_weight` semantics for HIGH rows. |
| `test_rebuild_n_mc_default_bounded` | PATCHED_CODE_TEST | Batch rebuild default now resolves through `calibration_batch_rebuild_n_mc() == 1000`; refit and transfer sentinel defaults were realigned. |
| `test_runtime_n_mc_floor` | ALREADY_COVERED | Existing `tests/test_runtime_n_mc_floor.py` plus `tests/test_evaluator_explicit_n_mc.py` cover runtime n_mc floor and explicit call threading. |
| `test_rebuild_per_track_savepoint` | PATCHED_STATIC_PLUS_EXISTING_RELATIONSHIP | New safe-subset test checks the code shape; `tests/test_rebuild_live_sentinel.py` continues to cover transaction sharding dynamically. |
| `test_no_per_city_alpha_tuning` | PATCHED_SAFE_STATIC_TEST | Targeted production calibration/strategy sources are statically checked. |
| `test_climate_zone_present` | DEFERRED_BY_OPERATOR_SCOPE | s3 is explicitly future/operator-reviewed taxonomy work. |
| `test_cluster_alpha_map_finite` | DEFERRED_BY_OPERATOR_SCOPE | Depends on s3/s6 cluster alpha design; not current production law. |

## Verification Plan

1. Read-only explorer mapping of current code against the 11 tests — complete.
2. Patch only safe static/code-only antibodies — complete.
3. Record blocked data-layer sub-laws in `known_gaps.md` with enough detail to
   drive a future implementation packet — complete.
4. Run focused tests for new active antibodies plus topology
   planning/map-maintenance checks — complete.
5. Critic review — `REVISE` on n_mc-incomplete-sentinel overlap, then
   `APPROVE` after repair.

## Verification Evidence

- Focused pytest:
  `tests/test_calibration_weighting_laws.py tests/test_config.py tests/test_runtime_n_mc_floor.py tests/test_evaluator_explicit_n_mc.py tests/test_rebuild_live_sentinel.py tests/test_calibration_transfer_policy_with_evidence.py tests/test_evaluate_calibration_transfer_oos.py`
  → `155 passed, 3 skipped`.
- `py_compile` on touched Wave37 source/tests: pass.
- `git diff --check` on touched Wave37 files: pass.
- `topology_doctor --planning-lock ... --plan-evidence PLAN.md`: pass.
- `topology_doctor --map-maintenance --map-maintenance-mode closeout ...`: pass.
- `topology_doctor --task-boot-profiles`: pass.

## Residual Disagreement

`docs/reference/zeus_calibration_weighting_authority.md` still contains a stale
sentence saying `rebuild_calibration_pairs_v2.py` defaults to
`ensemble_n_mc()`. Executable truth after Wave37 is
`calibration_batch_rebuild_n_mc() == 1000`. A direct reference-doc update was
not admitted because the document is missing `docs_registry` and
`reference_replacement` classification. The corrected current state is recorded
in `docs/to-do-list/known_gaps.md`; update the reference doc only through a
future admitted docs/topology cleanup route.
