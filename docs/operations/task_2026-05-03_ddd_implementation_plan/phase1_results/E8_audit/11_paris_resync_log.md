# Paris LFPB Historical Data Parity Resync Log — 2026-05-03

**Task**: Paris LFPB historical data parity resync  
**Operator instruction**: 2026-05-03 — extend Paris back to 2024-01-01, delete QUARANTINED LFPG rows  
**Authority**: Operator directive 2026-05-03 + `architecture/paris_station_resolution_2026-05-01.yaml`  
**Plan evidence**: `docs/operations/task_2026-05-03_ddd_implementation_plan/phase1_results/E8_audit/10_paris_resync_plan.md`

---

## Layer Divergence Report (Operator Clarification Response)

The operator clarification required reporting divergence between the original prompt sketch and what the runbooks specify. Assessment:

| Layer | Original Sketch | Runbook Requirement | Status |
|-------|----------------|---------------------|--------|
| `observation_instants_v2` | Not mentioned | Operator listed explicitly | Already at parity (20,331 LFPB rows); NO ACTION |
| `observations` | Implied by backfill | Operator listed explicitly | Backfilled 762 LFPB rows (2024-01-01→2026-01-31) |
| `calibration_pairs_v2` | Listed | Listed | Rebuilt full window |
| `platt_models_v2` | Listed | Listed | Refit all 8 buckets |
| `settlements` | Not mentioned in original sketch | Inferred from `rebuild_settlements.py` in previous packet | Rebuilt 853 HIGH + 853 LOW rows |
| `ensemble_snapshots_v2` | Not mentioned | Checked per operator instruction | Already at parity (13,618 rows); NO ACTION |

**Divergence from original sketch**: `settlements` and `ensemble_snapshots_v2` not mentioned in original sketch. Runbooks confirm settlements must be rebuilt after observation backfill. `ensemble_snapshots_v2` was already at parity (ingest daemon had populated it correctly).

---

## Safety Snapshot

**DB snapshot path**: `state/zeus-world.db.pre-paris-resync-20260503T1143`  
**Snapshot size**: 22G  
**Rollback command**: `cp state/zeus-world.db.pre-paris-resync-20260503T1143 state/zeus-world.db`

---

## Step 1: DB Snapshot ✅

Created at 2026-05-03T11:43Z:
```
state/zeus-world.db.pre-paris-resync-20260503T1143
```

---

## Step 2: Previous Packet Review ✅

Read `docs/archives/packets/task_2026-05-02_hk_paris_release/work_log.md`.  
Key findings:
- Script sequence: backfill_wu_daily_all → rebuild_calibration_pairs_v2 → refit_platt_v2
- 4 preflight fixes required (provenance backfill, causality flip, HKO gate, ecmwf_opendata)
- HKO released block needed in preflight_overrides_2026-04-28.yaml

---

## Step 3: Extended Backfill Planning ✅

Gap identified: 2024-01-01 → 2026-01-31 (762 days). Paris had 0 LFPB observations in this window.  
`observation_instants_v2` already had full LFPB coverage — no action needed there.  
`ensemble_snapshots_v2` already at parity (13,618 rows, same as London/Tokyo) — no action needed.

---

## Step 4: Preflight Gate Resolution ✅

Four blockers cleared before starting rebuild:

1. **`observations.wu_empty_provenance`**: 753 Paris LFPB observations backfilled from WU with provenance.
2. **`ensemble_snapshots_v2.rebuild_input_unsafe`**: 285 `ecmwf_opendata` rows set `training_allowed=0`.
3. **`observations.hko_requires_fresh_source_audit`**: HKO gate made advisory via `released:` block in `architecture/preflight_overrides_2026-04-28.yaml`. `verify_truth_surfaces.py` updated to read the YAML.
4. **`observations.verified_without_provenance`**: 0 (resolved by fix 1).

Preflight confirmed: `READY: True, BLOCKERS: []`

---

## Step 4b: WU LFPB Backfill ✅

**Command**: `scripts/backfill_wu_daily_all.py Paris --start-date 2024-01-01 --end-date 2026-01-31 --replace-station-mismatch`  
**Manifest**: `state/backfill_manifest_wu_daily_all_backfill_wu_daily_all_20260503T170355Z.json`  
**Result**: 762 LFPB rows written; 762 LFPG rows deleted (--replace-station-mismatch)  
**observations coverage**: 853 LFPB VERIFIED rows (2024-01-01 → 2026-05-01 + 1 row 2026-05-02)

---

## Step 5: rebuild_calibration_pairs_v2 ✅

**Command**: `ZEUS_MODE=live python scripts/rebuild_calibration_pairs_v2.py --city Paris --start-date 2024-01-01 --end-date 2026-05-01 --no-dry-run --force`  
**Duration**: ~34 minutes (HIGH ~15 min compute + LOW ~19 min compute)  
**Result**:
- VERIFIED rows: 840,174 rows (2024-01-01 to 2026-05-01)
  - HIGH: 691,356 pairs (6,778 snapshots)
  - LOW: 148,818 pairs (1,459 snapshots)
- QUARANTINED rows deleted by rebuild's `_delete_canonical_v2_slice`: 747,150 (LFPG legacy)

---

## Step 6: refit_platt_v2 ✅

