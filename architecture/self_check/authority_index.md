# Zeus authority index

This is the first durable routing file for humans and coding agents.

## 1. Read order

### Principal system-shape authority
1. `docs/architecture/zeus_durable_architecture_spec.md`

### Change-control authority
2. `docs/governance/zeus_change_control_constitution.md`

### Machine-checkable semantic authority
3. `architecture/kernel_manifest.yaml`
4. `architecture/invariants.yaml`
5. `architecture/zones.yaml`
6. `architecture/negative_constraints.yaml`
7. `architecture/maturity_model.yaml`

### Operator brief
8. `.claude/CLAUDE.md`

### Historical rationale (non-authoritative)
9. `docs/architecture/zeus_blueprint_v2.md`
10. `docs/KEY_REFERENCE/zeus_first_principles_rethink.md`
11. `docs/progress/zeus_progress.md`

## 2. Precedence

If two sources disagree:

1. machine-checkable semantic authority (when explicit)
2. principal architecture spec
3. change-control constitution
4. operator brief
5. historical rationale
6. code comments
7. generated code / LLM explanations

## 3. What each source is for

- principal spec: system shape, migration order, architecture priorities
- constitution: how changes are allowed to happen
- manifests: exact semantic atoms, zones, negative permissions, maturity stage
- operator brief: concise runtime/session guidance
- historical docs: reasoning context only

## 4. Never do this

- Never treat `docs/progress/zeus_progress.md` as architecture authority.
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
