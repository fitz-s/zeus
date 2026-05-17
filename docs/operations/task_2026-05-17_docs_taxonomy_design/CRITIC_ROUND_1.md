# Critic Round 1 — Opus critic verdict on SCAFFOLD + EXECUTION_PLAN

Created: 2026-05-17
Critic: opus (a25624fad9d7f951c)
Verdict: **FIX_REQUIRED**

## Probes

- **P1 (structural prevention coverage) = PARTIAL** — 5/7 failure modes get true structural antibodies. FM-07 (semantic domain collision: IFS vs ENS grid) and FM-10 (semantic false equivalence: settlement vs day0 sources) mapped to authority-dedup + dual-entry-drift antibodies; neither prevents the actual class. Need type-system antibody (e.g. `SourceFamily` NewType) per Fitz Constraint #1.
- **P2 (supersede justification cites) = FAIL** — `topology_doctor.py:11` actual is `:41` (NAMING_CONVENTIONS_PATH); `:12` actual is `:57` (DOCS_REGISTRY_PATH); `archival_check_0.py` lives at `maintenance_worker/core/archival_check_0.py` not repo root. Only `naming_conventions.yaml:140` archive_pattern citation is correct. Meta-ironic given W2 is the citation-rot antibody.
- **P3 (self-discoverability) = FAIL** — wave-packet template path guessed (`docs/operations/PACKET_TEMPLATE.md`); actual is `architecture/packet_templates/{bugfix,feature,refactor,schema}_packet.md` (4 files). SCAFFOLD §5 also omits `architecture/fatal_misreads.yaml` (relevant for FM-10 source-family equivalence registration).
- **P4 (LOC budget realism) = FAIL** — W4 cites "4 known" ARCHIVAL_RULES inbound refs; grep shows **15** (3 maintenance_worker code, 2 tests, archive_registry, INDEX.md, 4 packet bodies, 3 self-refs). 180 LOC budget likely 2-3x light. W6 cites 6 legacy_reference_*.md; actual **7** files.
- **P5 (Phase-2 honesty) = FAIL** — **3 orphan stubs** (`task_2026-04-13_topology_compiler_program`, `task_2026-04-14_session_backlog`, `task_2026-05-03_ddd_implementation_plan`) have no body in `archive/2026-Q2/` or `archive/cold/` (cold tree empty: 0 subdirs). W5 `test_archive_stub_integrity.py` blocks Phase-1 merge unless repaired. Phase-1 implicitly depends on Phase-2 mass migration.
- **P6 (audit-of-audit recursion) = PARTIAL** — 782→actual 785 (drift +3, OK); 81 .archived correct; 391→391 files but only 78 packets/subdirs (wording conflates); 6 legacy_reference→7 (off by 1); planner re-introduced 4 unverified line-number citations in §3 (P2 above).
- **P7 (wave dependency ordering) = PARTIAL** — W3 lands `naming_conventions.yaml::archive_pattern = docs/operations/archive/<YYYY>-Q<N>/<slug>/` referring to a path that ARCHIVAL_RULES.md (still packet-located) is the authority for; W4 then relocates ARCHIVAL_RULES. Reverse order (W4 then W3) avoids the brief authority-pointer inversion. Not a hard blocker.
- **P8 (bot-reviewability) = PARTIAL** — 1850 LOC fits floor + ceiling, but with W4 underestimated by ~10 inbound sites + W6 needing real (7 not 6) relocations, realistic Phase-1 is ~2500-3000 LOC. Still ships, but 650 buffer claimed in PLAN §2 is consumed before any critic revision.

## Top 3 concerns

1. Phase-1 ships a CI-gating test (`test_archive_stub_integrity.py`) that fails immediately because 3 stubs orphan — silent Phase-2 dependency violates "Phase-1 is independently coherent".
2. Antibody misclassification on FM-07/FM-10: authority-dedup + metadata-regen do not prevent semantic source-family false-equivalence; need type-system antibody.
3. Self-discoverability failures: wave-packet template path guessed (wrong), `fatal_misreads.yaml` omitted from checklist, citation rot present in the very SCAFFOLD whose Wave-2 antibody targets that rot.

## fix_required_items

