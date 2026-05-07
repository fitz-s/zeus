# Post-Merge LOW/HIGH Recalibration Plan

Created: 2026-05-07

Authority basis: PR #76 merged into `main` at `c3deb5fc`; operator directive to
prepare recalibration on main carefully with a plan before any production write.

## Objective

Prepare a controlled recalibration sequence after the LOW/HIGH alignment fixes
landed on `main`.

The goal is to rebuild and refit only the calibration cohorts whose forecast
object, settlement object, bin grid, source, cycle, horizon, and data_version are
explicitly proven. The plan does not authorize live promotion by itself.

## Non-Scope

- No production DB mutation before dry-run evidence is reviewed.
- No raw GRIB deletion, redownload, or grid-resolution remediation.
- No OpenData/TIGGE live calibration sharing by assertion alone.
- No fallback Platt live promotion without `CalibrationAuthorityResult` proof.
- No HIGH/LOW mixed refit, report, or promotion cohort.

## Main-State Baseline

Required baseline before any recalibration command:

1. Work from a clean checkout of `origin/main` at or after `c3deb5fc`.
2. Run calibration-pair rebuild preflight against the production world DB:

   ```bash
   /Users/leofitz/miniconda3/bin/python scripts/verify_truth_surfaces.py \
     --mode calibration-pair-rebuild-preflight \
     --world-db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
     --json
   ```

3. Block if `status != READY` or `blockers` is non-empty.
4. Record HIGH/LOW eligible snapshot counts and LOW contract-window safety count
   from that preflight output.

Current baseline observed from `origin/main@c3deb5fc` on 2026-05-07:

- calibration-pair rebuild preflight: `READY`;
- blockers: `[]`;
- HIGH rebuild-eligible snapshots: `385163`;
- LOW rebuild-eligible snapshots: `83545`;
- LOW evidence-required training rows with unsafe contract-window evidence: `0`.

Production DB dry-run caveat:

- a bounded production `rebuild_calibration_pairs_v2.py --dry-run` attempt hit
  `sqlite3.OperationalError: database is locked` while runtime DB users were
  active;
- do not kill runtime DB users for planning;
- schedule a quiet DB window or fix dry-run schema initialization before
  treating production dry-run execution as available.

Temporary-copy smoke:

- a temporary copy of `state/zeus-world.db` was used for command-shape smoke and
  deleted afterward;
- command: LOW/TIGGE contract-window v2, `--city Chicago`, dry-run;
- result: `Snapshots scanned: 0`, `estimated written pairs: 0`;
- implication: the merged code can execute the scoped dry-run, but current DB
  does not yet contain Chicago rows for that contract-window recovery
  data_version. Recovery rows must be backfilled/re-extracted before this slice
  can materially increase LOW pairs.

## Current Decision State

GO:

- read-only inventory on production DB;
- bounded LOW contract-window evidence backfill dry-runs;
- dry-run/smoke work on a temporary DB copy;
- scoped script fixes that make dry-run and backfill evidence safer.

NO-GO until cleared:

- production DB pair rebuild apply;
- production DB Platt refit apply;
- LOW promotion or canary activation;
- OpenData/TIGGE shared calibration authority;
- production Platt refit preflight until `idx_calibration_pairs_v2_refit_scope`
  exists in the production world DB;
- fallback Platt live use beyond the explicit `CalibrationAuthorityResult`
  gates already merged in PR #76.

The immediate remaining task is not refit. It is to prove that enough LOW
forecast-window-to-contract evidence exists, or can be safely materialized, for
the contract-window data_versions to produce distinct decision groups.

## Is Recalibration The Only Remaining Task?

No. Recalibration is necessary, but it is only the middle of the recovery path.
The LOW/HIGH asymmetry fix is complete only when the full money-path object is
preserved from forecast evidence through calibration authority and promotion:

```text
forecast-window evidence
-> recovered snapshot rows
-> calibration_pairs_v2
-> Platt/refit rows
-> CalibrationAuthorityResult
-> shadow/live promotion policy
-> evaluator decision evidence
```

