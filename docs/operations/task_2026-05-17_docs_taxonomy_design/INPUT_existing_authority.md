# Zeus Existing Taxonomy Authority Audit

## architecture/artifact_authority_status.yaml

### Authority status
- Loader/enforcer (if known from grep): `archival_check_0.py` (line 13)
- Last modified: 2026-05-16 07:55:53 -0500 3ed361e44b

### Verbatim extract (taxonomy-relevant only)
<!-- cite: architecture/artifact_authority_status.yaml:9 sha=bdd24398 -->
> architecture/artifact_authority_status.yaml:9-11 — The design docs (UNIVERSAL_TOPOLOGY_DESIGN §13 + ZEUS_BINDING_LAYER §8) specify: CURRENT_LOAD_BEARING | CURRENT_HISTORICAL | STALE_REWRITE_NEEDED | DEMOTE | QUARANTINE | ARCHIVED
<!-- cite: architecture/artifact_authority_status.yaml:22 sha=bdd24398 -->
> architecture/artifact_authority_status.yaml:22-24 — Fields: path: file or directory path (relative to zeus repo root, or absolute for cross-repo); status: CURRENT_LOAD_BEARING | CURRENT_HISTORICAL | STALE_REWRITE_NEEDED | DEMOTE | QUARANTINE | ARCHIVED
<!-- cite: architecture/artifact_authority_status.yaml:28 sha=bdd24398 -->
> architecture/artifact_authority_status.yaml:28 — archival_ok: whether archival tooling may archive this artifact (default: false)

### One-line semantic gloss
Machine-readable registry separating lifecycle status (open/closed) from authority status (load-bearing/not) to gate archival.

## architecture/naming_conventions.yaml

### Authority status
- Loader/enforcer (if known from grep): `scripts/topology_doctor.py` (line 11)
- Last modified: 2026-05-08 08:57:10 -0500 b9b93da6ee

### Verbatim extract (taxonomy-relevant only)
<!-- cite: architecture/naming_conventions.yaml:125 sha=188a8939 -->
> architecture/naming_conventions.yaml:125-126 — packet_ephemeral: pattern: "task_YYYY-MM-DD_<purpose>.py"
<!-- cite: architecture/naming_conventions.yaml:135 sha=188a8939 -->
> architecture/naming_conventions.yaml:135-138 — operations_packets: single_file_pattern: "docs/operations/task_YYYY-MM-DD_slug.md"; folder_pattern: "docs/operations/task_YYYY-MM-DD_slug/"; phase_folder_pattern: "docs/operations/task_YYYY-MM-DD_package/phases/task_YYYY-MM-DD_phase/"
<!-- cite: architecture/naming_conventions.yaml:139 sha=188a8939 -->
> architecture/naming_conventions.yaml:139 — grouping_rule: "Phases of the same package belong under one package folder, not as sibling top-level task folders."
<!-- cite: architecture/naming_conventions.yaml:140 sha=188a8939 -->
> architecture/naming_conventions.yaml:140 — archive_pattern: "docs/archives/work_packets/branches/<branch>/<program_domain>/YYYY-MM-DD_slug/"
<!-- cite: architecture/naming_conventions.yaml:142 sha=188a8939 -->
> architecture/naming_conventions.yaml:142 — docs: rule: Generic names like plan.md/progress.md are allowed only inside active task folders.

### One-line semantic gloss
Strict YYYY-MM-DD-slug patterns for packets with specific phase-nesting and archive-path requirements.

## architecture/docs_registry.yaml

### Authority status
- Loader/enforcer (if known from grep): `scripts/topology_doctor.py` (line 12)
- Last modified: 2026-05-16 23:23:20 -0500 fce8ea0e6e

