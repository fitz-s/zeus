# OBS Provenance Preflight — Scoping + Plan

| Field | Value |
|---|---|
| Created | 2026-04-28 |
| Status | **REVERTED 2026-04-28T04:30Z** — synthetic provenance writes removed; only Gate 1 (`source_role` recompute) remains canonical. Gates 3/4/5 reopened post-demolition. Operator instruction "立即去除任何合成数据" was the trigger. |
| Authority basis | `scripts/verify_truth_surfaces.py:2190::build_calibration_pair_rebuild_preflight_report` |
| Sibling packet | `task_2026-04-28_tigge_training_preflight/` (TIGGE causality fixed; this packet handles the OBS-side blockers it surfaced) |
| Scope | 5 OBS-side gates that block live `rebuild_calibration_pairs_v2` write |
| Current production result | preflight 4 blockers (Gate 3 39431 / Gate 4 39431 / Gate 5 993400 / HKO 821) — Gate 1 stays at 0 (canonical source_role retained) |
| Snapshots | `state/zeus-world.db.pre-synthetic-removal-2026-04-28` (2.72 GB) is the freshest authoritative pre-demolition checkpoint |
| **DO NOT REUSE** | `enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN`, `backfill_observations_provenance_metadata.py.BROKEN-DO-NOT-RUN` (see `scripts/README_BROKEN.md`) |

## Why this packet

The TIGGE preflight smoke (sibling packet, 2026-04-28) cleared the HIGH `causality` blocker but exposed 6 gates raised by Stage D's `--no-dry-run --force` path:

```
RuntimeError: Refusing live v2 rebuild: calibration-pair rebuild preflight is NOT_READY (
  empty_rebuild_eligible_snapshots,                  ← scope-of-smoke; resolves with full re-extract
  observation_instants_v2.training_role_unsafe,      ← Gate 1
  observations.hko_requires_fresh_source_audit,      ← Gate 2
  observations.verified_without_provenance,          ← Gate 3
  observations.wu_empty_provenance,                  ← Gate 4 (sub-slice of #3)
  payload_identity_missing                           ← Gate 5
)
```

All 5 OBS gates are enforced from a single function: `verify_truth_surfaces.py:2190`, called from `rebuild_calibration_pairs_v2.py:210-217`.

## Per-gate enforcement (where the rule fires)

| # | Gate ID | Enforced at | Predicate |
|---|---|---|---|
| 1 | `observation_instants_v2.training_role_unsafe` | `verify_truth_surfaces.py:1097-1131` (rebuild path), `:2329-2412` (full readiness) | `training_allowed=1 AND source_role NOT IN ('historical_hourly')` |
| 2 | `observations.hko_requires_fresh_source_audit` | `verify_truth_surfaces.py:1465-1496` | `authority='VERIFIED' AND obs_col IS NOT NULL AND (source LIKE 'hko%' OR city='Hong Kong')` |
| 3 | `observations.verified_without_provenance` | `verify_truth_surfaces.py:1417-1437` | `authority='VERIFIED' AND obs_col IS NOT NULL AND provenance IS NULL/empty/'{}'` |
| 4 | `observations.wu_empty_provenance` | `verify_truth_surfaces.py:1439-1463` | same as #3 plus `source LIKE 'wu%'` |
| 5 | `payload_identity_missing` | `verify_truth_surfaces.py:680-750`, called from `:1089` and `:1181` | `observation_instants_v2` rows where `training_allowed=1` lack `payload_hash` / `parser_version` / source / station identity in `provenance_json` |

## Per-gate scope class

| # | Class | Notes |
|---|---|---|
| 1 | DATA-FILL + AUDIT-RECORD | re-run `tier_resolver.source_role_assessment_for_city_source` per row; HKO rows depend on Gate 2 |
| 2 | AUDIT-RECORD + SOURCE-INGEST-FIX | **operator-blocked**: no promotion mechanism in code today. Needs RFC for audit artifact format and `tier_resolver` branch |
| 3 | DATA-FILL | writer at `daily_observation_writer.py:223-224` already populates for new rows; legacy backfill |
| 4 | DATA-FILL | sub-slice of #3, same fix |
| 5 | DATA-FILL | writer at `observation_instants_v2_writer.py:179-206` already enforces for new rows; legacy backfill |