If the first two steps do not produce safe LOW contract-window rows,
recalibration has nothing new to learn from. If the later authority/promotion
steps are skipped, refit can create models that are not safe to use live.

## Remaining Task Checklist

### A. Guardrail / Implementation Fixes

- [x] Harden `backfill_low_contract_window_evidence.py` against non-dict
  `boundary_policy` payloads before any apply.
- [x] Fix or avoid the production dry-run DB lock path caused by schema
  initialization in `rebuild_calibration_pairs_v2.py --dry-run`.
- [x] Make Platt refit preflight bounded enough to run before live refit apply.
- [x] Keep all fixes scoped and covered by focused tests; do not change raw GRIB
  download/grid settings in this lane.
- [x] Apply or verify the production DB schema index
  `idx_calibration_pairs_v2_refit_scope` before any live Platt refit preflight.

### B. Evidence And Coverage Inventory

- [x] Run current LOW/HIGH alignment inventory against production DB.
- [x] Run bounded TIGGE/MARS LOW contract-window backfill dry-runs.
- [x] Run bounded OpenData LOW contract-window backfill dry-runs.
- [x] Split evidence by city, metric, source, cycle, horizon, and data_version.
  The current diagnostic exposes this as a bounded-sample preview so it remains
  safe on the production DB; exhaustive counts still require targeted SQL before
  any apply.
- [x] Identify whether missing rows are due to absent raw JSON, absent legacy
  snapshots, rejected boundary windows, or already-materialized recovery rows.

### C. Recovery Row Materialization

- [x] If dry-runs show safe `would_insert` rows, apply recovery rows only by
  reviewed source/city/date/data_version scope.
- [ ] If dry-runs show missing extracted JSON, run extraction/backfill coverage
  repair before calibration.
- [ ] If dry-runs show missing legacy snapshots, run snapshot backfill/re-extract
  before calibration.
- [x] Verify inserted recovery rows carry contract-window evidence and do not
  mutate old LOW rows.

### D. Calibration Pair Rebuild

- [x] Re-run calibration-pair rebuild preflight immediately before apply.
- [ ] Dry-run LOW legacy and LOW contract-window data_versions independently.
- [x] Apply pair rebuild only for one approved data_version/scope at a time.
- [x] Verify LOW/HIGH, source, cycle, horizon, and data_version remain separated.
- [x] Confirm new LOW pairs improve distinct `decision_group_id` / `n_eff`, not
  just raw row count.

### E. Platt Refit / Recalibration

- [x] Run bounded Platt refit preflight.
- [x] Dry-run LOW Platt refit by explicit data_version.
- [x] Apply refit only after dry-run diagnostics show stable, sufficient buckets.
- [x] Keep low-n, inverted, unstable, or quarantined buckets out of live
  authority.
- [ ] Do not pool OpenData/TIGGE or 00Z/12Z unless separately proven compatible.

### F. Authority, Promotion, And Runtime Readiness

- [x] Verify `CalibrationAuthorityResult` route/provenance for production sample
  LOW/HIGH decisions.
  In-memory manager tests already cover LOW primary, LOW fallback block, and
  primary-domain mismatch. Production sample evidence now covers
  Chicago/LOW/TIGGE/00Z/full/contract-window as `PRIMARY_EXACT`.
- [x] Verify exact requested/served domain for city, metric, source, cycle,
  horizon, data_version, settlement semantics, and bin grid.
- [ ] Run evaluator/shadow evidence checks before any live/canary promotion.
- [x] Produce before/after report: eligible snapshots, recovery rows,
  calibration pairs, Platt buckets, `n_eff`, route counts, blocked reasons, and
  LOW/HIGH delta.
- [ ] Promote only cohorts whose contract-domain authority is exact and whose
  calibration evidence passes threshold.

## Apply-Blocking Friction

These issues must be resolved before any production `--no-dry-run` command:

- `rebuild_calibration_pairs_v2.py --dry-run` now opens explicit read-only DB
  connections and skips schema initialization. Production dry-run no longer
  needs to become a writer.
- `verify_truth_surfaces.py --mode platt-refit-preflight` now supports
  `--temperature-metric` and `--data-version` scope and fails fast when the
  refit-scope index is missing.
