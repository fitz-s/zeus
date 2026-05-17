# Zeus Docs Reorganization Reconnaissance (2026-05-17)

## 1. TOP-LEVEL TREE
| Entry | Type | Count (f) | Purpose / Note |
| :--- | :--- | :--- | :--- |
| AGENTS.md | File | - | Multi-agent collaboration rules & workspace identity |
| archive_registry.md | File | - | Index of archived content |
| archives/ | Dir | 69 | Frozen historical packets and sessions |
| artifacts/ | Dir | 12 | Specific task outputs and audit snapshots |
| authority/ | Dir | 4 | Canonical stable rules, constitution, architecture |
| lore/ | Dir | 5 | Knowledge accretion, topology index |
| methodology/ | Dir | 1 | Theoretical frameworks (e.g., adversarial debate) |
| operations/ | Dir | 623 | In-flight work, task packets, active state |
| README.md | File | - | Project entry point |
| reference/ | Dir | 39 | Stable lookups, specs, domain models, math |
| reports/ | Dir | 23 | Periodic reviews and legacy data inventories |
| review/ | Dir | 3 | PR and audit verdict maps |
| runbooks/ | Dir | 8 | Operational procedures and triage guides |
| to-do-list/ | Dir | 4 | Tracking gaps and future work |

## 2. FULL CATEGORY INVENTORY
| Top-level | Second-level Entries | Count (f) | Note |
| :--- | :--- | :--- | :--- |
| **archives/** | packets/, sessions/ | 69 | Deep historical storage |
| **artifacts/** | AGENTS.md, audit snapshots, review docs | 12 | Flat bucket of past deliverables |
| **authority/** | AGENTS.md, constitution, architecture, delivery | 4 | The "Bible" of the project |
| **lore/** | INDEX.json, topology/ | 5 | Structural history and patterns |
| **methodology/** | adversarial_debate... | 1 | Single-file theoretical anchor |
| **operations/** | task_*, active state, archive/, activation/ | 623 | Massive clutter; contains nested archive/ |
| **reference/** | AGENTS.md, modules/, math_spec, domain_model... | 39 | Core technical lookups |
| **reports/** | authority_history/, legacy_*, refactor_plans | 23 | Mixed historical and recent reviews |
| **review/** | AGENTS.md, code_review, review_scope_map | 3 | Small, focused |
| **runbooks/** | AGENTS.md, live-ops, triage guides, task_* | 8 | Functional guides |
| **to-do-list/** | AGENTS.md, known_gaps | 4 | Tracking |

## 3. LIFECYCLE STAGE per directory
| Directory | Lifecycle Stage | Note |
| :--- | :--- | :--- |
| authority/ | AUTHORITY | Stable rules and invariants. |
| reference/ | REFERENCE | Semantic lookups and specifications. |
| operations/ | MIXED | Conflates active tasks with historical archive/ (~393 files). |
| archives/ | ARCHIVE | Formal deep storage. |
| lore/ | LORE | Knowledge patterns (e.g. fatal_misreads). |
| review/ | REVIEW | Verification verdicts. |
| methodology/ | AUTHORITY | Conceptual stability. |
| reports/ | MIXED | Mixes periodic reports with "legacy" reference data. |
| runbooks/ | OPERATIONS | Actionable operational guides. |
| artifacts/ | ARCHIVE | Specific past outputs that should likely be in archives/. |

## 4. SCALE METRICS
- **Total file count under docs/**: 795
- **File count by top-level subdir**:
  1. `operations/`: 623
  2. `archives/`: 69
  3. `reference/`: 39
  4. `reports/`: 23
  5. `artifacts/`: 12
- **Largest messy directories**:
  - `docs/operations/archive/`: 393 files (nested archive smell)
  - `docs/operations/`: ~230 top-level entries (excluding archive/)
- **Clutter Alert**: `docs/operations/` is the primary entropy source.

## 5. NAMING PATTERNS (Sampled from operations/)
- `task_YYYY-MM-DD_<slug>/` (Standard directory packet)
- `task_YYYY-MM-DD_<slug>.archived` (File marked as done via extension)
- `task_YYYY-MM-DD_<slug>.md.archived` (Duplicate suffix inconsistency)
- `YYYY_MM_DD_SLUG.md` (Underscore date vs hyphen date)
- `SLUG_YYYY_MM_DD.md` (Inverted naming)
- **Finding**: Heavy reliance on `.archived` suffix within the `operations/` dir instead of moving to `archives/`.

## 6. CROSS-REFERENCE PRESSURE
| Pattern | Count | Pressure |
| :--- | :--- | :--- |
| `docs/operations/task_` | 394 | **EXTREME** (Hard to move/rename) |
| `docs/authority` | 127 | High (Anchor references) |
| `docs/reference` | 119 | High (Semantic references) |
| `docs/archive` | 115 | Medium-High (Historical links) |
| `docs/lore` | 11 | Low |

## 7. REDESIGN SIGNAL
- **Conflation**: `operations/` is used as both an "Active Workbench" and a "Pending Archive". The nested `operations/archive/` and the `.archived` suffix strategy creates a massive flat bucket.
- **Split**: "Legacy" reference data is currently under `reports/`, but conceptually belongs in `reference/` or `archives/`.
- **Axes for Taxonomy**:
  1. **Stability**: AUTHORITY (Never move) vs REFERENCE (Stable) vs OPERATIONS (Ephemeral).
  2. **Topic**: RISK vs DATA vs EXECUTION (Cross-cuts stability).
- **Quick Wins**:
  1. Move `docs/operations/archive/*` to `docs/archives/packets/` (requires link preservation).
  2. Move `docs/artifacts/` into `docs/archives/packets/` as they are frozen deliverables.
  3. Standardize `operations/` naming to strictly `task_YYYY-MM-DD_slug/`.
