File: docs/authority/AGENTS.md
Disposition: NEW
Authority basis: docs/authority/zeus_autonomous_delivery_constitution.md; docs/authority/zeus_top_tier_decision_register.md; current repo/operator reality.
Supersedes / harmonizes: scattered workflow guidance; dossier-only delivery rules; former docs/governance/AGENTS.md.
Why this file exists now: authority docs drift fastest under agentic work unless their scope is explicit.
Current-phase or long-lived: Long-lived.

# docs/authority AGENTS

This directory defines how Zeus is architected, changed, delivered, verified, and operated.

## Required posture
- never invent authority that is not backed by spec/manifests/runtime truth
- keep operator reality honest
- distinguish advisory from required
- keep current-phase vs end-state explicit

## Do
- update decision register when a high-stakes choice changes
- update runbook/cookbook when runtime commands or policy change
- mark sunset-review surfaces clearly

## Do not
- hide uncertainty under polished prose
- turn dossiers into primary authority
- let runbooks outrank constitutions or manifests

## File registry

| File | Purpose |
|------|---------|
| `zeus_architecture.md` | Architecture reference — DB schema, event spine, truth surfaces, projection model |
| `zeus_target_state.md` | Target-state — P9-P11 roadmap, endgame clause, convergence criteria |
| `zeus_autonomous_delivery_constitution.md` | Delivery constitution — packet discipline, escalation gates, closure rules |
| `zeus_change_control_constitution.md` | Deep packet governance rules (Chinese language) |
| `zeus_packet_discipline.md` | Packet discipline — program/packet/slice, closure, pre/post-closeout, waivers |
| `zeus_autonomy_gates.md` | Autonomy gates — post-P0.5 rule, team mode entry, escalation |
| `zeus_micro_event_logging.md` | Micro-event logging format, when to log, template |
| `zeus_openclaw_venus_delivery_boundary.md` | Boundary law between Zeus, Venus, and OpenClaw |
| `zeus_top_tier_decision_register.md` | High-stakes decision register — irreversible choices and their rationale |
| `zeus_discrete_settlement_support_amendment.md` | Amendment: discrete settlement semantics for integer rounding |
| `zeus_implementation_decisions.md` | P1-P8 implementation decisions — WHY/WHY NOT rationale, fact layer schemas, migration plan, coding OS |
| `team_policy.md` | Team mode usage rules (loaded on demand) |