- `backfill_low_contract_window_evidence.py` now guards non-dict
  `boundary_policy` payloads in recovery-row construction.
- The current production DB does not yet show material rows for at least the
  sampled TIGGE LOW contract-window recovery slice. Pair rebuild cannot improve
  LOW `n_eff` until recovery rows are present.
- Source has schema support for `idx_calibration_pairs_v2_refit_scope`, but the
  production DB does not yet have that index. Platt refit preflight remains
  NOT_READY until a controlled schema apply adds it.

## Implementation Evidence - 2026-05-07

Code structure completed:

- backfill recovery-row construction is safe for malformed/non-dict
  `boundary_policy`;
- rebuild dry-run uses read-only SQLite URI mode and does not call schema init;
- refit dry-run uses read-only SQLite URI mode and does not call schema init;
- Platt refit live preflight receives the requested metric/data_version scope;
- `calibration_pairs_v2` schema now defines
  `idx_calibration_pairs_v2_refit_scope`;
- scoped Platt preflight fails fast if that index is absent instead of scanning
  the full production pair table.
- LOW/HIGH alignment diagnostic now exposes bounded domain splits for snapshots,
  calibration pairs, and Platt models across city/metric/source/cycle/horizon
  and data_version without changing live behavior.

Focused verification:

- `python -m py_compile` on touched scripts/schema: PASS;
- `pytest tests/test_low_contract_window_backfill.py -q`: 13 passed;
- scoped `tests/test_truth_surface_health.py` selection: 8 passed;
- `pytest tests/test_verify_truth_surfaces_phase2_gate.py -q`: 5 passed;
- `pytest tests/test_rebuild_live_sentinel.py -q`: 5 passed, 2 skipped;
- `pytest tests/test_phase5_gate_d_low_purity.py -q`: 9 passed;
- `pytest tests/test_low_high_alignment_report.py -q`: 5 passed;
- `git diff --check`: PASS;
- `topology_doctor --planning-lock ...`: PASS;
- `topology_doctor --map-maintenance --map-maintenance-mode advisory ...`: PASS.

Known unrelated/stale test friction:

- `pytest tests/test_phase7a_metric_cutover.py tests/test_phase5_gate_d_low_purity.py -q`
  has one existing semantic failure in
  `TestR_BJ_OuterSavepointAtomicity.test_R_BJ_1_low_failure_rolls_back_high`.
  The test expects global outer-savepoint rollback, while current
  `rebuild_v2` documents and implements per-city/per-metric bounded commits.
  This was not changed in this slice.

Production read-only evidence:

- calibration-pair rebuild preflight: `READY`, blockers `[]`;
- HIGH rebuild-eligible snapshots: `385163`;
- LOW rebuild-eligible snapshots: `83545`;
- LOW evidence-required unsafe training rows: `0`;
- TIGGE/MARS LOW backfill dry-run, first 1000 files: `would_insert=1000`,
  `training_candidates=80`, `blocked_candidates=920`, `no_matching_snapshot=0`;
- OpenData LOW backfill dry-run, first 1000 files: `would_insert=265`,
  `training_candidates=41`, `blocked_candidates=224`,
  `no_matching_snapshot=735`;
- LOW TIGGE contract-window pair rebuild dry-run:
  `Snapshots scanned=0`, `estimated written pairs=0`;
- scoped LOW TIGGE contract-window Platt preflight:
  `NOT_READY`, blocker `calibration_pairs_v2.refit_scope_index_missing`.
- LOW/HIGH alignment diagnostic bounded split smoke, production DB:
  `snapshot_domain_rows=5`, `pair_split_ready=True`, `pair_domain_rows=1`,
  `platt_domain_rows=5`, `low_proven_training=0`,
  `derived_context_only=True`, `live_behavior_changed=False`.

Production controlled materialization evidence:

- `idx_calibration_pairs_v2_refit_scope` was created on production DB with
  prefix `(temperature_metric, data_version)`;
