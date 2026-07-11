# Quarantine Extermination Ledger — tests/scripts/maintenance_worker/bindings/architecture (2026-07-11)

Scope per team-lead: `rg -l -i quarantin` under tests/, scripts/, maintenance_worker/, bindings/, architecture/.
Buckets: B1 dies-with-disease (T1-T7 owned), B2 reshape-and-rename (survives, renamed), B3 dead-code (delete), B4 text-only (rewrite prose), B5-HISTORICAL (one-shot migration already ran, keep for lineage). Read-only census; no edits made.

Extra bucket found beyond mission doc's T1-T7: **T9-CANDIDATE — observation/settlement authority-tier QUARANTINED** (VERIFIED/UNVERIFIED/QUARANTINED tri-state on `observations`/`settlement_outcomes` rows, `quarantine_reason` column, `scripts/drain_settlement_quarantine.py` repeatable drain). Not named in mission doc T1-T7. Live, actively minting (Paris/HK downgrades, drain script repeatable not one-shot). Needs its own excision packet before T8 zero-grep gate closes. See LAW-SURFACES section.

## Bucket counts (this file's scope: architecture/, maintenance_worker/, bindings/, tests/maintenance_worker/)
B1=19  B2=17  B3=0  B4=5  B5-HISTORICAL=4

---

## bindings/ (B2 — rename alongside maintenance_worker rules packet)

- `bindings/zeus/config.yaml:73` `stale_worktree_quarantine_idle_days` B2 -> `stale_worktree_archive_idle_days`
- `bindings/zeus/config.yaml:89-104` `launchagent_backup_quarantine` block, `quarantine_dir` (x3 handlers), `stale_worktree_quarantine`, `in_repo_scratch_quarantine` B2 -> handler ids drop `quarantine`->`archive`; `quarantine_dir`->`archive_dir`
- `bindings/zeus/safety_overrides.yaml:114,116` comments "in-repo quarantine root for reversible quarantine moves" / "LaunchAgents backup quarantine destination" B4 -> reword to "archive"

## architecture/ (LAW surfaces — every hit is a registry/invariant/schema literal)

### T1-owned (EDLI bridge disposition)
- `architecture/test_topology.yaml:831` test_fill_bridge_dispositions_migration.py note "quarantine was unreachable live 2026-06-12" B1/T1

### T2-owned (global discovery gate / INV-27)
- `architecture/invariants.yaml:547-553` **INV-27** full statement+why+enforced_by block — B1/T2, this IS the mission doc's named target invariant, verbatim rewrite required per doc §T2 ("quarantine is no longer the canonical entry blocker").
- `architecture/invariants.yaml:146` INV-09 test citation `test_load_portfolio_rehydrates_chain_only_quarantine_fact_when_projection_degraded` B2 -> rename test to `chain_only_fact` naming (ChainOnlyFact type already exists per doc T5 note)
- `architecture/test_topology.yaml:722` comment "not just risk/heartbeat/ws/quarantine subset" B1/T2
- `architecture/improvement_backlog.yaml:213` backlog text "enum/string ChainState invariant controlled the live entry quarantine gate downstream" B1/T2

### T5-owned (QUARANTINED lifecycle phase retirement)
- `architecture/2026_04_02_architecture_kernel.sql:19` `'CHAIN_QUARANTINED'` chain_state enum literal B1/T5
- `architecture/2026_04_02_architecture_kernel.sql:43,54,100` `'quarantined'` phase literal (3 CHECK constraints — position_current/position_lots/position_events family) B1/T5
- `architecture/2026_04_02_architecture_kernel.sql:283,331` `'chain_only_quarantined'` (x2 tables) B1/T5
- `architecture/money_path_objects.yaml:203,303,325` QUARANTINED phase + `quarantined`/`CHAIN_QUARANTINED` enum lists B1/T5
- `architecture/kernel_manifest.yaml:42,63` `quarantined` / `CHAIN_QUARANTINED` mirror of kernel enum B1/T5
- `architecture/test_topology.yaml:1033` test_b066_quarantine_sentinel_ids.py registry row B1/T5
- `architecture/test_topology.yaml:1655` "M5 lifecycle/quarantine truth alignment owns unquarantine-after-open-orders proof" B1/T5
- `architecture/test_topology.yaml:1966` "RESCUE, SIZE-MISMATCH, and QUARANTINE branches across all 4 locked D6 fields" B1/T5

### T6-owned (control-plane ack tokens)
- `architecture/2026_04_02_architecture_kernel.sql:282,330` `'operator_quarantine_clear'` (x2) B1/T6

### T7-owned (semantic contamination: doc status, timestamp CHECK)
- `architecture/artifact_authority_status.yaml:11,24,38` `QUARANTINE` disposition literal B1/T7 — exact mission-doc target, rename to e.g. `SUPERSEDED_UNREVIEWED`
- `architecture/2026_04_02_architecture_kernel.sql:34` `CHECK (occurred_at ... OR occurred_at = 'QUARANTINE')` B1/T7 — exact mission-doc target ("state word inside timestamp type")
- `architecture/money_path_objects.yaml:903` bare `QUARANTINE` (same artifact-status enum) B1/T7

### decision_integrity_quarantine side-table (named in mission doc RESHAPE list, not numbered T1-T6 but tracked as its own packet — labeled DIQ-packet here)
- `architecture/db_table_ownership.yaml:2601-2613` full table entry (`decision_integrity_quarantine`, created_by, doc-string) B1/DIQ-packet
- `architecture/money_path_objects.yaml:615-630` `decision_integrity_quarantine_reason_codes` object + `QUARANTINED_NON_CONTRIBUTING_FORECAST_EXTREMA` B1/DIQ-packet
- `architecture/test_topology.yaml:3279-3344` three test registry rows: test_decision_integrity_quarantine.py, `_extended.py`, `_crossdb.py` (full metadata blocks incl. `DECISION_INTEGRITY_QUARANTINE_LOGIC`/`_IDEMPOTENT`, `QUARANTINE_DOWNSTREAM_EXCLUSION_*`, `QUARANTINE_CROSSDB_*` capability tags) B1/DIQ-packet — matches mission doc's explicit note (live callers in executor.py/evidence_report/command_recovery, cannot delete blind)
- `architecture/script_manifest.yaml:1674-1699` scripts/quarantine_bad_forecast_decisions.py full manifest entry B1/DIQ-packet — script itself lives in scripts/ (see scripts ledger section), this is its LAW registration

