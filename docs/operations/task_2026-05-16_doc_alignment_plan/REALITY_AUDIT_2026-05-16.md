# REALITY AUDIT — Required-Reading Docs (2026-05-16)

**Auditor:** critic (worktree `zeus-doc-alignment-2026-05-16`, branch `feat/doc-alignment-2026-05-16`, HEAD `ab0b7ea13f`)
**Scope:** Per task brief — every doc that auto-loads on Zeus session boot or for required pre-edit reads (15 files).
**Method:** Provenance audit per Fitz Constraint #4. Every cited file:line, symbol, command, PR#, SHA verified against current HEAD via `ls`/`git grep`/`git cat-file`/`gh pr view`.
**Categorization key:** POISON = would cause fresh-agent wrong action; STALE = out-of-date but agent reaches truth; OK = verified current.

---

## Pre-commitment predictions vs reality

| Prediction | Reality |
|---|---|
| AGENTS.md money-path likely 1-2 stale refs after execution refactors | All 7 paths exist; INV-37 + K1-split note matches `src/state/db.py:185` + `src/state/db_writer_lock.py`. **CLEAN.** |
| REVIEW.md Tier 0 extension — likely 1 missing/renamed | All 20+ Tier 0 paths exist including 5 newly-added (`maintenance_worker/core/*`, `topology_v_next/*`, `bindings/zeus/safety_overrides.yaml`). **CLEAN.** |
| module_manifest maturity — 1 mismatch despite WAVE 6 fix of 4 | 3 of 4 promoted modules verify cleanly; **execution rationale overstates** test-import count (71 claimed, 53–62 actual depending on glob). |
| INDEX.md 156 SHA-range — 1 wrong SHA in sample | Sampled 23/95 SHAs, **all 23 resolve via `git cat-file`**. CLEAN. Note: "156" in WAVE 6 critic = `wc -l` lines, not table rows (95). |
| fatal_misreads new entry likely OK | **CONFIRMED OK** — `maintenance_worker/core/archival_check_0.py` + `module_manifest.yaml` both exist. |

Empirical-test gap closed: pre-commitment expected ~5 issues; found 2 POISON, 1 STALE, 0 invariants drift in the read-only scan. Probes verified 100+ items.

---

## Findings table

