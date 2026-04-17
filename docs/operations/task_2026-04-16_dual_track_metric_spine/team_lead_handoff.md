# Team-Lead Handoff (for post-compact main-thread)

**Written**: 2026-04-16 pre-compact. Main-thread context reached ~85%; user decided to compact at natural Phase 4 close.

## IMMEDIATE NEXT ACTIONS (in order)

1. Read `~/.claude/agent-team-methodology.md` (the reusable playbook — your operating manual).
2. Read THIS file.
3. Read `docs/operations/task_2026-04-16_dual_track_metric_spine/plan.md` (master packet plan).
4. Read the four final dumps in `docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/`:
   - `exec_bob_final_dump.md` (observation client, validate_members_unit)
   - `exec_carol_final_dump.md` (cities.json, rebuild/refit scaffolding)
   - `testeng_emma_final_dump.md` (R-letters catalog, antibody patterns)
   - `scout_dave_final_dump.md` (directory cheat-sheet, hazard taxonomy)
5. `git log --oneline -15` — confirm commit state.

## Branch + commit state

Branch: `data-improve`
Last 8 commits:

```
5d0e191 Phase 4C + 4D: rebuild_calibration_pairs_v2 + refit_platt_v2 + team-lead dumps
5c48847 Phase 4B: GRIB→v2 ingest pipeline implemented (task #53) + M1/M2a/M3/M5/M6/M7
dcf6ca3 Phase 4A: foundation commits — API signatures + schema + quarantine + tests
6e5de84 Phase 3: observation_client low_so_far + source registry collapse — Gate B open
16e7385 Phase 2: World DB v2 schema + DT#1 commit ordering + DT#4 chain three-state
b025883 Phase 1: MetricIdentity spine + FDR family scope split
943e74d governance: Phase 0 dual-track constitution + death-trap law
df12d9c governance: Phase 0b machine manifests for dual-track + death-trap law
```

Working tree has pre-existing dirt from other agents (`state/*`, `docs/to-do-list/*.xlsx`, `README.md`, `raw/`, `zeus_dual_track_refactor_package_v2_2026-04-16/`, `.claude/worktrees/data-rebuild`) — NOT your concern. Ignore.

## Gates status

- **Gate A** open (Phase 2): same-(city, target_date) carries high + low in v2 tables.
- **Gate B** open (Phase 3): evaluator no longer rejects low Day0 for cities with working providers.
- **Gate C** PENDING (Phase 4E): high canonical cutover parity. Blocked on Phase 4.5 extractor producing real JSON.

## Team state at compact

| Name | Role | Model | Compacts | Status | Action |
|---|---|---|---|---|---|
| critic-alice | adversarial critic | opus | 0 | **IDLE, retain** | resume after re-intro |
| exec-bob | executor | sonnet | 3 | idle, dump written, awaiting shutdown | shutdown_request + spawn fresh `exec-dan` |
| exec-carol | executor | sonnet | 2 | idle, dump written, awaiting shutdown | shutdown_request + spawn fresh `exec-emma` |
| testeng-emma | test-engineer | sonnet | 2 | idle, dump written, awaiting shutdown | shutdown_request + spawn fresh `testeng-grace` |
| scout-dave | scout | sonnet | 0-1 | idle, dump written, awaiting shutdown | shutdown_request + spawn fresh `scout-finn` |

Team name: `zeus-dual-track` (config at `~/.claude/teams/zeus-dual-track/config.json`).

## Compact protocol + phantom-work protocol (standing, applies to you too)

- You are main-thread; same rules apply. After compact: re-read `~/.claude/CLAUDE.md`, `~/.claude/agent-team-methodology.md`, root `AGENTS.md`, the plan.md.
- Disk-verify every claim before acting. `git status` / `git diff` / real pytest output.
- When you brief fresh teammates, they inherit the phantom-work + compact protocols from the methodology doc.

## Phase 4 summary

Phase 4 broke into 5 sub-phases + 1 new intermediary:

- **4A** (`dcf6ca3`): foundation — API signatures, schema `members_unit`, quarantine `peak_window` tag, INV-15 hotfix, 5 R-test files.
- **4B** (`5c48847`): `ingest_grib_to_snapshots.py` implementation (stub→real). Reads pre-extracted JSON; writes `ensemble_snapshots_v2` with all provenance. Fails loudly on empty input dir (no-op until 4.5 produces JSON).
- **4C + 4D** (`5d0e191`): `rebuild_calibration_pairs_v2.py` + `refit_platt_v2.py`. Pipeline architecturally complete; awaiting real data.
- **4.5** (NOT STARTED): GRIB→JSON extractor. Assigned to fresh `exec-dan` (NOT exec-bob — bob retiring).
- **4E** (PENDING): parity_diff.md + Gate C verification. Runs ONLY after 4.5 produces real JSON.

## Phase 4.5 brief (for exec-dan)

When you spawn `exec-dan`, give this brief:

```
You are exec-dan, FRESH sonnet executor for Zeus Phase 4.5 — GRIB→JSON
extractor for the mx2t6 (high track) raw archive.

## Mandatory reads (all)
1. ~/.claude/agent-team-methodology.md (your operating manual)
2. Repo root: AGENTS.md
3. docs/authority/zeus_current_architecture.md §13-§22 + zeus_dual_track_architecture.md §2/§5/§6/§8
4. Scoped AGENTS.md for src/data, src/contracts, scripts
5. docs/operations/task_2026-04-16_dual_track_metric_spine/plan.md
6. docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/phase4_plan.md
7. docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/exec_bob_final_dump.md ← TEACHES YOU what JSON shape to produce
8. /Users/leofitz/.openclaw/workspace-venus/51 source data/TIGGE_LOCAL_CALENDAR_DAY_DUAL_TRACK_PLAN.md
9. /Users/leofitz/.openclaw/workspace-venus/51 source data/TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md (§3, §4, §5, §6 especially)

## Your task
Create scripts/extract_tigge_mx2t6_localday_max.py (high track).
- Input: raw GRIB files at ~/.openclaw/workspace-venus/51 source data/raw/tigge_ecmwf_ens_regions_mx2t6/ (420 files, ~65GB, 2024-01-01..2025-09-24).
- Output: JSON at ~/.openclaw/workspace-venus/51 source data/raw/tigge_ecmwf_ens_mx2t6_localday_max/{city_slug}/{issue_date}/tigge_ecmwf_mx2t6_localday_max_target_{target_date}_lead_{lead_day}.json
- Schema MUST match what ingest_grib_to_snapshots.py expects (read exec_bob_final_dump.md for exact fields).
- Dynamic step horizon per city (west-coast day7 may need step_204).
- Boundary classification (inner / outside / boundary) with boundary_ambiguous flag.
- Causality framework: pure_forecast_valid check; status defaults 'OK' for high.
- Per-member mx2t6 local-calendar-day max aggregation.
- manifest_hash = SHA-256 of content-addressed fields.
- members_unit = 'degC' or 'degF' (NEVER 'K' — NO silent default).

## R-invariants (testeng-grace drafts)
- R-Q: extraction never produces members_unit='K'.
- R-R: dynamic step horizon respects west-coast day7 > step_180.
- R-S: boundary_ambiguous=true snapshots land with training_allowed=false.
- R-T: causality N/A slots are labeled, not dropped.
- R-U: manifest_hash stable across re-extractions of the same GRIB.

## Cross-coordinate
Read exec_bob_final_dump for JSON shape. Match EXACTLY. If you deviate, ingest_grib_to_snapshots.py will silently drop fields.

## Dependencies
cfgrib or pygrib library. If missing, add to requirements.txt (not pinned in repo today — exec-bob's dump may have notes).

## Disk-verify + compact protocol
Per methodology doc. Every edit: grep + git status + test output.

## Deliver
Report to team-lead when script + extraction smoke-test on 3 GRIB files passes. Do NOT run full 420-file extraction without critic-alice verdict.
```

## Zero-Data Golden Window (STANDING, issued 2026-04-17 by user)

Zeus is in a one-time zero-data refactor state:
- v2 tables zero rows (ensemble_snapshots_v2, calibration_pairs_v2, platt_models_v2).
- TIGGE GRIB archive still downloading (420 files observed is NOT terminal).
- legacy v1 tables have data but will not flip to v2 until all refactor phases + user approval.

### Standing rules until user lifts

1. **No real ingest** into any v2 table. JSON on disk OK; DB row writes NOT OK.
2. **Smoke tests = unit tests first, then ≤1 GRIB file for structural validation**. Output to `/tmp/`, not committed.
3. **No full-batch extraction runs**. Full 420+ runs require user approval + complete download + prior critic PASS.
4. **Structural fixes are free right now**. Any bug caught pre-ingest saves a data audit + rebuild later. Bias every decision toward "fix structurally now, ingest later."
5. **Legacy code is untrusted until audited** (separate standing rule, methodology doc): any existing file in `scripts/` / `src/**` / outside-repo reference requires critic provenance audit before reuse.

### Why golden-window thinking matters

Translation loss applies: "this is the moment to do structural fixes for free" has ~20% intent-survival. Encode as on-disk standing rules (this section + methodology doc section "legacy code untrusted until audited"), not as prose intent.

## Open scope rulings (issued during Phase 4)

| Question | Ruling | Rationale |
|---|---|---|
| data_version tag: peak_window or calendar_day? | calendar_day (phase0 authority) | Phase 0 §2.2 is authoritative; remediation plan supersedes peak_window. |
| members_unit DEFAULT 'degC' acceptable? | Yes + validate_members_unit guard | Structural defense > default avoidance when writer-seam guard exists. |
| Parity Gate C thresholds | Accept critic proposal | median |Δp|≤0.005, p99≤0.02, Brier reg ≤2%, |ΔA|+|ΔB|≤0.10/bucket. |
| INV-15 hotfix: fold or standalone? | Standalone precursor | 4A.0 separate commit boundary. |
| causality_status field on Day0ObservationContext? | DENIED — Phase 6 scope | INV-16 binding but implementation seam is Day0LowNowcastSignal split. |
| temperature_metric on Day0ObservationContext? | DENIED — runtime object ≠ DB row | INV-14 governs canonical tables, not runtime context. |
| GRIB→JSON extractor: fold into 4B or separate? | SEPARATE (Phase 4.5) | Not a bolt-on; ~500 lines, own R-invariants, fresh agent context. |
| Dead-table drop in 4A bundle | YES for 3 (promotion_registry, model_eval_point, model_eval_run); NO for model_skill (live writer in etl_historical_forecasts.py). | v2 migration transaction is cleanest moment. |
| Phase 4 commit strategy | 5 sub-phase commits minimum | Critic review quality; rollback granularity; post-mortem precision. |

