# Zeus Docs Taxonomy Reorg — SCAFFOLD (Design Only)

Created: 2026-05-17
Last revised: 2026-05-17 (Critic Round 2 revisions)
Authority basis: prior scout INPUTs + direct grep validation (audit-of-audit) + advisor consult + post-critic grep verification
Scope: rules + minimal targeted relocations + structural antibodies. Mass migration is **Phase-2** (see EXECUTION_PLAN §Scope Split).

## Revision log (Critic Round 2)

| Fix | Source | Resolution |
|---|---|---|
| Critical #1: topology.yaml verdict cite wrong line | Critic R2 | §3 topology.yaml loader cite corrected to L34 TOPOLOGY_PATH |
| Critical #2: sha8 contract ambiguity | Critic R2 + D2 | All §3 cites normalized to locked format `(path, line, file_sha8)` where `file_sha8 = git show HEAD:<path> \| sha256sum \| cut -c1-8`; all sha8 values recomputed |
| Critical #3: W4 phantom insert vs update sites | Critic R2 | Moved to EXECUTION_PLAN W3 (post-renumber); split into update_existing (13 grep-verified) + insert_new (2) |
| Critical #4 / D1: Phase-1b vapor → move source_family.py into Phase-1 | Orchestrator D1 | All Phase-1b references purged; source_family.py + test moved into W5 (post-renumber); W5 LOC +80; §4 FM-10 updated |
| Major #5: FM-10 topic_authority kind-substitution | Critic R2 | §4 FM-10 row updated: prevention = source_family.py typed enum in W5 |
| Major #6 / D4: orthogonality test invariant shape | Critic R2 + D4 | §3 artifact_authority_status entry + §4 FM-lifecycle-ortho row updated with 3-assertion shape |
| Major #7 / D3: W3/W4 swap commentary-only | Critic R2 + D3 | Moved to EXECUTION_PLAN wave renumber |
| Major #8 / D5: W2 retro-cite algorithm unspecified | Critic R2 + D5 | Moved to EXECUTION_PLAN W2 row |

## Revision log (Critic Round 1)

| Fix | Source | Resolution |
|---|---|---|
| P2: topology_doctor.py line cites wrong | Critic | All `path:line` → `(path, line, sha8)` triplets in §3 |
| P3: wave-packet template path guessed | Critic | §5 updated with exact 4 filenames |
| P3: fatal_misreads.yaml omitted | Critic | §5 checklist item added |
| P1: FM-07/FM-10 antibody misclassified | Critic | §4 updated; source_family.py typed enum now in Phase-1 W5 |
| FM-09 orphan stubs premise | Critic R1 then grep | **Critic premise rejected** — grep shows 3 bodies in `archive/2026-Q2/`; §4 FM-09 mapping rule locked precisely |
| W6 file count 6→7 | Critic | §3 `docs/reports/legacy_reference_*.md` updated |
| zpkt/template clarification | Novel risk #5 | §7 note added |
| cite-marker collision | Novel risk #1 | §4 FM-01/04 row: marker placed above cited line |
| topology_doctor_docs_checks.py:303 already wired | Novel risk #2 | §3 docs_registry verdict W6 note added |
| `archive/cold/` zero-population | Novel risk #4 | §2 + §3 topology verdict: `expected_empty: true` mitigation |
| W2 dogfood: pre-W3 fixtures | Novel risk #3 | Addressed in EXECUTION_PLAN W2 row |
| lifecycle enum orthogonality test | Fix #8 | §4 new row added |

---

## §1 Central taxonomy axis (locked)

**Axis = Stability/Authority Lifecycle**. Topic axis (RISK/DATA/EXECUTION) is **rejected** for this phase.

