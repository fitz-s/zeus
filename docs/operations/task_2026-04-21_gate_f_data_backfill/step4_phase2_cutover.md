# Gate F Data Backfill — Step 4: Phase 2 Atomic Cutover Plan

Created: 2026-04-23
Last reused/audited: 2026-04-23
Authority basis: `.omc/plans/observation-instants-migration-iter3.md` Phase 2 + Phase 3 (lines 97–108); step3_phase1_fleet_closeout.md (Phase 1 closure); operator authorization 2026-04-23 ("走路径 A，现有数据还不完全，没有在 live 运行中").

## Scope

Execute plan v3 Phase 2 atomic cutover AND the Phase 3 ETL rebuild that must immediately follow (ETL readers must be migrated first, otherwise Phase 2 flip has zero runtime effect). Daemon is NOT live per operator at execution time, reducing risk.

## Risk posture

- **Live trading**: NOT running. Operator confirmed 2026-04-23. Independently verified via `launchctl list | grep zeus` returning exit codes (-15/0) consistent with stopped services.
- **Data completeness**: Operator acknowledged "现有数据还不完全" — 1,812,495 rows across 50 cities meets Gate 1→2 but is not a final-year-of-history dataset. HK remains accumulator-forward-only (plan v3 Option A, accepted gap). 2 tail days (2026-04-22 / 2026-04-23) are unbackfilled. Phase 2 proceeds on the premise that v2 > legacy openmeteo as signal input even if not perfect.
- **Rollback cost**: Atomic. Single `UPDATE zeus_meta SET value='v0'` reverses the flip in <1s. ETL rebuild is idempotent — re-running on legacy `observation_instants` restores the pre-cutover derived tables.

## Pre-work (must land before atomic flip)

### PW1 — Pilot leftover cleanup (1 commit)
- `DELETE FROM observation_instants_v2 WHERE data_version='v1.wu-native.pilot';` (93 rows: Chicago 46 + London 46 + Sao Paulo 1)
- Rationale: v1.wu-native full backfill supersedes pilot; pilot was a test iteration. Git history preserves the pilot commit trail.
- Verification: `SELECT COUNT(*) FROM observation_instants_v2 WHERE data_version='v1.wu-native.pilot'` == 0

### PW2 — ETL reader migration (1 commit)
Targets:
- `scripts/etl_diurnal_curves.py` (L43, L53): `FROM observation_instants` → `FROM observation_instants_current`
- `scripts/etl_hourly_observations.py` (L41, L43, L53): same

Rationale: per plan v3 L76 these reads MUST route through the VIEW for the atomic flip to propagate to derived tables. Without this migration, the flip is a no-op for `diurnal.py` / `monitor_refresh.py` because they read `diurnal_curves` / `temp_persistence`, which are ETL products — and the ETL scripts would still read the legacy `observation_instants` table regardless of `zeus_meta` state.

**Note**: `scripts/etl_temp_persistence.py` reads from `observations` (daily, separate lineage) — NO migration needed.

Provenance: update `Last reused/audited: 2026-04-23` + `Authority basis: plan v3 Phase 2 pre-work` headers on both scripts.

### PW3 — AC11 test (1 commit)
- Create `tests/test_diurnal_curves_empty_hk_handled.py`
- Test semantics: when `diurnal_curves` contains zero rows for `city='Hong Kong'`, `diurnal.get_peak_hour_context('Hong Kong', date(2026,4,23), 14)` must return `(None, 0.0, <reason>)` without raising, AND `diurnal.post_peak_confidence('Hong Kong', date(2026,4,23), 14)` must return `0.0` without raising.
- Register in `architecture/test_topology.yaml`.

### PW4 — Current-fact refresh (1 commit)
- Update `docs/operations/current_data_state.md`: v2 posture from "0 rows" to "1,812,495 rows (data_version='v1.wu-native'), 50 cities, zeus_meta='v0' pre-flip".
- Preserve `Last audited: 2026-04-23`.

## Cutover commit (PW5)

Single SQL transaction:

```sql
BEGIN;
UPDATE zeus_meta SET value='v1.wu-native' WHERE key='observation_data_version';
COMMIT;
```

Verification steps (all post-commit):
1. `SELECT value FROM zeus_meta WHERE key='observation_data_version'` == `v1.wu-native` (AC6)
2. `SELECT COUNT(*) FROM observation_instants_current` == 1,812,495 (VIEW now populated)
3. Antibody test suite green (4 existing + AC11 = 5 files)