### ensemble_snapshot_provenance rename (mission-doc-named B2 target #1)
- `architecture/source_rationale.yaml:492,495` `ensemble_snapshot_quarantine_contract` authority_role + "partitions quarantined..." B2 -> rename to `ensemble_snapshot_admissibility_contract` (matches doc's suggested `rejected_data_versions`/`is_admissible`)

### source-contract block rename (mission-doc-named B2 target #2, market_scanner)
- `architecture/script_manifest.yaml:1164-1190` `state/source_contract_quarantine.json` (x5), `--quarantine-path` CLI flag (x2), "Quarantine may block new entries only" promotion_barrier text (x2), "Source-contract quarantine may block..." B2 -> rename file/flag to `source_contract_block.json` / `--block-path`, promotion_barrier text -> "Source block may block..."
- `architecture/digest_profiles.py:195` `state/source_contract_quarantine.json` downstream ref B2 (same rename)
- `architecture/topology.yaml:989` `state/source_contract_quarantine.json` B2 (same rename)
- `architecture/naming_conventions.yaml:106` naming-rationale text citing `source_contract_quarantine` runtime surface B2 (update once file renamed)

### maintenance-worker file-audit-move rename (mission-doc-named B2 target #3) — see maintenance_worker/ section below for code; these are the LAW-surface registrations
- `architecture/script_manifest.yaml:699` chain-mirror invariant reason text "quarantine writer" B1/T5 (describes T5 drain target, not the maintenance-worker moves)

### T9-CANDIDATE — settlement/observation authority-tier QUARANTINED (NOT in mission doc T1-T7, flagged new)
- `architecture/fatal_misreads.yaml:181` "do not open quarantine reactivation tickets" B4, rewrite once T9-candidate renames vocabulary
- `architecture/preflight_overrides_2026-04-28.yaml:13,18,158-159,177,179,183,194` HK/Paris downgrade-to-QUARANTINED evidence log refs B5-HISTORICAL — event already ran + released 2026-05-02 (line 194 explicit), but literal `QUARANTINED` text is the live authority-tier word so still inherits eventual T9-candidate rename for consistency
- `architecture/settlement_dual_source_truth_2026_05_07.yaml:64` `quarantine_reason` column comment "set when authority=QUARANTINED" B1/T9-candidate — live schema column
- `architecture/test_topology.yaml:73` test_harvester_truth_writer_m1_settled_at.py note "missing fetch time forces QUARANTINED" B1/T9-candidate
- `architecture/test_topology.yaml:89` test_drain_settlement_quarantine.py note (operator-corrected 2026-07-04, active antibody) B2/T9-candidate — survives under new name once authority tier + drain script renamed
- `architecture/history_lore.yaml:1756,1783,2071,2079,2108,2248` VERIFIED/UNVERIFIED/QUARANTINED taxonomy narrative (active law, not just history) B4/T9-candidate
- `architecture/paris_station_resolution_2026-05-01.yaml` (whole file, ~15 hits) B5-HISTORICAL — dated 2026-05-01, describes completed row-level UPDATE + "released; QUARANTINED LFPG calibration_pairs_v2 rows deleted" (line 177, past tense, done). Literal QUARANTINED word inherits T9-candidate rename for consistency but migration itself already executed.

### Historical / already-run migrations (B5-HISTORICAL, not T9-candidate)
- `architecture/history_lore.yaml:2486-2558` `CWA_STATION_IS_LEGACY_QUARANTINE_PATH_NOT_DEAD_CODE` entry — self-described legacy-retroactive path, Taipei rows written by harvester retroactive reconstruction through 2026-03-22, explicitly "not dead code" (keep). B5-HISTORICAL, do not delete, but literal name could still gain a doc-only word update.
- `architecture/db_table_ownership.yaml:609-619` `settlement_commands_era_quarantine` table, `status='ERA_QUARANTINED'`, created by one-shot `migrate_settlement_commands_in_flight_at_era_flip.py` — B2 (table+status literal rename, e.g. `settlement_commands_era_archive`/`ERA_ARCHIVED`) with B5-HISTORICAL note: the migration that populated it already ran (era-flip is a singular past event), so rename is pure relabeling, no data-semantics risk. Still live-checked by `check_db_table_delta` gate.

### Unrelated-concept text (B4, distinct "quarantine" sense — test-debt, not data)
- `architecture/test_topology.yaml:4` `owner_packet: "Packet 5 test suite law gate and debt quarantine"` B4 — this is a historical packet *name* label for test-debt tracking, unrelated to any data mechanism. Low-priority text rename (e.g. "debt ledger").
- `architecture/ast_rules/semgrep_zeus.yml:97` semgrep rule message "...use canonical strategy_key or reject/quarantine." B4 — advice text endorsing the word; reword to "reject/isolate" or similar, no rule-logic change.

---

## maintenance_worker/ + tests/maintenance_worker/

Two DISTINCT mechanisms share the word here — verified by reading (not filename-guessing) per team-lead's constraint:

### Mechanism A — SELF_QUARANTINE brick (kill-switch / halt-and-alert on detected divergence)
Files: `maintenance_worker/core/kill_switch.py` (whole file: `_SELF_QUARANTINE_FILE`, `is_self_quarantined`, `write_self_quarantine`, docstring lines 6,10,13,15,36,57-91,108,119,159-160), `maintenance_worker/core/refusal.py:11,23,52,96-97`, `maintenance_worker/core/validator.py:29,465`, `maintenance_worker/core/engine.py:283,535,552,555`, `maintenance_worker/core/guards.py:12,241-262,334`, `maintenance_worker/types/modes.py:38,55`, `maintenance_worker/cli/entry.py:281`, `maintenance_worker/core/subprocess_guard.py:229` (comment only).

Verdict: **B2 RESHAPE-AND-RENAME**, all sites, single rename target. This is NOT the disease pattern (no upstream error made permanent, no authority-hiding, no global gate on trading) — it's the maintenance worker's own fail-closed circuit breaker: on unexpected mutation it halts itself and refuses to run again until a human deletes the marker. Legitimate, scoped, evidence-backed release path (human reconciles + deletes file). Propose rename `SELF_QUARANTINE` -> `SELF_HALT` (matches existing module name `kill_switch.py`); `is_self_quarantined`->`is_self_halted`; `write_self_quarantine`->`write_self_halt`; `SELF_QUARANTINED` refusal-reason enum member -> `SELF_HALTED`.
Tests (all B2, survive under new name): `tests/maintenance_worker/test_core/test_kill_switch.py` (entire file, ~25 hits), `tests/maintenance_worker/test_core/test_validator.py:507-542`, `tests/maintenance_worker/test_core/test_refusal.py:9,68,97-121`, `tests/maintenance_worker/test_core/test_engine.py:218-234,386-389,825`, `tests/maintenance_worker/test_types/test_modes.py:34-53`, `tests/maintenance_worker/test_types/test_results.py:53`, `tests/maintenance_worker/test_integration/test_zero_byte_state_cleanup_live.py:111,152`, `tests/maintenance_worker/test_integration/test_validator_evidence_flow.py:108,361-364`, `tests/maintenance_worker/test_core/test_guards.py:32,58-73,250,275,293`.

### Mechanism B — reversible audit-move rules (mission-doc-named B2 target #3: "file-audit rename")
Files: `maintenance_worker/rules/stale_worktree_quarantine.py` (whole file), `maintenance_worker/rules/launchagent_backup_quarantine.py` (whole file), `maintenance_worker/rules/untracked_top_level_quarantine.py` (whole file), `maintenance_worker/rules/in_repo_scratch_quarantine.py` (whole file), `maintenance_worker/types/candidates.py:28`.

Verdict: **B2 RESHAPE-AND-RENAME** confirmed by reading — every one is a dry-run-only (`live_default: false`) proposal of a reversible file MOVE to an archive dir, never a delete, never a silent-exclude/gate mechanism. Matches mission doc exactly ("materialization-queue orphaned-lock move + maintenance-worker moves: file audit rename → orphaned_lock_archive / audit-move naming"). Propose: module/file names drop `quarantine`->`archive_move` (`stale_worktree_archive_move.py` etc.); verdict constants `STALE_QUARANTINE_CANDIDATE`->`STALE_ARCHIVE_MOVE_CANDIDATE`, `UNTRACKED_QUARANTINE_CANDIDATE`->`UNTRACKED_ARCHIVE_MOVE_CANDIDATE`, `SCRATCH_QUARANTINE_CANDIDATE`->`SCRATCH_ARCHIVE_MOVE_CANDIDATE`; `quarantine_dir` config key -> `archive_dir`; task_ids drop `quarantine`->`archive_move`.
Tests (all B2, survive under new name): `tests/maintenance_worker/test_rules/test_stale_worktree_quarantine.py` (whole file), `tests/maintenance_worker/test_rules/test_launchagent_backup_quarantine.py` (whole file), `tests/maintenance_worker/test_rules/test_untracked_top_level_quarantine.py` (whole file), `tests/maintenance_worker/test_rules/test_in_repo_scratch_quarantine.py` (whole file), `tests/maintenance_worker/test_rules/test_parser.py:551-555`, `tests/maintenance_worker/test_bindings/test_zeus_config.py:130`.

### T7-tied (artifact_authority_status QUARANTINE consumer)
- `maintenance_worker/core/archival_check_0.py:45,50,199` doc-authority-status set incl. `QUARANTINE` B1/T7 (consumes the same enum architecture/artifact_authority_status.yaml renames)
- `tests/maintenance_worker/test_archival_check_0.py:112,117` `test_quarantine_returns_load_bearing` asserting `status: "QUARANTINE"` B1/T7 — survives as renamed test asserting new status literal once T7 lands (recorded here as B1 per doc's own bucket rule: "no separate work; ledger records the mapping", though T7 itself is a rename not a removal)

---

## LAW SURFACES NEEDING AMENDMENT (owning T-target)

1. `architecture/invariants.yaml` **INV-27** (line 547) — T2, full statement rewrite (verbatim mission-doc target).
2. `architecture/invariants.yaml` INV-09 test citation (line 146) — T2/T5, test-name rename only.
3. `architecture/2026_04_02_architecture_kernel.sql` — T5 (lines 19,43,54,100,283,331 lifecycle/chain-state literals), T6 (lines 282,330 `operator_quarantine_clear`), T7 (line 34 `occurred_at` CHECK literal). Highest-risk single file — kernel SQL, 4 separate T-targets converge here.
4. `architecture/artifact_authority_status.yaml` — T7, `QUARANTINE` disposition rename (lines 11,24,38).
5. `architecture/db_table_ownership.yaml` — DIQ-packet (`decision_integrity_quarantine` table, lines 2601-2613) + new B2 (`settlement_commands_era_quarantine`, lines 609-619, tied to T9-candidate vocabulary).
6. `architecture/test_topology.yaml` — heaviest single registry (11 distinct row groups across T1/T2/T5/T7/DIQ-packet/T9-candidate); every renamed test needs its `test_topology.yaml` row updated in the same packet per repo law (registries updated same-packet, not deferred).
7. `architecture/script_manifest.yaml` — source-contract-block rename (B2, ~11 lines) + DIQ-packet manifest entry (lines 1674-1699) + T5-tied chain-mirror text (line 699).
8. `architecture/source_rationale.yaml` — `ensemble_snapshot_quarantine_contract` rename (lines 492,495), mission-doc-named target #1.
9. `architecture/money_path_objects.yaml` — T5 (lifecycle enums), T7 (bare QUARANTINE), DIQ-packet (reason codes) all three converge (lines 203,303,325,615-630,903).
10. `architecture/kernel_manifest.yaml` — T5 mirror of kernel enum (lines 42,63) — must stay in lockstep with #3.
11. **NEW T9-candidate needed**: `architecture/settlement_dual_source_truth_2026_05_07.yaml`, `architecture/fatal_misreads.yaml`, `architecture/history_lore.yaml`, `architecture/preflight_overrides_2026-04-28.yaml`, `architecture/paris_station_resolution_2026-05-01.yaml`, plus `scripts/drain_settlement_quarantine.py` (in scripts scope) and its tests — none of these are covered by mission-doc T1-T7. This is the observation/settlement authority-tier QUARANTINED (VERIFIED/UNVERIFIED/QUARANTINED), a live, repeatable-drain, disease-shaped-by-the-doc's-own-definition mechanism (upstream data conflict -> authority downgrade -> excluded from settlement grading -> requires manual drain). Recommend operator/planner define T9 before the T8 zero-grep gate can close.
12. `bindings/zeus/config.yaml` + `bindings/zeus/safety_overrides.yaml` — must be edited in the SAME packet as the maintenance_worker rules rename (Mechanism B above) since they carry the `quarantine_dir` config keys the rules read at runtime.

---

## New candidate concepts found beyond mission doc T1-T9-candidate (flag for operator/planner)

- **CALIB-candidate**: `scripts/refit_platt.py:690-708` — Platt-fit bucket rejection on inverted slope (`QUARANTINE {bucket_key}`, `buckets_quarantined`), separate from decision_integrity_quarantine and T9. LEGITIMATE-shaped (scoped bucket rejection, reported not hidden). B2, propose `buckets_rejected`/`is_admissible_calibration`.
- **FORECAST-INGEST-BOUNDARY-candidate**: `scripts/extract_tigge_mn2t6_localday_min.py`, `scripts/ingest_grib_to_snapshots.py:362,634`, `tests/test_low_fsr_boundary_ambiguity_majority_fix.py`, `tests/test_phase4_5_extractor.py:209-227`, `tests/test_phase5_fixpack.py` (boundary portion) — TIGGE ensemble-member boundary-ambiguity majority-threshold snapshot rejection. LEGITIMATE-shaped (scoped, majority-threshold, reported). B2, propose `boundary_excluded`/`is_admissible_boundary`, likely same family as ensemble_snapshot_provenance B2 but distinct module.
- **LOOP-GUARD-candidate**: `scripts/ops/loop_guard.py:10,460`, `scripts/ops/loop_status.sh:8,108`, `tests/test_loop_guard.py` — repo-loop-runner's own allowlist-diff enforcement + restore-on-violation, same shape as maintenance_worker's SELF_HALT kill-switch (legitimate circuit breaker, not data-authority hiding). B2, propose rename parallel to maintenance_worker Mechanism A (e.g. `enforce_halt`/`VIOLATION_HALT`).
- **STRATEGY-LOCALIZATION-candidate**: `tests/test_riskguard.py:2211-2445` `localized_orange_quarantine` — Brier strategy localization flag, unrelated to RiskGuard row-exclusion (T3) despite being in the same test file. B2, propose `localized_orange_flagged`.
- **STRATEGY-KEY-FILTER (word-only)**: `tests/test_ws_poll_reaction.py:24,277-281` "quarantine unknown strategy keys" — uses "quarantine" only as a verb for exclusion, no state/table backing. B4, reword to "exclude"/"reject".

## scripts/ (43 files)

### DIQ-packet (decision_integrity_quarantine consumers)
- `scripts/quarantine_bad_forecast_decisions.py` (whole file) B1/DIQ — CLI wired to all 6 quarantine functions in src/state/decision_integrity_quarantine.py; script itself should rename alongside the module (e.g. `invalidate_bad_forecast_decisions.py`)
- `scripts/quarantine_invalid_live_actionable_certificates.py` (whole file) B1/DIQ + B2/cert-invalidation — this IS the mission-doc-named "invalid-certificate submit rejection" B2 target AND writes to decision_integrity_quarantine; rename script to `invalidate_bad_live_actionable_certificates.py`, functions to invalidation semantics
- `scripts/refit_platt.py:86,152-181,538-542` B1/DIQ (`_quarantine_ref_for_conn`, attaches trade.decision_integrity_quarantine to exclude non-contributing pairs) — separately also has CALIB-candidate hit above
- `scripts/check_dynamic_sql.py:199,203` B1/DIQ — dynamic-SQL allowlist entry for decision_integrity_quarantine.py, needs update on module rename
- `scripts/semantic_linter.py:85` B1/DIQ — same allowlist pattern
- `scripts/check_live_restart_preflight.py:43,1084-1310,2513-2643` B1/DIQ+T5 (dual-tagged) — `REDECISION_ELIGIBLE_QUARANTINE_CHAIN_STATES`, `_decision_certificate_quarantine_hashes`, phase='quarantined' checks, `_chain_backed_quarantine_requires_redecision`. **This is the restart-redecision file the current branch (p2-pending-exit-restart-redecision) is centered on** — flag as highest-coordination-risk file in scripts/.

### T5-owned (lifecycle phase)
- `scripts/repair_dust_exit_projection.py:159` B1/T5
- `scripts/check_live_release_gate.py:478-519` B1/T5 (`proven_quarantined_zero_exposure`, `phase='quarantined'` query feeding release-gate math)
- `scripts/replay_parity.py:23` B1/T5
- `scripts/nuke_rebuild_projections.py:41` B1/T5 (TERMINAL_PHASES frozenset)
- `scripts/state_census.py:286` B1/T5
- `scripts/dev/replay_position_phase.py:12` B1/T5 (comment)
- `scripts/migrations/202605_position_events_occurred_at_iso_check.py` (whole file) **B5-HISTORICAL** — one-shot migration that ADDED the exact CHECK literal T7 targets (`occurred_at = 'QUARANTINE'`) and T5 literals (`CHAIN_QUARANTINED`, `'quarantined'` x2). Already ran; stays as lineage. A NEW migration in the T5/T7 packets reverses/renames these literals going forward.

### T2-owned (global gate)
- `scripts/healthcheck.py:2273` B1/T2 (`quarantine_expired` cycle field surfaces the gate being removed)

### T9-candidate (settlement/observation authority tier) — largest new bucket found
- `scripts/fit_settlement_sigma_floor.py:43` B1/T9
- `scripts/audit_day0_extreme_undercapture.py:192` B1/T9
- `scripts/backfill_settlement_outcomes_canonical_2026_06_02.py:18,135` **B5-HISTORICAL** (dated one-shot backfill, already ran) — CHECK/enum literal still T9-candidate vocabulary
- `scripts/build_ens_residual_evidence.py:214-217` B1/T9 (loud-log, does not hide — closer to legitimate, but still consumes the tier being renamed)
- `scripts/build_ft_staging_db.py:60` B1/T9 (staging DB schema mirror of authority CHECK)
- `scripts/per_city_model_mae.py:55` B1/T9
- `scripts/audit_observation_instants.py:31,207` B1/T9
- `scripts/fit_bias_scale.py:24,259,1388-1521,1600` B1/T9 (A6 addendum, QUARANTINED-exclusion accounting in calibration)
- `scripts/backfill_settlements_via_gamma_2026.py:225-335` B1/T9 — **active minter**: writes fresh QUARANTINED placeholder rows for Gamma events with no local obs; strongest live evidence T9-candidate is not historical
- `scripts/backfill_settlement_outcome_type.py:13,50,55` B2/T9 — already contains the precedent rename `QUARANTINED -> DISPUTED` (settlement_outcome_type=100); use `DISPUTED` as T9-candidate's target vocabulary
- `scripts/migrations/202605_backfill_settlement_outcomes.py:114-115` **B5-HISTORICAL** (dated one-shot migration)
- `scripts/drain_settlement_quarantine.py` (whole file, ~40 hits) B1/T9 — **the central T9-candidate mechanism**: repeatable (explicitly "not one-shot" per its own docstring) drain of the QUARANTINED backlog in settlement_outcomes; script name + all identifiers rename together once T9-candidate lands (e.g. `drain_settlement_disputed.py`)
- `scripts/arm_live_mode.sh:18,36,38` B4/T9 (checklist comments, HK/Paris historical references)
- `scripts/verify_truth_surfaces.py:461` B4/T9 (comment, HK quarantine-release cycle)
- `scripts/migrate_b071_token_suppression_to_history.py:64-65` **B5-HISTORICAL** (named one-shot migration b071, already ran) — consumes T5 (`chain_only_quarantined`) + T6 (`operator_quarantine_clear`) literals

### ensemble_snapshot_provenance rename (mission-doc B2 target #1)
- `scripts/rebuild_calibration_pairs.py` (whole file, ~25 hits) B2 — `DataVersionQuarantinedError`, `is_quarantined`, `snapshots_quarantined` stat, "SPEC-QUARANTINED" print lines
- `scripts/rebuild_calibration_pairs_canonical.py:78,80,99-106,419` B2 — same rename; comment at 99-106 already documents ensemble_snapshot_provenance as the owning module (corroborates target)
- `scripts/seed_isolated_calibration_db.py:101` B2 (comment)

### source-contract block rename (mission-doc B2 target #2)
- `scripts/source_contract_auto_convert.py` (whole file, ~55 hits) B2
- `scripts/watch_source_contract.py` (whole file, ~30 hits) B2
- `scripts/venus_sensing_report.py:3,45,274-308` B2
- `scripts/AGENTS.md:70,72` B4 (doc table describing the two scripts above; rename alongside)

### FORECAST-INGEST-BOUNDARY-candidate
- `scripts/extract_tigge_mn2t6_localday_min.py` (whole file, ~8 hits) B2
- `scripts/download_replacement_forecast_current_targets.py:231-232` B2 (city verification fallback, same shape)
- `scripts/ingest_grib_to_snapshots.py:362,634` B2

### Unrelated-concept text (B4)
- `scripts/topology_doctor_test_checks.py:150` B4 — "reverse-antibody must be rewritten or quarantined" is test-debt sense, not data mechanism

### LOOP-GUARD-candidate
- `scripts/ops/loop_guard.py:10,460` B2
- `scripts/ops/loop_status.sh:8,108` B2

### Missed in first pass, added on coverage recheck
- `scripts/migrate_settlement_commands_in_flight_at_era_flip.py` (whole file) B2 + **B5-HISTORICAL note** — one-shot operator migration (already ran, era-flip is a singular past event) that creates `settlement_commands_era_quarantine` table + `ERA_QUARANTINED` status; same rename as `architecture/db_table_ownership.yaml:609-619` (target: `settlement_commands_era_archive`/`ERA_ARCHIVED`)
- `scripts/backfill_london_f_to_c_2026_05_08.py` (whole file, ~15 hits) **B5-HISTORICAL** — dated Phase D backfill for the 317 London rows quarantined under fix #262 (already shipped, per `architecture/history_lore.yaml:2193`); literal `QUARANTINED`/`quarantine_reason` inherits T9-candidate rename for consistency only, no functional risk

**scripts/ bucket counts**: B1=19 (incl. sub-tags DIQ/T5/T2/T9), B2=16, B3=0, B4=4, B5-HISTORICAL=7

---

## tests/ batch 1 (50 files, re-driven after original fan-out died on API 400)

### T1-owned
- `tests/test_fill_bridge_dispositions_migration.py` (whole file) B1/T1 — exact mission-doc-named test (`_quarantine_aggregate`, `QUARANTINED_BRIDGE_FAILURE` CHECK, "froze quarantine live" bug reproduction)

### T2-owned
- `tests/test_healthcheck.py:3364-3393` B1/T2 (`portfolio_quarantined` entries_blocked_reason, `quarantine_expired` cycle field — direct consumer of the gate being scoped) — rest of file (1545,1721-1802) is B1/T5 phase fixtures

### T3-owned
- `tests/test_riskguard.py:1348-1484` B1/T3 — **exact mission-doc match**: "loader must QUARANTINE the bad row... consistency stays 'pass'" is verbatim the T3 disease description; rewrite to assert `consistency_lock="degraded"`. (lines 2211-2445 in same file are the unrelated STRATEGY-LOCALIZATION-candidate, see above)

### T5-owned (QUARANTINED lifecycle phase) — largest group in this batch
`tests/test_exit_safety.py:5663-5837`, `tests/test_user_channel_ingest.py:1104-1139`, `tests/test_phase10b_dt_seam_cleanup.py:1610`, `tests/strategy/test_shift_bin_wiring.py:186-497`, `tests/test_inv_proj1_phase_projection_recomputable.py:86-98`, `tests/strategy/test_fill_up_wiring.py:162-167`, `tests/test_k4_slice_i.py:34-98`, `tests/test_b063_rescue_events.py:375` (comment, low-signal), `tests/test_lifecycle.py` (whole file, ~15 hits incl. `quarantine_size_mismatch`), `tests/test_no_new_scar_state.py` (whole file) — **the mission-doc-named ratchet baseline test itself**, `tests/test_k1_review_fixes.py:4,140-157` (`QUARANTINE_SENTINEL`, `is_quarantine_placeholder`), `tests/test_lifecycle_pending_exit_guard.py` (whole file), `tests/test_phase10c_dt_seam_followup.py:561-568` (`query_chain_only_quarantine_rows`), `tests/test_b066_quarantine_sentinel_ids.py` (whole file, mission-doc-named), `tests/test_run_mode_failure_surfaces.py:896`, `tests/test_reconcile_chain_mirror.py` (whole file, ~15 hits — **the chain-mirror reconciler test mission doc names for T5's drain step**), `tests/test_dual_track_law_stubs.py:715-722`, `tests/test_db.py:1649,1878`, `tests/test_chain_shares_persist_synced.py` (whole file, ~15 hits), `tests/test_settle_positions_uses_enqueue_redeem.py:214`, `tests/test_a5_phase_equivalence.py` (whole file — central A5/A7-quarantine-split terminology test), `tests/integration/test_qkernel_spine_blockers_pr409.py:1191-1518`, `tests/test_k5_slice_k.py:98-122`, `tests/test_p3_price_channel_ingest_lift.py:436-449`
Verdict: B1/T5 for all — every test dies or is rewritten to assert the ChainOnlyFact/settled/voided replacement per T5's drain sequence.
Sub-note: `tests/test_heartbeat_supervisor.py:1843-1844` is already `@pytest.mark.skip(reason="M5 lifecycle/quarantine truth alignment owns unquarantine-after-open-orders proof.")` — B1/T5 but currently dead-skipped; T5 packet either un-skips+rewrites or formally deletes.

### T6-owned
- `tests/test_command_recovery.py:129` B1/T6 (`suppression_reason == "operator_quarantine_clear"`) — same file also B1/T5 (phase='quarantined' extensively) and B1/DIQ (see below); flag as multi-target file, coordinate packets before editing

### DIQ-packet
- `tests/test_decision_integrity_quarantine.py` (whole file) B1/DIQ — mission-doc-protected canonical unit test (cannot delete blind)
- `tests/test_command_recovery.py:1371,1454-1465,7644-7925` B1/DIQ — `quarantine=True` param, direct INSERT INTO decision_integrity_quarantine, tests for EDLI certificate quarantine refusal (`test_live_edli_entry_projection_refuses_quarantined_actionable_certificate` etc.) — this file converges T5+T6+DIQ, highest coordination risk in tests batch 1
- `tests/conftest.py:348` B1/DIQ — allowlist/registry entry for `scripts/quarantine_bad_forecast_decisions.py` ("pending_track_a6: standalone quarantine CLI; PR-E work in progress")

### T9-candidate
`tests/test_calibration_observation.py:27,150,164,366-375`, `tests/test_truth_authority_enum.py:45-191` (**central enum test — `TruthAuthority.QUARANTINED`, `requires_human_review`**), `tests/test_harvester_truth_writer_m1_settled_at.py:12,113-120` (M1 money-path antibody, high-value per test_topology.yaml), `tests/test_settlements_authority_trigger.py` (whole file — `QUARANTINED->VERIFIED` trigger), `tests/test_evaluate_calibration_transfer_oos.py:553`, `tests/analysis/test_settlement_guard_report.py:381`, `tests/test_authority_strict_learning.py:11`, `tests/test_obs_v2_writer.py:136`, `tests/test_fit_bias_scale.py:385-518`, `tests/test_settlement_outcomes_ds1_schema.py:48`, `tests/test_ingest_provenance_contract.py:65-67`
Verdict: B1/T9-candidate for all — survives as rewritten assertions once T9-candidate packet defined (likely `DISPUTED`, per `scripts/backfill_settlement_outcome_type.py` precedent).

### ensemble_snapshot_provenance rename (B2)
- `tests/test_phase4_platt_v2.py:149` B2 (comment)
- `tests/test_opendata_data_version_producer_subset_gate.py` (whole file) B2 — `DataVersionQuarantinedError`, "_v1 quarantine drift"
- `tests/test_phase5_fixpack.py:536-698` B2 (`DataVersionQuarantinedError` assertions) — lines 17-307 are the separate FORECAST-INGEST-BOUNDARY-candidate (see below)

### FORECAST-INGEST-BOUNDARY-candidate (B2)
- `tests/test_low_fsr_boundary_ambiguity_majority_fix.py` (whole file) B2
- `tests/test_phase4_5_extractor.py:209-227` B2
- `tests/test_phase5_fixpack.py:17-307` B2 (boundary-quarantine portion, distinct from the DataVersionQuarantinedError portion in same file)

### source-contract block rename (B2)
- `tests/test_market_scanner_provenance.py` (whole file, ~130 hits) B2 — the definitive test file for the rename; `SOURCE_CONTRACT_QUARANTINE_PATH_ENV`, `apply_source_quarantines`, `is_city_source_quarantined`, `release_source_contract_quarantine`, `active_source_contract_quarantines` all rename together

### LOOP-GUARD-candidate (B2)
- `tests/test_loop_guard.py` (whole file) B2
- `tests/AGENTS.md:62` B2 (doc table entry describing test_loop_guard.py, rename alongside)

### New concept found this batch — STRATEGY-LOCALIZATION-candidate (B2)
- `tests/test_riskguard.py:2211-2445` (see above) B2

### STRATEGY-KEY-FILTER word-only (B4)
- `tests/test_ws_poll_reaction.py:24,277-281` B4

### Historical / low-signal (B4)
- `tests/conftest.py:295,338,367,460` B4 — F26 cleanup (2026-05-18) already removed STALE_REWRITE+QUARANTINED test classes; purely retrospective comments
- `tests/test_insufficient_prior_conservative_sigma.py:109` B4 (comment, "old gate set auto-quarantines")

**tests batch 1 bucket counts**: B1=37 files, B2=9 files, B3=0, B4=5 files (some files carry 2 buckets — see multi-target flags above)

---

## tests/ batch 2 (51 files)

B1=27 (T1-T6 diseased-mechanism consumers), B2=3 (DataVersionQuarantinedError→DataVersionRejected; check_quarantine_timeouts→check_chainonly_timeouts), B3=2 (T6 control-plane ack helpers, dead once T6 lands), B4=21 (text/comment/schema-literal only).

```
tests/test_phase10a_hygiene.py:228 B3 T6 — remove query_chain_only_quarantine_rows
tests/test_phase10a_hygiene.py:313 B3 T6 — quarantine_clear acknowledgment token deletion
tests/test_check_live_restart_preflight.py:1594 B1/T3 — certificate exclusion logic
tests/test_check_live_restart_preflight.py:2092 B1/T4,T5 — route quarantine to redecision
tests/test_dedup_gate_token.py:225 B1/T5 — quarantine state gate behavior
tests/test_chain_state_vocabulary_antibody.py:126 B1/T4 — entry_authority_quarantined removed
tests/test_decision_integrity_quarantine_extended.py:38 B1/DIQ — entire tagging system (mission-doc-protected)
tests/test_entry_gate_authority_promotion.py:77 B1/T2 — has_quarantine gate
tests/test_harvester_metric_identity.py:774 B1/T9-candidate — TruthAuthority.QUARANTINED
tests/test_settlement_pairing_contract.py:80 B4 — comment only
tests/test_k4_slice_j.py:1 B4 — file header only
tests/test_live_release_gate.py:390 B1/T2 — quarantine-proof behavior
tests/test_canonical_data_versions_namespace.py:121 B2 — DataVersionRejected (ensemble_snapshot_provenance rename)
tests/test_live_safety_invariants.py:77 B1/T9-candidate — quarantine filtering exclusion
tests/test_cross_module_invariants.py:50 B1/T2 — _has_quarantined_positions
tests/test_build_evidence_integration.py:184 B4 — schema CHECK text
tests/test_phase7a_metric_cutover.py:461 B2 — DataVersionRejected (ensemble_snapshot_provenance rename)
tests/test_boundary_rule_majority_threshold.py:83 B4 — text (FORECAST-INGEST-BOUNDARY-candidate adjacent)
tests/test_allowlist_migration_f26.py:8 B4 — cleanup status doc
tests/test_inv_family_exclusive_sizing.py:613 B1/T5 — quarantined position exposure
tests/state/test_inv_position_state_enum_closed.py:7 B4 — comment only
tests/test_canonical_projections.py:80 B1/T5 — explicit_quarantine phase derivation
tests/test_harvester_truth_writer_source_disagreement.py:5 B1/T9-candidate — SOURCE_DISAGREEMENT reason
tests/test_migration_position_events_occurred_at_iso_check.py:23 B4 — schema constraint text (T7-adjacent)
tests/state/test_position_lots_reconciliation_inv_lots.py:354 B4 — WHERE comment
tests/state/test_transition_phase_invariant.py:315 B1/T4 — entry_authority_quarantine transition
tests/state/test_position_events_check_constraint.py:10 B4 — schema CHECK text
tests/test_repair_review_required_no_venue_exposure.py:166 B4 — setup data
tests/state/test_inv_no_fake_position_in_trading_path.py:8 B4 — legacy mapping comment
tests/state/test_table_registry_coherence.py:47 B4 — registry comment
tests/test_phase4_rebuild.py:269 B4 — tag exclusion metaphor
tests/state/test_inv_size_mismatch_canonical_phase.py:7 B4 — legacy state comment
tests/state/test_inv_review_required_durable.py:34 B1/T4 — quarantined projection emission
tests/state/test_inv_part3_followups.py:24 B2 — check_chainonly_timeouts (ChainOnlyFact rename)
tests/state/test_inv_f2_typed_event_timestamps.py:59 B1/T4 — quarantined_at timestamp
tests/state/test_position_current_bridge_invariant.py:90 B4 — phase list text
tests/state/test_inv_chain_only_review_lifecycle.py:55 B1/T6 — chain_only_quarantined suppression
tests/state/test_inv_venue_position_observed_event.py:182 B4 — event enum data
tests/test_settlements_verified_row_integrity.py:117 B1/T9-candidate — QUARANTINED authority acceptance
tests/test_provenance_5_projections.py:970 B4 — data literal
tests/test_settlement_unit_trigger_deployment_2026_06_03.py:91 B4 — schema CHECK text
tests/test_source_temporal_policy.py:113 B4 — policy metadata
tests/test_lifecycle_terminal_predicate.py:78 B1/T5 — non-terminal anchor dies at phase removal
tests/test_phase5b_low_historical_lane.py:12 B4 — FORECAST-INGEST-BOUNDARY-candidate adjacent text
tests/test_risk_allocator.py:688 B4 — test data
tests/test_harvester_truth_writer_null_bin.py:119 B1/T9-candidate — null_bin → QUARANTINED authority
tests/test_single_writer_per_state_mechanism.py:14 B4 — quarantine-coercion comment
tests/test_edge_observation.py:25 B4 — STRATEGY-KEY-FILTER word-only comment
tests/test_chain_reconciliation_corrected_guard.py:372 B1/T5 — QUARANTINE branch (survives as ChainOnlyFact)
tests/test_attribution_drift.py:31 B4 — insufficient-signal comment
tests/test_openmeteo_ecmwf_ifs9_bucket_transport.py:729 B4 — Amsterdam mismatch comment
tests/test_k1_slice_d.py:145 B1/T5 — quarantine sentinel/placeholder logic
tests/test_bayes_precision_fusion_no_leak_history_join.py:15 B1/T9-candidate — QUARANTINED authority exclusion
```

Note: two files (`test_check_live_restart_preflight.py`, `test_phase10a_hygiene.py`) carry multiple T-tags — same coordination-risk pattern as `test_command_recovery.py` in batch 1.

---

## tests/ batch 3 (50 files)

B1=29 (T1/T2/T4/T5/T6 diseased-mechanism consumers), B2=9 (reshape-and-rename, survives), B3=0, B4=11 (text-only), B5=0.

```
tests/test_phase4_parity_gate.py:1 B2 — ensemble_snapshot_provenance validation, rename is_quarantined()
tests/test_phase4_ingest.py:135 B4 — docstring text only
tests/test_settlement_semantics_f_to_c.py:181 B1/T1 — authority=QUARANTINED assertions
tests/test_ingest_status_v2_rollup.py:110 B4 — field-name comment only
tests/test_dedup_reentry_blocking_canonical.py:35 B1/T5 — phase='quarantined' enum
tests/test_replacement_forecast_calibration_quarantine.py:7 B2 — rename validate_replacement_forecast_calibration()
tests/test_emos_sole_calibrator.py:190 B4 — comment text only
tests/test_harvester_dr33_live_enablement.py:11 B1/T1 — authority=QUARANTINED provenance
tests/test_exchange_reconcile.py:2251 B1/T5,T6 — phase='quarantined' + chain_only_quarantined tokens
tests/test_rebuild_live_sentinel.py:499 B2 — mock is_quarantined()
tests/test_settlements_parity.py:59 B4 — CHECK schema text only
tests/test_cycle_runner_discovery_gate_authority.py:7 B1/T2 — has_quarantine gate blocker (INV-27's own test)
tests/scripts/test_audit_day0_extreme_undercapture.py:219 B1/T1 — QUARANTINED settlements excluded
tests/test_p0_hardening.py:787 B1/T2 — _has_quarantined_positions() (INV-27's own test)
tests/test_architecture_contracts.py:343 B1/T5,T6 — operator_quarantine_clear tokens + chain_quarantined builder
tests/money_path/test_edli_market_substrate_warm_cycle.py:156 B1/T5 — quarantined phase scope
tests/test_pe_reconstruction_relationships.py:44 B1/T1 — authority=QUARANTINED + quarantine_reason
tests/test_k6_slice_n.py:60 B1/T4 — orphan order quarantine
tests/scripts/test_backfill_settlement_outcome_type.py:65 B2/T9-candidate — QUARANTINED→SettlementOutcome.DISPUTED (confirms DISPUTED precedent)
tests/test_settlement_axes_a8_a9.py:168 B1/T1 — settlement authority=QUARANTINED
tests/test_truth_surface_health.py:1500 B1/T1 — QUARANTINED excluded from training
tests/test_harvester_m1_settled_at_invariant.py:10 B1/T1 — authority=QUARANTINED M1 guard
tests/scripts/test_drain_settlement_quarantine.py:3 B1/T9-candidate — pairs with scripts/drain_settlement_quarantine.py drain mechanism
tests/test_replacement_forecast_emos_identity.py:5 B4 — docstring text only
tests/test_day0_obs_fastlane_optionbc.py:110 B2 — quarantined_implausible counter
tests/test_day0_first_principles_antibodies.py:479 B2 — quarantined_implausible + spike logic
tests/test_day0_hard_fact_exit.py:25 B2 — quarantined position + spike logic
tests/test_cross_module_relationships.py:187 B1/T5 — terminal phases enum
tests/test_tigge_boundary_ambiguity_majority_fix.py:13 B2 — FORECAST-INGEST-BOUNDARY-candidate rename
tests/execution/test_resting_absorbed_resolver.py:188 B4 — comment text only
tests/fixtures/before_p2_sqlite_master.sql:49 B4 — schema CHECK literals (fixture snapshot, keep as historical schema record)
tests/test_runtime_guards.py:1713 B1/T2,T6 — chain_quarantine + has_quarantine gate + quarantine_clear
tests/test_observation_atom.py:101 B1/T1 — QUARANTINED validation error
tests/test_chain_reconciliation_occurred_at_iso.py:136 B1/T1 — mock is_quarantined=False
tests/test_p1_save_order.py:68 B1/T4,T5 — terminal phase enum + decision_integrity_quarantine
tests/AGENTS.md:62 B4 — doc table entry (also cited under LOOP-GUARD-candidate above)
tests/test_settlements_unique_migration.py:105 B1/T1 — settlement migration QUARANTINED
tests/execution/test_entry_actionable_certificate_guard.py:10 B1/T4 — decision_integrity_quarantine table + cert reject (invalid-certificate B2 target's test)
tests/execution/test_venue_sync_contract.py:1014 B1/T5 — quarantined phase redecision
tests/events/test_continuous_redecision_emit.py:1800 B1/T4,T5 — phase='quarantined' + entry_authority_quarantined
tests/test_materialization_queue_stale_lock.py:7 B2 — quarantined_stale_locks → orphaned_lock_archive (mission-doc-named B2 target #3)
tests/events/test_fill_bridge_settled_routing_quarantine.py:5 B1/T1 — QUARANTINED_BRIDGE_FAILURE disposition (mission-doc-named T1 test)
tests/test_auto_pause_entries.py:73 B1/T4 — mock check_quarantine_timeouts
tests/test_calibration_bins_canonical.py:825 B2 — R14 is_quarantined() → is_rejected()
tests/test_position_metric_resolver.py:84 B4 — comment text only
tests/test_decision_integrity_quarantine_crossdb.py:5 B1/DIQ — cross-DB decision_integrity_quarantine writes
tests/test_calibration_retrain.py:83 B4 — schema CHECK literal only
tests/engine/test_chain_sync_exit_wired_in_edli_mode.py:256 B4 — attribute setting only
tests/engine/test_shift_bin_reactor_integration.py:170 B1/T5 — chain-backed quarantine + entry_authority_quarantined
```

Cross-check: `tests/scripts/test_drain_settlement_quarantine.py` (B1/T9-candidate, repeatable) confirms `scripts/drain_settlement_quarantine.py` is NOT one-shot-historical — matches earlier T9-candidate flag. `tests/scripts/test_backfill_settlement_outcome_type.py` confirms the `DISPUTED` rename precedent independently.

---

## FINAL TOTALS (all 5 sub-scopes: architecture/bindings/maintenance_worker mine + scripts mine + tests batch1 mine + tests batch2 + tests batch3)

| Bucket | architecture/bindings/mw | scripts/ | tests batch1 | tests batch2 | tests batch3 | **Total** |
|---|---|---|---|---|---|---|
| B1 DIES-WITH-DISEASE | 19 | 19 | 37 | 27 | 29 | **131** |
| B2 RESHAPE-AND-RENAME | 17 | 16 | 9 | 3 | 9 | **54** |
| B3 DEAD-CODE | 0 | 0 | 0 | 2 | 0 | **2** |
| B4 TEXT-ONLY | 5 | 4 | 5 | 21 | 11 | **46** |
| B5-HISTORICAL | 4 | 7 | 0 | 0 | 0 | **11** |

Counts are per hit-group/file-cluster (granularity varies by section — see inline detail for line-level breakdown), not raw grep-line counts. Coverage (file-level) verified separately below.

## Named T-target tally (cuts across buckets)
- T1 (EDLI bridge): ~15 files/hit-groups
- T2 (global gate / INV-27): ~10
- T3 (RiskGuard pass-with-exclusion): 2 (test file + its own T3-tagged block)
- T4 (fill_tracker quarantine minting / redecision): ~12
- T5 (lifecycle phase retirement): ~55 (largest single target by volume)
- T6 (control-plane ack tokens): ~15
- T7 (semantic contamination: artifact status, timestamp CHECK): ~6
- DIQ-packet (decision_integrity_quarantine side-table): ~15
- **T9-candidate (settlement/observation authority tier — NOT in mission doc)**: ~30, largest gap
- B2-only candidate families not tied to any T: ensemble_snapshot_provenance (~10), source-contract block (~10), FORECAST-INGEST-BOUNDARY (~8), LOOP-GUARD (~6), CALIB-candidate (1), STRATEGY-LOCALIZATION (1)