| # | Doc | File:line | Claim | Reality | Category | Recommended action |
|---|---|---|---|---|---|---|
| 1 | `docs/operations/AGENTS.md` | line 74 | `Active archival rules: ARCHIVAL_RULES.md` (implies same-dir sibling at `docs/operations/ARCHIVAL_RULES.md`) | File at that path **does not exist**. Only copy is `docs/authority/ARCHIVAL_RULES.md`. | **POISON** | Either move/copy the file to `docs/operations/ARCHIVAL_RULES.md`, OR fix line 74 + line 233 to cite the actual path. Fresh agent following the citation will fail. |
| 2 | `docs/operations/AGENTS.md` | line 233 | `Active archival rules: see ARCHIVAL_RULES.md.` (second occurrence) | Same as above. | **POISON** (same as #1) | Single fix repairs both. |
| 3 | `architecture/module_manifest.yaml` | line ~145 (execution module) | `maturity: stable  # promoted 2026-05-16: 3 test_execution_*.py + 71 tests import src.execution` | `ls tests/test_execution_*.py` = **3 OK**. `grep -rl "src.execution" tests/` = **53–62** depending on import-pattern (`grep -rE "src\.execution|from execution|import execution"` returns 62 unique files; tight pattern `from src.execution`/`import src.execution` returns 53). **71 is over-stated.** | **STALE** | Update rationale to actual measured count, OR remove the count (the maturity verdict itself is sound — module is well-tested). |

(No findings against `AGENTS.md` root, `REVIEW.md`, `architecture/topology.yaml`, `architecture/core_claims.yaml`, `architecture/fatal_misreads.yaml`, `architecture/data_rebuild_topology.yaml`, `architecture/db_table_ownership.yaml`, `architecture/invariants.yaml`, `docs/operations/current_state.md`, `docs/operations/current_data_state.md`, `docs/operations/current_source_validity.md`, `docs/operations/INDEX.md`, `docs/lore/INDEX.json` + 3 lore cards.)

---

## What was empirically verified (per anti-silent-failure rule)

- **15/15 in-scope doc files exist** on HEAD.
- **AGENTS.md money-path**: 7/7 source files exist (`src/main.py`, `src/engine/cycle_runner.py`, `src/engine/evaluator.py`, `src/execution/executor.py`, `src/engine/monitor_refresh.py`, `src/execution/exit_triggers.py`, `src/execution/harvester.py`).
- **AGENTS.md K1 DB-split claim**: `src/state/table_registry.py` exists (14.6K, defines `WORLD_CLASS`/`FORECAST_CLASS` enums, `assert_db_matches_registry()`); `src/state/db.py:185` has `def get_forecasts_connection_with_world` (with ATTACH+SAVEPOINT docstring confirming AGENTS.md description).
- **AGENTS.md Settlement section**: `src/contracts/settlement_semantics.py:97 def assert_settlement_value()` exists; resolver address `0x69c47De9D4D3Dad79590d61b9e05918E03775f24` appears literally in fatal_misreads.yaml and is referenced by harvester (matches AGENTS.md prose).
- **AGENTS.md LifecyclePhase enum**: All 10 states exist in `src/state/lifecycle_manager.py:9-19` with exact spelling (PENDING_ENTRY, ACTIVE, DAY0_WINDOW, PENDING_EXIT, ECONOMICALLY_CLOSED, SETTLED, VOIDED, QUARANTINED, ADMIN_CLOSED, UNKNOWN).
- **AGENTS.md topology_doctor commands**: All cited flags exist in `scripts/topology_doctor_cli.py` (verified directly: `--navigation`, `--planning-lock`, `--task-boot-profiles`, `--map-maintenance`, `--code-review-graph-status`, `--history-lore`, `--fatal-misreads`, `--route-card-only`, `--preflight`, `--strict-health`). The `digest` subcommand also exists with `--task --files --intent --write-intent` flags. Note: `topology_doctor.py` itself imports `topology_doctor_cli` for actual argparse wiring; flags are correctly described.
- **AGENTS.md secondary doc paths**: 17/17 architecture YAMLs cited exist. 6/6 reference docs in §3 exist. `.agents/skills/zeus-ai-handoff/SKILL.md`, `.claude/hooks/pre-merge-contamination-check.sh`, `docs/methodology/adversarial_debate_for_project_evaluation.md`, `.claude/skills/zeus-methodology-bootstrap/SKILL.md`, `docs/authority/zeus_current_architecture.md`, `docs/authority/zeus_current_delivery.md`, `docs/to-do-list/known_gaps.md` all exist.
- **REVIEW.md Tier 0**: 100% of cited paths exist — `src/execution/*` (8 files), `src/contracts/{settlement_semantics,execution_price,venue_submission_envelope,fx_classification}.py`, `src/state/*` (8 files), `src/riskguard/`, `src/control/`, `src/supervisor_api/`, `src/main.py`, `src/engine/{cycle_runner,evaluator,monitor_refresh}.py`, `architecture/2026_04_02_architecture_kernel.sql`, `maintenance_worker/core/{validator,apply_publisher}.py`, `scripts/topology_v_next/{admission_engine,hard_safety_kernel}.py`, `bindings/zeus/safety_overrides.yaml`.
- **architecture/topology.yaml authority_note** (WAVE 3 rewrite, lines 5-10): Text now correctly says topology serves as "active nav authority for topology_doctor.py (not read-only registry)" — consistent with topology_doctor_cli.py loading it (confirmed via TOPOLOGY_PATH constant in topology_doctor.py:34). **Drift fully closed.**
- **architecture/fatal_misreads.yaml**: 9 entries total. New entry `artifact_authority_status_missing_gate` (severity: high) cites `maintenance_worker/core/archival_check_0.py` and `architecture/module_manifest.yaml` — both exist. Schema valid (all required fields present: id, severity, false_equivalence, correction, proof_files, invalidation_condition, tests, task_classes). Type-encoded HKO entry's `type_encoded_at: src/contracts/settlement_semantics.py:HKO_Truncation` verified — class `HKO_Truncation` exists at `src/contracts/settlement_semantics.py:247`. Tests `test_hko_policy_required_for_hong_kong` + `test_hko_policy_invalid_for_non_hong_kong` exist at `tests/test_settlement_semantics.py:31` and adjacent.
- **architecture/core_claims.yaml**: 10/10 sampled proof_target files exist (`src/types/temperature.py`, `src/contracts/{execution_price,alpha_decision,vig_treatment,settlement_semantics}.py`, `src/strategy/{market_fusion,oracle_penalty,oracle_estimator}.py`); 3/3 test files exist (`tests/test_temperature.py`, `tests/test_no_bare_float_seams.py`, `tests/test_alpha_target_coherence.py`).
- **architecture/db_table_ownership.yaml**: 83 table entries; loader at `src/state/table_registry.py` (line 121 `_load_registry()`) verified.
- **architecture/data_rebuild_topology.yaml**: All `protects` paths exist (sampled: `src/calibration/decision_group.py`, `src/calibration/store.py`, `src/types/market.py`, `scripts/refit_platt.py`, `src/execution/harvester.py`, etc.); SIDECAR-1 source_plan ARCHIVED note remains documented; K1-split addendum on `ensemble_snapshots_v2` matches `architecture/db_table_ownership.yaml`.
- **architecture/invariants.yaml**: 38 invariant entries (INV-01 → INV-37 + INV-Harvester-Liveness; numerical gap at INV-12/13/21 ordering noted but per scope **READ ONLY** — flagged for tracking, not for repair).
- **docs/operations/current_state.md**: HEAD reference `a924766c8a` verified (`gh pr view 121` confirms merge SHA `a924766c8ac299fe702a85edcedeac395d12c283`). All 4 PR refs (#114, #116, #117, #119, #120, #121) confirmed via `gh pr view`. Active packet path exists.
- **docs/operations/current_data_state.md**: K1 split addendum 2026-05-16 matches `architecture/db_table_ownership.yaml` (forecast-class tables list aligns); structural-limit caution on LOW markets cross-references `fatal_misreads.yaml::polymarket_low_market_history_starts_2026_04_15` which exists at line 170.
- **docs/operations/current_source_validity.md**: Paris LFPG→LFPB conversion 2026-05-03 evidence section cites real scripts (`scripts/watch_source_contract.py`, `scripts/backfill_wu_daily_all.py`, `scripts/rebuild_settlements.py`, `scripts/rebuild_calibration_pairs_v2.py`, `scripts/refit_platt_v2.py`) and `architecture/paris_station_resolution_2026-05-01.yaml`. Spot-confirmed scripts exist; authority YAML existence not re-checked (out of WAVE 7 scope per `paris yaml parse error` deferral).
- **docs/operations/INDEX.md**: 95 markdown table rows; 23/23 sampled anchor SHAs resolve (`2cb1c421`, `650136bd`, `dfb1451af6`, `ff5c09f283`, `d99bbf9500`, `fe8d0d79a5`, `9cc3d5fefd`, `2f436a6b21`, `eba80d2b9d`, `e0b4c00276`, `595be616e3`, `89ac23f7f1`, `b9b93da6ee`, `7e727b1f49`, `1d9859d90a`, `d7db6ba2ef`, `f6a796b966`, `2e00271cee`, `3f73872236`, `16690c36f6`, `1f2158a14e`, `95617f5300`, `010d930941`). SHA-range column format `<first>..<last>` is correct git-range syntax.
- **docs/lore/INDEX.json + 3 lore cards**: All 3 `extracted_from` paths resolve to `docs/operations/task_2026-05-15_p8_authority_drift_3_blocking/POSTMORTEM.md` (file exists); all 3 card files exist at `docs/lore/topology/*.md`.

Total items independently verified: **120+**.

---

## What's Missing / Open Questions (unscored)

- WAVE 7 deferrals carried forward per task brief, not reflagged:
  - `architecture/paris_station_resolution_2026-05-01.yaml` parse error (not re-tested).
  - `architecture/invariants.yaml` INV-12/13/21 numbering gap (READ ONLY scope; not flagged as drift).
- Topology_doctor `digest` subcommand exists; AGENTS.md uses both `--navigation` and `digest` forms. Both work but new agents may not realize they're the same surface. Not a finding — just an ergonomic note.
- `WAVE_6_CRITIC_C.md` P7 documents "INDEX.md 156-row count" as `wc -l` lines, not table rows (95). Task brief inherited the 156 number; clarified for any auditor reviewing this report.

---

## Verdict Justification

The wave-by-wave doc alignment work is **substantively complete**. Three findings:

- 2 POISON entries are the **same root cause** (one missing file or two stale citations to it). One commit fixes both.
- 1 STALE entry is a wrong test-count (71 vs ~53–62) embedded in a module_manifest comment — does not change the maturity verdict, fresh agent ignores or moves past it.

No invariants drift in READ-ONLY scope. No `architecture/topology.yaml` authority_note regression (WAVE 3 rewrite holds). No INDEX.md SHA rot (23/23 sample resolve). No path/symbol rot in money-path, REVIEW Tier 0, core_claims, fatal_misreads. PR # citations in current_state.md verified live.

**Mode operated in: THOROUGH.** Did not escalate to ADVERSARIAL — escalation triggers were CRITICAL finding (0), 3+ MAJOR (0 / only 2 POISON, both rooted in same file), or systemic pattern (no — clean across all 15 docs). Realist check did not downgrade any finding: ARCHIVAL_RULES.md missing is correctly POISON (fresh agent reading docs/operations/AGENTS.md will hit a real dead-end), the 71-count is correctly STALE (downstream verdict unchanged).

### Verdict: **FIX_BEFORE_PR**

Single-commit fix to repair ARCHIVAL_RULES.md citation (POISON #1 + #2). Optional second-commit to update execution maturity rationale count (STALE #3) — can ship in PR or follow-up.

After ARCHIVAL_RULES fix: **CLEAN_FOR_PR**.

---

*Audit complete. POISON: 2 (same root). STALE: 1. OK: 12 docs + 120+ individual items.*