- Chicago/TIGGE LOW contract-window backfill apply:
  `inserted=500`, `training_candidates=500`, `blocked_candidates=0`,
  attribution `FULLY_INSIDE_TARGET_LOCAL_DAY=500`;
- old Chicago LOW v1 rows were not mutated:
  `provenance_json LIKE '%low_contract_window_backfill%'` on v1 rows = `0`;
- Chicago/TIGGE LOW contract-window pair rebuild dry-run:
  `snapshots passing gates=500`, `estimated written pairs=46000`,
  contract/observation/unit rejects all `0`;
- Chicago/TIGGE LOW contract-window pair rebuild apply:
  `Pairs written=46000`, `decision_group_id` count = `500`;
- scoped Platt preflight after pair rebuild: `READY`, blockers `[]`,
  mature buckets = `2`;
- Platt refit dry-run:
  `Chicago DJF n_eff=452 A=+1.213 B=+0.025 C=+0.357 Brier=0.0104`,
  `Chicago MAM n_eff=48 A=+1.523 B=+0.058 C=+1.288 Brier=0.0095`,
  `quarantined=0`;
- production Platt refit apply wrote two `VERIFIED` active rows for
  Chicago/LOW/TIGGE/00Z/full/contract-window;
- runtime lookup sample:
  `get_calibrator(... Chicago, 2024-01-15, low, cycle=00, source_id=tigge_mars,
  horizon_profile=full)` returns
  `tigge_mn2t6_local_calendar_day_min_contract_window_v2`, level `1`;
- `CalibrationAuthorityResult` for the same sample:
  `route=PRIMARY_EXACT`, `live_eligible=True`, `block_reasons=[]`,
  requested domain equals served domain, all compatibility gates true.

Production rollback scope for this slice:

```sql
DELETE FROM platt_models_v2
WHERE temperature_metric='low'
  AND cluster='Chicago'
  AND data_version='tigge_mn2t6_local_calendar_day_min_contract_window_v2'
  AND cycle='00'
  AND source_id='tigge_mars'
  AND horizon_profile='full';

DELETE FROM calibration_pairs_v2
WHERE city='Chicago'
  AND temperature_metric='low'
  AND data_version='tigge_mn2t6_local_calendar_day_min_contract_window_v2'
  AND source_id='tigge_mars'
  AND cycle='00'
  AND horizon_profile='full';

DELETE FROM ensemble_snapshots_v2
WHERE city='Chicago'
  AND temperature_metric='low'
  AND data_version='tigge_mn2t6_local_calendar_day_min_contract_window_v2'
  AND source_id='tigge_mars'
  AND provenance_json LIKE '%low_contract_window_backfill%';
```

Topology/performance friction found and fixed:

- the first domain-split diagnostic used full-table `GROUP BY` on production
  calibration-pair history and did not return in an acceptable interactive
  window;
- the diagnostic now labels the split as `bounded_sample_before_group` with
  `sample_limit_rows=50000`, preserving operator visibility without turning a
  report into a production-scale aggregation job.

Current structural decision:

- GO: Chicago/LOW/TIGGE/00Z/full contract-window runtime data is usable by the
  calibration read path as primary exact authority.
- NO-GO: broader city/source/cycle rollout, OpenData/TIGGE pooling, 12Z sharing,
  and live/canary promotion beyond exact-domain authority samples until each
  cohort repeats the same evidence chain.

Final materialization update, 2026-05-07:

- TIGGE LOW contract-window 00Z recovery snapshots:
  `347082` rows, `75099` training-safe rows, `271983` boundary-rejected rows.
- TIGGE LOW contract-window 12Z recovery snapshots:
  `1624` rows, `374` training-safe rows, `1250` boundary-rejected rows.
- TIGGE LOW contract-window 00Z pairs:
  `7390138` rows / `74839` distinct decision groups.
- TIGGE LOW contract-window 12Z pairs:
  `36478` rows / `374` distinct decision groups.
- Active LOW TIGGE 00Z Platts:
  `175 VERIFIED` buckets (`n_eff=55..1993`) and `21 UNVERIFIED` shadow buckets
  (`n_eff=15..48`).
