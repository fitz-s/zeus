File: docs/authority/AGENTS.md
Disposition: NEW
Authority basis: docs/authority/zeus_current_architecture.md; docs/authority/zeus_current_delivery.md; current repo/operator reality.
Supersedes / harmonizes: scattered workflow guidance; dossier-only delivery rules; former governance-directory instructions.
Why this file exists now: authority docs drift fastest under agentic work unless their scope is explicit.
Current-phase or long-lived: Long-lived.

# docs/authority AGENTS

This directory contains durable authority law only.

It is not a holding area for packet deliverables, ADRs, fix-pack notes,
rollback doctrine, or historical governance evidence.

## Required posture
- never invent authority that is not backed by spec/manifests/runtime truth
- keep operator reality honest
- distinguish advisory from required
- keep current-phase vs end-state explicit
- keep this directory small enough that a cold-start agent can see the full
  durable law surface without guessing which files are current

## Do
- update runbook/cookbook when runtime commands or policy change
- mark sunset-review surfaces clearly
- update current authority files when active law changes
- move packet/ADR/history material to evidence surfaces instead of keeping it
  here
- preserve demoted history under reports or archive interfaces

## Do not
- hide uncertainty under polished prose
- turn dossiers into primary authority
- let runbooks outrank constitutions or manifests
- leave `task_YYYY-MM-DD_*`, `*_adr.md`, fix-pack notes, or one-off packet
  doctrine in this directory

## File registry

| File | Purpose |
|------|---------|
| `zeus_current_architecture.md` | Current architecture law — truth ownership, lifecycle semantics, risk behavior, zone boundaries |
| `zeus_current_delivery.md` | Current delivery law — authority order, planning lock, packet doctrine, completion protocol |
| `zeus_change_control_constitution.md` | Deep packet governance rules (Chinese language) |
| `ARCHIVAL_RULES.md` | Active archival rules for workspace packets and operations hygiene |

Historical architecture/design files live in `docs/reports/authority_history/`
or the archive interface. They are evidence, not active law.
