# Work Log — Paris Station Resolution (LFPG → LFPB)

Created: 2026-05-01
Last reviewed: 2026-05-01
Authority basis: today's smoke discovery + gamma-api primary evidence + cities.json _changelog precedent (Tel Aviv / Taipei 2026-04-15)
Status: STAGED, NOT COMMITTED. Operator reviews and commits.

## Verdict

**Canonical Paris station: LFPB (Paris-Le Bourget Airport).**

Polymarket migrated Paris settlement from LFPG (Charles de Gaulle) to LFPB (Le Bourget) sometime around 2026-04-18 → 2026-04-19 for HIGH (per known_gaps.md OPEN P1 entry). All currently active May 2026 markets (22 child markets across the HIGH and LOW events) use LFPB. There is no current-side ambiguity: gamma is uniformly LFPB; Zeus's cities.json was lagging.

## Primary-source evidence

1. `https://gamma-api.polymarket.com/events?tag_id=103040&closed=false&limit=50` (daily-temperature tag).
   - `lowest-temperature-in-paris-on-may-1-2026`: event description and 11 child market `description` + `resolutionSource` fields all name "Paris-Le Bourget Airport Station" with URL `https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB`.
   - `highest-temperature-in-paris-on-may-1-2026`: same description, same URL, 11 child markets.
   - 22/22 active Paris markets → LFPB. 0/22 → LFPG.
   - Snapshot: `evidence/gamma_paris_active_2026-05-01.json`.

2. `https://gamma-api.polymarket.com/events?tag_id=103040&closed=true&limit=100` historical sweep:
   - 6 closed Paris HIGH events (Feb 18-23 2026) all carry LFPG in their description text.
   - Snapshot: `evidence/gamma_paris_closed_2026-05-01.json`.
   - Confirms the migration is real and unidirectional (old=LFPG, new=LFPB) and matches the 2026-04-18→19 boundary in known_gaps.md.

3. `config/cities.json` (pre-edit): `wu_station=LFPG`, `settlement_source=…/paris/LFPG`, `lat=49.0097`, `lon=2.5479`. Confirmed mismatch.

4. Today's smoke log: `station 'LFPB' != configured 'LFPG' reason=MISMATCH` rejected 6 Paris markets. Sources field already cited the LFPB Wunderground URL. The market_scanner code was correctly rejecting; only cities.json was stale.

5. `state/zeus-world.db` query results:
   - `observations` Paris: 839 VERIFIED rows + 1 QUARANTINED row, all `station_id='LFPG'`, `source='wu_icao_history'`, dates 2024-01-01..2026-04-19.
   - `settlements` Paris: 56 VERIFIED rows (writer p_e_reconstruction_2026-04-23, all derived from `obs_id=80442` which is an LFPG observation) + 5 QUARANTINED rows (Apr 23-27, the upstream-blocked LOW slice).
   - `platt_models_v2` Paris cluster: 8 VERIFIED rows (4 seasons × HIGH/LOW), all derived from LFPG-spine pairs.

6. Predecessor pattern: `cities.json` already records two prior Polymarket station migrations (Tel Aviv `LLBG` Wunderground→NOAA, Taipei `46692`→`RCSS`). Both responded by updating cities.json + adding `_changelog` entry. Same shape applied here.

## Files changed (staged, not committed)

1. `config/cities.json`:
   - Paris entry: `wu_station LFPG → LFPB`; `airport_name → Paris-Le Bourget Airport`; `settlement_source → …/bonneuil-en-france/LFPB`; `lat/lon → 48.969398 / 2.44139`; `wu_pws → null`; `meteostat_station → null`.
   - `_changelog`: prepended a 2026-05-01 entry mirroring Tel Aviv / Taipei format.

2. `architecture/paris_station_resolution_2026-05-01.yaml` (NEW):
   - Mirrors `preflight_overrides_2026-04-28.yaml` shape.
   - Records the canonical decision, primary-source citations, legacy-data disposition recommendation (downgrade 839 obs + 56 settlements + 8 platt rows from VERIFIED → QUARANTINED with reason `paris_lfpg_legacy_pre_lfpb_migration`), and the 7-step apply checklist.
   - `apply_status: PLANNED` — runtime apply (DB UPDATE + backfill + refit) is operator-gated.

3. `src/data/meteostat_bulk_client.py:94`:
   - Removed the `"LFPG": "07157"` (Paris CDG) WMO mapping that was the wrong-station fallback for Paris.
   - Replaced with a comment block explaining the deletion. Result: tier_resolver's meteostat_bulk fallback for Paris is now disabled until a verified LFPB WMO id is added — which is the safe behavior (was previously routing Paris fallback to a different physical station).

