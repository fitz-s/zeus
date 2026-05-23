# Revise Round 1 BATCH_DONE — Sonnet executor (a897ea7d0fb1212a9)

Created: 2026-05-17

**scaffold_revised**: yes — SCAFFOLD.md 203 → 248 lines (+45). Revision log header, sha8 triplets on 4 cited paths, FM-07/FM-10 row split with source_family deferred to Phase-1b, FM-09 orphan mapping rule locked, FM-lifecycle-ortho row added, §5 wave-packet template exact 4 filenames + fatal_misreads.yaml checklist, §7 zpkt clarification, §2 cold-zone annotation, §3 7-file legacy_reference list, topology verdict cold-zone `expected_empty` mitigation, W6 note on topology_doctor_docs_checks.py:303 existing gate.

**plan_revised**: yes — EXECUTION_PLAN.md 81 → 131 lines (+50). Revision log header, §1 LOC ~1850→~2100, Phase-1b sibling for source_family, §1a Phase-2 dependency disposition (premise rejected with grep evidence), wave table W2/W3/W4/W5/W6 all revised, W4 inbound sites 4→15 with full enumeration, W6 7 files + fatal_misreads.yaml, W3 gate adds freshness_checks + digest, W3 exec order swapped to after W4, W5 adds lifecycle orthogonality test, §4 probes updated.

**p5_choice**: **(c) — critic premise REJECTED**. Grep shows all 3 "orphan" stubs have bodies at `docs/operations/archive/2026-Q2/task_*.md`. Critic searched for directory-shaped bodies; these are single-file archives. `test_archive_stub_integrity.py` ships green with correct `<slug>.md.archived` → `<slug>.md` mapping rule. Neither (a) nor (b) needed.

**citations_reverified**: 4/4. All `path:line` in §3 → `(path, line, sha8)` triplets:
- topology_doctor.py:41 (sha8=58ec7cab) — NAMING_CONVENTIONS_PATH
- topology_doctor.py:57 (sha8=58ec7cab) — DOCS_REGISTRY_PATH
- archival_check_0.py:6+78 (sha8=8a662ff7)
- naming_conventions.yaml:140 (sha8=188a8939)
- docs_registry.yaml loader sha8=200e29ee noted

**fix_items_addressed**: 9/9
1. FM-07/FM-10 type-system antibody kind corrected; source_family.py → Phase-1b
2. P5 orphan stubs — premise REJECTED with grep evidence; test mapping rule locked
3. Citation rot §3 → triplets
4. §5 template path → exact 4 filenames; fatal_misreads.yaml added
5. W4 4→15 sites enumerated; LOC 180→350
6. W6 6→7 files; LOC 280→310
7. W3 gate adds freshness_checks + digest; W3 swapped after W4
8. Lifecycle orthogonality test added (W5)
9. W2 self-exclusion via retro-cite pass

**novel_risks_addressed**: 4/5 (#5 reframed)
- #1 cite-marker collision → marker on own line above cited text
- #2 topology_doctor_docs_checks.py:303 already wired → W6 enumerates re-use
- #3 W2 dogfood pre-W3 fixtures → synthetic-failing fixture sub-step in W2
- #4 cold zone zero-pop → `expected_empty: true` mitigation + 3 options for W3 executor
- #5 reframed → zpkt uses inline rendering, no template data dependency

**final_loc_estimate**: ~2100 vs critic's 2500-3000 forecast. Breakdown: W1=220, W2=420, W3=250, W4=350, W5=570, W6=310. Buffer ~400 to 2500 ceiling.

**residual_concerns**:
1. `archive/cold/` zero-pop zone: W3 executor verifies topology_doctor's `parent_coverage` behavior on empty zones; 3 mitigations enumerated — no operator decision.
2. W3/W4 exec order swap: wave table ordered W1→W6 for readability but W4 must land before W3; W3 executor must check W4 completion. Brief discipline only.