Rationale: tree already encodes stability (`authority/` / `reference/` / `operations/` / `archives/`). 782 files reference `docs/operations/task_*` (validated: `grep -rl ... | wc -l`). A topic re-axis would require renaming every active and historical packet and cascade-rewriting all 782 cross-references; that breaks `feedback_pr_unit_of_work_not_loc` and offers near-zero structural defect prevention compared to fixing broken cells on the existing axis.

What changes: the **rules** governing each existing axis cell (allowed contents, naming, lifecycle transitions), and a small number of **targeted relocations** (≤15 files) to repair conflated cells.

What does NOT change in this packet:
- the four top-level axis directories (`authority/`, `reference/`, `operations/`, `archives/`) themselves;
- the `task_YYYY-MM-DD_*` naming convention for active packets;
- the 391 archived task packets currently under `docs/operations/archive/2026-Q2/`.

---

## §2 New taxonomy structure (directory layout + naming rules)

```
docs/
  AGENTS.md                       # router (keep)
  README.md                       # entry (keep)
  archive_registry.md             # archive index pointer (keep)

  authority/                      # DURABLE LAW ONLY (no packet evidence, no dated audits)
    AGENTS.md
    zeus_change_control_constitution.md
    zeus_current_architecture.md
    zeus_current_delivery.md
    ARCHIVAL_RULES.md             # NEW HOME (relocated from packet body — see §3 verdict)

  reference/                      # CANONICAL DURABLE REFERENCES (no volatile facts)
    AGENTS.md
    modules/                      # module books (keep)
    (existing reference files)

  operations/                     # ACTIVE WORKBENCH + LIVE CONTROL
    AGENTS.md
    INDEX.md
    POLICY.md
    current_state.md              # the live pointer (keep thin)
    current_data_state.md
    current_source_validity.md
    packet_scope_protocol.md
    known_gaps.md                 # compatibility pointer to docs/to-do-list/
    task_YYYY-MM-DD_<slug>/                              # standard active packet
    task_YYYY-MM-DD_<package>/phases/task_YYYY-MM-DD_<phase>/   # phased package
    <named_observation>/          # whitelisted long-lived observations (see registry)
    archive/<YYYY>-Q<N>/<slug>/   # CANONICAL ARCHIVE ROOT (391 files stay here)
    archive/cold/<YYYY>/<slug>/   # >180d hard-sweep target (currently empty; topology.yaml zone uses expected_empty: true — see §3 topology verdict)

  lore/                           # structural knowledge accretion
    INDEX.json
    topology/<YYYYMMDD>-<slug>.md

  methodology/                    # theoretical frameworks (small, stable)

  reports/                        # periodic reviews
    authority_history/            # demoted historical authority (preserve)
    AGENTS.md
    (dated reports only — legacy_reference_* files move to reference/legacy/)

  reference/legacy/               # NEW: 7 legacy_reference_*.md files relocated here (see §3 for exact list)

  review/                         # PR + audit verdict maps (keep)
  runbooks/                       # operational procedures (keep)
  to-do-list/                     # gap tracking (keep)

  archives/                       # CROSS-REPO + SESSION COLD STORAGE (NOT packet archive)
    sessions/                     # session post-mortems (1 file today)
    work_packets/                 # currently empty; DEPRECATED — see §3 SUPERSEDE
```

### Naming rules (delta from `architecture/naming_conventions.yaml`)

