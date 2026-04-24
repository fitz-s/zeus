# Execution Packets

All packets preserve unrelated dirty work, forbid blanket staging, and keep authority cleanup separate from source/runtime/data behavior.

## P0 — Online Boot Surface Realignment

### Packet name

`workspace_authority_reconstruction_p0_online_boot_surface_realignment`

### Objective

Make the tracked, reviewer-visible boot surfaces tell the same story as repo reality:

- archives are historical and hidden by default,
- graph.db is tracked derived context, not authority,
- graph/context engines are first-class,
- current_state is a live pointer, not a runtime scrapbook.

### Exact files allowed to edit

- AGENTS.md
- workspace_map.md
- docs/README.md
- docs/AGENTS.md
- docs/archive_registry.md
- docs/operations/AGENTS.md
- docs/operations/current_state.md
- architecture/topology.yaml
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json

### Exact files forbidden to edit

- src/**
- tests/**
- scripts/**
- docs/authority/**
- docs/archives/**
- state/**
- raw/**
- .omx/**
- .omc/**
- .code-review-graph/graph.db
- architecture/** except architecture/topology.yaml

### Expected modifications

- Rewrite `AGENTS.md` around runtime/workspace split and authority/context/history layers.
- Rewrite `workspace_map.md` as a visibility matrix.
- Rewrite `docs/README.md` and `docs/AGENTS.md` so archives stop appearing as a live visible peer subtree.
- Add `docs/archive_registry.md` as the visible archive access protocol.
- Slim `docs/operations/current_state.md` into a live control pointer.
- Register the new archive interface in `architecture/topology.yaml`.
- Create/update the active reconstruction packet docs.

### Staging strategy

- Do **not** use `git add -A`.
- Stage only the named files.
- If unrelated dirty work exists, leave it untouched.
- Review with `git diff --cached --check` before commit.

### Verification commands

```bash
python scripts/topology_doctor.py --planning-lock --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --plan-evidence docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md --json
python scripts/topology_doctor.py --work-record --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --work-record-path docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md --json
python scripts/topology_doctor.py --change-receipts --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --receipt-path docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json --json
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py --context-budget --json
python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files AGENTS.md workspace_map.md docs/README.md docs/AGENTS.md docs/archive_registry.md docs/operations/AGENTS.md docs/operations/current_state.md architecture/topology.yaml --json
git diff --cached --check
git status --short
```

### Closeout commands

```bash
git diff --cached --stat
git diff --cached --check
git status --short
```

### Rollback plan

- Unstage the packet files only.
- Revert only the allowed-file batch if the packet fails review.
- Do not touch unrelated dirty work.

### Risk if skipped

Online reviewers continue to inherit the wrong mental model of what is visible, what is live, and what is derived.

### Dependencies

None.
This is the first packet.

### Expected commit message (Lore Commit Protocol)

```text
docs(authority): realign online boot surfaces to machine reality

Lore: BOOT_SURFACE_REALIGNMENT
Packet: task_2026-04-20_workspace_authority_reconstruction
Why: make visible authority truthful for online Pro/review and local Codex
```

## P1 — Machine Visibility and Registry Alignment

### Packet name

`workspace_authority_reconstruction_p1_machine_visibility_alignment`

### Objective

Make the P0 policy sticky by aligning topology/schema/map-maintenance/context-budget and the minimum necessary tests/checkers.

### Exact files allowed to edit

- architecture/topology.yaml
- architecture/topology_schema.yaml
- architecture/map_maintenance.yaml
- architecture/context_budget.yaml
- architecture/artifact_lifecycle.yaml
- docs/README.md
- docs/AGENTS.md
- docs/archive_registry.md
- docs/operations/AGENTS.md
- docs/operations/current_state.md
- scripts/topology_doctor_map_maintenance.py
- scripts/topology_doctor_registry_checks.py
- tests/test_topology_doctor.py
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json

### Exact files forbidden to edit

- src/**
- scripts/code_review_graph_mcp_readonly.py
- scripts/topology_doctor_code_review_graph.py
- .code-review-graph/graph.db
- docs/archives/**
- state/**
- raw/**
- .omx/**
- .omc/**

### Expected modifications

- Update topology/schema so hidden archives are no longer modeled like a visible default docs subtree.
- Add or adjust companion rules for `docs/archive_registry.md`.
- Add context-budget protection for `current_state.md` and archive interface.
- Add only the tests/checkers needed to protect these new claims.

### Staging strategy

- Stage architecture/checker/test files separately from docs files.
- Review the staged diff in two passes: semantics first, test fallout second.

### Verification commands

```bash
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py --strict --json
python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files architecture/topology.yaml architecture/topology_schema.yaml architecture/map_maintenance.yaml architecture/context_budget.yaml architecture/artifact_lifecycle.yaml docs/archive_registry.md docs/README.md docs/AGENTS.md docs/operations/current_state.md docs/operations/AGENTS.md scripts/topology_doctor_map_maintenance.py scripts/topology_doctor_registry_checks.py tests/test_topology_doctor.py --json
pytest -q tests/test_topology_doctor.py -k "docs or registry or current_state or map_maintenance"
git diff --cached --check
```

### Closeout commands

```bash
git diff --cached --stat
git diff --cached --check
pytest -q tests/test_topology_doctor.py -k "docs or registry or current_state or map_maintenance"
```

### Rollback plan

Revert only the P1 batch. Leave P0 intact.

### Risk if skipped

P0 remains a prose repair that can drift back out of sync with machine checks.

### Dependencies

- P0 implemented and reviewed.

### Expected commit message (Lore Commit Protocol)

```text
chore(topology): encode visibility and archive-interface rules in machine law

Lore: VISIBILITY_LAW_ALIGNMENT
Packet: task_2026-04-20_workspace_authority_reconstruction
Why: prevent boot-surface truth from drifting away from topology checks
```

## P2 — Graph Portability and Online Summary Upgrade

### Packet name

`workspace_authority_reconstruction_p2_graph_portability_and_summary`

### Objective

Upgrade Code Review Graph from underexplained tracked blob to portable, trustworthy derived context plane.

### Exact files allowed to edit

- .gitignore
- .code-review-graph/.gitignore
- .code-review-graph/graph_meta.json
- architecture/topology.yaml
- architecture/artifact_lifecycle.yaml
- architecture/context_budget.yaml
- architecture/script_manifest.yaml
- scripts/code_review_graph_mcp_readonly.py
- scripts/topology_doctor.py
- scripts/topology_doctor_cli.py
- scripts/topology_doctor_code_review_graph.py
- scripts/topology_doctor_context_pack.py
- tests/test_topology_doctor.py
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json

### Exact files forbidden to edit

- src/**
- docs/authority/**
- docs/archives/**
- state/**
- raw/**
- .omx/**
- .omc/**
- runtime DBs

### Expected modifications

- Remove hardcoded repo-root assumptions from `scripts/code_review_graph_mcp_readonly.py`.
- Prefer repo-relative or explicitly disclosed path handling.
- Optionally add tracked `.code-review-graph/graph_meta.json` sidecar.
- Teach topology_doctor graph/status/context-pack lanes to surface path mode and usability cleanly.
- Add targeted tests only.

### Staging strategy

- Stage tooling changes first, then tests, then any graph meta sidecar.
- Stage regenerated graph artifacts only after verification and explicit human approval.

### Verification commands

```bash
python scripts/topology_doctor.py --code-review-graph-status --json
python scripts/topology_doctor.py --context-packs --json
python -m py_compile scripts/code_review_graph_mcp_readonly.py scripts/topology_doctor.py scripts/topology_doctor_cli.py scripts/topology_doctor_code_review_graph.py scripts/topology_doctor_context_pack.py
pytest -q tests/test_topology_doctor.py -k "code_review_graph or context_pack"
git diff --cached --check
```

### Closeout commands

```bash
git diff --cached --stat
git diff --cached --check
python scripts/topology_doctor.py --code-review-graph-status --json
```

### Rollback plan

- Revert wrapper/tooling/test changes as one batch.
- If graph artifacts were staged, unstage them first and verify the code-only diff separately.

### Risk if skipped

The repo will keep a powerful tracked graph artifact while carrying laptop-bound portability defects and weak online transparency.

### Dependencies

- P0 complete.
- P1 strongly preferred.
- Local graph build/update workflow verified.

### Expected commit message (Lore Commit Protocol)

```text
chore(graph): harden code-review-graph portability and online visibility

Lore: DERIVED_CONTEXT_PORTABILITY
Packet: task_2026-04-20_workspace_authority_reconstruction
Why: make tracked graph context usable across worktrees and online review
```

## P3 — Historical Compression and Residual Hygiene

### Packet name

`workspace_authority_reconstruction_p3_history_compression_and_residual_cleanup`

### Objective

Compress the most important historical lessons into visible, dense surfaces and remove residual stale references without widening into archive ingestion.

### Exact files allowed to edit

- workspace_map.md
- docs/README.md
- docs/AGENTS.md
- docs/archive_registry.md
- architecture/history_lore.yaml
- architecture/context_budget.yaml
- tests/test_topology_doctor.py
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/plan.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/work_log.md
- docs/operations/task_2026-04-20_workspace_authority_reconstruction/receipt.json

### Exact files forbidden to edit

- src/**
- scripts/** except tests needed for lore routing
- .code-review-graph/graph.db
- docs/archives/** bodies
- state/**
- raw/**
- .omx/**
- .omc/**

### Expected modifications

- Enrich `docs/archive_registry.md` with better category/index guidance.
- Promote only high-signal historical lessons into `architecture/history_lore.yaml`.
- Clean up remaining stale archive/default-read references in visible docs.
- Optionally tune context budgets for the final visible-history surface.

### Staging strategy

- Separate lore updates from docs wording cleanup.
- Treat all archive-derived promotions as explicit, sanitized rewrites.

### Verification commands

```bash
python scripts/topology_doctor.py --history-lore --json
python scripts/topology_doctor.py --docs --json
pytest -q tests/test_topology_doctor.py -k "history or docs or archive"
git diff --cached --check
```

### Closeout commands

```bash
git diff --cached --stat
git diff --cached --check
python scripts/topology_doctor.py --history-lore --json
```

### Rollback plan

Revert only the lore/docs cleanup batch.
Do not touch earlier packets.

### Risk if skipped

The repo will be operationally repaired but still weaker than it could be at preserving hard-won lessons in a compact, visible form.

### Dependencies

- P0 complete.
- P1/P2 optional but preferred.

### Expected commit message (Lore Commit Protocol)

```text
docs(history): compress archive lessons into visible routing surfaces

Lore: HISTORY_COMPRESSION
Packet: task_2026-04-20_workspace_authority_reconstruction
Why: preserve high-signal lessons without making archives default context
```
