# Zeus Docs Taxonomy Reorg — EXECUTION_PLAN

Created: 2026-05-17
Last revised: 2026-05-17 (Critic Round 2 revisions)
Authority basis: SCAFFOLD.md (sibling) + `feedback_pr_unit_of_work_not_loc` + `feedback_frontload_predictable_remediation`
Total budget: ≤2500 self-authored LOC for **Phase-1 PR** (this packet). Phase-2 split documented below.

## Revision log (Critic Round 2)

| Fix | Source | Resolution |
|---|---|---|
| D1: Phase-1b purged; source_family.py + test moved into Phase-1 W5 | Orchestrator D1 | §1 Phase-1b block removed; §1 Phase-1 list updated; W5 +80 LOC; LOC totals updated |
| D3: waves renumbered to actual exec order | Orchestrator D3 | §2 wave table renumbered: W1→W2→W3(was W4)→W4(was W3)→W5→W6; all internal cross-refs updated; "W3 executes after W4" parentheticals removed |
| D5: W2 retro-cite algorithm spelled out step-by-step | Orchestrator D5 | §2 W2 row updated with 4-step algorithm |
| Critical #3: W3 (post-renumber) split into update_existing + insert_new | Critic R2 | W3 row: 13 grep-verified update_existing sites + 2 insert_new sites; "4 packet bodies" claim removed |
| Missing: W4 topology schema for expected_empty | Critic R2 | W4 (post-renumber, was W3) row notes schema extension deliverable |
| Missing: W4 sibling topology files check | Critic R2 | W4 notes test_topology.yaml + data_rebuild_topology.yaml parallel check |
| Missing: W4 rollback step | Critic R2 | W4 mitigation chain adds revert step |
| docs/AGENTS.md checklist-vs-wave-table fix | Critic R2 | W6 files-touched now explicitly includes `docs/AGENTS.md` |

## Revision log (Critic Round 1)

| Fix | Source | Resolution |
|---|---|---|
| W4 inbound count 4→15 | Critic P4 + grep | W4 LOC revised; list of 15 sites enumerated below |
| W6 file count 6→7 | Critic P4 + grep | W6 LOC/files revised |
| W3 verification gate incomplete | Critic fix #7 | W3 gate adds freshness_checks + digest exits |
| W2 self-exclusion vs retro-cite | Critic fix #9 | W2 adds retro-cite pass for SCAFFOLD/PLAN |
| Phase-2 dependency on orphan stubs | Critic fix #2 | See §"Phase-2 dependency disposition" — premise rejected |
| W5 lifecycle orthogonality test | Critic fix #8 | W5 adds test_lifecycle_field_orthogonality.py |
| W2 pre-W3 fixtures | Novel risk #3 | W2 adds synthetic-failing fixture sub-step |
| LOC re-tally | Critic P8 | §2 totals updated |

---

## §1 Scope split (Phase-1 vs Phase-2)

**Phase-1 (THIS PR, ~2200 LOC est., revised)** — rules + structural antibodies + targeted relocations (≤15 files moved):
- 7 structural prevention artifacts (tests + scripts + boot profile; +1 lifecycle orthogonality test).
- `src/contracts/source_family.py` typed enum (`SourceFamily`) + `tests/test_source_family_collision.py` (~80 LOC, FM-10 type-system antibody — moved from vapor Phase-1b into Phase-1 W5 per D1).
- 4 manifest edits (`topology.yaml`, `docs_registry.yaml`, `naming_conventions.yaml`, `task_boot_profiles.yaml`).
- 5 AGENTS.md edits (root, REVIEW, docs/, docs/operations/, docs/authority/).
- 1 ARCHIVAL_RULES.md relocation (packet → `docs/authority/`).
- 7 `legacy_reference_*.md` relocations (`docs/reports/` → `docs/reference/legacy/`).
- Wave-packet template update (4 files in `architecture/packet_templates/`).
- `architecture/fatal_misreads.yaml` update (FM-10 source-family entry).

