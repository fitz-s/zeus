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

## Dated documents and supersession

A date in a filename (e.g. `*_2026-06-13.md`) marks WHEN an authority was
ratified, never an expiry. Such a doc is still law until explicitly superseded.
To keep "which law is in force" answerable at a glance:

- Every dated authority doc carries a top-of-file `Status:` line — one of
  `ACTIVE`, `SUPERSEDED_BY: <file>`, or `ARCHIVED_REFERENCE`.
- When a newer doc replaces an older one in the same domain, mark the old
  `SUPERSEDED_BY:` and the new `ACTIVE` — do not delete the old (it is the
  ratification record), but it stops being consulted as live law.
- The newest `ACTIVE` doc in a domain wins on conflict.
- `current` in a filename (`zeus_current_*`) means **in-force law**, not
  "present runtime posture." Runtime posture lives in `docs/operations/`, never
  here. Do not encode bankrolls, flag states, SHAs, or "currently …" runtime
  snapshots in any file in this directory.

## File registry

| File | Status | Purpose |
|------|--------|---------|
| `zeus_current_architecture.md` | ACTIVE | In-force architecture law — truth ownership, lifecycle semantics, risk behavior, zone boundaries |
| `zeus_current_delivery.md` | ACTIVE | In-force delivery law — authority order, planning lock, packet doctrine, completion protocol |
| `zeus_change_control_constitution.md` | ACTIVE | Deep packet governance rules (Chinese language) |
| `ARCHIVAL_RULES.md` | ACTIVE | Active archival rules for workspace packets and operations hygiene |
| `replacement_final_form_2026_06_09.md` | ACTIVE | Probability strategy of record — the replacement_forecast chain |
| `regime_unification_2026-06-12.md` | ACTIVE | Single-q regime law (one live probability authority) |
| `statistical_calibration_authority_2026-06-12.txt` | ACTIVE | Calibration authority (base) |
| `statistical_calibration_authority_2026-06-12_README.md` | ACTIVE | Calibration authority README |
| `statistical_calibration_addendum_2026-06-13.md` | ACTIVE | Calibration authority extension to the 2026-06-12 base |
| `exit_portfolio_execution_authority_2026-06-13.md` | ACTIVE | Exit / portfolio-Kelly / dynamic-execution math |
| `consult2_crossvalidation_fable5_2026-06-13.md`, `statistical_calibration_crossvalidation_fable5_2026-06-12.md` | ARCHIVED_REFERENCE | Cross-validation evidence behind the calibration authority |

Historical architecture/design files live behind the archive interface
(`docs/archive_registry.md`). They are evidence, not active law.
