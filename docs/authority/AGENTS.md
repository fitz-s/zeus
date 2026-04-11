File: docs/authority/AGENTS.md
Disposition: NEW
Authority basis: docs/authority/zeus_current_architecture.md; docs/authority/zeus_current_delivery.md; current repo/operator reality.
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
- update runbook/cookbook when runtime commands or policy change
- mark sunset-review surfaces clearly
- update current authority files when active law changes

## Do not
- hide uncertainty under polished prose
- turn dossiers into primary authority
- let runbooks outrank constitutions or manifests

## File registry

| File | Purpose |
|------|---------|
| `zeus_current_architecture.md` | Current architecture law — truth ownership, lifecycle semantics, risk behavior, zone boundaries |
| `zeus_current_delivery.md` | Current delivery law — authority order, planning lock, packet doctrine, completion protocol |
| `zeus_change_control_constitution.md` | Deep packet governance rules (Chinese language) |
| `zeus_packet_discipline.md` | Packet discipline — program/packet/slice, closure, pre/post-closeout, waivers, market-math requirements, micro-event logging |
| `zeus_autonomy_gates.md` | Autonomy gates — destructive-ops human gate, team mode entry/restrictions, one-packet-at-a-time rule |
| `zeus_openclaw_venus_delivery_boundary.md` | Boundary law between Zeus, Venus, and OpenClaw |

Historical architecture/design files live in `docs/archives/governance_doc_restructuring/` and are not part of the default authority read path.