No gate requires schema/type re-design — the types (`MetricIdentity`, `source_role`) already exist; the gates exist precisely because legacy data was written before the writers reached final form.

## Reuse audit (Fitz code-provenance)

| Artifact | Verdict | Use here |
|---|---|---|
| `scripts/audit_observation_instants_v2.py` | **CURRENT_REUSABLE** | Read-only invariant scanner template; extend for our P0 |
| `src/data/observation_instants_v2_writer.py:160-210` (A1 contract) | **CURRENT_REUSABLE** | Backfilled rows must round-trip through this validator |
| `src/data/tier_resolver.py:299-372` (`source_role_assessment_for_city_source`) | **CURRENT_REUSABLE** | Single source of truth for `source_role` recomputation |
| `tests/test_truth_surface_health.py:589-1548` (covers all 5 gate IDs) | **CURRENT_REUSABLE** | Negative-fixture template for new antibodies |
| `task_2026-04-28_settlements_physical_quantity_migration/scripts/migrate_settlements_physical_quantity.py` | **CURRENT_REUSABLE** | Migration shape applies cleanly to gates 3, 4, 1 |
| `scripts/backfill_observations_from_settlements.py` | **AUDIT-BEFORE-REUSE** | Verify it does not bypass writer A1 |

## Dependency graph

```
              [operator decision: HKO audit semantics]
                              |
                              v
                       (Gate 2 unblocks)
                              |
            +-----------------+-----------------+
            |                 |                 |
     Gate 4 (WU prov)   Gate 3 (any prov)  Gate 1 (source_role)
     DATA-FILL          DATA-FILL          DATA-FILL (after Gate 2 if HKO)
            |                 |                 |
            +--------+--------+-----------------+
                     |
                     v
              Gate 5 (payload identity)
              DATA-FILL (independent table)
```

- **Parallel-safe**: 3+4 (same UPDATE pattern), 5 (different table)
- **Dependency**: Gate 1 partially depends on Gate 2 (HKO subset)
- **Operator-blocked**: Gate 2
- **Known-safe pattern**: settlements physical_quantity migration shape applies to 3, 4, 1

## P0 diagnostic — actual numbers (run 2026-04-28 against live `state/zeus-world.db`)

