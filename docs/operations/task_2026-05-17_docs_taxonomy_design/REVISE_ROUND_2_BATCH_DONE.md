# Revise Round 2 BATCH_DONE — Sonnet executor (a5e7246b87237b2ed)

Created: 2026-05-17

**scaffold_revised**: yes — SCAFFOLD.md 268 lines (rewritten)
**plan_revised**: yes — EXECUTION_PLAN.md 144 lines (rewritten)

## Critical fixes (4/4)

1. §3 `architecture/topology.yaml` verdict loader cite: `L41 NAMING_CONVENTIONS_PATH + L57 DOCS_REGISTRY_PATH` → `(scripts/topology_doctor.py, line 34 TOPOLOGY_PATH, file_sha8=58ec7cab)`
2. All §3 cites normalized to D2 `(path, line, file_sha8)`. Recomputed sha8: `58ec7cab` (topology_doctor.py), `188a8939` (naming_conventions.yaml), `2d629100` (topology_doctor_docs_checks.py), `8a662ff7` (archival_check_0.py)
<!-- cite: scripts/archive_migration_2026-05-16.py:44 sha=c4b0c91e -->
3. W3 (post-renumber) enumerates `update_existing` (13 grep-verified) + `insert_new` (2: `docs/authority/AGENTS.md`, `REVIEW.md`); "4 packet bodies" dropped; 13th site = `scripts/archive_migration_2026-05-16.py:44` docstring
4. All Phase-1b refs purged; `source_family.py` + `test_source_family_collision.py` moved to Phase-1 W5 (~80 LOC); §4 FM-10 updated

## Major fixes (4/4)

5. §4 FM-10 row: removed `topic_authority` kind-substitution; "Phase-1 FM-10 prevention = source_family.py typed enum in W5"
6. FM-lifecycle-ortho row + §3 artifact_authority_status: locked 3-assertion shape (a) pairwise-disjoint, (b) ARCHIVED ⇒ historical, (c) LOAD_BEARING ⇒ ∉ ARCHIVED
7. Waves renumbered: W1→W2→W3(was W4 ARCHIVAL_RULES relocation)→W4(was W3 manifests)→W5→W6. All "W3 after W4" parentheticals removed; §4 critic probes relabeled
8. W2 retro-cite algorithm: (1) enumerate via regex, (2) resolve each to D2 triplet, (3) replace inline, (4) re-run lint exit 0

## Missing items (6/6)

1. `topology_doctor.py:11`/`:12` residue: zero (grep verified)
2. `topology_doctor_freshness_checks.py` + `topology_doctor_digest.py`: EXIST (ls confirmed) → W4 gate PASS
3. `topology.yaml expected_empty: true` schema field: does NOT exist → W4 must extend schema; rollback fallback = `.gitkeep` seed
4. W4 rollback added: "if schema-update fails, revert all 3 yaml via git checkout"
5. `test_topology.yaml` (L39) + `data_rebuild_topology.yaml` (L42) siblings: W4 carries deliverable to check + patch
6. `docs/AGENTS.md` checklist-vs-wave inconsistency: W6 files-touched now includes it; §3 adds verdict entry (EXTEND, Wave 6)

## Other

- citations_normalized: 5 §3 entries + D2 format contract in §3 header
- phase_1b_purged: yes (W5 LOC delta +80)
- wave_renumber: W1(scout) → W2(citation-lint) → W3(ARCHIVAL_RULES relocation) → W4(manifest updates) → W5(structural antibodies + FM-10 type-system) → W6(legacy_reference + AGENTS.md)
- final_loc: 2220 / 2500. Breakdown: W1=220, W2=420, W3=350, W4=270, W5=650, W6=310

## Residual concerns

- `topology.yaml expected_empty: true` schema does not exist — W4 executor must extend or use .gitkeep. **executor-judgment**: recommend schema extension
- W3 13th site = `task_2026-05-16_doc_alignment_plan/REALITY_AUDIT_2026-05-16.md` historical audit cites old ARCHIVAL_RULES path — **executor-judgment** to update unless **operator-decision-needed** on historical integrity
- `docs/AGENTS.md` vs `docs/authority/AGENTS.md` insert semantics: W6 row + §3 verdict cover it; not in §5 checklist as explicit item — **executor-judgment** (W6 row authoritative)
