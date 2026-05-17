# Docs Taxonomy Failure Mode Inventory (regenerated 2026-05-17)

## §1 Failure mode ledger
| ID | Date | Source | Failure mode (1 line) | Quote/Evidence | Root mechanism |
| :--- | :--- | :--- | :--- | :--- | :--- |
| FM-01 | 2026-05-16 | INVENTORY.md | Citation Rot: file:line references drift >80% in authority docs | "Spot-check (5/5 broken) ... Line citations are >80% stale" | High edit frequency in shared files vs static docs |
| FM-02 | 2026-05-15 | Lore/INDEX.json | Metadata Drift: YAML action requires matching conditional entry | "keep_conditional yaml action requires matching AGENTS.md conditional reads entry" | Manual dual-entry requirement (topology vs AGENTS.md) |
| FM-03 | 2026-05-15 | feedback_audit_recursive | Audit Fabrication: Agents hallucinate gaps from wrong directory search | "audit confused total archive size (46) with document scope (7)" | Tooling/Agent lacks source-of-truth grounding (baselining) |
| FM-04 | 2026-04-23 | feedback_zeus_plan_rot | Plan Premise Mismatch: Action taken on non-existent or moved code | "T4.0 said decision_log had decision_snapshot_id column -> it doesn't" | Stale read-only investigation used for later execution |
| FM-05 | 2026-05-11 | task_2026-05-11_tigge | Discoverability Gap: wired config vs env var confusion | "ZEUS_ENTRY_FORECAST_ROLLOUT_MODE is not wired. Use config/settings.json" | Lack of "wired vs dead" signal in config docs |
| FM-06 | 2026-04-23 | feedback_semantic_boot | Surface Forensics Trap: SQL probes miss architectural inversions | "started with SQL probes before reading invariants ... initially missed the architectural inversion" | Taxonomy allows skipping manifests for direct probes |
| FM-07 | 2026-05-08 | task_2026-05-08_ecmwf | Domain Model Collision: Model grid vs dissemination grid confusion | "haiku's three-part claim is half-correct: accurate for IFS model grid, inaccurate for ENS" | Overlapping doc authorities (IFS docs vs OpenData README) |
| FM-08 | 2026-04-26 | feedback_verify_paths | Path Hallucination: Prompts cite non-existent directory structures | "referenced src/data/hko_*.py (doesn't exist)" | Legacy memory of older structures (HKO path drift) |
| FM-09 | 2026-05-16 | CLEANUP_REPORT.md | Lifecycle Confusion: Archive intent superseded by PR deletion | "PR #122 explicitly deleted ... archive copy NOT preserved" | Race condition between manual cleanup and PR automation |
| FM-10 | 2026-05-17 | fatal_misreads.yaml | Semantic False Equivalence: Treating non-interchangeable sources as same | "daily_settlement_source == day0_live_source == historical_hourly_source" | Taxonomy grouping (all are 'sources') masks functional diffs |

## §2 Clustered taxonomy
- **CITATION_ROT (FM-01, FM-04)** — Physical file:line/column references go stale within hours.
- **AUDIT_FABRICATION (FM-03, FM-08)** — Agents check wrong directories or hallucinate gaps due to baseline drift.
- **METADATA_DRIFT (FM-02, FM-10)** — Manual counters and role-definitions drift from ground truth.
- **LIFECYCLE_CONFUSION (FM-09)** — Docs/state stuck in "deprecated but active" or "archived but deleted" limbo.
- **DUPLICATE_AUTHORITY (FM-07)** — Same rule/grid described in 2 places with different levels of Zeus-relevance.
- **DISCOVERABILITY_GAP (FM-05, FM-06)** — Doc exists but agent skips to forensics or follows dead env-var signals.

## §3 Silent failures
- **Baseline drift in manifests**: `module_manifest.yaml` missing `maintenance_worker` (FM-16) — agents wouldn't know to audit it.
- **Registry collision**: `bindings/zeus/` exists but is unregistered (FM-16) — invisible to topology-aware agents.
- **Hook feedback loop**: Hook design failures (FM-May8) mask discipline violations — agent thinks it's clean but logic is leaked.

## §4 Pattern signals (for design — NOT design)
- **Structural**: CITATION_ROT and METADATA_DRIFT are structural; manual dual-entry (YAML + Doc) guarantees drift.
- **Procedural**: DISCOVERABILITY_GAP and SURFACE_FORENSICS are procedural; boot-order enforcement (Semantic Boot) kills them.
- **Invisible**: AUDIT_FABRICATION is a grounding problem; requires "ls-before-ask" discipline.