### Verbatim extract (taxonomy-relevant only)
<!-- cite: architecture/docs_registry.yaml:27 sha=200e29ee -->
> architecture/docs_registry.yaml:27-39 — allowed_doc_classes: router, authority, reference, module_reference, operations, runbook, report, artifact, checklist, archive_interface, extraction_source, package_input
<!-- cite: architecture/docs_registry.yaml:40 sha=200e29ee -->
> architecture/docs_registry.yaml:40-47 — allowed_next_actions: keep, extract_then_move, extract_then_reassess, move_to_operations, demote_after_extraction, archive_after_closeout, retain_as_evidence
<!-- cite: architecture/docs_registry.yaml:48 sha=200e29ee -->
> architecture/docs_registry.yaml:48-54 — allowed_lifecycle_states: durable, active, temporary, transitional, historical, closed
<!-- cite: architecture/docs_registry.yaml:58 sha=200e29ee -->
> architecture/docs_registry.yaml:58-67 — parent_coverage_allowed_patterns: docs/operations/task_*/, docs/operations/*_package_*/, docs/operations/*_observation/, docs/operations/ws_poll_reaction/, docs/operations/attribution_drift/, docs/reports/, docs/artifacts/, docs/to-do-list/, docs/runbooks/

### One-line semantic gloss
Machine-readable classification system for docs (classes, roles, states) governing their lifecycle from active to archive.

## architecture/topology.yaml

### Authority status
- Loader/enforcer (if known from grep): `scripts/topology_doctor.py`
- Last modified: 2026-05-17 01:05:57 -0500 49a86c0ada

### Verbatim extract (taxonomy-relevant only)
<!-- cite: architecture/topology.yaml:40 sha=482af838 -->
> architecture/topology.yaml:40-42 — path: "docs"; status: active; zone: docs; authority_role: registry; forbidden_misread: Docs root is a router; subroot roles define authority.
<!-- cite: architecture/topology.yaml:43 sha=482af838 -->
> architecture/topology.yaml:43-48 — path: "docs/authority"; status: active; zone: docs_authority; authority_role: schema_law; forbidden_misread: Authority docs route current law; machine manifests and code/tests still win conflicts.
<!-- cite: architecture/topology.yaml:49 sha=482af838 -->
> architecture/topology.yaml:49-54 — path: "docs/reference"; status: active; zone: docs_reference; authority_role: reference_only; forbidden_misread: Reference docs are canonical durable references only; volatile current facts, dated audits, packet evidence, and support snapshots must not live here.
<!-- cite: architecture/topology.yaml:55 sha=482af838 -->
> architecture/topology.yaml:55-60 — path: "docs/reference/modules"; status: active; zone: docs_reference; authority_role: reference_only; forbidden_misread: Module books are dense reference/cognition surfaces; they do not become authority, current facts, or packet status logs.
<!-- cite: architecture/topology.yaml:61 sha=482af838 -->
> architecture/topology.yaml:61-65 — path: "docs/operations"; status: active; zone: docs_operations; authority_role: operations_pointer

### One-line semantic gloss
Top-level zone definitions for docs, mapping subdirectories to authority roles (registry, law, reference, pointer).

## docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/ARCHIVAL_RULES.md

### Authority status
- Loader/enforcer (if known from grep): Referenced by `docs/operations/AGENTS.md` and `scripts/archive_migration_2026-05-16.py`
- Last modified: 2026-05-16 11:24:06 -0500 66c4ca82ec

### Verbatim extract (taxonomy-relevant only)
> ARCHIVAL_RULES.md:21-22 — Every packet directory under docs/operations/task_*/ receives one verdict on each maintenance run:
> ARCHIVAL_RULES.md:24-34 — ACTIVE (modified <30d or Status: ACTIVE_LAW/AUTHORITY/IN_PROGRESS); WINDING_DOWN (not modified 30-60d); ARCHIVE_CANDIDATE (not modified 60+d, no AUTHORITY status, passes ALL checks); LOAD_BEARING_DESPITE_AGE (failed exemption check); ALREADY_ARCHIVED (<name>.archived stub exists)
> ARCHIVAL_RULES.md:86-87 — Compute target path: docs/operations/archive/<YYYY>-Q<1-4>/<original-name>/
> ARCHIVAL_RULES.md:90-101 — Create stub at original path: docs/operations/<original-name>.archived (~12 line metadata block)
> ARCHIVAL_RULES.md:120-123 — Quarterly Hard Sweep: re-evaluates packets >180d; move from archive/<YYYY>-Q<N>/ to archive/cold/<YYYY>/; add cold_archived: true to stub.
> ARCHIVAL_RULES.md:129-130 — Special Case: Wave Packets: Wave packets (task_*_wave[0-9]+) are an ATOMIC GROUP; archive together or none.

### One-line semantic gloss
Detailed lifecycle state machine for operations packets, defining candidate criteria, target paths (quarterly/cold), and stub requirements.

## AGENTS.md (root)

### Authority status
- Loader/enforcer (if known from grep): Primary entry point for all agents.
- Last modified: 2026-05-16 11:48:44 -0500 428794a3f4

