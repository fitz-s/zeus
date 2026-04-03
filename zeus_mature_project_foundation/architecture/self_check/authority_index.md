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
10. `docs/reference/zeus_first_principles_rethink.md`
11. `ZEUS_PROGRESS.md`

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

- Never treat `ZEUS_PROGRESS.md` as architecture authority.
- Never treat `status_summary.json` or `positions.json` as canonical truth.
- Never let a coding agent infer authority order from retrieval similarity.