| Rule | Status | Pattern |
|---|---|---|
| Active packet | KEEP | `docs/operations/task_YYYY-MM-DD_<slug>/` |
| Phased packet | KEEP | `docs/operations/task_YYYY-MM-DD_<package>/phases/task_YYYY-MM-DD_<phase>/` |
| Single-file packet | KEEP | `docs/operations/task_YYYY-MM-DD_<slug>.md` |
| Quarterly archive | **CANONICAL** | `docs/operations/archive/<YYYY>-Q<N>/<slug>/` (per ARCHIVAL_RULES; code-enforced by `scripts/archive_migration_2026-05-16.py`) |
| Cold archive | KEEP | `docs/operations/archive/cold/<YYYY>/<slug>/` |
| Archived stub | NORMALIZED | `<original-name>.archived` (NOT `<original-name>.md.archived`) — 81 stubs today, normalization needed |
| Cross-repo cold | NEW | `docs/archives/<class>/<slug>/` (sessions, vendored snapshots only; NEVER zeus packets) |
| Whitelisted observation | KEEP | `docs/operations/<observation_name>/` enumerated in `architecture/docs_registry.yaml::parent_coverage_allowed_patterns` |
| Date format | LOCKED | `YYYY-MM-DD` only (ban `YYYY_MM_DD`; 4+ violators in `docs/operations/`, e.g. `UNMATCHED_GAMMA_CITIES_2026_05_07.md`, `LIVE_RESTART_2026_05_07.md`) |

---

## §3 Existing authority verdicts (per-doc)

**Citation format contract (locked, D2):** all cites use triplet `(path, line, file_sha8)` where `file_sha8 = git show HEAD:<path> | sha256sum | cut -c1-8`. This enables line-rot detection: if the file changes, sha8 changes, and all line refs auto-flag as stale.

For each authority doc surveyed in `INPUT_existing_authority.md`:

### `architecture/artifact_authority_status.yaml`
**Verdict: CANONICAL_KEEP.**
Loader: `(maintenance_worker/core/archival_check_0.py, line 6 header-cite + line 78 load fn, file_sha8=8a662ff7)`. Last modified 2026-05-16. Status enum (`CURRENT_LOAD_BEARING | CURRENT_HISTORICAL | STALE_REWRITE_NEEDED | DEMOTE | QUARANTINE | ARCHIVED`) is orthogonal to `docs_registry.lifecycle_state` — keep both, document the orthogonality in `docs/authority/AGENTS.md`. Test for orthogonality: `tests/test_lifecycle_field_orthogonality.py` (new, Wave 5) — see §4 FM-lifecycle-ortho row for locked 3-assertion shape; docs-only orthogonality drifts in 30 days.

### `architecture/docs_registry.yaml`
**Verdict: EXTEND.**
Loader: `(scripts/topology_doctor.py, line 57 DOCS_REGISTRY_PATH, file_sha8=58ec7cab)`. EXTENSION (Wave 2):
- Add `legacy_reference` to `allowed_doc_classes` (covers `docs/reference/legacy/*`).
- Add `docs/reference/legacy/` to `parent_coverage_allowed_patterns`.
- Add `superseded_chain` field per entry (chain of `superseded_by` resolves to leaf — covered by Wave-5 test).
- Add `topic_authority` field to schema (FM-07 antibody; enables `tests/test_supersedes_chain.py` to assert at-most-one canonical claim per topic).

**W6 note**: `(scripts/topology_doctor_docs_checks.py, line 303, file_sha8=2d629100)` already special-cases `legacy_reference_*` pattern — it flags files at `docs/reference/` with `legacy_reference_` prefix as mislocated and requires them at a different path. W6 must enumerate this existing gate and verify it passes after relocation to `docs/reference/legacy/` — do NOT re-implement the check, only extend the allowed path pattern.

### `architecture/naming_conventions.yaml`
**Verdict: SUPERSEDE_WITH_REASON (one line only).**
Loader: `(scripts/topology_doctor.py, line 41 NAMING_CONVENTIONS_PATH, file_sha8=58ec7cab)`. Conflict: `(architecture/naming_conventions.yaml, line 140, file_sha8=188a8939)` reads `archive_pattern: "docs/archives/work_packets/branches/<branch>/<program_domain>/YYYY-MM-DD_slug/"`. Reality: `scripts/archive_migration_2026-05-16.py` writes to `docs/operations/archive/<YYYY>-Q<N>/<slug>/`. Code wins (per memory `feedback_inv17_db_over_json` analog: implementation > stale spec). Wave-4 edit (post-renumber: now W4 = old W3): change line 140 to the code-canonical path and add `cold_archive_pattern: "docs/operations/archive/cold/<YYYY>/<slug>/"`.