## Open forward risks

- **Kelvin/degC drift**: pre-mortem identified as most-likely silent 2-week failure. validate_members_unit guard is the antibody; R-O + R-Q integration tests exercise it.
- **Gate C parity meaningful ONLY after Phase 4.5**: today 4E would "PASS" on zero data. Block Gate C declaration until 4.5 produces real JSON.
- **Phase 5 (low historical lane)** depends on the SAME extractor pattern + ingest pipeline. exec-bob's dump maps the symmetry; exec-carol's identifies where the low path diverges (causality N/A handling, boundary quarantine).
- **Phase 6 (Day0 split)** depends on Phase 5 data + causality_status being populated correctly by the extractor.
- **Scripts-tier CITY_STATIONS in backfill_wu_daily_all.py + oracle_snapshot_listener.py** — Phase C chore, not blocking any main phase.
- **db.py god-object split** — Phase 2 architect recommended; chore after Phase 4E.

## Phase roadmap post-compact

```
Phase 4.5 — exec-dan implements scripts/extract_tigge_mx2t6_localday_max.py
            critic-alice wide review
            commit
Phase 4E  — parity_diff_v2_vs_legacy.py + Gate C verification on real data
            critic review
            commit
Phase 5   — low historical lane: reuse extract script (mn2t6) + ingest (already low-ready) + rebuild (refit metric='low') + Gate D low-purity verification
Phase 6   — Day0 runtime split: Day0HighSignal + Day0LowNowcastSignal + router
Phase 7   — metric-aware rebuild full cutover + Platt refit for both tracks
Phase 8   — low shadow mode
Phase 9   — low limited activation (Gate F) — risk-critical; DT#2/DT#5/DT#7 implementations land here
```

## Fresh team composition recommendation

Keep critic-alice (opus, zero compacts, multi-phase memory). Replace 4 sonnets:

- `exec-dan` (sonnet, fresh) — Phase 4.5 GRIB→JSON extractor owner
- `exec-emma` (sonnet, fresh) — Phase 5 low lane + Phase 7 rebuild (parallel to dan for cross-validation)
- `testeng-grace` (sonnet, fresh) — R-Q..R-U for Phase 4.5/5/6/7
- `scout-finn` (sonnet, fresh) — inventory + hazard catalog for Phase 5 onward

Each fresh spawn's brief must include:
1. Mandatory reads (methodology + authority + plan + predecessor's final dump).
2. Role (persistent across multiple phases).
3. First small warmup task so discipline calibrates before high-stakes work.

## Do NOT

- Spawn any new work pre-compact (user instruction: stop).
- Send shutdown_request to the 4 sonnets yet (they stay idle; post-compact you shutdown + replace as one coordinated round).
- Commit anything else. Working tree has other-agent dirt; do not stage it.

## First actions post-compact (concrete)

1. Re-read the handoff above.
2. Read `~/.claude/agent-team-methodology.md`.
3. Read the 4 teammate final dumps (cite specific pages).
4. `git log --oneline -10` to confirm commit state matches this doc.
5. SendMessage critic-alice: "main-thread compacted and re-loaded. critic-alice, you stay. confirm authority chain still loaded and L0-L5 checklist intact."
6. SendMessage the 4 sonnets (one by one): shutdown_request with `request_id`.
7. Spawn fresh `exec-dan` + `exec-emma` + `testeng-grace` + `scout-finn` with briefs above.
8. Dispatch scout-finn + testeng-grace for Phase 4.5 pre-work (inventory of extraction scripts that already exist in `scripts/` but don't implement the full contract; R-Q..R-U drafting).
9. Dispatch exec-dan for Phase 4.5 main body.
10. critic-alice reviews when exec-dan reports.

## Status files on disk right now

- `docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/phase4_plan.md` — Phase 4 plan
- `docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/phase4a_critic_verdicts.md` — 4A review
- `docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/phase4b_critic_verdicts.md` — 4B review
- `docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/phase4cd_critic_verdicts.md` — 4C+4D review
- `docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/scout_inventory.md` — Phase 4 scout findings
- `docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/low_data_gap_audit.md` — scientist one-shot audit (TIGGE + DB state)
- `docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/phase4_architect_preread.md` — critic-alice's Phase 4 pre-read
- `docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/phase3_to_phase4_*_learnings.md` × 4 — between-phase value extraction
- `docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/*_final_dump.md` × 4 — retirement knowledge

Nothing is unsaved. Everything that matters is on disk or in the remote branch.
