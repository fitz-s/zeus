# Source-Contract Auto-Conversion Work Log

## 2026-04-30

Implemented Phase A controller:

- Added `scripts/source_contract_auto_convert.py`.
- Reused `scripts/watch_source_contract.py` for Gamma analysis and city
  source quarantine writes.
- Added deterministic same-provider WU station-change confirmation threshold.
- Added date-scope derivation with explicit `backfill_wu_daily_all.py`
  `today - 2` executable-window handling.
- Added durable receipts under `state/source_contract_auto_convert/`.
- Added optional Discord notification via existing RiskGuard Discord webhook
  resolver, with `--discord --discord-required` for cron.
- Added blocked/manual branches for provider-family change, unsupported source,
  ambiguous source, insufficient market evidence, missing target date, and
  current runtime apply gaps.
- Added `mini_llm_execution` to each candidate, including direct-completion
  status, allowed commands, forbidden actions, evidence manifest, stop
  conditions, and a report template.
- Added `--write-mini-report` / `--mini-report-path` to write a deterministic
  Markdown report for Venus/mini handoff.
- Added `workspace_locator` and `safe_execution_contract` to show exact file
  locations, current-phase allowed write globs, forbidden write globs,
  destructive command tokens, and stop/report conditions.
- Added an fcntl-based cron lock with `--lock-path` and test-only `--no-lock`
  so overlapping cron runs fail closed instead of racing on quarantine/receipt
  writes.
- Added a dedicated topology digest profile so source-conversion cron work is
  not misrouted into the R3 live-readiness profile when shared operations and
  architecture files are touched.

Current phase remains plan-only:

- No config/cities.json mutation.
- No production DB mutation.
- No automatic quarantine release.
- No backfill/rebuild/refit apply execution.

Verification:

- `python -m py_compile scripts/source_contract_auto_convert.py scripts/watch_source_contract.py scripts/venus_sensing_report.py` PASS
- `python -m pytest -q tests/test_market_scanner_provenance.py -k "auto_convert or conversion_plan or source_watch"` PASS, 9 passed / 18 deselected
- `python -m pytest -q tests/test_market_scanner_provenance.py` PASS, 27 passed
- `python -m pytest -q tests/test_digest_profile_matching.py -k "source_contract_auto_conversion or source_contract_watch or source_current_fact"` PASS, 3 passed / 39 deselected
- `python -m pytest -q tests/test_market_scanner_provenance.py tests/test_digest_profile_matching.py` PASS, 69 passed
- `python scripts/topology_doctor.py --navigation --task "source contract auto conversion cron controller with Discord date scope" --files scripts/source_contract_auto_convert.py tests/test_market_scanner_provenance.py docs/operations/task_2026-04-30_source_auto_conversion/plan.md architecture/script_manifest.yaml` PASS; profile `source contract auto conversion runtime`, admission `admitted`
- `python scripts/topology_doctor.py --planning-lock --changed-files scripts/source_contract_auto_convert.py tests/test_market_scanner_provenance.py architecture/script_manifest.yaml scripts/AGENTS.md docs/operations/AGENTS.md docs/operations/task_2026-04-30_source_auto_conversion/plan.md --plan-evidence docs/operations/task_2026-04-30_source_auto_conversion/plan.md` PASS
- `python scripts/topology_doctor.py --freshness-metadata --changed-files scripts/source_contract_auto_convert.py tests/test_market_scanner_provenance.py architecture/script_manifest.yaml scripts/AGENTS.md docs/operations/AGENTS.md docs/operations/task_2026-04-30_source_auto_conversion/plan.md architecture/naming_conventions.yaml` PASS
- `python scripts/topology_doctor.py --naming-conventions` PASS
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files ...` PASS
- `python scripts/digest_profiles_export.py --check` PASS
- `git diff --check` PASS

Known verification blocker:

- `python scripts/topology_doctor.py --scripts --json` still fails on
  pre-existing unrelated weekly diagnostic script registry issues:
  `calibration_observation_weekly.py`, `learning_loop_observation_weekly.py`,
  and `ws_poll_reaction_weekly.py`. The new
  `source_contract_auto_convert.py` naming issue was resolved and is not in
  the remaining issue list.

Residual runtime gaps before full apply automation:

- Add a deterministic config writer for same-provider WU station transitions.
- Add `--start-date`, `--end-date`, and `--temperature-metric` scope to
  `scripts/rebuild_settlements.py`, including low-track settlement rebuild.
- Add scoped Platt refit or bucket selection to avoid all-bucket refits for a
  single city/source transition.
- Decide whether cron may run WU backfill apply directly or only after dry-run
  manifest review.
- Define production DB backup/lock preflight before any apply-capable phase.

## 2026-04-30 Phase B

Implemented deterministic apply automation:

- Added `source_contract_auto_convert.py --execute-apply --force`.
- Kept default cron behavior plan/dry-run only; apply requires explicit flag,
  force flag, cron lock, active source quarantine, auto-confirmed
  same-provider WU branch, DB backup, exact station metadata, and complete
  evidence refs.
- Added station metadata resolution with override/static seed support and
  AviationWeather lookup for unknown ICAO stations; Paris `LFPB` has a
  deterministic offline metadata seed.
- Added deterministic `config/cities.json` writer for WU station/source/airport
  metadata. If exact metadata is unavailable, the conversion blocks and
  quarantine remains active.
- Added source-validity append artifact recording when a city converted from
  old station to new station and which evidence files completed.
- Changed `backfill_wu_daily_all.py` to derive WU city/station/unit from live
  `config/cities.json`, accept `--start-date`, `--end-date`, `--db`, and
  delete/refetch station-mismatch rows under `--replace-station-mismatch`.
- Added high/low/all date-scoped settlement rebuild support to
  `rebuild_settlements.py`.
- Added city/date/metric filters to `rebuild_calibration_pairs_v2.py`.
- Added metric/cluster/season/data-version filters to `refit_platt_v2.py`.
- Added runtime city-config reload helpers and moved market discovery matching
  to the runtime config view, so a running scanner can observe city config
  changes without process restart.
- Confirmed quarantine blocks only new discovery; existing market price/sibling
  lookup paths remain available for exits.
- Added mocked Paris end-to-end apply test proving config update, scoped command
  sequence, evidence refs, quarantine release, and transition history.
- Added `execute_apply_controller` to the mini/Venus step protocol and stamp it
  with exact current-run paths, so a smaller model can run one deterministic
  command instead of manually editing config or composing SQL/backfill steps.

Verification:

- `/Users/leofitz/miniconda3/bin/python -m py_compile scripts/source_contract_auto_convert.py scripts/watch_source_contract.py scripts/venus_sensing_report.py scripts/backfill_wu_daily_all.py scripts/rebuild_settlements.py scripts/rebuild_calibration_pairs_v2.py scripts/refit_platt_v2.py src/config.py src/data/market_scanner.py` PASS
- `/Users/leofitz/miniconda3/bin/python -m pytest -q tests/test_market_scanner_provenance.py` PASS, 30 passed
- `/Users/leofitz/miniconda3/bin/python -m pytest -q tests/test_backfill_scripts_match_live_config.py` PASS, 22 passed
- `/Users/leofitz/miniconda3/bin/python -m pytest -q tests/test_rebuild_pipeline.py -k rebuild_settlements` PASS, 5 passed / 7 deselected
- `/Users/leofitz/miniconda3/bin/python -m pytest -q tests/test_phase7a_metric_cutover.py` PASS, 17 passed
- `/Users/leofitz/miniconda3/bin/python -m pytest -q tests/test_digest_profile_matching.py -k source_contract_auto_conversion` PASS, 1 passed / 41 deselected
- `/Users/leofitz/miniconda3/bin/python -m pytest -q tests/test_market_scanner_provenance.py tests/test_backfill_scripts_match_live_config.py tests/test_config.py tests/test_calibration_manager.py` PASS, 108 passed
- `/Users/leofitz/miniconda3/bin/python -m pytest -q tests/test_phase7a_metric_cutover.py tests/test_digest_profile_matching.py -k "source_contract_auto_conversion or test_R_B"` PASS, 18 passed / 41 deselected
- `/Users/leofitz/miniconda3/bin/python scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-04-30_source_auto_conversion/plan.md` PASS
- `/Users/leofitz/miniconda3/bin/python scripts/topology_doctor.py --freshness-metadata ...` PASS
- `/Users/leofitz/miniconda3/bin/python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout ...` PASS
- `/Users/leofitz/miniconda3/bin/python scripts/digest_profiles_export.py --check` PASS

## 2026-04-30 Post-Merge Review Hardening

Review of the restored worktree patch found two merge blockers and one brittle
test assertion. The fixes are part of the reviewed patch:

- Replaced the worktree-name assertion in the mini execution packet test with
  an exact `source_contract_auto_convert.ROOT` assertion.
- Tightened Platt refit scoping. The source-conversion controller now passes
  `--city`, `--start-date`, `--end-date`, and repeated `--season` selectors to
  `refit_platt_v2.py`; the refit helper derives the exact affected
  `(cluster, season, data_version)` bucket keys from the scoped
  `calibration_pairs_v2` rows, then refits only those full buckets.
- Added a fixture safety gate. `--execute-apply --fixture` now fails before any
  apply mutation when any default production write surface is present
  (`state/zeus-world.db`, `config/cities.json`,
  `docs/operations/current_source_validity.md`, default source quarantine, or
  default evidence root). Fixture-backed apply remains allowed only when every
  write path is an explicit non-production override.

Post-hardening verification:

- `/Users/leofitz/miniconda3/bin/python3 -m py_compile scripts/source_contract_auto_convert.py scripts/watch_source_contract.py scripts/venus_sensing_report.py scripts/backfill_wu_daily_all.py scripts/rebuild_settlements.py scripts/rebuild_calibration_pairs_v2.py scripts/refit_platt_v2.py src/config.py src/data/market_scanner.py` PASS
- `/Users/leofitz/miniconda3/bin/python3 -m pytest -q -p no:cacheprovider tests/test_market_scanner_provenance.py -k "auto_convert or source_contract or conversion_plan or platt_refit_derives"` PASS, 11 passed / 35 deselected
- `/Users/leofitz/miniconda3/bin/python3 -m pytest -q -p no:cacheprovider tests/test_market_scanner_provenance.py` PASS, 46 passed
- `/Users/leofitz/miniconda3/bin/python3 -m pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py -k source_contract_auto_conversion` PASS, 1 passed / 136 deselected
- `/Users/leofitz/miniconda3/bin/python3 -m pytest -q -p no:cacheprovider tests/test_rebuild_pipeline.py -k rebuild_settlements` PASS, 5 passed / 7 deselected
- `/Users/leofitz/miniconda3/bin/python3 -m pytest -q -p no:cacheprovider tests/test_backfill_scripts_match_live_config.py -k wu_backfill` PASS, 3 passed / 19 deselected
- `/Users/leofitz/miniconda3/bin/python3 -m pytest -q -p no:cacheprovider tests/test_phase7a_metric_cutover.py` PASS, 17 passed
- `git diff --cached --check` PASS
- `scripts/topology_doctor.py --navigation ... --files <staged files>` PASS,
  profile `source contract auto conversion runtime`, admission `admitted`
- `scripts/topology_doctor.py --planning-lock ...` PASS
- `scripts/topology_doctor.py --freshness-metadata ...` PASS
- `scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout ...` PASS
- `scripts/digest_profiles_export.py --check` PASS