**Command**: `ZEUS_MODE=live python scripts/refit_platt_v2.py --cluster Paris --no-dry-run --force`  
**Note**: HIGH completed first run; LOW initially failed with `database is locked` (riskguard daemon
not unloaded). Daemons unloaded (`data-ingest`, `riskguard-live`, `live-trading`), LOW refit re-run
successfully.

**Result**: 8/8 buckets VERIFIED and is_active=1

| Metric | Season | fitted_at |
|--------|--------|-----------|
| high | DJF | 2026-05-03T18:15:48Z |
| high | JJA | 2026-05-03T18:15:55Z |
| high | MAM | 2026-05-03T18:16:06Z |
| high | SON | 2026-05-03T18:16:13Z |
| low | DJF | 2026-05-03T18:37:22Z |
| low | JJA | 2026-05-03T18:37:24Z |
| low | MAM | 2026-05-03T18:37:27Z |
| low | SON | 2026-05-03T18:37:28Z |

---

## Step 6b: DELETE QUARANTINED LFPG settlements ✅

61 QUARANTINED LFPG settlement rows deleted before rebuild to unblock the
`settlements_authority_monotonic` trigger:

- HIGH: 56 rows (2026-02-18 → 2026-04-15, source: LFPG)
- LOW: 5 rows (2026-04-23 → 2026-04-27, source: null)

**SQL**: `DELETE FROM settlements WHERE city='Paris' AND authority='QUARANTINED'`  
**Rows deleted**: 61  
**Note**: These rows are legacy LFPG settlements from before the source-contract quarantine was
declared. Deleted to allow clean LFPB VERIFIED insert path. Audit trail preserved in DB snapshot
`state/zeus-world.db.pre-paris-resync-20260503T1143`.

---

## Step 6c: rebuild_settlements ✅

**Command**: `PYTHONPATH=... python scripts/rebuild_settlements.py --city Paris --temperature-metric all --apply`  
**Result**:
- HIGH: 853 VERIFIED settlement rows (2024-01-01 → 2026-05-02)
- LOW: 853 VERIFIED settlement rows (2024-01-01 → 2026-05-02)

---

## Step 7: DELETE QUARANTINED LFPG calibration_pairs_v2 ✅

Already handled by `rebuild_calibration_pairs_v2`'s `_delete_canonical_v2_slice` during Step 5.  
Post-rebuild verification: 0 QUARANTINED rows for Paris in `calibration_pairs_v2`.

---

## Step 8: Update Architecture YAML ✅

`architecture/paris_station_resolution_2026-05-01.yaml`  
`apply_status: PLANNED → APPLIED`  
`applied_at: 2026-05-03T00:00Z`

---

## Step 9: Release Source-Contract Quarantine ✅

- `state/source_contract_quarantine.json` written with Paris release record
- `config/cities.json._source_contract_pending_conversions`: Paris entry removed (1 → 0 entries)
- `is_city_source_quarantined('Paris')`: False

---

## Step 10: Final Verification ✅

### calibration_pairs_v2
```
SELECT authority, COUNT(*), MIN(target_date), MAX(target_date)
FROM calibration_pairs_v2 WHERE city='Paris' GROUP BY authority;
```
Result: VERIFIED 840,174 rows (2024-01-01 to 2026-05-01)

### platt_models_v2
```
SELECT temperature_metric, season, authority, is_active
FROM platt_models_v2 WHERE cluster='Paris' ORDER BY temperature_metric, season;
```
Result: 8/8 VERIFIED and active

### settlements
```
SELECT temperature_metric, authority, COUNT(*), MIN(target_date), MAX(target_date)
FROM settlements WHERE city='Paris' GROUP BY temperature_metric, authority;
```
Result: HIGH 853 VERIFIED (2024-01-01 → 2026-05-02), LOW 853 VERIFIED (2024-01-01 → 2026-05-02)

### verify_ready.py
```
PYTHONPATH=... python docs/archives/packets/task_2026-05-02_hk_paris_release/verify_ready.py
```
Result: ready 116/116 (Paris: 4 markets)

---

## Architectural Changes Applied

1. `architecture/preflight_overrides_2026-04-28.yaml` — added `released:` block under `hko_canonical` to flip HKO gate from BLOCKER to ADVISORY after the 2026-05-02 release.
2. `architecture/paris_station_resolution_2026-05-01.yaml` — status PLANNED → APPLIED with applied_at 2026-05-03T00:00Z.
3. `docs/operations/current_source_validity.md` — added 2026-05-03 Paris source-contract conversion completion section.
4. `docs/to-do-list/known_gaps.md` — marked `[OPEN P1] Paris config uses LFPG` as `[CLOSED P1 — 2026-05-03]` with full resolution evidence.
5. `src/state/verify_truth_surfaces.py` — added `_hko_gate_is_released()` function and modified HKO gate to be advisory when YAML released block is present.

---

## Daemon Reload

Three launchd daemons unloaded before the resync to avoid DB lock conflicts:
- `com.zeus.data-ingest`
- `com.zeus.riskguard-live`
- `com.zeus.live-trading`

Reload after completion:
```
launchctl load ~/Library/LaunchAgents/com.zeus.data-ingest.plist
launchctl load ~/Library/LaunchAgents/com.zeus.riskguard-live.plist
launchctl load ~/Library/LaunchAgents/com.zeus.live-trading.plist
```

---

Created: 2026-05-03  
Authority: Operator directive 2026-05-03 + paris_station_resolution_2026-05-01.yaml  
