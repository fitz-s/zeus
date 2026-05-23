# W1 Ground Truth — Verified File/Cite Counts
# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: direct grep/find on worktree state at commit 1895a6e0c6

Verified numbers replacing stale numerics in INPUT_inventory.md (INPUT_inventory.md:52-59 claimed counts).
All measurements taken from worktree root `/zeus-docs-taxonomy-design-2026-05-17` at 2026-05-17.

---

## §1 File counts by directory

### Top-level docs/ subdirectory file counts (recursive, type=file)

| Directory | INPUT_inventory.md claim | Verified count | Delta | Note |
|---|---|---|---|---|
| `docs/` total | 795 | **740** | −55 | INPUT overstated |
| `docs/archives/` | 69 | **1** | −68 | INPUT measured entries not files; `archives/` has 1 file (sessions post-mortem) |
| `docs/artifacts/` | 12 | **11** | −1 | Minor drift |
| `docs/authority/` | 4 | **4** | 0 | Match |
| `docs/lore/` | 5 | **4** | −1 | Minor drift |
| `docs/methodology/` | 1 | **1** | 0 | Match |
| `docs/operations/` | 623 | **642** | +19 | New packets added since INPUT |
| `docs/reference/` | 39 | **38** | −1 | Minor drift |
| `docs/reports/` | 23 | **22** | −1 | Minor drift |
| `docs/review/` | 3 | **3** | 0 | Match |
| `docs/runbooks/` | 8 | **8** | 0 | Match |
| `docs/to-do-list/` | 4 | **3** | −1 | Minor drift |

### Archive disambiguation
INPUT_inventory.md confused directory entry count with recursive file count for `docs/operations/archive/`:

| Measurement axis | INPUT claim | Verified |
|---|---|---|
| `ls docs/operations/archive/2026-Q2/ \| wc -l` (top-level entries) | "393 files" | **82 entries** |
| `find docs/operations/archive -type f \| wc -l` (recursive files) | "393 files" | **391 files** |

Verdict: INPUT_inventory.md §4 "393 files" matches the recursive file count (391 ≈ 393 with 2-file drift). The "82 entries" is the correct directory-slot count for quarterly archive.

---

## §2 ARCHIVAL_RULES cross-reference counts