1. `SCAFFOLD.md::§4 FM-07/FM-10 row` → add `src/contracts/source_family.py` typed enum + `tests/test_source_family_collision.py` antibody because authority-dedup + metadata-regen do not prevent semantic false equivalence between IFS/ENS grids or settlement/day0/hourly sources; antibody KIND is type-system, not registry.
2. `EXECUTION_PLAN.md::§2 W5` → either (a) move the 3 orphan stub repairs (`task_2026-04-13_topology_compiler_program`, `task_2026-04-14_session_backlog`, `task_2026-05-03_ddd_implementation_plan`) into Phase-1 as a W4.5 wave, OR (b) ship `test_archive_stub_integrity.py` as `pytest.mark.xfail` + tracked TODO until Phase-2 lands, because test as written blocks Phase-1 merge.
3. `SCAFFOLD.md::§3 architecture/naming_conventions.yaml + architecture/docs_registry.yaml + ARCHIVAL_RULES.md` → re-grep all four line-number citations and replace with `(path, line_range, content_sha8)` per W2's own contract; current cites `topology_doctor.py:11/12` are `:41/:57` actual, `archival_check_0.py:13` lives at `maintenance_worker/core/archival_check_0.py`, because shipping an unfresh citation in the SCAFFOLD that ships the citation-rot antibody is self-undermining (per `feedback_zeus_plan_citations_rot_fast`).
4. `SCAFFOLD.md::§5 self-discoverability checklist` → replace "Wave-packet template (search repo for current template; likely `docs/operations/PACKET_TEMPLATE.md` or in `zpkt` source)" with explicit list `architecture/packet_templates/{bugfix,feature,refactor,schema}_packet.md` (4 files) + add `architecture/fatal_misreads.yaml` (register FM-10-class source-family entry).
5. `EXECUTION_PLAN.md::§2 W4` → revise inbound-ref count from 4 to 15 (`maintenance_worker/core/archival_check_0.py`, `maintenance_worker/rules/wave_family.py`, `maintenance_worker/rules/closed_packet_archive_proposal.py`, `tests/maintenance_worker/test_wave_family.py`, `tests/maintenance_worker/test_archival_check_0.py`, `docs/archive_registry.md`, `docs/operations/AGENTS.md`, `docs/operations/archive/2026-Q2/INDEX.md`, 4 packet bodies, 3 self-refs) and re-estimate LOC because 180 LOC / 6 files is 2-3x light.
6. `EXECUTION_PLAN.md::§2 W6` → change "6 legacy_reference_*.md files" to **7 files**: actual list is `legacy_reference_{data_strategy, settlement_source_provenance, data_inventory, repo_overview, market_microstructure, statistical_methodology, quantitative_research}.md`.
7. `EXECUTION_PLAN.md::§2 W3 verification gate` → add `python scripts/topology_doctor_freshness_checks.py` and `python scripts/topology_doctor_digest.py` exit-0 gates (both consume `naming_conventions.yaml`); current gate only runs `topology_doctor --navigation`, leaving 2 of 3 loaders unverified after schema change.
8. `SCAFFOLD.md::§3 docs_registry vs artifact_authority_status orthogonality` → add `tests/test_lifecycle_field_orthogonality.py` asserting the three enum sets (`artifact_authority_status.status`, `docs_registry.lifecycle_state`, `ARCHIVAL_RULES` verdicts) maintain documented orthogonality, because documentation-only orthogonality drifts in 30 days.
9. `EXECUTION_PLAN.md::§2 W2` → add scope-exclusion rule for `doc_citation_lint.py` covering `docs/operations/task_2026-05-17_docs_taxonomy_design/**` OR add W6 retro-cite pass for SCAFFOLD/PLAN themselves, because the packet contains 15+ pre-existing `path:line` cites the lint will reject on land.

## Novel risks not in SCAFFOLD

- W2 cite-marker format `<!-- cite: path:line sha=... -->` collides with manual `grep "path:line"` workflows; agents grep cited file:line constantly and auto-injected comments will pollute every search result on cited lines.
- `topology_doctor_docs_checks.py:303` already special-cases `legacy_reference_*` pattern requiring location `docs/reference/`, contradicting SCAFFOLD's "new doc_class registration" framing — partially-wired already; W6 may collide with existing implementation if not enumerated.
- Phase-1 dogfood risk: W2 cite-lint is highest-novelty surface; first artifact tested by it is W3-W6's own cite generation. If W2 misses an edge case, W3+ stalls; PLAN doesn't gate this with a pre-W3 corpus of synthetic-failing cite fixtures.
- `archive/cold/` is empty (0 subdirs) but SCAFFOLD §2 + W3 add it as a zone in `topology.yaml`; topology_doctor strict mode may reject zone with zero-population if it enforces `parent_coverage` proof.
- Wave-packet template at `architecture/packet_templates/*.md` is consumed by `scripts/zpkt.py` for packet bootstrap; updating templates without updating zpkt rendering logic risks template-as-data vs template-as-format drift.