- Active LOW TIGGE 12Z Platts:
  `7 UNVERIFIED` shadow buckets (`n_eff=16..32`) and no `VERIFIED` buckets.
- Active `VERIFIED` LOW contract-window Platts with `n_eff < 50`: `0`.
- Runtime lookup samples confirm exact 00Z primary buckets are live-eligible
  while 12Z, low-n, missing, and quarantined LOW buckets return raw/level-4
  instead of borrowing pool fallback.

See `REPORT.md` in this packet for the before/after table and verification
commands.

## Slice 1 - Evidence Inventory

Run read-only inventory before rebuild:

```bash
/Users/leofitz/miniconda3/bin/python scripts/diagnose_low_high_alignment.py \
  --db-path /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
  --stdout
```

Acceptance:

- report separates HIGH and LOW;
- LOW boundary rejection and contract-window recovery rows are visible;
- active Platt route/maturity counts are visible;
- no claim of live promotion is made from this diagnostic.

## Slice 2 - LOW Contract-Window Evidence Backfill Dry Runs

Before pair rebuild, run bounded dry-runs that answer whether recovery rows can
be materialized from already-extracted JSON and existing legacy snapshots.

TIGGE/MARS dry-run:

```bash
/Users/leofitz/miniconda3/bin/python scripts/backfill_low_contract_window_evidence.py \
  --db-path /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
  --source-family tigge_mars \
  --limit 1000
```

OpenData dry-run:

```bash
/Users/leofitz/miniconda3/bin/python scripts/backfill_low_contract_window_evidence.py \
  --db-path /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
  --source-family ecmwf_open_data \
  --limit 1000
```

Then repeat by representative cities and both cycles where raw evidence exists:

- tropical UTC+8/9 LOW: Kuala Lumpur, Singapore, Jakarta;
- temperate/control LOW: Chicago, NYC, Amsterdam;
- source families: `tigge_mars`, `ecmwf_open_data`;
- cycles: 00Z and 12Z must remain separate evidence cohorts.

Acceptance:

- `would_insert` and `training_candidates` are non-zero for at least one
  representative LOW recovery cohort before any all-scope apply;
- ambiguous midnight-crossing rows remain blocked;
- deterministic previous/next-day rows are counted separately and are not
  silently relabeled;
- dry-run output distinguishes `no_matching_snapshot` from source JSON absence;
- if dry-run mostly reports `no_matching_snapshot`, the next step is re-extract
  or snapshot backfill, not pair rebuild;
- if dry-run mostly reports missing JSON/raw input, the next step is raw-data
  inventory and extraction coverage repair, not calibration.

## Slice 3 - LOW Contract-Window Evidence Apply Gate

Backfill apply is allowed only after Slice 2 gives a scoped, reviewed recovery
set and the fragile `boundary_policy` guard is fixed or proven unreachable by
tests.

Apply command shape:

```bash
/Users/leofitz/miniconda3/bin/python scripts/backfill_low_contract_window_evidence.py \
  --apply --force \
  --db-path /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
  --source-family <tigge_mars|ecmwf_open_data> \
  --city <city> \
  --start-date <YYYY-MM-DD> \
  --end-date <YYYY-MM-DD>
```

Acceptance:

- only new contract-window recovery data_version rows are inserted;
- old legacy LOW rows are not mutated;
- inserted rows carry `FULLY_INSIDE_TARGET_LOCAL_DAY` evidence when used for
  training;
- `source_id`, cycle, horizon, target local date, metric, and data_version stay
  explicit;
- rollback query is scoped to the same source/city/date/data_version slice.

## Slice 4 - Pair Rebuild Dry Runs

Dry-run each data_version independently. Do not use `--no-dry-run` here.

LOW legacy:

```bash
/Users/leofitz/miniconda3/bin/python scripts/rebuild_calibration_pairs_v2.py \
  --dry-run \
  --temperature-metric low \
  --data-version tigge_mn2t6_local_calendar_day_min_v1 \
  --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
```

LOW TIGGE contract-window recovery:

```bash
/Users/leofitz/miniconda3/bin/python scripts/rebuild_calibration_pairs_v2.py \
  --dry-run \
  --temperature-metric low \
  --data-version tigge_mn2t6_local_calendar_day_min_contract_window_v2 \
  --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
```