| Scope | Count | Note |
|---|---|---|
| Total files referencing `ARCHIVAL_RULES` (all subdirs) | **35** | `grep -rln "ARCHIVAL_RULES" .` |
| Minus design-packet self-refs (`task_2026-05-17_docs_taxonomy_design/`) | **28** | Matches CRITIC_ROUND_3.md V3 "28 total" |
| W3 update-existing scope (maintenance_worker/, docs/archive_registry, docs/operations/AGENTS.md, scripts/, tests/maintenance_worker/, docs/operations/archive/2026-Q2/INDEX.md, docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md, 3 other task packets) | **13** | As enumerated in EXECUTION_PLAN.md §2 W3 row |
| Insert-new scope (docs/authority/AGENTS.md, REVIEW.md) | **2** | As enumerated in EXECUTION_PLAN.md §2 W3 row |
| Historical packet docs (excluded from W3 scope per CRITIC_ROUND_3.md followup #1) | **13** | `docs/operations/task_2026-05-16_doc_alignment_plan/**` + `task_2026-05-15_runtime_improvement_engineering_package/**` |

**Breakdown of the 28 outside design-packet:**
- `maintenance_worker/core/archival_check_0.py` — W3 update_existing
- `maintenance_worker/rules/closed_packet_archive_proposal.py` — W3 update_existing
- `maintenance_worker/rules/wave_family.py` — W3 update_existing
- `tests/maintenance_worker/test_wave_family.py` — W3 update_existing
- `tests/maintenance_worker/test_archival_check_0.py` — W3 update_existing
- `tests/maintenance_worker/test_rules/test_closed_packet_archive_proposal.py` — W3 update_existing
- `tests/maintenance_worker/test_rules/test_dispatcher.py` — W3 update_existing (verify header vs prose per CRITIC_ROUND_3.md open question)
- `docs/archive_registry.md` — W3 update_existing
- `docs/operations/AGENTS.md` — W3 update_existing
- `docs/operations/archive/2026-Q2/INDEX.md` — W3 update_existing
- `docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md` — W3 update_existing
- `scripts/archive_migration_2026-05-16.py` — W3 update_existing
- `docs/operations/task_2026-05-17_reference_authority_docs_phase/PLAN.md` — **ACTIVE sibling task** (CRITIC_ROUND_3.md open question; not in W3 scope; operator scheduling question)
- `docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md` — W3 update_existing
- `docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml` — W3 update_existing
- Historical packet docs (13) — excluded per CRITIC_ROUND_3.md followup #1 scoped grep

---

## §3 docs/operations/ structure

| Measurement | Verified count |
|---|---|
| Top-level entries (files + dirs) in `docs/operations/` | **142** |
| Top-level directory entries in `docs/operations/` | **44** |
| `.archived` stubs at `docs/operations/` top-level | **81** |
| Files in `docs/operations/archive/2026-Q2/` (recursive) | **391** |
| Directories in `docs/operations/archive/2026-Q2/` (top-level entries) | **82** |
| `docs/operations/archive/cold/` existence | **DOES NOT EXIST** — `cold/` subdir absent entirely |

Note: `docs/operations/archive/cold/` is not just empty; it does not exist. SCAFFOLD.md §2 describes it as "currently empty"; SCAFFOLD.md §3 topology verdict says `expected_empty: true`. W4 executor must either create the dir with `.gitkeep` or extend topology.yaml schema to handle absent-path zones.

---

## §4 Naming violations

| Pattern | Verified count | Examples |
|---|---|---|
| `YYYY_MM_DD` underscore date (violating `YYYY-MM-DD` rule) | **3** | `CLOUD_EXTRACT_PATCH_2026_05_07.md`, `LIVE_RESTART_2026_05_07.md`, `UNMATCHED_GAMMA_CITIES_2026_05_07.md` |
| `.archived` stubs total | **81** | Mix of `<slug>.archived` and `<slug>.md.archived` forms |

SCAFFOLD.md §2 claimed "4+ violators" for underscore dates — verified count is 3. Phase-2 sweep targets these.

---

## §5 Cross-reference pressure (verified)

| Pattern | INPUT_inventory.md claim | Verified count (grep -rln) | Note |
|---|---|---|---|
| `docs/operations/task_` | 394 (occurrences) | **809** (files containing pattern) | INPUT measured occurrences; this is file count |
| `docs/authority` | 127 | **523** (files containing pattern) | Different measurement axis |
| `docs/reference` | 119 | **1176** (files containing pattern) | Different measurement axis |

Note: INPUT_inventory.md §6 measured occurrence count (grep -c), not file count (grep -l). Both metrics are valid; this table uses file count for consistency with SCAFFOLD.md §1 "782 files reference docs/operations/task_*" claim. SCAFFOLD.md §1 claims 782 — verified file count is 809. SCAFFOLD's 782 may have been measured at an earlier commit.

---

## §6 legacy_reference_*.md files (W6 scope)

Verified exact list (matches SCAFFOLD.md §3 exactly):
1. `docs/reports/legacy_reference_data_inventory.md`
2. `docs/reports/legacy_reference_data_strategy.md`
3. `docs/reports/legacy_reference_market_microstructure.md`
4. `docs/reports/legacy_reference_quantitative_research.md`
5. `docs/reports/legacy_reference_repo_overview.md`
6. `docs/reports/legacy_reference_settlement_source_provenance.md`
7. `docs/reports/legacy_reference_statistical_methodology.md`

Count: **7** — matches SCAFFOLD.md §3.

---

## §7 sha8 verification for SCAFFOLD.md §3 cites

All sha8s from SCAFFOLD.md §3 recomputed at current HEAD (1895a6e0c6) and verified:

| File | Cited sha8 | Verified sha8 | Match |
|---|---|---|---|
| `scripts/topology_doctor.py` | 58ec7cab | **58ec7cab** | YES |
| `scripts/topology_doctor_docs_checks.py` | 2d629100 | **2d629100** | YES |
| `architecture/naming_conventions.yaml` | 188a8939 | **188a8939** | YES |
| `maintenance_worker/core/archival_check_0.py` | 8a662ff7 | **8a662ff7** | YES |

All sha8s current. SCAFFOLD.md §3 cite anchors remain valid at W1 execution time.

---

## §8 Superseded INPUT_inventory.md claims (summary)

| INPUT_inventory.md claim | W1 verdict | Correct value |
|---|---|---|
| `docs/` total = 795 files | SUPERSEDED | 740 files |
| `docs/archives/` = 69 files | SUPERSEDED | 1 file (INPUT counted entries not files) |
| `docs/operations/` = 623 files | SUPERSEDED | 642 files |
| `operations/archive/` = 393 files | CLARIFIED | 391 files (recursive); 82 entries (top-level 2026-Q2) |
| `docs/operations/task_` cross-refs = 394 | MEASUREMENT-AXIS | 809 files; INPUT measured occurrences not files |
| Underscore-date violators = "4+" | SUPERSEDED | 3 verified violators |
