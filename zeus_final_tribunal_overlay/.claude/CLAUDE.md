File: .claude/CLAUDE.md
Disposition: PATCH
Authority basis: AGENTS.md; architecture/self_check/authority_index.md; docs/governance/zeus_autonomous_delivery_constitution.md.
Supersedes / harmonizes: the previous `.claude/CLAUDE.md` authority section that pointed to historical top-level docs.
Why this file exists now: some operator and Claude-side flows still read `.claude/CLAUDE.md`, so it must stop acting like rival authority during transition.
Current-phase or long-lived: Current-phase only.

# Zeus Claude compatibility shim

This file is a compatibility brief.
It is **not** the principal architecture or governance authority.

## Read order
1. `AGENTS.md`
2. `architecture/self_check/authority_index.md`
3. `docs/governance/zeus_autonomous_delivery_constitution.md`
4. scoped `AGENTS.md`
5. then code

## Authority rule
- machine-checkable manifests outrank this file
- constitutions outrank this file
- historical docs do not outrank this file, and this file does not promote them

## Historical docs
The following are rationale/history unless an active authority file explicitly references them:
- `docs/specs/zeus_spec.md`
- `docs/architecture/zeus_blueprint_v2.md`
- `docs/architecture/zeus_design_philosophy.md`
- `docs/plans/zeus_live_plan.md`

## External boundary
Workspace/OpenClaw/Venus files remain external operator surfaces unless a packet is explicitly boundary-focused.