LOW OpenData contract-window recovery:

```bash
/Users/leofitz/miniconda3/bin/python scripts/rebuild_calibration_pairs_v2.py \
  --dry-run \
  --temperature-metric low \
  --data-version ecmwf_opendata_mn2t3_local_calendar_day_min_contract_window_v2 \
  --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
```

HIGH baseline:

```bash
/Users/leofitz/miniconda3/bin/python scripts/rebuild_calibration_pairs_v2.py \
  --dry-run \
  --temperature-metric high \
  --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
```

Acceptance:

- each run reports candidate, written, blocked, and block-reason counts;
- LOW contract-window runs reject rows lacking persisted contract evidence;
- no adjacent-day or ambiguous LOW row becomes a training candidate;
- HIGH dry-run stays materially stable versus pre-merge baseline.

## Slice 5 - Pair Rebuild Apply Gate

Apply is allowed only after Slice 4 evidence is reviewed.

Required operator-reviewed facts:

- target data_version;
- city/date scope or explicit all-city scope;
- expected delete/write row count;
- rollback query for the same data_version scope;
- preflight still READY immediately before apply.

Apply command shape:

```bash
/Users/leofitz/miniconda3/bin/python scripts/rebuild_calibration_pairs_v2.py \
  --no-dry-run --force \
  --temperature-metric low \
  --data-version <one-approved-data-version> \
  --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
```

Stop if the command would operate on more than one data_version without an
explicit operator note in the work log.

## Slice 6 - Platt Refit Dry Runs

After pair rebuild apply and a fresh preflight, dry-run refit by explicit
data_version:

```bash
/Users/leofitz/miniconda3/bin/python scripts/refit_platt_v2.py \
  --dry-run --strict \
  --temperature-metric low \
  --data-version <one-approved-data-version> \
  --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
```

Acceptance:

- bucket list is explicit by cluster/season/cycle/source/horizon/data_version;
- `n_samples` and effective group evidence are visible;
- low-n or inverted/unstable buckets are not promoted;
- refit does not mix LOW/HIGH or legacy/contract-window data_versions.

## Slice 7 - Platt Refit Apply Gate

Apply refit only after Slice 6 dry-run output is accepted.

Apply command shape:

```bash
/Users/leofitz/miniconda3/bin/python scripts/refit_platt_v2.py \
  --no-dry-run --force --strict --assume-schema-ready \
  --temperature-metric low \
  --data-version <one-approved-data-version> \
  --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
```

Acceptance:

- scoped platt-refit preflight is `READY` immediately before using
  `--assume-schema-ready`;
- all written rows are `VERIFIED` only when model diagnostics pass;
- QUARANTINED or unstable rows cannot become live authority;
- contract-window LOW rows are reachable through `get_calibrator(...,
  source_id='tigge_mars'|'ecmwf_open_data')`.

## Slice 8 - Promotion Readiness

Promotion is a separate gate, not part of rebuild/refit.

Required evidence:

- `CalibrationAuthorityResult` route and served/requested domain for sample
  cities and both 00Z/12Z where present;
- LOW/HIGH separate reliability summary;
- fallback/quarantine route counts;
- `n_eff` thresholds by city/metric/cycle/source/horizon/data_version;
- no unresolved OpenData/TIGGE equivalence assertion being used as live proof.

## OpenData/TIGGE Handling Rule

OpenData and TIGGE may represent the same ECMWF ensemble physical quantities
only after paired proof at the forecast-object level. That proof does not create
shared calibration authority by itself.

Before any shared treatment:

- compare the same source family, issue time, cycle, member, step range, grid,
  `mn2t6`/`mx2t6` quantity, unit, and interpolation/extraction settings;
- confirm both paths construct the same city-local target-day extrema object;
- confirm the same settlement source field, rounding/truncation policy, and
  canonical bin grid;
- keep 00Z and 12Z as separate calibration cohorts unless validation proves
  cycle compatibility for the same contract outcome domain;