**Phase-2 (separate PR, later)** — mass migration (NOT in this PR):
- Normalize 81 `.archived` stub suffixes (`<slug>.md.archived` → `<slug>.archived`).
- Date-format sweep (4+ `YYYY_MM_DD` → `YYYY-MM-DD` in `docs/operations/` top-level files).
- Audit/move 391 files under `docs/operations/archive/2026-Q2/` if any need quarter reclassification.
- Cross-ref rewrites for the 782 files containing `docs/operations/task_` (only if Phase-1 relocations break refs — most won't because we preserve `task_*` naming and ARCHIVAL_RULES.md is rare-cited).

Reason for split: bundling mass migration would push LOC well past 2500 and create a ship-blocking review burden. Phase-1 is the coherent unit ("install the structural antibodies + relocate the misplaced authority + register the legacy class"). Phase-2 is bulk cleanup gated by Phase-1 tests passing.

---

## §1a Phase-2 dependency disposition

**Critic's concern** (fix #2): 3 orphan stubs (`task_2026-04-13_topology_compiler_program`, `task_2026-04-14_session_backlog`, `task_2026-05-03_ddd_implementation_plan`) have no body, so `test_archive_stub_integrity.py` fails on Phase-1 land, creating a silent Phase-2 dependency.

**Premise rejected** — grep evidence (2026-05-17, worktree):
```
docs/operations/task_2026-04-13_topology_compiler_program.md.archived   ← stub
docs/operations/archive/2026-Q2/task_2026-04-13_topology_compiler_program.md  ← body ✓

docs/operations/task_2026-04-14_session_backlog.md.archived              ← stub
docs/operations/archive/2026-Q2/task_2026-04-14_session_backlog.md       ← body ✓

docs/operations/task_2026-05-03_ddd_implementation_plan.md.archived      ← stub
docs/operations/archive/2026-Q2/task_2026-05-03_ddd_implementation_plan.md ← body ✓
```

Critic searched for directory-shaped bodies (`task_*/`) but these are single-file archives (`task_*.md`). `test_archive_stub_integrity.py` with the correct mapping rule (stub `<slug>.md.archived` resolves to `archive/<YYYY>-Q<N>/<slug>.md`) ships green. Phase-1 has no orphan blocker.

**Choice**: Neither (a) nor (b). The stubs are not orphaned. Test ships with stub→body mapping rule locked as above. W5 executor must encode this exact rule in the test assertion.

---

## §2 Wave-by-wave plan (Phase-1 only)

**Execution sequence: W1 → W2 → W3 → W4 → W5 → W6**

W3 (ARCHIVAL_RULES relocation) executes BEFORE W4 (manifest/topology updates) to prevent the authority-pointer-inversion: W4 edits `naming_conventions.yaml` which references ARCHIVAL_RULES; W4 must reference the new canonical path.

| Wave | Goal | Files touched (paths or pattern) | Verification gate | Est. LOC | Est. files | Critic tier |
|---|---|---|---|---|---|---|
| W1 | Land scout ground-truth script (FM-03/08 antibody) — used by all subsequent waves and future scouts | `scripts/scout_ground_truth.py` (new), `tests/test_scout_ground_truth_freshness.py` (new) | `python scripts/scout_ground_truth.py --emit docs/ > /tmp/gt.json && pytest tests/test_scout_ground_truth_freshness.py -v` | 220 | 2 | opus |
| W2 | Land citation-rot lint + tests (FM-01/04 antibody) — most novel architecture; needs heaviest critic | `scripts/doc_citation_lint.py` (new), `tests/test_doc_citation_resolver.py` (new), CI hook config snippet; **sub-step A**: `tests/fixtures/citation_lint/` with synthetic-failing fixtures (missing-file, wrong-line, sha-mismatch) verified caught by lint before W3 proceeds; **sub-step B (retro-cite, locked per D5)**: retro-cite pass on SCAFFOLD.md + EXECUTION_PLAN.md — algorithm: (1) enumerate all `path:line` cites in SCAFFOLD/PLAN via `grep -n` regex `[(].*:.*[)]`; (2) for each cite, resolve to canonical triplet `(path, line, file_sha8)` using format locked in D2 — compute `git show HEAD:<path> \| sha256sum \| cut -c1-8`; (3) replace each cite inline with the normalized triplet; (4) re-run `python scripts/doc_citation_lint.py docs/operations/task_2026-05-17_docs_taxonomy_design/` — require exit 0 before W3 proceeds. | `python scripts/doc_citation_lint.py docs/authority/ architecture/ AGENTS.md REVIEW.md docs/operations/task_2026-05-17_docs_taxonomy_design/ && pytest tests/test_doc_citation_resolver.py -v` | 420 | 4 | **opus** (mandatory per `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi` — novel cite-resolution architecture) |
| W3 | Relocate ARCHIVAL_RULES.md to canonical home + update reference sites + leave pointer stub | move `docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/ARCHIVAL_RULES.md` → `docs/authority/ARCHIVAL_RULES.md`; create `.relocated` stub at old path; **update_existing (13 grep-verified sites)**: `docs/archive_registry.md`, `docs/operations/AGENTS.md` (lines 84 + 246), `docs/operations/archive/2026-Q2/INDEX.md`, `docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md`, `docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md`, `docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml`, `docs/operations/task_2026-05-16_doc_alignment_plan/REALITY_AUDIT_2026-05-16.md`, `maintenance_worker/core/archival_check_0.py` (header), `maintenance_worker/rules/closed_packet_archive_proposal.py` (header), `maintenance_worker/rules/wave_family.py` (header), `tests/maintenance_worker/test_archival_check_0.py` (header), `tests/maintenance_worker/test_wave_family.py` (header), `scripts/archive_migration_2026-05-16.py` (docstring L44); **insert_new (2 sites)**: `docs/authority/AGENTS.md` (add ARCHIVAL_RULES to allowlist), `REVIEW.md` (add to Tier 3 list) | `grep -r "04_workspace_hygiene/ARCHIVAL_RULES" .` returns only the `.relocated` stub; `python scripts/archive_migration_2026-05-16.py --dry-run` exits 0 | 350 | 16 | sonnet |
| W4 | Update 3 architecture manifests (taxonomy structural source) | `architecture/topology.yaml` (extend: `docs/reference/legacy` zone, `docs/operations/archive/cold` zone with `expected_empty: true` field — **schema must be extended in same wave** to support this field; fallback: `.gitkeep` seed; also verify/update `architecture/test_topology.yaml` + `architecture/data_rebuild_topology.yaml` for parallel zone entries), `architecture/docs_registry.yaml` (extend per §3 verdict), `architecture/naming_conventions.yaml` (line 140 corrected, `cold_archive_pattern` added) | `python scripts/topology_doctor.py --navigation && python scripts/topology_doctor_freshness_checks.py && python scripts/topology_doctor_digest.py` (all 3 must exit 0) | 270 | 3–5 | opus |
| W5 | Land remaining structural antibodies (FM-02, FM-07, FM-09, FM-05/06, FM-lifecycle-ortho, FM-10 type-system) | `scripts/generate_agents_md_from_topology.py` (new), `tests/test_agents_md_in_sync.py` (new), `tests/test_supersedes_chain.py` (new), `tests/test_archive_stub_integrity.py` (new — mapping rule: `<slug>.md.archived` → archive body; ships green per orphan-stub grep), `tests/test_lifecycle_field_orthogonality.py` (new — 3-assertion shape per D4), `src/contracts/source_family.py` (new typed enum `SourceFamily`, ~40 LOC), `tests/test_source_family_collision.py` (new, ~40 LOC), extend `scripts/topology_doctor_docs_checks.py`, `architecture/task_boot_profiles.yaml` (add `docs_taxonomy_reorg` profile), `tests/test_task_boot_profiles_coverage.py` (extend) | All new pytests green; `python scripts/generate_agents_md_from_topology.py --check` exits 0 | 650 | 10 | opus |
| W6 | Relocate 7 `legacy_reference_*.md` files; update cross-refs; update 5 AGENTS.md files + `docs/AGENTS.md`; update 4 wave-packet templates; update `architecture/fatal_misreads.yaml` | move `docs/reports/legacy_reference_*.md` (7 files) → `docs/reference/legacy/`; create `docs/reference/legacy/AGENTS.md`; edit `docs/AGENTS.md`, `docs/reports/AGENTS.md`, `docs/reference/AGENTS.md`, root `AGENTS.md`; edit 4 packet templates; update `architecture/fatal_misreads.yaml`; verify `(scripts/topology_doctor_docs_checks.py, line 303, file_sha8=2d629100)` gate passes after relocation | `pytest tests/test_doc_citation_resolver.py -v` (validates updated cites); `grep -rn "docs/reports/legacy_reference" .` returns 0 (or only redirect notes); `python scripts/topology_doctor_docs_checks.py` exits 0 | 310 | 15 | sonnet |

**Totals**: 6 waves, ~2200 LOC (revised), ~50 files. Buffer of ~300 LOC to 2500 ceiling.

**LOC breakdown**:
- W1: 220 (unchanged)
- W2: 420 (unchanged)
- W3: 350 (was W4; +170 for full site enumeration)
- W4: 270 (was W3; +20 for schema extension + sibling yaml check)
- W5: 650 (+80 for source_family.py + test, +30 for lifecycle ortho test shape)
- W6: 310 (unchanged)
- Total: 2220 vs 2500 ceiling = ~280 LOC buffer.

---

## §3 Pre-cited remediation patterns (per `feedback_frontload_predictable_remediation`)

| If critic flags... | Then patch... |
|---|---|
| **A**: "W2 cite-resolver regex fails on multi-line cite blocks" | **B**: extend regex to also match `<!-- cite_block: ... -->` fenced form; add fixture in `tests/test_doc_citation_resolver.py::test_multiline_block`. |
| **A**: "W2 content-hash will require re-generating every cite on any line-shift even when content unchanged" | **B**: switch from `(line_range, content_sha8)` to `(start_anchor_sha8, content_sha8)` — anchor is sha of first non-blank line of cited region; lint resolves by anchor-search then range-validate. |
| **A**: "W4 manifest changes break `topology_doctor --navigation`" | **B**: update `architecture/topology_doctor_yaml_schema.yaml` (if present) in same wave to declare new fields; verify `--navigation` exits 0 before commit. **Mitigation chain**: (A) add `expected_empty` field to schema yaml; (B) if schema-update also fails, revert all 3 yaml edits via `git checkout architecture/{topology,naming_conventions,docs_registry}.yaml` and re-approach. |
| **A**: "W3 stub at old ARCHIVAL_RULES path is itself in an active-packet body and violates the same K3/K4 rule" | **B**: instead of `.relocated` stub at packet path, add forwarding line at the parent packet's top-level README; delete the file outright (it's a redirect, not authority). |
| **A**: "W5 `generate_agents_md_from_topology.py` would overwrite hand-written sections in 9 AGENTS.md files" | **B**: scope the generator to a delimited block `<!-- AUTOGEN:taxonomy_routing START --> ... <!-- AUTOGEN:taxonomy_routing END -->`; check-mode diffs only this block. |
| **A**: "W5 `test_supersedes_chain.py` finds existing cycles in current `docs_registry.yaml`" | **B**: pre-fix the cycles in same wave (likely small); if cycle is intentional ("two-way reference"), add `mutual_reference: true` field exempted from cycle check. |
| **A**: "W6 6 legacy files have inbound references that break" | **B**: pre-generate a `mv` map in `tools/legacy_reference_redirect.json`; W6 patch updates all inbound cites via `sed -i` driven by the map. |
| **A**: "scout drift (per audit-of-audit memory): some `legacy_reference_*` file count or names are wrong in SCAFFOLD" | **B**: W6 first action = `find docs/reports -name 'legacy_reference_*'` and use actual list; do not trust SCAFFOLD names. |
| **A**: "Phase-1 PR is over 2500 LOC" | **B**: defer W6 to a sibling Phase-1c PR (W1–W5 ship first, structural antibodies are independently coherent). |
| **A**: "Critic doubts whether ARCHIVAL_RULES belongs in `docs/authority/` vs `docs/operations/`" | **B**: cite `docs/authority/AGENTS.md:10-13` ("durable authority law only") and `zeus_change_control_constitution.md:102-108` (K3/K4 isolation) — these settle the question in `docs/authority/`'s favor. |

