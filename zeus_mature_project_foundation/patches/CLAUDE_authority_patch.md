# CLAUDE.md authority-stack patch

Replace the current “Design Authority” section with the following block:

## Design authority (mature-project stack)

Read these in order before architecture or schema work:

1. `docs/architecture/zeus_durable_architecture_spec.md`
   - principal architecture authority
   - owns system shape, lifecycle law, migration order, and gate matrix

2. `docs/governance/zeus_change_control_constitution.md`
   - change-control authority
   - owns packet grammar, zero-context routing, negative permissions, and evidence burden

3. `architecture/kernel_manifest.yaml`
4. `architecture/invariants.yaml`
5. `architecture/zones.yaml`
6. `architecture/negative_constraints.yaml`
7. `architecture/maturity_model.yaml`
   - machine-checkable semantic authority

`.claude/CLAUDE.md` is an operator brief, not the principal architecture source.

Historical rationale only (non-authoritative unless explicitly referenced by the principal spec):
- `docs/architecture/zeus_blueprint_v2.md`
- `docs/reference/zeus_first_principles_rethink.md`
- `ZEUS_PROGRESS.md`