## Phase 3 immediate rebuild (PW6)

Runs right after PW5 (same packet, same session). Plan v3 L105–108 treats this as a separate phase but the step-4 operator gate collapses it in because Phase 3 must land before any live-trading resume.

```bash
python scripts/etl_diurnal_curves.py          # rebuilds diurnal_curves from v2
python scripts/etl_hourly_observations.py     # rebuilds hourly_observations from v2
```

Verification:
- `SELECT COUNT(*) FROM diurnal_curves` > 0 and `< 10_000` (expected ~4500–5000 for 50 cities × 4 seasons × 24 hours, minus sparse cells)
- `SELECT COUNT(*) FROM hourly_observations` ≥ 1,800,000

Phase 3 Brier regression test (plan v3 AC9) is deferred: daemon not running means no active calibration path to benchmark. Record baseline for future live resume.

## Acceptance criteria (measurable)

| AC | Check | Pass condition |
|---|---|---|
| AC6 | `SELECT value FROM zeus_meta WHERE key='observation_data_version'` | `'v1.wu-native'` |
| VIEW | `SELECT COUNT(*) FROM observation_instants_current` | `1,812,495` (matches v1.wu-native rowcount) |
| PILOT | `SELECT COUNT(*) FROM observation_instants_v2 WHERE data_version='v1.wu-native.pilot'` | `0` |
| READER-DIURNAL | `grep -c "FROM observation_instants[^_]" scripts/etl_diurnal_curves.py` | `0` (only `observation_instants_current` / `observation_instants_v2`) |
| READER-HOURLY | `grep -c "FROM observation_instants[^_]" scripts/etl_hourly_observations.py` | `0` |
| AC11 | `pytest -q tests/test_diurnal_curves_empty_hk_handled.py` | green |
| ANTIBODY | `pytest -q tests/test_tier_resolver.py tests/test_obs_v2_writer.py tests/test_hk_rejects_vhhh_source.py tests/test_backfill_scripts_match_live_config.py tests/test_diurnal_curves_empty_hk_handled.py` | 68 + AC11 passed |
| REBUILD-DIURNAL | `SELECT COUNT(*) FROM diurnal_curves` | `> 0` and `< 10000` |
| REBUILD-HOURLY | `SELECT COUNT(*) FROM hourly_observations` | `≥ 1,800,000` |

## Rollback plan

Per-phase:

| Step | Rollback | Cost |
|---|---|---|
| PW1 pilot DELETE | `git revert <pw1-sha>` — but pilot data was obsolete anyway; in practice no restore | ~0 |
| PW2 ETL reader migration | `git revert <pw2-sha>` | ~0; ETL idempotent |
| PW3 AC11 test | `git revert <pw3-sha>` | ~0 |
| PW4 current_data_state refresh | `git revert <pw4-sha>` | ~0 |
| **PW5 atomic flip** | `sqlite3 state/zeus-world.db "UPDATE zeus_meta SET value='v0' WHERE key='observation_data_version';"` | <1s |
| PW6 ETL rebuild | Re-run ETL on legacy `observation_instants` (ETL uses VIEW → auto-falls-back after PW5 rollback because view returns 0 rows when key='v0') | 1–3 min |

**Full session rollback**: `git reset --hard 4e99a51` + SQL flip back to `'v0'` + re-run ETL = under 5 min total.

## Out of scope

- Phase 4 legacy deprecation (+30d post-cutover DROP of `observation_instants`) — separate packet later
- Tail backfill for 2026-04-22 / 2026-04-23 — low priority while daemon offline
- HK accumulator-forward activation — separate concern tied to `hko_hourly_accumulator` daemon status
- Brier score regression test (AC9) — requires live-trading resume to measure
- Ogimet HTML scrape fallback — plan v3 "nice-to-have" follow-up

## References

- `.omc/plans/observation-instants-migration-iter3.md` — plan v3 (Phase 2 + Phase 3 + AC6/AC9/AC11)
- `docs/operations/task_2026-04-21_gate_f_data_backfill/step3_phase1_fleet_closeout.md` — Phase 1 closure documentation
- `scripts/audit_observation_instants_v2.py` — nightly audit (pre/post-flip parity must hold)
- `docs/operations/current_data_state.md` — current-fact surface (PW4 refreshes)
