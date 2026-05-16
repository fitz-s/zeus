# Object-Meaning Invariance Wave7 Plan

Created: 2026-05-05

Scope: forecast source identity crossing into calibration bucket lookup for
entry and monitor/exit calibration consumers.

## Boundary Selection

Selected boundary: forecast/model evidence -> calibration bucket identity ->
calibrated belief used by entry/monitor decisions.

Why this can affect live money:
- Entry decisions use forecast evidence, Platt calibration, market fusion, edge
  selection, and sizing.
- Monitor and Day0 refresh use the same calibration lookup before exit/hold
  economics.
- A source-family alias can select the wrong calibration bucket, miss v2
  calibration, or fall through to legacy high-only calibration.

Material values:
- `ens_result["source_id"]`: forecast source/provider identity.
- `ens_result["data_version"]`: metric/source-family data-version identity.
- `cycle`, `source_id`, `horizon_profile`: Platt v2 bucket axes.
- `cal._bucket_source_id`: loaded calibrator bucket identity used for transfer
  evidence.

Downstream consumers:
- `src/engine/evaluator.py` entry candidate path.
- `src/engine/monitor_refresh.py` ENS and Day0 monitor refresh paths.
- `src/engine/replay.py` diagnostic replay calibration path.
- `src/execution/harvester.py` settlement-to-learning pair write path.
- Transfer sigma evidence lookup in `src/engine/evaluator.py`.
- Decision evidence, risk sizing, monitor/exit decisions, and learning/report
  surfaces that read accepted decisions.

Stale or bypass paths considered:
- Shared `derive_phase2_keys_from_ens_result` returns raw source ids, but
  topology did not admit that helper for this wave.
- `src/calibration/manager.py` maps `tigge_mars` to source family `tigge` but
  does not map the runtime forecast-source alias `tigge`.
- `src/engine/replay.py` calls `get_calibrator` separately and must not use
  schema-default TIGGE calibration for diagnostic forecast rows.
- `src/execution/harvester.py` calls `add_calibration_pair_v2` separately after
  settlement, and schema/helper defaults can relabel unsupported learning rows
  as `tigge_mars`.

## Lineage Table