### `architecture/topology.yaml`
**Verdict: EXTEND.**
Loader: `(scripts/topology_doctor.py, line 34 TOPOLOGY_PATH, file_sha8=58ec7cab)`. Last modified 2026-05-17. EXTENSION (Wave 4, post-renumber):
- Add `path: "docs/reference/legacy"` zone entry (legacy reference data demoted from `docs/reports/`).
- Add `path: "docs/operations/archive/cold"` zone entry (cold archive target). **CRITICAL**: `archive/cold/` is currently empty (0 subdirs — verified by `find docs/operations/archive/cold -maxdepth 1 | wc -l = 0`). The `topology.yaml` schema does NOT currently support `expected_empty: true` — Wave-4 executor must also extend schema to declare this field, OR add a `.gitkeep` seed file, OR verify that topology_doctor's `parent_coverage` check exempts explicitly declared empty zones. **W4 must extend `topology.yaml` schema to support `expected_empty: true`** (schema extension, ~20 LOC); fallback: `.gitkeep` seed.
- Update `path: "docs/archives"` zone definition to remove "work_packets" subrole and mark as session/cross-repo only.
- **Sibling loader check**: `architecture/test_topology.yaml` (loader: `scripts/topology_doctor.py, line 39 TEST_TOPOLOGY_PATH`) and `architecture/data_rebuild_topology.yaml` (loader: `scripts/topology_doctor.py, line 42 DATA_REBUILD_TOPOLOGY_PATH`) — W4 executor must check if `docs/reference/legacy` and `docs/operations/archive/cold` zone entries are also needed in these sibling files, or if they inherit from `topology.yaml`. Treat as W4 deliverable to check and patch if required.

### `docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/ARCHIVAL_RULES.md`
**Verdict: SUPERSEDE_WITH_REASON (location, not content).**
<!-- cite: docs/authority/AGENTS.md:10 sha=a86ccb64 -->
Content is canonical (referenced by `scripts/archive_migration_2026-05-16.py` and `docs/operations/AGENTS.md`). Location is wrong: a packet-internal file MUST NOT define repo-wide archival law (per `docs/authority/AGENTS.md:10-13` "durable authority law only" + the constitution K3/K4 isolation rule). Wave-3 edit (post-renumber): relocate to `docs/authority/ARCHIVAL_RULES.md`, leave a 3-line pointer stub at the old path with `.relocated` extension, update all references — see EXECUTION_PLAN W3 for full enumeration.

### `AGENTS.md` (root)
**Verdict: EXTEND.**
Last modified 2026-05-16. EXTENSION (Wave 6):
- Update "durable workspace kernel" paragraph to cite new ARCHIVAL_RULES location.
- Add taxonomy-discoverability one-liner per `feedback_redesign_self_discoverable`: "Docs taxonomy: stability axis. See `docs/AGENTS.md` for routing."

### `REVIEW.md` (root)
**Verdict: EXTEND.**
Last modified 2026-05-16. EXTENSION (Wave 6): update Tier 3 list to include `docs/authority/ARCHIVAL_RULES.md`; remove obsolete `docs/archives/work_packets/` mention if present. **Note**: No existing `ARCHIVAL_RULES` reference found in `REVIEW.md` — this is an INSERT, not UPDATE.

### `docs/operations/AGENTS.md`
**Verdict: EXTEND.**
Last modified 2026-05-16. EXTENSION (Wave 3, post-renumber): update line 84 and line 246 reference paths; add explicit bullet "ARCHIVAL_RULES.md now lives at `docs/authority/ARCHIVAL_RULES.md`".