- keep source-specific data_versions separate in refit/promotion until
  evidence supports a planned transfer or hierarchical pooling design.

## Stop Conditions

Stop and re-plan before:

- running any `--no-dry-run` command without current preflight READY evidence;
- touching `state/*.db` outside approved rebuild/refit script command shapes;
- combining multiple LOW data_versions in one apply;
- running live evaluator promotion;
- treating a fallback calibrator as primary exact;
- using OpenData/TIGGE equivalence without paired issue/member/step/bin proof;
- modifying raw download/grid settings as part of recalibration.

## Verification Bundle For First Dry-Run Round

Minimum first-round commands:

```bash
/Users/leofitz/miniconda3/bin/python scripts/verify_truth_surfaces.py \
  --mode calibration-pair-rebuild-preflight \
  --world-db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
  --json

/Users/leofitz/miniconda3/bin/python scripts/diagnose_low_high_alignment.py \
  --db-path /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
  --stdout

/Users/leofitz/miniconda3/bin/python scripts/backfill_low_contract_window_evidence.py \
  --db-path /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
  --source-family tigge_mars \
  --limit 1000

/Users/leofitz/miniconda3/bin/python scripts/backfill_low_contract_window_evidence.py \
  --db-path /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
  --source-family ecmwf_open_data \
  --limit 1000

/Users/leofitz/miniconda3/bin/python scripts/rebuild_calibration_pairs_v2.py \
  --dry-run --temperature-metric low \
  --data-version tigge_mn2t6_local_calendar_day_min_contract_window_v2 \
  --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db
```

The first dry-run round is complete only when outputs show either an actionable
safe backfill scope or a clear no-go reason. Refit dry-run is intentionally not
part of the first round because it is downstream of successful recovery-row
materialization and pair rebuild apply.

## Third-Party Critic Review - 2026-05-07

Independent read-only critic review returned `NO-GO` for broader continuation,
but did not recommend rollback or quarantine of the already-applied Chicago LOW
production scope. The blocker was validation-layer correctness:

- `scripts/verify_truth_surfaces.py` allowed `calibration_pairs_v2` preflight
  to proceed when Phase 2 stratification columns were absent.
- The maturity query then grouped by `cycle`, `source_id`, and
  `horizon_profile`, causing `sqlite3.OperationalError` instead of a stable
  fail-closed validation result in older/minimal schemas.

Repair applied:

- `calibration_pairs_v2` preflight required columns now include `cycle`,
  `source_id`, and `horizon_profile`.
- ready-state tests seed those columns explicitly so legacy-schema fixtures fail
  only where the schema-gate tests intend them to fail.

Post-repair evidence:

```bash
/Users/leofitz/miniconda3/bin/python -m pytest \
  tests/test_truth_surface_health.py::TestTrainingReadinessP0 \
  tests/test_truth_surface_health.py::TestP4Readiness \
  tests/test_verify_truth_surfaces_phase2_gate.py -q
# 83 passed

/Users/leofitz/miniconda3/bin/python scripts/verify_truth_surfaces.py \
  --mode platt-refit-preflight \
  --world-db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
  --temperature-metric low \
  --data-version tigge_mn2t6_local_calendar_day_min_contract_window_v2 \
  --json
# status READY, blockers []

/Users/leofitz/miniconda3/bin/python -m pytest \
  tests/test_low_contract_window_backfill.py \
  tests/test_low_high_alignment_report.py \
  tests/test_verify_truth_surfaces_phase2_gate.py \
  tests/test_rebuild_live_sentinel.py \
  tests/test_phase5_gate_d_low_purity.py \
  tests/test_truth_surface_health.py::TestTrainingReadinessP0 \
  tests/test_truth_surface_health.py::TestP4Readiness -q
# 115 passed, 2 skipped
```

Production slice recheck:

```text
snapshots_contract_window|500
calibration_pairs_contract_window|46000
platt_models_contract_window|2
refit_scope_index|1
```

Updated continuation posture: the critic's blocker is repaired for this slice.
Continuation remains scoped; next actions are not recalibration-only and must
still pass contract-domain/runtime-readiness gates before any broader promotion.