### Verbatim extract (taxonomy-relevant only)
<!-- cite: AGENTS.md:169 sha=090a5103 -->
> AGENTS.md:169-178 — The durable workspace kernel is: machine manifests under architecture/**; architecture/module_manifest.yaml; scoped AGENTS.md routers; docs/reference/modules/**; docs/operations/current_state.md, docs/to-do-list/known_gaps.md, and the active packet folder; derived context engines (topology_doctor, source rationale, history lore, Code Review Graph)
<!-- cite: AGENTS.md:388 sha=090a5103 -->
> AGENTS.md:388-400 — Authority classification: Authority (instruction, manifests, tests, packet control, source, DB truth); Derived context (topology_doctor, source_rationale, history_lore, Code Review Graph); History (visible interface docs/archive_registry.md and dense lessons architecture/history_lore.yaml). Archive bodies are cold storage.
<!-- cite: AGENTS.md:417 sha=090a5103 -->
> AGENTS.md:417-419 — Registry routes: src/** -> architecture/source_rationale.yaml, scripts/* -> architecture/script_manifest.yaml, tests/test_*.py -> architecture/test_topology.yaml, docs/reference/* -> docs/reference/AGENTS.md and architecture/reference_replacement.yaml, DB table ownership -> architecture/db_table_ownership.yaml.

### One-line semantic gloss
Defines the "durable workspace kernel" and provides high-level authority classification and registry routing.

## REVIEW.md (root)

### Authority status
- Loader/enforcer (if known from grep): Governs all review sessions.
- Last modified: 2026-05-16 11:42:11 -0500 8b9f9158c0

### Verbatim extract (taxonomy-relevant only)
<!-- cite: REVIEW.md:168 sha=e01b370d -->
> REVIEW.md:168-170 — Tier 3 — Docs / instructions / agent surfaces: AGENTS.md, docs/authority/**, docs/operations/current_*.md, docs/reference/**, docs/review/**, REVIEW.md, workspace_map.md, docs/archive_registry.md
<!-- cite: REVIEW.md:174 sha=e01b370d -->
> REVIEW.md:174-175 — Deprioritized: docs/archives/**, docs/artifacts/**, docs/reports/**, docs/operations/archive/**, closed docs/operations/task_*/** packets

### One-line semantic gloss
Categorizes docs into Tiers for review priority, identifying authority surfaces (Tier 3) vs deprioritized archives.

## docs/operations/AGENTS.md

### Authority status
- Loader/enforcer (if known from grep): Scoped router for operations directory.
- Last modified: 2026-05-16 23:23:20 -0500 fce8ea0e6e

### Verbatim extract (taxonomy-relevant only)
<!-- cite: docs/operations/AGENTS.md:63 sha=2bebfb6f -->
> docs/operations/AGENTS.md:63-65 — The closing agent must move the packet body to docs/operations/archive/<YYYY>-Q<N>/, update docs/operations/archive/<YYYY>-Q<N>/INDEX.md, remove active pointers.
<!-- cite: docs/operations/AGENTS.md:82 sha=2bebfb6f -->
> docs/operations/AGENTS.md:82-84 — Closed packet evidence is archived under docs/operations/archive/<YYYY>-Q<N>/ and indexed in docs/operations/archive/<YYYY>-Q<N>/INDEX.md.
<!-- cite: docs/operations/AGENTS.md:255 sha=2bebfb6f -->
> docs/operations/AGENTS.md:255-262 — New independent multi-file packages use task_YYYY-MM-DD_name/; new phases under task_YYYY-MM-DD_package/phases/task_YYYY-MM-DD_phase/; archive completed/superseded packets to archive/ and leave only active packets, monitoring, current-fact, and compatibility pointers.

### One-line semantic gloss
Operations-specific routing rules, mandating packetized task structure and quarterly archival with INDEX updates.

## docs/authority/AGENTS.md

### Authority status
- Loader/enforcer (if known from grep): Scoped router for authority directory.
- Last modified: 2026-04-23 00:30:35 -0500 f4aca0a757

### Verbatim extract (taxonomy-relevant only)
<!-- cite: docs/authority/AGENTS.md:10 sha=a86ccb64 -->
> docs/authority/AGENTS.md:10-13 — This directory contains durable authority law only. It is not a holding area for packet deliverables, ADRs, fix-pack notes, rollback doctrine, or historical governance evidence.
<!-- cite: docs/authority/AGENTS.md:27 sha=a86ccb64 -->
> docs/authority/AGENTS.md:27-29 — move packet/ADR/history material to evidence surfaces instead of keeping it here; preserve demoted history under reports or archive interfaces.