### `docs/authority/AGENTS.md`
**Verdict: EXTEND.**
Last modified 2026-04-23. EXTENSION (Wave 3, post-renumber): add `ARCHIVAL_RULES.md` to the explicit allowlist for this directory; reaffirm "durable law only" ban. **Note**: No existing `ARCHIVAL_RULES` reference found in `docs/authority/AGENTS.md` — this is an INSERT, not UPDATE.

### `docs/authority/zeus_change_control_constitution.md`
**Verdict: CANONICAL_KEEP.**
K3/K4 isolation rule is the philosophical anchor for the ARCHIVAL_RULES relocation. No edit needed.

### `.claude/CLAUDE.md`
**Verdict: CANONICAL_KEEP.**
Bootstrap pointer only. No taxonomy content.

### `docs/reports/legacy_reference_*.md` (7 files)
**Verdict: SUPERSEDE_WITH_REASON.**
Exact list (verified by `find docs/reports -name 'legacy_reference_*.md' | sort`):
1. `legacy_reference_data_inventory.md`
2. `legacy_reference_data_strategy.md`
3. `legacy_reference_market_microstructure.md`
4. `legacy_reference_quantitative_research.md`
5. `legacy_reference_repo_overview.md`
6. `legacy_reference_settlement_source_provenance.md`
7. `legacy_reference_statistical_methodology.md`

These are not periodic reports — they are durable reference data mis-located. `docs/reports/AGENTS.md` covers "periodic reviews". Wave-6 edit: relocate to `docs/reference/legacy/` and register `legacy_reference` doc_class (per `docs_registry.yaml` EXTEND verdict). Note: `(scripts/topology_doctor_docs_checks.py, line 303, file_sha8=2d629100)` already checks this pattern and currently flags these as mislocated; after relocation the gate should pass automatically.

### `docs/AGENTS.md`
**Verdict: EXTEND.**
Last modified (check at W6 start). EXTENSION (Wave 6): update routing block to add taxonomy stability-axis routing one-liner directing agents to the correct `docs/` subdirectory for each content class.

---

## §4 Failure-mode → structural prevention (NO docs-only)

Each prevention is **types/tests/scripts/hooks**. Docs-only is explicitly insufficient (per brief).