4. `scripts/oracle_snapshot_listener.py:71`:
   - Static map updated `"Paris": ("LFPG", "FR") → ("LFPB", "FR")`.
   - This module reads only `config/cities.json` and writes only oracle-shadow snapshots; it does not mutate zeus-world.db.

5. `tests/test_market_scanner_provenance.py`:
   - Added an autouse class-level fixture `_pre_migration_paris` to `TestSourceContractGate` that swaps live Paris (now LFPB) for its synthetic pre-migration LFPG fixture inside `runtime_cities()`. This keeps the drift-detection / mismatch-alert / auto-conversion-planner regression tests valid independently of cities.json state.
   - Two `--config-path` apply-path tests (`test_auto_convert_execute_apply_writes_evidence_and_releases_quarantine`, `test_auto_convert_execute_apply_rolls_back_config_and_source_fact_on_failure`) now write a synthetic pre-migration cities.json (LFPG) into tmp instead of copying live cities.json, so the auto-converter's `wu_station == LFPG` pre-condition check still passes for the test fixture.
   - All 58 tests in the file pass.

## What I did NOT do (and why)

- **Did not run a DB UPDATE** to downgrade the 839 LFPG observations / 56 LFPG settlements / 8 LFPG-derived Platt rows from VERIFIED → QUARANTINED. Per task instructions ("DO NOT COMMIT. Stage your changes; report so the operator reviews and commits") I treated DB mutation as commit-grade work. The recommended row predicates are in `architecture/paris_station_resolution_2026-05-01.yaml` and the operator can apply them via a deterministic UPDATE script after taking a snapshot to `state/zeus-world.db.pre-paris-lfpb-migration-2026-05-01`.

- **Did not run `scripts/backfill_wu_daily_all.py`** for Paris LFPB. The script reads station mapping from cities.json dynamically (verified at lines 123-138), so it will pick up LFPB automatically once cities.json change commits. The 90-day backfill is a network-bound side-effecting operation; better that the operator triggers it explicitly post-commit.

- **Did not run `scripts/rebuild_calibration_pairs_v2.py` / `scripts/refit_platt_v2.py`** for the Paris cluster. Same reasoning — depends on the LFPB obs landing first, which depends on the backfill, which depends on the commit.

- **Did not touch HK** — parent-orchestrated downstream step (per task constraint).

- **Did not touch `src/ingest_main.py`** — parallel agent B (per task constraint).

- **Did not touch `src/data/source_health_probe.py`** — parent fixed it (per task constraint).

- **Did not touch any docs/archives/ packets carrying LFPG references** — those are historical evidence (P-E reconstruction plan, deep-map snapshots) that record what happened. Mutating them would erase audit history.

## Verification

- `python3 -c "from src.config import load_cities; ..."` confirms Paris loads with `wu_station=LFPB`, `settlement_source=…/LFPB`, `lat=48.969398`, `lon=2.44139`, `cluster=Paris`.
- `python -m pytest tests/test_market_scanner_provenance.py` — 58/58 passed.
- `python -m pytest tests/test_calibration_bins_canonical.py tests/test_observation_atom.py tests/test_digest_profile_matching.py` — 211/211 passed.
- Final `grep LFPG\|LFPB src/ scripts/ config/`: every remaining LFPG mention is intentional context (auto-convert seed, backfill comment about avoiding stale LFPG, JSON `_changelog` "old" values). No stale hardcodes remain.

## Operator next steps (post-commit)

Per `architecture/paris_station_resolution_2026-05-01.yaml::apply_steps_required_post_decision`:

1. Snapshot `state/zeus-world.db` to `…pre-paris-lfpb-migration-2026-05-01`.
2. Apply the QUARANTINE downgrade UPDATE per row predicates in the YAML.
3. Trigger `scripts/backfill_wu_daily_all.py --start today-90 --end today-2 --cities Paris` (or equivalent invocation per the script's CLI).
4. Confirm new rows write `station_id='LFPB'`, `source='wu_icao_history'`, `authority='VERIFIED'`.
5. Run `scripts/rebuild_calibration_pairs_v2.py` then `scripts/refit_platt_v2.py` for Paris cluster (HIGH+LOW × DJF/MAM/JJA/SON).
6. Re-run smoke / source-contract probe; confirm Paris MISMATCH count = 0.
7. Mark `architecture/paris_station_resolution_2026-05-01.yaml::apply_status` from PLANNED → APPLIED and close the `known_gaps.md` OPEN P1 Paris entry.
