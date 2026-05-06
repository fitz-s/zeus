# Wave 17 Object-Meaning Invariance: legacy outcome_fact producer fail-closed

Status: in progress
Scope: legacy outcome_fact producer/backfill guard only; no DB mutation, no backfill execution, no data relabeling.

## Route Evidence

- Root `AGENTS.md`: read from prompt/context.
- Scoped reads: `scripts/AGENTS.md`, `src/state/AGENTS.md`, `tests/AGENTS.md`, `docs/operations/AGENTS.md`.
- Semantic boot: `python3 scripts/topology_doctor.py --task-boot-profiles` -> `topology check ok`.
- Broad read-only route:
  - Command: `python3 scripts/topology_doctor.py --navigation --task "object-meaning legacy outcome_fact producer Wave17 must not backfill or write authorityless outcome rows into live/report/learning truth" --write-intent read_only --files scripts/backfill_outcome_fact.py src/state/db.py src/execution/harvester.py tests/test_db.py docs/operations/task_2026-05-05_object_invariance_wave17/PLAN.md docs/operations/AGENTS.md`
  - Result: `navigation ok: False`; generic advisory; high-fanout files required typed intent; script manifest provenance identified `scripts/backfill_outcome_fact.py` as active `etl_writer`, `dangerous_if_run=True`.
- Script repair route:
  - Command including docs: `python3 scripts/topology_doctor.py --navigation --task "add or change script: Wave17 fail-closed legacy outcome_fact backfill producer; no default DB write, dry-run default, manifest target truth, legacy non-authority warning" --intent "add or change script" --task-class repair --write-intent edit --files scripts/backfill_outcome_fact.py architecture/script_manifest.yaml tests/test_topology_doctor.py docs/operations/task_2026-05-05_object_invariance_wave17/PLAN.md docs/operations/AGENTS.md`
  - Result: `navigation ok: False`; admitted script/manifest/test but rejected packet docs.
  - Follow-up command without docs: `python3 scripts/topology_doctor.py --navigation --task "add or change script: Wave17 fail-closed legacy outcome_fact backfill producer; no default DB write, dry-run default, manifest target truth, legacy non-authority warning" --intent "add or change script" --task-class repair --write-intent edit --files scripts/backfill_outcome_fact.py architecture/script_manifest.yaml tests/test_topology_doctor.py`
  - Result: `navigation ok: True`; admitted script/manifest/test.
- Packet route:
  - Command: `python3 scripts/topology_doctor.py --navigation --task "object-meaning settlement authority cutover Wave17 plan packet for legacy outcome_fact producer backfill fail-closed repair" --write-intent edit --files docs/operations/task_2026-05-05_object_invariance_wave17/PLAN.md docs/operations/AGENTS.md`
  - Result: `navigation ok: True`; admitted this plan and `docs/operations/AGENTS.md`.

Topology compatibility notes recorded:
- The semantic object-meaning route detected neither the dangerous script profile nor the docs packet route in one pass; typed script intent was required.
- The script profile admitted script/manifest/tests but rejected packet evidence paths, so plan evidence had to route separately through the object settlement profile.
- Manifest provenance was useful: it exposed active status, dangerous flag, and target/write mismatch before code edits.

## Phase 0 Map Delta

Relevant money/report segment:

`chronicle SETTLEMENT legacy rows -> scripts/backfill_outcome_fact.py -> outcome_fact -> diagnostic/report/replay/smoke consumers`

Authority surfaces:
- `chronicle`: historical event log, not settlement authority for current verified settlement semantics.
- `position_events_legacy`: legacy lifecycle lookup for strategy/entry timestamp.
- `outcome_fact`: legacy lifecycle projection; no settlement authority, evidence class, or learning eligibility columns.
- `architecture/script_manifest.yaml`: operator routing and danger classification for script execution.

Canonical hierarchy:

Verified settlement authority rows outrank `chronicle` and `outcome_fact`. A legacy backfill may only repair historical lifecycle projection rows behind explicit operator-approved apply guard; it must not silently manufacture settlement/report/learning truth.

## Phase 1 Boundary Selection

Selected boundary: `chronicle SETTLEMENT` -> `outcome_fact` backfill producer.

Why selected:
- It can actively create legacy outcome rows that downstream readers must treat as non-authoritative.
- It was active in manifest, dangerous if run, and code defaulted to a write when run without flags.
- Manifest said target/write was `state/zeus_trades.db`, while code wrote `state/zeus.db`.

## Phase 2 Material Value Lineage

| Value | Real object denoted | Origin | Authority/evidence | Unit/side | Time basis | Transform | Persistence | Consumers | State |
|---|---|---|---|---|---|---|---|---|---|
| `chronicle.trade_id` | historical event-linked trade id | `chronicle` | legacy event log | id | chronicle event time | used as `outcome_fact.position_id` | `outcome_fact` | diagnostics/replay/report | repaired guard |
| `chronicle.details_json.pnl` | legacy recorded PnL projection | `chronicle` | legacy lifecycle/event payload | USD | settlement event timestamp | copied into outcome_fact | `outcome_fact.pnl` | legacy consumers | guarded/labeled |
| `chronicle.details_json.outcome` | legacy win/loss projection | `chronicle` | legacy lifecycle/event payload | boolean | settlement event timestamp | copied into outcome_fact | `outcome_fact.outcome` | legacy consumers | guarded/labeled |
| `decision_snapshot_id` | decision-time hypothesis link | `chronicle.details_json` | legacy optional field | id | decision time if present | copied | `outcome_fact` | replay/report diagnostics | guarded only |
| script manifest target | declared mutation authority | `architecture/script_manifest.yaml` | routing/governance metadata | path | operator run time | route/guard | manifest | topology/operator | repaired |