| FM ID | Failure mode | Structural prevention (artifact + kind) | Why this kills the category |
|---|---|---|---|
| FM-01, FM-04 | CITATION_ROT (file:line drift >80%) | **Test** `tests/test_doc_citation_lint.py` + **script** `scripts/doc_citation_lint.py` that parses `path:line-line` patterns in `docs/authority/**`, `architecture/**`, `AGENTS.md`, `REVIEW.md` and fails CI if any cite points to non-existent line, mismatched content hash, or moved file. Citations encode `(path, line_range, content_sha8)` triple via a `<!-- cite: path:start-end sha=abc12345 -->` HTML comment placed on its own line **above** the cited text (not inline) to avoid polluting agent `grep path:line` workflows. Self-application: SCAFFOLD.md and EXECUTION_PLAN.md must themselves pass the lint (retro-cite pass — locked to retro-cite path per D5; W6 exclusion fork deleted). | Stale cites become a typed CI error, not a doc-review oversight. |
| FM-03, FM-08 | AUDIT_FABRICATION (hallucinated paths/gaps) | **Script** `scripts/scout_ground_truth.py` invoked by scout subagent contracts; emits a JSON snapshot (file counts, dir tree, cross-ref counts) used as scout INPUT instead of free-form grep. Plus **test** `tests/test_scout_ground_truth_freshness.py` (snapshot ≤30 min old). | Scouts grounded by machine output, not by recalled directory structure. Drift detectable. |
| FM-02 | METADATA_DRIFT (manual dual-entry) | **Script** `scripts/generate_agents_md_from_topology.py` regenerates the "conditional reads" block of every `AGENTS.md` from `architecture/topology.yaml` + `architecture/docs_registry.yaml` (single source). + **test** `tests/test_agents_md_in_sync.py` (regen → diff = 0). | Dual-entry eliminated structurally; one source-of-truth. |
| FM-05, FM-06 | DISCOVERABILITY_GAP (skip-to-forensics, dead env vars) | **Hook extension** `architecture/task_boot_profiles.yaml`: add `docs_taxonomy_reorg` boot profile that auto-loads on any task touching `docs/**` taxonomy + **test** `tests/test_task_boot_profiles_coverage.py` ensures every authority doc relocation triggers a boot-profile update. | Discovery enforced before any forensic action; missing profile = test failure, not a runtime trap. |
| FM-07 | DUPLICATE_AUTHORITY (overlapping rule descriptions) | **Test** `tests/test_supersedes_chain.py` walks `docs_registry.yaml.entries[].superseded_by`, asserts (a) no cycles, (b) every active doc has at most one "canonical for topic X" claim. + **field** `topic_authority` added to `docs_registry.yaml` schema. | Two docs claiming same topic = CI failure. |
| FM-10 | SEMANTIC_SOURCE_FAMILY_FALSE_EQUIVALENCE (IFS vs ENS grid; settlement vs day0 sources) | **Type-system antibody**: `src/contracts/source_family.py` typed enum (`SourceFamily`) making IFS/ENS/DAY0/SETTLEMENT non-interchangeable at the type level. + **test** `tests/test_source_family_collision.py`. Ships in **Phase-1 W5** (~80 LOC, within budget). Phase-1 FM-10 prevention = source_family.py typed enum in W5. No docs-level substitution. | Category impossible in code, not just detectable in docs. |
| FM-09 | LIFECYCLE_CONFUSION (archived-but-deleted) | **State-machine assertion** in `scripts/topology_doctor_docs_checks.py`: every `*.archived` stub must resolve to an existing archive body. Mapping rule (locked per grep verification): `<slug>.md.archived` → `docs/operations/archive/<YYYY>-Q<N>/<slug>.md` (single-file packets) OR `docs/operations/archive/<YYYY>-Q<N>/<slug>/` (directory packets) OR `archive/cold/<YYYY>/` equivalent. Mismatch = error. + **test** `tests/test_archive_stub_integrity.py`. Note: critic's premise that 3 stubs were orphaned was incorrect — grep confirms `task_2026-04-13_topology_compiler_program.md.archived`, `task_2026-04-14_session_backlog.md.archived`, `task_2026-05-03_ddd_implementation_plan.md.archived` all have bodies at `docs/operations/archive/2026-Q2/task_*.md`. Test ships green in Phase-1 with no orphan blocker. | Stub without body, or body without stub, becomes a CI error. |
| FM-lifecycle-ortho | LIFECYCLE_FIELD_ORTHOGONALITY_DRIFT (enum sets diverge silently) | **Test** `tests/test_lifecycle_field_orthogonality.py` (Wave 5) with locked 3-assertion shape (D4): **(a)** pairwise-disjoint value sets — `set(artifact_authority_status.status) ∩ set(docs_registry.lifecycle_state) ∩ set(ARCHIVAL_RULES verdicts) == ∅`; **(b)** for each artifact with `artifact_authority_status.status == ARCHIVED`, `docs_registry.lifecycle_state ∈ {historical}`; **(c)** for each artifact with `ARCHIVAL_RULES verdict == LOAD_BEARING`, `artifact_authority_status.status ∉ {ARCHIVED}`. | Documentation-only orthogonality drifts in 30 days; 3-assertion test is permanent antibody. |

Silent-failures from `INPUT_failure_modes.md §3`:
- **module_manifest baseline drift** → handled by existing `topology_doctor` registry-coverage tests (no new artifact; extend assertion to `maintenance_worker/` path).
- **Hook design failures** → out of scope for this packet (covered by separate `feedback_hook_design_failure_cascades` antibody).

---

