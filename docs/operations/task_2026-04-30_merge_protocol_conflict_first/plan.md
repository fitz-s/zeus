# Merge Protocol Conflict-First Plan

Date: 2026-04-30
Branch: `topology-profile-resolver-stability-2026-04-29`
Status: implementation packet

## Goal

Replace the unconditional cross-worktree merge critic gate with a
conflict-first protocol:

1. inspect the merge/conflict surface first
2. merge directly when there are no conflicts
3. resolve narrow mechanical conflicts directly
4. escalate to critic verdict evidence only for broad, high-risk, cross-zone,
   or semantically ambiguous conflicts

## Touched Authority Surfaces

- root `AGENTS.md`
- `.agents/skills/zeus-ai-handoff/SKILL.md`
- `.claude/hooks/pre-merge-contamination-check.sh`
- `.claude/settings.json`
- `architecture/worktree_merge_protocol.yaml`
- `architecture/AGENTS.md`
- `architecture/topology.yaml`

## Invariant References

- Preserve the contamination-remediation antibody: broad/high-risk merge
  conflicts still require independent critic evidence.
- Remove unconditional process tax for clean or narrow merges.
- Hook behavior must match active documentation.

## Acceptance

- The source of the old rule is documented.
- Hook no longer blocks protected-branch merge-class commands solely because
  `MERGE_AUDIT_EVIDENCE` is absent.
- Hook still validates and blocks invalid/BLOCK evidence when
  `MERGE_AUDIT_EVIDENCE` is provided.
- Architecture protocol, root guidance, skill guidance, and hook registration
  describe the same conflict-first flow.
