# Zeus authority index

This is the first durable routing file for humans and coding agents.

## 1. Read order

### Root authority guide
1. `ZEUS_AUTHORITY.md`

### Principal present-tense architecture authority
2. `docs/architecture/zeus_durable_architecture_spec.md`

### Terminal target-state / endgame authority
3. `docs/zeus_FINAL_spec.md`

### Change-control authority
4. `docs/governance/zeus_change_control_constitution.md`
5. `docs/governance/zeus_autonomous_delivery_constitution.md`

### Machine-checkable semantic authority
6. `architecture/kernel_manifest.yaml`
7. `architecture/invariants.yaml`
8. `architecture/zones.yaml`
9. `architecture/negative_constraints.yaml`
10. `architecture/maturity_model.yaml`

### Repo operating brief
11. `AGENTS.md`

### Active control / execution surfaces
12. `docs/control/current_state.md`
13. current work packet named in `docs/control/current_state.md`
14. `docs/known_gaps.md`

### Historical rationale / archives (non-authoritative)
15. `docs/architecture/zeus_blueprint_v2.md`
16. `docs/KEY_REFERENCE/zeus_first_principles_rethink.md`
17. `docs/archives/**`
18. `docs/reference/workspace_map.md`

## 2. Precedence

If two sources disagree:

1. machine-checkable semantic authority (when explicit)
2. principal present-tense architecture authority
3. terminal target-state / endgame authority
4. change-control authority
5. repo operating brief
6. active control / execution surfaces
7. historical rationale / archives
8. code comments
9. generated code / LLM explanations

## 3. What each source is for

- principal present-tense architecture: current system shape, migration order, architecture priorities, and present-tense routing
- root authority guide: one-file compression of the system foundation, live invariants, negative constraints, and boundary rules
- terminal target-state / endgame: finality framing, target-state intent, and endgame clause
- change-control authority: how changes are allowed to happen
- manifests: exact semantic atoms, zones, negative permissions, maturity stage
- repo operating brief: repo-native execution rules and reading discipline
- active control surfaces: one live current-state pointer plus the current work packet and antibody register
- historical docs / archives: reasoning context only

## 4. Never do this

- Never treat `docs/archives/**` or `docs/reference/workspace_map.md` as principal authority.
- Never let `ZEUS_AUTHORITY.md` compete with the exact precedence or machine-checkable authority files it summarizes.
- Never treat `status_summary.json` or `positions.json` as canonical truth.
- Never let a coding agent infer authority order from retrieval similarity.
- Never treat `zeus_mature_project_foundation/` as the active law location after the mirrored authority files have been installed.

## 5. Source-package note

`zeus_mature_project_foundation/` is preserved in-repo as the imported source package that supplied the current authority install.

- It is for provenance, diffing, and future reconciliation.
- It is not the active authority path for normal repo work.
- Normal repo work should read and edit the mirrored active files in:
  - `architecture/`
  - `docs/architecture/`
  - `docs/governance/`
  - `docs/rollout/`
