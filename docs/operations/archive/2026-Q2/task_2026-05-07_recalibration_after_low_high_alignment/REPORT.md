# LOW/HIGH Alignment Recovery Report

Created: 2026-05-07

Branch: `low-high-recalibration-structure-2026-05-07`

Production DB: `/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db`

> **M1 clarification (PR #93 critic, 2026-05-07)** — The 175 production
> `VERIFIED` LOW Platt rows referenced below were materialized in the prior
> `low-high-recalibration-structure-2026-05-07` worktree session by the PR #80
> agent (production refit committed 2026-05-07; commit history on that branch).
> This PR (#93) does NOT re-run the production refit. It documents the recovery
> outcome and tightens the gates (preflight required-column check, source/cycle
> stratification, LOW purity at the live read seam) that should govern any
> future refit. Treat the "After this run" column as a description of state
> already on disk, not as evidence of work performed by this PR.

## Executive Result

The TIGGE/MARS LOW contract-window recovery is now materialized for both 00Z
and 12Z, but only 00Z currently has runtime-usable LOW Platt authority.

- 00Z: usable for exact-domain LOW calibration where a `VERIFIED` primary bucket
  exists.
- 12Z: recovered and pair-built, but shadow-only because every fitted 12Z bucket
  has `n_eff < 50`.
- Fallback: LOW pool fallback is blocked at the live read seam; missing,
  low-n, unverified, or quarantined LOW buckets return raw/level-4 instead of
  being silently rescued by another city.

## Before / After

| Surface | Before this run | After this run |
| --- | ---: | ---: |
| LOW TIGGE contract-window recovery snapshots, 00Z | 500 Chicago-only canary rows | 347,082 rows |
| LOW TIGGE contract-window recovery snapshots, 12Z | 0 rows | 1,624 rows |
| LOW TIGGE contract-window training-safe snapshots, 00Z | 500 | 75,099 |
| LOW TIGGE contract-window training-safe snapshots, 12Z | 0 | 374 |
| LOW TIGGE contract-window pairs, 00Z | 46,000 Chicago-only pairs | 7,390,138 pairs / 74,839 groups |
| LOW TIGGE contract-window pairs, 12Z | 0 | 36,478 pairs / 374 groups |
| Active 00Z LOW Platts, `VERIFIED` | 2 Chicago canary rows | 175 buckets, min `n_eff=55`, max `n_eff=1993` |
| Active 00Z LOW Platts, shadow `UNVERIFIED` | 0 | 21 buckets, `n_eff=15..48` |
| Active 12Z LOW Platts, `VERIFIED` | 0 | 0 |
| Active 12Z LOW Platts, shadow `UNVERIFIED` | 0 | 7 buckets, `n_eff=16..32` |
| Active `VERIFIED` LOW Platts with `n_eff < 50` | not enforced | 0 |

## Runtime Samples

Observed after full 00Z refit and 12Z shadow refit:

| Sample | Result |
| --- | --- |
| Chicago LOW TIGGE 00Z DJF | `cal=True`, level `1`, `PRIMARY_EXACT`, `live=True`, `n_eff=1892` |
| Chicago LOW TIGGE 00Z MAM | `cal=True`, level `1`, `PRIMARY_EXACT`, `live=True`, `n_eff=387` |
| Chicago LOW TIGGE 12Z DJF | `cal=False`, level `4`, `RAW_UNCALIBRATED`, `live=False` |
| Kuala Lumpur LOW TIGGE 00Z DJF | `cal=False`, level `4`, `RAW_UNCALIBRATED`, `live=False` |
| Singapore LOW TIGGE 00Z DJF | `cal=True`, level `2`, `PRIMARY_EXACT`, `live=True`, `n_eff=76` |
| NYC LOW TIGGE 00Z SON | `cal=False`, level `4`, `RAW_UNCALIBRATED`, `live=False` |

## Code Changes

- `backfill_low_contract_window_evidence.py`
  - added `--cycle` filter and cycle split stats;
  - preserves old rows and only inserts recovery data_version rows.
- `rebuild_calibration_pairs_v2.py`
  - added `--cycle`, `--source-id`, and `--horizon-profile` filters;
  - scopes fetch, dry-run count, delete, and live write path to the same cohort;
  - keeps legacy schema compatibility for old tests/tools.
- `refit_platt_v2.py`
  - added the same stratification filters;
  - keeps old schema compatibility;
  - writes LOW `n_eff < 50` as `UNVERIFIED` shadow instead of live `VERIFIED`;
  - preserves existing `VERIFIED` rows when a low-n shadow refit exists.
- `src/calibration/manager.py`
  - modern LOW source-tagged requests only search contract-window data_versions;
  - LOW primary with `n_eff < 50` is blocked at the live read seam;
  - LOW season-pool fallback is blocked until a contract-bin-preserving fallback
    proof exists.

## Verification

Passed:

```bash
python3 -m py_compile \
  scripts/backfill_low_contract_window_evidence.py \
  scripts/rebuild_calibration_pairs_v2.py \
  scripts/refit_platt_v2.py \
  src/calibration/manager.py

/Users/leofitz/miniconda3/bin/python -m pytest \
  tests/test_calibration_manager_low_fallback_regression.py \
  tests/test_low_contract_window_backfill.py \
  tests/test_phase5_gate_d_low_purity.py \
  tests/test_phase5b_low_historical_lane.py \
  tests/test_ensemble_signal.py \
  tests/test_verify_truth_surfaces_phase2_gate.py \
  tests/test_rebuild_live_sentinel.py \
  tests/test_phase7a_metric_cutover.py::TestR_BH_DeleteSliceMetricScoped \
  -q
```

Result: `112 passed, 2 skipped`.

Production dry-run repeatability check:

```bash
/Users/leofitz/miniconda3/bin/python scripts/rebuild_calibration_pairs_v2.py \
  --dry-run \
  --temperature-metric low \
  --data-version tigge_mn2t6_local_calendar_day_min_contract_window_v2 \
  --cycle 12 \
  --source-id tigge_mars \
  --horizon-profile full \
  --db /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \
  --n-mc 100
```

Result: `374` snapshots scanned, `36,478` estimated pairs, `0` contract,
observation, unit, or settlement rejects.

## Known Non-Blocking Friction

`tests/test_phase7a_metric_cutover.py::TestR_BJ_OuterSavepointAtomicity` still
expects global outer-savepoint rollback. Current `rebuild_v2` documents and
implements bounded per-city/per-metric commits, so that stale test fails if the
whole file is run. This is not from the LOW recovery changes, but it should be
resolved in a separate topology-approved cleanup because it encodes a rollback
law that no longer matches the script.

## Operational State

The data ingest launch agent was temporarily unloaded to avoid DB writer-lock
competition during the large rebuild/refit. It was restarted after verification:

- service: `gui/501/com.zeus.data-ingest`;
- state: `running`;
- pid observed after restart: `33671`.

## Promotion State

Rebuild/refit is complete for TIGGE LOW contract-window runtime data, but
promotion is still cohort-specific.

GO:

- exact-domain 00Z LOW buckets with active `VERIFIED` rows and `n_eff >= 50`.

NO-GO:

- all 12Z LOW buckets;
- LOW buckets whose primary is missing, `UNVERIFIED`, low-n, or quarantined;
- OpenData/TIGGE pooled calibration;
- LOW fallback Platt live use.