UNKNOWN: existing legacy DB rows were not inspected or relabeled. This wave changes only the producer guard and manifest truth.

## Phase 3 Findings

W17-F1 (S0/S1): `scripts/backfill_outcome_fact.py` wrote to `state/zeus.db` by default unless `--dry-run` was provided. This violates live-money repair discipline for a dangerous backfill path and can create authorityless `outcome_fact` rows without explicit operator apply, dry-run evidence, or rollback plan.

W17-F2 (S1): The script docstring claimed future settlements write to `outcome_fact`, preserving the old meaning that `outcome_fact` is the settlement result surface rather than a legacy lifecycle projection.

W17-F3 (S1): `architecture/script_manifest.yaml` declared `backfill_outcome_fact.py` target/write as `state/zeus_trades.db`, while code wrote `state/zeus.db`. The operator/topology surface disagreed with executable truth.

W17-F4 (S1): The manifest entry lacked dry-run default, apply flag, promotion barrier, and explicit legacy non-authority semantics, despite `dangerous_if_run=True`.

## Phase 4 Repair

Restored invariant: a producer of `outcome_fact` rows must fail closed by default, declare its mutation target truthfully, and explicitly preserve the rows' legacy non-authoritative meaning.

Code/manifest repair:
- `scripts/backfill_outcome_fact.py` now defaults to dry-run.
- Applying writes requires `--apply --confirm-legacy-outcome-fact-backfill`.
- Added `--db PATH` so the target path is explicit and testable.
- Missing DB paths fail closed before SQLite creates a new empty database file.
- Added a warning and return summary carrying `legacy_lifecycle_projection_not_settlement_authority`.
- Manifest row changed from unguarded `etl_writer` to guarded `repair` with `dry_run_default: true`, `apply_flag: "--apply"`, `target_db/write_targets: state/zeus.db`, promotion barrier, and required test.

Relationship tests:
- `test_backfill_outcome_fact_manifest_declares_legacy_apply_guard`
- `test_backfill_outcome_fact_defaults_to_dry_run`
- `test_backfill_outcome_fact_missing_db_does_not_create_file`

## Phase 5 Verification

- `python3 -m py_compile scripts/backfill_outcome_fact.py tests/test_topology_doctor.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_topology_doctor.py -k 'backfill_outcome_fact'` -> `3 passed, 356 deselected`.
- CLI guard smoke:
  - `python3 scripts/backfill_outcome_fact.py --apply --db /tmp/zeus-wave17-missing.db` -> argparse error requiring `--confirm-legacy-outcome-fact-backfill`.
  - `python3 scripts/backfill_outcome_fact.py --dry-run --db /tmp/zeus-wave17-missing.db` -> exits `1`, reports `error_missing_db`, and does not create the file.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave17/PLAN.md` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files ...` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files ...` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --naming-conventions` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --schema` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --task-boot-profiles` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --scripts --json` -> still globally fails on 28 pre-existing script manifest/naming issues, but `scripts/backfill_outcome_fact.py` is no longer among issues.
- `git diff --check` -> pass.

## Critic REVISE Repair

Critic verdict: `REVISE`.

Critic finding:
- `tests/test_cross_module_relationships.py` still encoded the old settlement PnL authority chain
  `chronicle.SETTLEMENT -> outcome_fact -> trade_decisions.settlement_edge_usd -> risk_state.realized_pnl`
  and failed on missing or divergent `outcome_fact` rows. That preserved pressure to promote
  legacy `outcome_fact` rows as settlement economics authority.

Repair:
- Added `tests/test_cross_module_relationships.py` to the object-meaning settlement authority cutover route so the active antibody can be maintained under the same invariant boundary.
- Regenerated `architecture/digest_profiles.py` and added digest-profile regression coverage for the admitted relationship test.
- Updated the relationship test to assert only authoritative settlement PnL consumers:
  `chronicle.SETTLEMENT -> trade_decisions.settlement_edge_usd -> risk_state.realized_pnl`.
- `outcome_fact` is now explicitly labeled as `legacy_lifecycle_projection_not_settlement_authority` in the importable relationship runner. Legacy divergence is reported as diagnostic note only, not as authority failure.

Post-REVISE verification:
- `python3 -m py_compile tests/test_cross_module_relationships.py architecture/digest_profiles.py` -> pass.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- Static sweep for old authority-pressure strings (`outcome_fact has NO ROW`, `missing from outcome_fact`, `SD-2 regression`, `chronicle.SETTLEMENT.*outcome_fact`) -> no active test/source hits; only this plan's non-authority note remains.
- `pytest -q -p no:cacheprovider tests/test_cross_module_relationships.py -k 'settlement_pnl_flows_to_all_surfaces'` -> `1 skipped, 5 deselected` because the current DB has no `SETTLEMENT` events to check.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_object_meaning_settlement_authority_cutover_routes_to_wave5_profile` -> pass.
- `pytest -q -p no:cacheprovider tests/test_topology_doctor.py -k 'backfill_outcome_fact'` -> pass.
- `python3 scripts/topology_doctor.py --task-boot-profiles` -> `topology check ok`.
- Wave17 planning-lock, freshness metadata, map maintenance, schema, naming, digest mirror, and `git diff --check` -> pass.

Critic re-review:
- Verdict: `APPROVE`.
- Sweep covered producer guard, manifest/topology, cross-module relationship runner, replay/report, economics/learning, RiskGuard/status, diagnostics/reports/smoke paths.
- No remaining Wave17 object-meaning finding; residual global `--scripts --json` issues are pre-existing unrelated script manifest/naming debt and do not include current target scripts.