| Value | Real object denoted | Origin | Authority | Unit/side | Time basis | Transform | Persistence | Consumers | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ens_result.source_id` | Runtime forecast provider/source identity | `src/data/ensemble_client.py`, `src/data/tigge_client.py`, executable forecast reader | Forecast source registry + ingest payload | Provider/source id, not calibration bucket id | issue/fetch/decision/monitor | Must preserve provider identity for evidence; explicitly transform before calibration lookup | decision evidence, snapshots | evaluator, monitor, reports | Ambiguous before repair for `tigge` alias |
| `ens_result.data_version` | Forecast metric/source-family data version | metric identity factories, forecast rows | typed metric/source-family contract | high/low local-day metric family | issue/decision/monitor | Used to reject unknown source families and prove family consistency | snapshots/decision context | evaluator, calibration lookup | Preserved |
| Platt lookup `source_id` | Calibration bucket source identity | evaluator/monitor derived keys | Platt v2 schema + calibration store | calibration bucket axis | decision/monitor | Must canonicalize forecast alias `tigge` -> bucket id `tigge_mars`; OpenData remains `ecmwf_open_data` | platt_models_v2 | calibration manager/store | Broken before repair |
| `cal._bucket_source_id` | Actual loaded calibrator source bucket | calibration store row | verified v2 model row | calibration bucket axis | decision | Must match source evidence or explicit transfer policy | transient cal object | transfer sigma gate | Dependent |
| Harvester pair `source_id` | Calibration-pair source bucket or explicit non-bucket audit identity | `src/execution/harvester.py` -> `add_calibration_pair_v2` | settlement + decision snapshot context | calibration-pair bucket axis, not generic forecast source | settlement/learning | Supported data versions map to bucket id; unsupported data versions must write explicit `unsupported_*`, never schema default | calibration_pairs_v2 | refit/OOS/report learning readers | Broken before repair |

## Findings

W7-F1 (S1/S2): Runtime TIGGE forecast evidence can carry
`source_id="tigge"` while Platt v2 bucket identity and transfer evidence use
`source_id="tigge_mars"`. Entry and monitor paths pass raw derived source ids
to `get_calibrator`, so the same physical/model forecast can become a different
calibration-bucket object. Live effect is gated by TIGGE operator enablement;
legacy/tests and future TIGGE activation remain reachable. Economic impact:
wrong or missing calibration can change posterior, edge, sizing, or exit/hold
decisions, and high-only legacy fallback can contaminate decision quality.

W7-F2 (S1): Monitor fallback and diagnostic replay sources without a registered
calibration bucket can fall through to get_calibrator defaults/legacy fallback,
using TIGGE/high calibration as if unsupported source evidence had calibration
authority. Live entry is source-policy-gated; monitor/exit and replay/report
surfaces remain reachable.

W7-F3 (S2): Harvester settlement learning rows with unsupported
`data_version` and a valid issue cycle can reach `add_calibration_pair_v2` with
`source_id=None`. Because both the schema and helper default to `tigge_mars`,
an audit-only/live fallback p_raw row can be persisted as a TIGGE calibration
bucket object. `training_allowed` blocks refit by default, but reports, OOS
checks, and future bypasses can still consume the mislabeled row identity.

## Repair Invariant

Forecast provider/source identity must remain unchanged in evidence,
provenance, reports, and bias-reference lookup. Only the Platt lookup boundary
may transform it into a calibration bucket source id, and that transform must
be explicit, source-policy-owned, tested, and fail closed for unknown
data-version families already rejected by evaluator gates.

## Allowed Repair Scope

Admitted route:

`python3 scripts/topology_doctor.py --navigation --task "DSA-02 DSA-03 forecast source identity: canonical source_id for calibration bucket lookup" --write-intent edit --files src/data/forecast_source_registry.py src/engine/evaluator.py src/engine/monitor_refresh.py tests/test_forecast_source_registry.py tests/test_decision_evidence_runtime_invocation.py tests/test_runtime_guards.py`

Replay add-on route:

`python3 scripts/topology_doctor.py --navigation --task "DSA-02 DSA-03 forecast source identity: replay calibration lookup must not use schema default source bucket" --write-intent edit --files src/engine/replay.py tests/test_replay_skill_eligibility_filter.py`

Harvester add-on route:

`python3 scripts/topology_doctor.py --navigation --task "pricing semantics authority cutover: harvester learning context source identity must not default unsupported source to TIGGE bucket" --write-intent edit --files src/execution/harvester.py tests/test_harvester_metric_identity.py`

Allowed files:
- `src/data/forecast_source_registry.py`
- `src/engine/evaluator.py`
- `src/engine/monitor_refresh.py`
- `src/engine/replay.py`
- `tests/test_forecast_source_registry.py`
- `tests/test_decision_evidence_runtime_invocation.py`
- `tests/test_runtime_guards.py`
- `tests/test_ensemble_client.py`
- `tests/test_replay_skill_eligibility_filter.py`
- `src/execution/harvester.py`
- `tests/test_harvester_metric_identity.py`

Not allowed in this wave:
- `src/calibration/forecast_calibration_domain.py`
- `src/calibration/manager.py`
- `src/calibration/store.py`
- `src/types/metric_identity.py`
- production DB mutation, calibration retrain/refit, source routing changes,
  active TIGGE fetch enablement, or live venue side effects.

## Verification Plan

- Unit/source-policy test for calibration lookup canonicalization.
- Relationship tests proving entry and monitor paths pass `tigge_mars` to
  calibration lookup while preserving `forecast_source_id:tigge` evidence.
- Replay lookup tests proving TIGGE data_version maps to `tigge_mars` and
  diagnostic forecast rows cannot use schema-default calibration.
- Harvester relationship tests proving unsupported settlement-learning rows
  persist as explicit `unsupported_*` source identities and malformed cycle
  evidence fails closed before schema defaults can materialize.
- Focused tests from the Phase 1 source policy route.
- Planning-lock check with this plan as evidence.
- Critic review before advancing beyond Wave7.

## Topology Compatibility Notes

- The broad object-meaning boundary first routed as generic/advisory because
  high-fanout files could not select a profile.
- The shared calibration helper and manager are semantically central but not
  admitted by the source-policy route; the repair must therefore be anchored in
  the source-policy helper plus admitted live consumers.
- Exact typed intent `DSA-02 DSA-03 forecast source identity` was required for
  admission.
- A deeper shared-helper repair in `src/calibration/store.py` was blocked as
  scope expansion under the pricing route even though the schema-default hazard
  is the shared mechanism. This is a genuine topology compatibility issue to
  carry forward: broad invariant repairs need a route that admits the common
  persistence helper when downstream files expose the same object drift.
- The same static defaulting shape exists in the dangerous long-lived repair
  script `scripts/rebuild_calibration_pairs_v2.py`. Its current data-version
  and training filters reduce the active risk, but the script route stayed
  generic/advisory and replay route rejected it as out-of-scope. Treat this as
  unresolved topology compatibility, not as a silently completed class repair.
- `--fatal-misreads --json` still fails on missing archived low-backfill proof
  paths under `docs/operations/task_2026-04-28_settlements_low_backfill/`. This
  is unrelated to Wave7 behavior but remains a topology/path-maintenance
  compatibility issue.