## §5 Self-discoverability checklist (per `feedback_redesign_self_discoverable`)

**Every item below MUST land in the SAME PR. Deferring any item = redesign not done.**

- [ ] `architecture/topology.yaml` updated (zones for `docs/reference/legacy/`, `docs/operations/archive/cold/`, revised `docs/archives` role; schema extended for `expected_empty: true` field).
- [ ] `architecture/docs_registry.yaml` updated (`legacy_reference` doc_class, `superseded_chain` + `topic_authority` fields, new `parent_coverage_allowed_patterns` entry).
- [ ] `architecture/naming_conventions.yaml` updated (archive_pattern line 140 corrected, `cold_archive_pattern` added).
- [ ] `architecture/task_boot_profiles.yaml` updated (`docs_taxonomy_reorg` profile).
- [ ] Root `AGENTS.md` updated (durable kernel paragraph + taxonomy one-liner).
- [ ] `REVIEW.md` updated (Tier 3 list includes `docs/authority/ARCHIVAL_RULES.md`).
- [ ] `docs/AGENTS.md` updated (routing block). *(Also in W6 files-touched.)*
- [ ] `docs/operations/AGENTS.md` updated (ARCHIVAL_RULES path).
- [ ] `docs/authority/AGENTS.md` updated (allowlist + reaffirm law-only ban).
- [ ] Wave-packet templates updated with new naming/archival expectations. Exact files (verified — these are the 4 `architecture/packet_templates/` files, NOT `zpkt` source which uses inline rendering independent of these templates):
  - `architecture/packet_templates/bugfix_packet.md`
  - `architecture/packet_templates/feature_packet.md`
  - `architecture/packet_templates/refactor_packet.md`
  - `architecture/packet_templates/schema_packet.md`
- [ ] `architecture/fatal_misreads.yaml` updated: register FM-10-class source-family semantic false equivalence as a named fatal misread (so future agents boot with explicit warning about IFS/ENS/DAY0 conflation).
- [ ] All structural-prevention artifacts (tests + scripts + hook profile) land in same PR. Count update: 7 new test/script artifacts + `src/contracts/source_family.py` typed enum + `tests/test_source_family_collision.py` in Phase-1 W5.

---

## §6 Outstanding decisions deferred to Wave-1 critic (NOT to operator)

1. `docs/archives/` retention policy: keep as session/cross-repo cold storage (current §2 proposal), or delete entirely (1 file)? Defer to Wave-1 critic with a one-line analysis citing `docs/operations/AGENTS.md` and the constitution. Default proposal: keep, narrow scope to sessions/cross-repo.
<!-- cite: AGENTS.md:388 sha=090a5103 -->
2. `docs/reports/authority_history/` (9 files): keep in `reports/` (current §2 proposal) or move to `docs/archives/authority_history/`? Defer. Default: keep — `authority_history` is genuinely "demoted historical authority" per `AGENTS.md:388-400` and matches `reports/` role.
3. Stub-suffix normalization: 81 `.archived` files — some are `<slug>.archived`, some `<slug>.md.archived`. Whether to normalize all → `<slug>.archived` in this PR or defer to Phase-2 mass migration. Default: defer to Phase-2 (test in this PR detects mismatch but doesn't block).

(Per `feedback_architecture_homework_before_operator_punt`: all three have principled defaults — no operator escalation.)

---

## §7 Clarification: zpkt and packet_templates are independent

`scripts/zpkt.py` does NOT consume `architecture/packet_templates/*.md` as data. It uses inline `_render_plan_template()` / `_render_work_log_template()` functions for packet bootstrap. The 4 `architecture/packet_templates/` files are **human-readable reference templates**, not zpkt input. Therefore:
- Updating the templates in §5 does NOT require updating zpkt rendering logic.
- Template-as-data vs template-as-format drift (novel risk #5) does not apply here.
- W6 template edits are standalone doc edits only.
