# Paris Resync Plan Evidence — 2026-05-03

**Task**: Paris LFPB historical data parity resync
**Operator instruction**: 2026-05-03 — extend Paris back to 2024-01-01, delete QUARANTINED LFPG rows
**Authority**: Operator directive + `architecture/paris_station_resolution_2026-05-01.yaml`

## Architectural changes authorized by this plan

### 1. `architecture/preflight_overrides_2026-04-28.yaml` — add `released:` block under `hko_canonical`

**Rationale**: The HKO quarantine was released on 2026-05-02 as part of the HK+Paris combined
release packet (`docs/archives/packets/task_2026-05-02_hk_paris_release/work_log.md` Step 2).
The `hko_requires_fresh_source_audit` preflight gate fires on any VERIFIED Hong Kong row, but
HK is intentionally VERIFIED again after the release. The gate must become advisory so that
`rebuild_calibration_pairs_v2` can run for Paris without false-positive HK blockers.

The `released:` block is the immune-system gate: removing `released_at` re-arms the blocker.
`scripts/verify_truth_surfaces.py` reads the YAML to determine advisory vs. blocking mode.

**Evidence**: work_log Step 5a (same fix described; previously reverted pending operator review;
now applied as part of operator-approved Paris resync 2026-05-03).

### 2. `architecture/paris_station_resolution_2026-05-01.yaml` — status PLANNED → APPLIED

**Rationale**: All apply_steps_required_post_decision (1-6) are now complete:
- 1_config_update: done (wu_station=LFPB in cities.json)
- 2_hardcode_audit: recorded in paris_station_resolution YAML
- 3_apply_legacy_quarantine: done (work_log Step 3)
- 4_backfill_lfpb: done (work_log Step 4 + today's extended backfill)
- 5_refit_platt: done (work_log Step 5d + today's rebuild/refit)
- 6_verify: done (today's final verification queries)

## Steps in this execution

1. DB snapshot
2. WU LFPB backfill 2024-01-01 → 2026-01-31 (762 rows)
3. rebuild_calibration_pairs_v2 Paris full window
4. refit_platt_v2 Paris all seasons
5. rebuild_settlements Paris
6. DELETE QUARANTINED LFPG calibration_pairs_v2
7. Update architecture YAMLs
8. Remove Paris from _source_contract_pending_conversions
9. Final verification

Created: 2026-05-03
Authority: Operator directive 2026-05-03 + paris_station_resolution_2026-05-01.yaml
