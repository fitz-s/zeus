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
| `zeus_packet_discipline.md` | Sunset-pending packet discipline law; merge into current delivery in authority-kernel P1/P2 |
| `zeus_autonomy_gates.md` | Sunset-pending autonomy gate law; merge into current delivery in authority-kernel P1/P2 |
| `zeus_openclaw_venus_delivery_boundary.md` | Sunset-pending Zeus/Venus/OpenClaw boundary law; merge into current delivery in authority-kernel P1/P2 |
| `zeus_live_backtest_shadow_boundary.md` | Sunset-pending live/backtest/shadow boundary law; merge into current authority in authority-kernel P1/P2 |
| `zeus_data_rebuild_adr.md` | Sunset-pending data rebuild ADR evidence; demote after core authority retarget in P2 |
| `zeus_dual_track_architecture.md` | Sunset-pending dual-track law; merge into current architecture in authority-kernel P1/P2 |
| `zeus_k4_fix_pack_adr.md` | Sunset-pending K4.5 fix-pack ADR evidence; demote in P2 |

Historical architecture/design files live in `docs/reports/authority_history/`
or the archive interface. They are evidence, not active law.