### One-line semantic gloss
Enforces a "durable law only" policy for the authority directory, explicitly banning ephemeral packet deliverables.

## docs/authority/zeus_change_control_constitution.md

### Authority status
- Loader/enforcer (if known from grep): Deep governance constitution.
- Last modified: 2026-04-23 00:11:00 -0500 42917e3a36

### Verbatim extract (taxonomy-relevant only)
<!-- cite: docs/authority/zeus_change_control_constitution.md:102 sha=4968e716 -->
> docs/authority/zeus_change_control_constitution.md:102-108 — K4 — Experimental / Disposable Layer (试验层) must be isolated: notebooks, one-off scripts, temporary backfill diagnostics, ad hoc reports. K3/K4 绝不能直接成为 authority 或 governance source.
<!-- cite: docs/authority/zeus_change_control_constitution.md:222 sha=4968e716 -->
> docs/authority/zeus_change_control_constitution.md:222-225 — Packet limits: Ordinary packet <= 4 files; authority-bearing packet <= 2 files.
<!-- cite: docs/authority/zeus_change_control_constitution.md:313 sha=4968e716 -->
> docs/authority/zeus_change_control_constitution.md:313-316 — Shadow persistence: 禁止新增新的 *_tracker.json, *_summary.json 被运行时读取作为 authority.

### One-line semantic gloss
Deep governance layer that enforces isolation of experimental/disposable artifacts and limits packet size.

## .claude/CLAUDE.md

### Authority status
- Loader/enforcer (if known from grep): Project-local agent entry point.
- Last modified: 2026-05-07 06:40:09 -0500 fe8d0d79a5

### Verbatim extract (taxonomy-relevant only)
> .claude/CLAUDE.md:1 — Must navigate to detailed project instructions, navigation, authority order, and working rules at the project root AGENTS.md.

### One-line semantic gloss
Bootstrap pointer that immediately redirects agents to the root AGENTS.md for authority.

## CONFLICTS / SILENCES DETECTED (input-stage only — do NOT resolve)

- **Conflict**: `naming_conventions.yaml` (line 140) specifies `docs/archives/work_packets/branches/...` but `ARCHIVAL_RULES.md` (line 87) and `docs/operations/AGENTS.md` (line 82) specify `docs/operations/archive/<YYYY>-Q<N>/`.
- **Conflict**: `artifact_authority_status.yaml` lists status `CURRENT_HISTORICAL` (line 11), while `docs_registry.yaml` uses `lifecycle_state: historical` (line 53). `ARCHIVAL_RULES.md` introduces a separate `ALREADY_ARCHIVED` verdict (line 34).
- **Silence**: No authority file explicitly defines the structure or naming for `docs/lore/` or `docs/runbooks/` beyond the general `allowed_doc_classes` in `docs_registry.yaml`.
- **Silence**: `naming_conventions.yaml` mentions `docs/archives/` but this directory is listed as "deprioritized" in `REVIEW.md` and doesn't seem to have its own `AGENTS.md`.
- **Silence**: `docs/operations/AGENTS.md` mentions `INDEX.md` in quarterly archives, but there is no machine-readable schema for these index files in the YAML manifests.

## ENFORCERS GREP

Run: `grep -rln "artifact_authority_status\|docs_registry\|ARCHIVAL_RULES\|naming_conventions" src/ scripts/ maintenance_worker/ tests/ 2>/dev/null | head -40`

Report:
- `scripts/topology_doctor.py` - Loads `naming_conventions.yaml` and `docs_registry.yaml`.
- `scripts/topology_doctor_freshness_checks.py` - Loads `naming_conventions.yaml`.
- `scripts/topology_doctor_docs_checks.py` - Loads `docs_registry.yaml`.
- `scripts/topology_doctor_digest.py` - Loads `docs_registry.yaml` and `naming_conventions.yaml`.
- `scripts/archive_migration_2026-05-16.py` - References `ARCHIVAL_RULES.md`.
- `scripts/zpkt.py` - References `docs_registry.yaml`.
- `docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/ARCHIVAL_RULES.md` - Explicitly mentions `artifact_authority_status` in Check #0.