---

## §4 Per-wave critic dispatch contract (per `feedback_critic_general_review_plus_probe_contract`)

Each wave-close critic brief includes:
1. **General review**: spot-trace 3 random new/edited files for the standard review template.
2. **Wave-specific probe** (5-10 assertions):
   - W1: scout output schema stable across two consecutive runs.
   - W2: cite-lint detects a planted broken-cite (orchestrator inserts one, critic must catch); retro-cite pass verified on SCAFFOLD.md + EXECUTION_PLAN.md (lint exits 0 on both).
   - W3: zero inbound references to old ARCHIVAL_RULES path (except `.relocated` stub); `archive_migration_2026-05-16.py --dry-run` exit 0; critic spot-checks 3 of 13 updated reference sites.
   - W4: `topology_doctor --navigation` exit 0; `topology_doctor_freshness_checks.py` exit 0; `topology_doctor_digest.py` exit 0; `topology_doctor_docs_checks.py` exit 0; no new `forbidden_misread` regressions; `expected_empty` schema field recognized by topology_doctor.
   - W5: all new pytest files green (includes lifecycle orthogonality 3-assertion shape + source_family collision test); `generate_agents_md_from_topology.py --check` exit 0; FM-coverage matrix (W5 prevents FM-02, FM-05, FM-06, FM-07, FM-09, FM-10, FM-lifecycle-ortho).
   - W6: zero broken inbound refs to relocated `legacy_reference_*` files; `docs/reference/legacy/AGENTS.md` exists and registered in `docs_registry.yaml`; `topology_doctor_docs_checks.py` exit 0 (verifies existing :303 gate passes post-relocation); 4 packet templates updated; `docs/AGENTS.md` updated.
3. **Anti-rubber-stamp rotation**: rotate critic identity each wave (per memory anti-rubber-stamp pattern).

---

## §5 Wave boundary discipline (per `feedback_commit_per_phase_or_lose_everything`)

- Commit at every wave exit (6 commits total). Squash-merge on PR-open.
- Per `feedback_pr_open_is_precious_one_shot_dogfood_first`: before opening PR, mental-trace bot-review on W2 (citation-lint) — it's the highest novelty surface and bots will probably propose alternative cite-resolution architectures. Pre-draft response talking points so we don't churn the bot thread.
- Per `feedback_accumulate_changes_before_pr_open`: do NOT push to a draft PR mid-orchestration. Push once at end of W6.
