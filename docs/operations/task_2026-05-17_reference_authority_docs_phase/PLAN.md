# Plan: TIER 0+1 Reference + Authority Doc Alignment (Post-PR-#124) — v2 (critic-amended)

**Phase target:** dedicated remediation of reference + authority docs **not** covered by PR #124's reality audit.
**Status:** v2 = v1 + 5 critic-REVISE amendments + minors. Awaiting orchestrator dispatch on WAVE 0.
**Author:** planner (orchestrator), 2026-05-16. Scheduled WAVE 0 dispatch: 2026-05-17.
**Branch:** `feat/ref-authority-docs-2026-05-17` (fresh from origin/main post-PR-#124 merge).
**Worktree:** `/Users/leofitz/.openclaw/workspace-venus/zeus-ref-authority-docs-2026-05-17`.
**v1 fate:** orphaned on deleted `zeus-doc-alignment-2026-05-16` worktree before commit; v2 reconstructs from transcript + critic verdict + fresh FCI4.

## Change log v1 → v2

- **A1 (CRITICAL)**: replaced phantom `python -m maintenance_worker.cli.entry validate` with grep-verified `dry-run` invocation; WAVE 0 SCOUT 0B records exact per-binding loader-test commands.
- **A2 (CRITICAL)**: every `topology_doctor` invocation now `PYTHONPATH=. python -m scripts.topology_doctor ...` (module form; verified to import cleanly).
- **A3 (MAJOR)**: `config/reality_contracts/{data,economic,execution,protocol}.yaml` moved from TIER 3 deferred to TIER 0B in-scope (loader `src/contracts/reality_contracts_loader.py` exists today; verified).
- **A4 (MAJOR)**: all TIER 0A/0B/0C `Lines` columns replaced with actual `wc -l` measurements; §11 effort budget re-derived from real totals.
- **A5 (MAJOR)**: TIER 1 AGENTS.md count reconciled to 40 (= 46 total − 5 observation subdirs − 1 archive); every path explicitly enumerated; WAVE 3 batch sizing re-derived for 40 docs.
- **m-a (minor)**: §7 risk register adds (a) 3-PR auto-reviewer fatigue, (b) multi-parallel-session collision, (c) worker-self-review prohibition rows.
- **m-b (minor)**: every WAVE critic dispatch capped at "brief ≤30 lines" per `feedback_long_opus_revision_briefs_timeout`.
- **m-c (minor)**: WAVE 1 explicitly notes "critic ≠ executor; dispatch fresh critic subagent, not the editor."
- **m-d (minor)**: §3 TIER 1 footnote cites `workspace_map.md` extant at repo root (FCI4-verified).

---

## 1. Context — what PR #124 already shipped

PR #124 (merged HEAD `9fd3ac46c5`, ~287 doc files) completed: 6 semantic-drift fixes in `architecture/{topology,core_claims,data_rebuild_topology,module_manifest,fatal_misreads}.yaml`; ARCHIVAL_RULES bare-path POISON repair in `AGENTS.md` + `docs/operations/AGENTS.md`; REVIEW.md Tier 0 surface additions (5 paths); `docs/operations/INDEX.md` SHA-range column rewrite; `docs/operations/current_{state,data_state,source_validity}.md` refresh; `architecture/module_manifest.yaml` maturity rationale rewrites; 28 + 69 May-batch archive migrations.

PR-#124 WAVE 6 reality audit verified **15 boot-loaded docs** with **120+ items grep-checked**; final density was **2 POISON + 1 STALE → <2% rot**. That audit covered the auto-load-every-session subset only.

## 2. Phase scope — what's left and why this phase is necessary

After PR #124 there are **90 untouched docs** in load-bearing locations. These are the docs agents drill into on TASK-specific reads — not loaded every session, but accuracy is critical when loaded (governance YAMLs, runtime-coupled YAMLs, per-module AGENTS.md).

**Why this phase is "extremely important"** (operator directive): a stale governance YAML produces a fresh-agent wrong-action with zero warning; a stale runtime-coupled YAML can cause real backward-incompat breakage on the next loader run. PR #124 deliberately scoped TIGHT (boot docs only); this phase closes the next-largest blast radius.

### 2.1 Scope decision — TIER 0+1 ONLY this phase, TIER 2+3 deferred

- **THIS PHASE: TIER 0 (~18 docs) + TIER 1 (40 docs)** — see §3.
- **DEFERRED to follow-up phase (stub at §13):** TIER 2 (~23 docs) + TIER 3 (~15 docs after reality_contracts promotion).

TIER 2/3 deferral is **scope decision, not omission**. §13 names the docs, predicts effort, queues a successor task dir.

---

## 3. Tier inventory (untouched-by-PR-#124 subset, FCI4-verified `wc -l`)

### TIER 0A — Governance-locked YAMLs (8 docs / 1,895 LOC)

Require an INV-27-style amendment block in the same commit; edit without amendment = governance violation.

| File | LOC (actual) | Why locked |
|---|---|---|
| `architecture/invariants.yaml` | 748 | INV-01..INV-37 + INV-Harvester-Liveness — change-control regime |
| `architecture/db_table_ownership.yaml` | 731 | 83 table entries, loader-backed (`src/state/table_registry.py`) |
| `architecture/negative_constraints.yaml` | 168 | Hard "DO NOT" rules — must list source-of-rule |
| `architecture/kernel_manifest.yaml` | 118 | Constitutional kernel manifest |
| `architecture/maturity_model.yaml` | 48 | Defines stable/maturing/experimental criteria |
| `architecture/runtime_modes.yaml` | 33 | Mode definitions read by main + riskguard |
| `architecture/runtime_posture.yaml` | 31 | Live/paper/dry posture rules |
| `architecture/world_schema_version.yaml` | 18 | Schema versioning — loader hits it |

### TIER 0B — Runtime-coupled YAMLs (10 docs / 5,669 LOC) — A3 promotion

Loader exists; backward-compat check required after every edit.

| File | LOC (actual) | Loader / consumer |
|---|---|---|
| `architecture/source_rationale.yaml` | 2,085 | Source-routing authority — consumed by ingest |
| `architecture/test_topology.yaml` | 1,390 | Loaded by `topology_doctor` + per-task test-routing |
| `architecture/script_manifest.yaml` | 929 | Loaded by script-routing checks |
| `architecture/task_boot_profiles.yaml` | 405 | Loaded by `topology_doctor --task-boot-profiles` |
| `architecture/topology_v_next_binding.yaml` | 361 | Topology v_next binding contract |
| `bindings/zeus/config.yaml` | 111 | Zeus binding consumed by maintenance_worker + main |
| `config/reality_contracts/data.yaml` | 205 | Loaded by `src/contracts/reality_contracts_loader.py` |
| `config/reality_contracts/protocol.yaml` | 89 | Loaded by `src/contracts/reality_contracts_loader.py` |
| `config/reality_contracts/economic.yaml` | 48 | Loaded by `src/contracts/reality_contracts_loader.py` |
| `config/reality_contracts/execution.yaml` | 46 | Loaded by `src/contracts/reality_contracts_loader.py` |

### TIER 0C — Authority MDs (4 docs / 1,485 LOC)

Structural truth, no loader, standard FCI4 audit.

| File | LOC (actual) |
|---|---|
| `docs/authority/zeus_change_control_constitution.md` | 646 |
| `docs/authority/zeus_current_architecture.md` | 440 |
| `docs/authority/zeus_current_delivery.md` | 352 |
| `docs/authority/AGENTS.md` | 47 |

### TIER 1 — Nav / Per-subdir AGENTS.md (40 docs) — A5 reconciliation

`git ls-files '**/AGENTS.md' '*AGENTS.md'` = 46 total. Minus 5 deferred observation-subdir AGENTS.md (`docs/operations/{attribution_drift,calibration_observation,edge_observation,learning_loop_observation,ws_poll_reaction}/AGENTS.md` → TIER 3) and 1 archived (`docs/operations/archive/2026-Q2/task_2026-04-16_dual_track_metric_spine/AGENTS.md`) = **40 in-scope TIER 1 paths**:

Root + meta (3): `AGENTS.md`, `.agents/skills/AGENTS.md`, `.github/workflows/AGENTS.md`
Architecture (4): `architecture/AGENTS.md`, `architecture/{ast_rules,packet_templates,self_check}/AGENTS.md`
Config (2): `config/AGENTS.md`, `config/reality_contracts/AGENTS.md`
Docs (8): `docs/{AGENTS,artifacts/AGENTS,authority/AGENTS,reference/AGENTS,reference/modules/AGENTS,reports/AGENTS,review/AGENTS,runbooks/AGENTS}.md` + `docs/to-do-list/AGENTS.md` + `docs/operations/AGENTS.md` (already touched by PR #124 — INCLUDE for cross-ref check only, no edit unless drift surfaces)
Source (17): `src/AGENTS.md` + `src/{analysis,calibration,contracts,control,data,engine,execution,ingest,observability,risk_allocator,riskguard,signal,state,strategy,supervisor_api,types,venue}/AGENTS.md`
Tests + scripts (3): `tests/AGENTS.md`, `tests/contracts/AGENTS.md`, `scripts/AGENTS.md`

Footnote: cross-reference triangle is `AGENTS.md` ↔ `docs/operations/AGENTS.md` ↔ `workspace_map.md` (verified extant at repo root, 2026-05-16).

### TIER 2 — DEFERRED (named here, not planned this phase)

23 docs: `architecture/{admission_severity,capabilities,change_receipt_schema,city_truth_contract,code_idioms,code_review_graph_protocol,context_budget,context_pack_profiles,data_sources_registry_2026_05_08,ecmwf_opendata_tigge_equivalence_2026_05_06,history_lore,improvement_backlog,lifecycle_grammar,map_maintenance,naming_conventions,reference_replacement,reversibility,settlement_dual_source_truth_2026_05_07,strategy_profile_registry,worktree_merge_protocol,zeus_grid_resolution_authority_2026_05_07,zones}.yaml` + `architecture/{calibration_transfer_oos_design_2026-05-05,math_defects_2_3_2_4_3_1_design_2026-05-05,agent_pr_discipline_2026_05_09}.md`.

### TIER 3 — DEFERRED

15 docs: `docs/methodology/adversarial_debate_for_project_evaluation.md` (826 LOC), `docs/review/{code_review,review_scope_map}.md`, `docs/to-do-list/{known_gaps,known_gaps_archive}.md`, `docs/operations/{LIVE_LAUNCH_HANDOFF,LIVE_RESTART_2026_05_07,live_rescue_ledger_2026-05-04,packet_scope_protocol,POLICY,PLIST_UPDATE_FOR_RELOCK,tigge_daemon_integration,CLOUD_EXTRACT_PATCH_2026_05_07,UNMATCHED_GAMMA_CITIES_2026_05_07,known_gaps,activation/UNLOCK_CRITERIA}.md` + 5 observation `docs/operations/*/AGENTS.md` + `architecture/{ast_rules/forbidden_patterns,packet_templates/{bugfix,feature,refactor,schema}_packet,self_check/{authority_index,zero_context_entry},lifecycle_grammar}.md` + `architecture/2026_04_02_architecture_kernel.sql` (405 LOC) + `bindings/zeus/{install_metadata_template.json,launchd_plist.plist}`.

### Tier totals (FCI4-verified)

- TIER 0A: 8 docs / 1,895 LOC
- TIER 0B: 10 docs / 5,669 LOC
- TIER 0C: 4 docs / 1,485 LOC
- TIER 1: 40 docs / ~4,000 LOC est. (most <200 LOC each)
- **In-scope this phase: 62 docs / ~13,000 LOC**
- TIER 2 (deferred): 25 docs / ~8,500 LOC est.
- TIER 3 (deferred): 23 docs / ~6,500 LOC est.

---

## 4. PR shape — 3-PR split recommendation (operator confirms at WAVE 0 close)

Per memory `feedback_pr_unit_of_work_not_loc`: ship coherent units, not a single mega-PR. The three TIER 0 sub-classes are three natural coherent units.

| PR | Scope | Est. LOC | Critic tier | Gate |
|---|---|---|---|---|
| PR-A | TIER 0A (8 docs) + INV-27-style amendment block | 300-600 | opus | constitution-amendment review |
| PR-B | TIER 0B (10 docs incl. 4 reality_contracts) | 500-1,000 | opus | loader-tests + `PYTHONPATH=. python -m scripts.topology_doctor --strict-health` + `python -m maintenance_worker.cli.entry dry-run` |
| PR-C | TIER 0C (4 MDs) + TIER 1 (40 AGENTS.md) | 500-900 | opus-on-0C, sonnet-on-1 | FCI4 audit + cross-ref triangle |

**Alternative considered: single mega-PR.** Rejected because (a) governance amendment + runtime-coupled changes have different review concerns; (b) per memory `feedback_accumulate_changes_before_pr_open` paid auto-reviewers parallelize across PRs better than within one 1,500-LOC PR; (c) gate-fail recovery is cleaner when scoped.

**Operator decides at WAVE 0 close** (per memory `feedback_architecture_homework_before_operator_punt`): 3-PR split recommended; single-PR override acceptable if operator prefers one ship-cycle.

---

## 5. Wave plan

Wave count provisional pending WAVE 0 findings. Per PR #124 empirical signal (<2% rot on highest-traffic docs), if WAVE 0 finds <5 drifts per 10 docs, collapse WAVES 1–3 into a single remediation wave per PR-track.

### WAVE 0 — SCOUT (haiku, ~2-3 hr, parallel)

NOT hygiene (PR #124 did hygiene). Pure read-only reality scan.

Dispatch 3 parallel haiku scouts:

- **Scout 0A** — TIER 0A (8 docs): findings table (file → claim → reality → POISON/STALE/OK). For invariants.yaml: cross-check each INV-NN against runtime callers via `grep -rn "INV-NN"` in `src/`. Write `SCOUT_0A.md`.
- **Scout 0B** — TIER 0B (10 docs): identify loader for each (`src/contracts/reality_contracts_loader.py` known; others via `grep -rn "<filename>" scripts/ src/ maintenance_worker/`); confirm schema match; RECORD the exact loader-test command per doc (e.g. `python -m maintenance_worker.cli.entry dry-run --config bindings/zeus/config.yaml`, `PYTHONPATH=. python -m scripts.topology_doctor --task-boot-profiles --strict-health`). Write `SCOUT_0B.md` including a "Loader Command Table" used by WAVE 2.
- **Scout 0C+1** — TIER 0C (4 docs) + TIER 1 (40 AGENTS.md): findings table. For per-subdir AGENTS.md: cross-check every file:line citation against current HEAD; cross-check every symbol via `git grep`. Write `SCOUT_0C.md`.

Each scout writes to `docs/operations/task_2026-05-17_reference_authority_docs_phase/SCOUT_{0A,0B,0C}.md` using the schema from `REALITY_AUDIT_2026-05-16.md` (file | location | claim | reality | category | recommended action).

**Audit-of-audit** (per memory `feedback_audit_of_audit_antibody_recursive`, 50% scout self-error baseline): orchestrator dispatches a verification haiku per scout, sampling ≥25% of STALE/POISON verdicts via independent grep. Brief ≤30 lines.

**Gate**: 3 SCOUT_*.md persisted + 3 verification reports; ≥75% scout accuracy on POISON+STALE; orchestrator publishes `SCOUT_SUMMARY.md` with drift count by tier; operator confirms 3-PR split (or overrides).

### WAVE 1 — TIER 0A remediation (PR-A) (~3-4 hr, opus critic)

Architectural anchor: every TIER 0A edit ships with a constitutional amendment block in the same commit, modeled on INV-27 carve-out precedent. Authority doc: `docs/authority/zeus_change_control_constitution.md` (646 LOC, verified extant).

For each finding in SCOUT_0A.md:

1. Commit message header: `AMENDMENT: <doc>::<id> [REASON: <stale-reality | new-loader-requirement | misclassified>]`.
2. Edit doc to match reality. Preserve numeric ID gaps (do NOT re-number).
3. Run `python -m pytest tests/test_invariants.py` (and any invariant-specific tests SCOUT surfaces).
4. If edit touches `invariants.yaml`: confirm INV-NN cited in `src/` still resolves to the changed semantics.

**OPUS CRITIC** (per memory `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi`). Critic = fresh subagent, **not** the editor (m-c). Brief ≤30 lines (m-b). Probes:
- Did each amendment block name the source-of-rule?
- Did any edit silently re-number an existing INV-NN/table-NN?
- Did any edit invalidate a runtime caller?
- Did the commit message cite a verification probe?

**Gate**: opus critic CLEAR_PASS or ACCEPT_WITH_FOLLOWUP; pytest invariants green; no re-numbering; PR-A ready to open.

### WAVE 2 — TIER 0B remediation (PR-B) (~5-6 hr, opus critic) — A1+A3 updated

For each finding in SCOUT_0B.md:

1. Edit doc to match current loader/schema.
2. Run the doc-specific loader-test recorded in SCOUT_0B's "Loader Command Table":
   - `bindings/zeus/config.yaml` → `python -m maintenance_worker.cli.entry dry-run --config bindings/zeus/config.yaml` (verified CLI surface; subcommands are `{run,dry-run,status,init}`)
   - `architecture/task_boot_profiles.yaml` → `PYTHONPATH=. python -m scripts.topology_doctor --task-boot-profiles --strict-health` (verified module form)
   - `architecture/test_topology.yaml` → `PYTHONPATH=. python -m scripts.topology_doctor --tests --strict-health`
   - `architecture/script_manifest.yaml` → `PYTHONPATH=. python -m scripts.topology_doctor --scripts --strict-health`
   - `architecture/source_rationale.yaml` → ingest-loader test (SCOUT 0B identifies exact path)
   - `architecture/topology_v_next_binding.yaml` → `PYTHONPATH=. python -m scripts.topology_doctor --strict-health`
   - `config/reality_contracts/{data,economic,execution,protocol}.yaml` → smoke test via `python -c "from src.contracts.reality_contracts_loader import *; ..."` (SCOUT 0B records exact loader-API call)
3. Backward-compat: every edit must preserve loader-public schema unless amendment explicitly says otherwise.

**OPUS CRITIC** (fresh subagent, brief ≤30 lines). Probes:
- Did each edit run its loader-test? Output captured?
- Any schema changes carry a migration note?
- `PYTHONPATH=. python -m scripts.topology_doctor --strict-health` output captured in commit?
- Did any edit break a maintenance_worker rule-load?

**Gate**: opus critic PASS; loader-tests green; `topology_doctor --strict-health` zero new failures vs baseline (baseline captured at §12); PR-B ready.

### WAVE 3 — TIER 0C + TIER 1 remediation (PR-C) (~5-6 hr, mixed critic) — A5 batch resizing

Dispatch ≤5 parallel sonnet executors (per memory `feedback_dispatch_brief_concise`), each handling 8-10 docs from TIER 1 + 1 TIER 0C MD where possible. Per-batch brief ≤30 lines (m-b).

For each finding in SCOUT_0C.md:

1. Edit per FCI4 (cite `file::symbol` not `file:line` per memory `feedback_zeus_plan_citations_rot_fast`).
2. Cross-reference triangle: `AGENTS.md` ↔ `docs/operations/AGENTS.md` ↔ `workspace_map.md` MUST stay consistent. Orchestrator runs triangle-consistency check via haiku grep across 3 paths (m-d).
3. For per-subdir AGENTS.md: verify every claimed `file:line` via `git grep` within 10 min of edit (per memory `feedback_grep_gate_before_contract_lock`).

**CRITIC**: opus on TIER 0C (4 MDs), sonnet on TIER 1 (40 AGENTS.md). Fresh critic subagent, brief ≤30 lines. Per memory `feedback_default_dispatch_reviewers_per_phase`: critic dispatched automatically at wave-close.

**Gate**: critic PASS per sub-tier; triangle consistent; PR-C ready.

### WAVE 4 — Final reality audit + PR open (~2 hr, opus)

Mirror PR-#124 WAVE 6 pattern:

1. Single opus auditor reads ALL TIER 0+1 edits across the 3 PRs (or single PR if operator overrode).
2. Re-verifies every cited path, symbol, command via fresh `git grep` / `ls` within 10-min window.
3. Produces `FINAL_REALITY_AUDIT_2026-05-17.md` matching `REALITY_AUDIT_2026-05-16.md` schema.
4. Verdict gate: FIX_BEFORE_PR (fix + re-audit) | CLEAN_FOR_PR (open PRs).

After CLEAN_FOR_PR: open PR-A, PR-B, PR-C per chosen split. Each PR description steers reviewers to authority models / runtime impact, not formatting (per memory `feedback_ultrareview_pr_must_redirect_to_deep_logic`).

**Gate**: final audit CLEAN_FOR_PR; PRs opened; carry-forward queue closed (§9).

---

## 6. Quality gates summary

| Gate | Wave | Mechanism |
|---|---|---|
| Scout accuracy | WAVE 0 | Verification haiku samples 25% of POISON+STALE; ≥75% pass |
| TIER 0A governance | WAVE 1 | Every commit has amendment block + opus critic (fresh subagent, ≤30-line brief) |
| TIER 0B loader compat | WAVE 2 | Each edit runs its loader-test (table in SCOUT 0B); `PYTHONPATH=. python -m scripts.topology_doctor --strict-health` zero new failures |
| TIER 0C+1 FCI4 | WAVE 3 | All citations grep-verified within 10 min; cross-ref triangle check |
| Final | WAVE 4 | Opus reality audit on ALL phase output before PR open |

---

## 7. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Touching `invariants.yaml` without amendment = governance violation | HIGH | WAVE 1 amendment block per commit; opus critic probes |
| Touching `bindings/zeus/config.yaml` breaks maintenance_worker | HIGH | WAVE 2 `maintenance_worker.cli.entry dry-run` before commit |
| Touching `task_boot_profiles.yaml` breaks fresh-agent boot | HIGH | WAVE 2 confirms schema; SCOUT 0B maps loader |
| Touching `config/reality_contracts/*` breaks contract loader | HIGH | WAVE 2 imports loader smoke-test (per SCOUT 0B command table) |
| AGENTS.md ↔ docs/operations/AGENTS.md ↔ workspace_map.md triangle drift | MEDIUM | WAVE 3 orchestrator triangle check |
| Scout self-error on STALE verdicts (PR #124: 50% self-error) | MEDIUM | Per-scout verification haiku at WAVE 0 close |
| Long opus revision briefs systematically timeout | MEDIUM | All briefs ≤30 lines (m-b); revisions to sonnet not opus |
| `file:line` citation rot during plan execution | MEDIUM | All cites grep-verified within 10 min; prefer `file::symbol` |
| 3-PR auto-reviewer fatigue / cross-PR contradiction (m-a) | MEDIUM | Open PRs sequentially with 1 wave's gap; thread PR-B body referring back to PR-A merged context |
| Multi-parallel-session collision on new worktree (m-a) | MEDIUM | Single-session lock declared at WAVE 0; no other agent edits `feat/ref-authority-docs-2026-05-17` until WAVE 4 close |
| Worker self-reviews its own edits (m-a) | MEDIUM | Every critic dispatch explicitly uses fresh subagent ID; orchestrator enforces critic ≠ executor (m-c) |
| `paris_station_resolution_2026-05-01.yaml` parse error (PR #124 carry-forward) | LOW | Addressed in §9 — operator decides quarantine vs fix |
| `invariants.yaml` INV-12/13/21 numbering gap | LOW | READ ONLY unless WAVE 0 surfaces new evidence |
| PR diff size exceeds reviewer tools | LOW | 3-PR split keeps each ~500-1,000 LOC |

---

## 8. Anti-patterns to avoid (Fitz philosophy + memory)

- **File:line rot**: use `file::symbol` per `feedback_zeus_plan_citations_rot_fast`. If line number required, grep-verify within 10 min.
- **Single-probe diagnosis**: per "one failed command is data, three is diagnosis" — WAVE 2 loader-test failures get 3 distinct probes before BLOCKED.
- **Worker writes report orchestrator should write**: orchestrator owns SCOUT_SUMMARY.md + FINAL_REALITY_AUDIT_*.md. Workers write SCOUT_*.md only.
- **Critic rubber-stamp**: opus critic on every TIER 0 wave (4-for-4 ROI per memory). No "low-risk → skip critic."
- **Critic = editor**: forbidden (m-c). Every critic dispatch is a fresh subagent.
- **Scope-creep into TIER 2/3**: if WAVE 0 SCOUT surfaces high-value TIER 2/3 finding, log to §13 stub; do NOT expand this phase.
- **Mega-PR drift**: 3-PR split is the recommendation; do not re-bundle without operator override.
- **Translation loss** (universal methodology §2): encode insight in CODE (loader-tests, schema-validators) not just MD. WAVE 2 loader-tests survive cross-session; MD edits don't.
- **Opus on revision**: per `feedback_long_opus_revision_briefs_timeout` — opus for ORIGINAL critic/design; revisions to sonnet.

---

## 9. Carry-forward queue from PR #124

| Item | Source | Disposition |
|---|---|---|
| INV-27 governance amendment | PR #124 WAVE 1.5 critic | **WAVE 1 (PR-A)** — bundled with TIER 0A amendments |
| Hook v2 protocol amendment | PR #124 WAVE 4 | **NOT this phase** — orthogonal; defer to hook-track owner |
| `architecture/paris_station_resolution_2026-05-01.yaml` parse error | PR #124 WAVE 7 deferral | **WAVE 0 SCOUT 0B** confirms state; WAVE 2 fix OR quarantine per operator |
| 8 `evidence/` files archive | PR #124 ARCHIVE_QUEUE | **NOT this phase** — operator-decision; post-merge archive PR |
| 59-dir May-batch archive backlog (10 KEEP_ACTIVE, 40 ROUTINE, 9 LESSON) | PR #124 ARCHIVE_QUEUE_FOR_NEXT_PR.md | **NOT this phase** — separate cleanup PR |
| stash@{0} dispatch.py + registry.yaml + zeus-router.mjs | PR #124 STASH_DISPOSITION | **APPLIED in PR #124 WAVE 4** — canonical stash drop deferred |
| stash@{1} AGENTS.md diff | PR #124 STASH_DISPOSITION | **NOT this phase** — operator-decision |
| TIER 2 (25 docs) + TIER 3 (23 docs) audit | This plan §3 | **Follow-up phase** — see §13 stub |

---

## 10. Plan deliverables

| Deliverable | Path | Wave |
|---|---|---|
| This PLAN.md (v2) | `docs/operations/task_2026-05-17_reference_authority_docs_phase/PLAN.md` | now |
| PLAN_CRITIC.md (v1 critic verdict archived) | same dir | now |
| SCOUT_0A.md | same dir | WAVE 0 |
| SCOUT_0B.md (incl. Loader Command Table) | same dir | WAVE 0 |
| SCOUT_0C.md | same dir | WAVE 0 |
| SCOUT_SUMMARY.md | same dir | WAVE 0 close |
| WAVE_{1,2,3}_CRITIC.md | same dir | per-wave close |
| FINAL_REALITY_AUDIT_2026-05-17.md | same dir | WAVE 4 |
| PR-A, PR-B, PR-C bodies | GitHub | WAVE 4 |

---

## 11. Estimated effort (re-derived from FCI4 LOC totals)

| Wave | Duration | Critic tier | Commits |
|---|---|---|---|
| WAVE 0 SCOUT | 2-3 hr | haiku + audit-of-audit haiku | 1 (scout artifacts) |
| WAVE 1 TIER 0A (1,895 LOC, 8 docs) | 3-4 hr | opus | 6-10 |
| WAVE 2 TIER 0B (5,669 LOC, 10 docs) | 5-6 hr | opus | 8-12 (per-doc + loader-test logs) |
| WAVE 3 TIER 0C+1 (~5,500 LOC, 44 docs) | 5-6 hr | opus + sonnet | 8-14 (batched) |
| WAVE 4 FINAL | 2 hr | opus | 3 (audit + PR opens) |
| **Total** | **17-21 hr** | | **26-40 commits** |

**Total LOC budget**: 1,300-2,500 across 3 PRs (~500-900 per PR).

---

## 12. Pre-execution checklist (orchestrator runs before WAVE 0)

- [x] PR #124 merged to main (HEAD `9fd3ac46c5`, confirmed)
- [x] Fresh worktree created off origin/main: `/Users/leofitz/.openclaw/workspace-venus/zeus-ref-authority-docs-2026-05-17`
- [x] This PLAN.md committed (this commit)
- [ ] pytest baseline captured: `python -m pytest tests/ -q --tb=no | tee state/pytest_baseline_2026-05-17.txt`
- [ ] topology_doctor baseline captured: `PYTHONPATH=. python -m scripts.topology_doctor --strict-health 2>&1 | tee state/topology_doctor_baseline_2026-05-17.txt`
- [x] All 18 TIER 0 paths confirmed extant (FCI4 §3)
- [x] `python -m maintenance_worker.cli.entry --help` confirmed surface (`{run,dry-run,status,init}`)
- [x] `PYTHONPATH=. python -m scripts.topology_doctor --help` confirmed module form
- [x] AGENTS.md count: 46 total → 40 in-scope (FCI4 §3 TIER 1)
- [x] `src/contracts/reality_contracts_loader.py` extant → A3 promotion justified
- [x] `workspace_map.md` extant at root → m-d triangle anchor verified
- [ ] Opus plan-critic pass on v2 (delta-only review of A1-A5 + minors against v1 critic verdict in PLAN_CRITIC.md)

---

## 13. Follow-up phase stub — TIER 2 + TIER 3

**Successor task dir:** `docs/operations/task_2026-05-XX_reference_authority_docs_phase_tier_2_3/` (XX assigned post-merge of PR-A/B/C).

**Scope:** 25 TIER 2 docs (~8,500 LOC, mostly `architecture/*.yaml` not loader-coupled) + 23 TIER 3 docs (~6,500 LOC, methodology/review/operational reference incl. 5 observation `AGENTS.md`).

**Predicted effort:** ~10-14 hr / 1-2 PRs (~800-1,500 LOC).

**Wave model:** WAVE 0 SCOUT (haiku) + WAVE 1 batch remediation + WAVE 2 final audit. Lower criticality → sonnet critic acceptable for most.

**Trigger:** open as follow-up packet after PR-C merges. WAVE 0 SCOUT findings from THIS phase may surface TIER 2/3 issues for early triage.

---

## 14. FCI4 / 3-day lifecycle / structural-fix discipline (operator language)

- **FCI4** = Fitz Constraint #4 (data provenance): every cited path/symbol/PR# grep-verified within 10 min of plan use. v2 applied FCI4 at write-time: all 22 TIER 0 paths + 40 TIER 1 paths `wc -l`'d, CLI surfaces confirmed, loader existence confirmed. Re-verify within 10 min of WAVE 0 dispatch.
- **3-day lifecycle**: SCOUT_*.md are transient; FINAL_REALITY_AUDIT is authoritative. 3 days post-merge → scout artifacts archive-eligible.
- **Structural-fix discipline** (universal methodology §1): every TIER 0 finding asks "1 edit or N edits as symptom of 1 structural decision?" Example: if SCOUT 0A finds 5 invariants citing dead symbols, structural answer may be loader change, not 5 doc edits.

---

## 15. Sign-off

- Planner verdict: v2 = v1 + 5 critic amendments + 4 minors; FCI4 re-run on every cited LOC + CLI command.
- v1 critic verdict: REVISE (archived at `PLAN_CRITIC.md`).
- v2 critic dispatch (per Final Checklist): fresh opus subagent, brief ≤30 lines: "delta-review v2 against v1 critic 5 amendments + 4 minors; confirm A1-A5 land cleanly; verdict CLEAR_PASS / ACCEPT_WITH_FOLLOWUP / REVISE."

*PLAN v2 complete. PLAN_V2_DONE pending commit + push.*