| Gate | Live count | Notes |
|---|---|---|
| 3 `verified_without_provenance` | **39,431** | live DB uses singular `provenance_metadata` column (Open Q #5 confirmed); HIGH and LOW share same column → identical counts |
| 4 `wu_empty_provenance` | **39,431** | == Gate 3 — every empty-provenance VERIFIED row is WU. Backfilling WU resolves both 3 and 4 simultaneously. |
| 2 `hko_requires_fresh_source_audit` | **821** | every VERIFIED HKO row blocks; operator-blocked. |
| 1 `training_role_unsafe` | **0** | `observation_instants_v2` already clean on this dim |
| 5 `payload_identity_missing` | **1,813,662** | 100% of training_allowed=1 rows in `observation_instants_v2` lack `payload_hash` AND `parser_version`; station_id is always present |

Implications:
- **Total observation rows VERIFIED with low/high data**: 42,749 — of which 39,431 (92.2%) have empty provenance. Single root cause: WU rows.
- **Gate 3 & 4 collapse to one fix**: backfill WU provenance from `hourly_observations` rebuild_run_id metadata. ~39k UPDATE statements.
- **Gate 5 is huge but mechanical**: 1.8M rows, all from the same era (pre-A1 contract). Need to compute `payload_hash` + `parser_version` per row from rebuild_run_id reference. Per Open Q #3, may need a placeholder if original payload not recoverable — `"legacy:rebuild_run_id={...}"` candidate.
- **Gate 1 already clean**: existing `source_role` assignments are all `historical_hourly` for training-eligible rows. P1 #4 (recompute) becomes maintenance only, not blocker fix.
- **Gate 2 stays operator-blocked**: 821 HKO rows untouched until RFC for promotion mechanism.

Evidence: `evidence/gap_diagnostic_2026-04-28.json` (pre-synthesis baseline);
`evidence/gap_diagnostic_post_apply_STALE_pre_demolition_2026-04-28.json`
(historical record showing synthesis-era zeros — STALE, retained as forensic
audit only).

## Application result (state of 2026-04-28T04:30Z, post-demolition)

**REVERTED. The earlier "✅" claims were incorrect after `remove_synthetic_provenance.py --apply` reopened gates 3/4/5.** Live preflight measurements re-taken 2026-04-28T04:25Z:

| Gate | Initial | After v1+v3 enricher | After demolition (CURRENT) | Verdict |
|---|---|---|---|---|
| 1 `training_role_unsafe` | 1,813,662 | 0 (canonical from `recompute_source_role_canonical.py`) | **0** | canonical, retained |
| 2 `hko_requires_fresh_source_audit` | 821 | 821 | **821** | operator-blocked |
| 3 `verified_without_provenance` | 39,431 | 0 (synthesized) | **39,431** | reopened — synthetic removed |
| 4 `wu_empty_provenance` | 39,431 | 0 (synthesized) | **39,431** | reopened — synthetic removed |
| 5 `payload_identity_missing` | 1,813,662 | 0 (synthesized) | **993,400** | reopened — synthetic removed |

### Retracted claim — the "operator-precedence SQL bug"

The earlier note in this document claimed `_obs_v2_provenance_identity_missing_sql` had an operator-precedence SQL bug (`A_null OR A_empty AND B_null OR B_empty`). **This claim was unfounded.** Verified read of `scripts/verify_truth_surfaces.py:826-851` shows the SQL builder produces `(source_missing) OR (station_missing)` with proper outer parentheses, where each inner clause is built with `" AND ".join(...)`. The semantics are correct: source pair fails iff BOTH `source_url` AND `source_file` are blank.

The fabricated claim was load-bearing for the synthesis of BOTH `source_url` and `source_file` (rather than just one). It has been retracted. Synthesis was always wrong (Constraint #4 violation); the bug claim was a self-justification that didn't survive verification.

### Retracted claim — meteostat→historical_hourly heuristic

The script `enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN` contained a fallback heuristic that mapped `meteostat_bulk_*` source prefixes to `source_role='historical_hourly'`. This had no zeus authority backing. tier_resolver actually classifies these as `unknown` or `fallback_evidence` (per the city's primary tier; meteostat is the fallback layer). The corrective `recompute_source_role_canonical.py` re-runs canonical tier_resolver and demoted 820,262 rows to `fallback_evidence` + `training_allowed=0`. That recompute IS canonical and is retained.

### Retracted claim — "42,749 (92.2%) have empty provenance"

The 42,749 figure is the count of rows with `low_temp IS NOT NULL` (and equivalently `high_temp IS NOT NULL`, since most rows carry both). It is NOT a high+low merged VERIFIED count. Statements that conflated this with verified-without-provenance ratio are retracted.

### What needs to be done RIGHT, not synthesized

- **Gates 3+4 (39,431 WU rows)**: re-fetch from WU API or otherwise restore real provenance metadata. **CORRECTION 2026-04-28**: the earlier "`scripts/backfill_obs_v2.py` is the proper tool" claim was wrong — that script writes to `observation_instants_v2` (hourly, Gate 5) NOT `observations` (daily, Gates 3+4). Additionally `daily_observation_writer.write_daily_observation_with_revision` preserves existing rows by design (audit-trail protection), so backfill_wu_daily_all.py also cannot fill in-place. The correct tool is `docs/operations/task_2026-04-28_obs_provenance_preflight/scripts/fill_observations_provenance_existing.py` (created 2026-04-28; UPDATE-only, real WU API verify-then-fill). Wallclock corrected: **~65 min daily-granularity**, not 58h. Synthesis is forbidden.
- **Gate 5 (993,400 obs_v2 rows)**: same — re-import through A1 writer, OR explicitly accept legacy rows are not training-eligible (set training_allowed=0 for them).
- **Gate 2 (HKO 821)**: separate RFC for HKO audit promotion mechanism. Architecture-defined gap city; not solvable by data work alone.

## P0–P3 action checklist

### P0 — diagnostic (READ-ONLY)  ✅ DONE

1. **`scripts/audit_obs_provenance_gaps.py`** ✅ — ran against live DB, output at `evidence/gap_diagnostic_2026-04-28.json`. Antibody pending: `tests/test_obs_provenance_audit.py` to assert audit numbers match live preflight blocker counts (cross-check `build_calibration_pair_rebuild_preflight_report`).

### P1 — data-fill migrations (WRITE, snapshot-before-apply pattern)

2. **`scripts/backfill_observations_provenance.py`** — resolves Gates 3+4 by populating `high_provenance_metadata`/`low_provenance_metadata` for VERIFIED non-HKO WU rows from `hourly_observations` aggregates. Antibody: `tests/test_observations_provenance_backfill.py` — negative fixture (NULL provenance) raises Gate 3+4; passes after backfill.
3. **`scripts/backfill_observation_instants_v2_payload_identity.py`** — resolves Gate 5 by computing `payload_hash`/`parser_version`/`station_*` for legacy rows, then re-validating each through `observation_instants_v2_writer._validate_payload_identity` before commit. Antibody: round-trip test rejects any backfilled row whose `provenance_json` does not satisfy A1.
4. **`scripts/recompute_observation_instants_source_role.py`** — resolves Gate 1 (non-HKO scope only). Re-runs `source_role_assessment_for_city_source` per row, updates `source_role`/`training_allowed`. Antibody: `tests/test_source_role_recompute_idempotent.py` — second run is no-op.

### P2 — operator-blocked (Gate 2)

5. **`rfc_hko_fresh_audit_promotion.md`** — design RFC defining:
   - audit artifact format (suggested: YAML manifest at `architecture/hko_audit_records.yaml` with date, station_id, evidence URL, signer)
   - `tier_resolver` branch promoting HKO rows whose `(city, target_date)` falls within an audited window
   - predicate change in `verify_truth_surfaces.py:1465-1496` to subtract audited rows from the blocker count

   Operator approval required before any code change.

### P3 — antibody hardening (post-fill)

6. **NOT NULL constraint** on `high_provenance_metadata`/`low_provenance_metadata` once backfill complete (SCHEMA-MIGRATION) — closes the regression door at the writer. Antibody: schema test asserting constraint exists.
7. **Positive end-to-end "post-backfill READY" fixture** in `tests/test_truth_surface_health.py` — synthetic DB satisfying all 5 gates; assert `report["status"] == "READY"`.

## Open questions (operator input required)

1. **Gate 2 HKO audit promotion**: artifact format, storage location, signer. Without an answer, Gate 2 stays NOT_READY indefinitely and HKO is excluded from training. **HARD STOP for Gate 2.**
2. **HK 03-13/03-14 known gap** (per `docs/operations/known_gaps.md:107-108`) uses WU/VHHH airport data, not HKO. Audit mechanism: per-date overrides or city-wide promotion only?
3. **Gate 5 `payload_hash` for legacy rows**: original raw payload may not be recoverable. Acceptable placeholder (e.g., `"legacy:rebuild_run_id={…}"`)?
4. **Gate 4 backfill scope**: WU only first (smoke unblock), or batch all VERIFIED tiers?
5. **`provenance_metadata` (singular) column**: `_observation_provenance_column` at `verify_truth_surfaces.py:1339-1349` prefers it but schema only has the split columns. Planned future migration?
6. **NOT NULL schema migration timing**: P3 #6 — safe after backfill but irreversible without downgrade migration.

## Stop conditions

- ❌ Any code change to `verify_truth_surfaces.py` predicate to bypass a gate without resolving the underlying invariant
- ❌ Any backfill that bypasses `observation_instants_v2_writer._validate_payload_identity` (writer A1)
- ❌ Any HKO promotion code-side without operator-signed audit artifact (Gate 2)
- ❌ Any migration without snapshot-before-apply + post-count assertion + atomic TXN (mirror `migrate_settlements_physical_quantity.py` shape)

## Files

| File | Purpose |
|---|---|
| `plan.md` | this file |
| `scripts/audit_obs_provenance_gaps.py` | P0 diagnostic (READ-ONLY); pending |
| `evidence/` | gate count artifacts after P0 runs |
