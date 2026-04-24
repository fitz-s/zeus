# Topology Compiler Program

Created: 2026-04-13
Branch: `data-improve`
Status: active

## Purpose

Build a machine-checkable topology layer so a zero-context agent can determine:

- which laws apply globally
- which laws apply to touched files
- what files are allowed or forbidden for a task
- what downstream surfaces can be affected
- which tests and static gates prove safety

## Packet Sequence

| Packet | Status | Scope |
|---|---|---|
| Packet 0 | closed | read-only topology inventory baseline |
| Packet 1 | closed | WMO half-up executable rounding law and recurrence gates |
| Packet 2 | closed | topology compiler MVP and digest command |
| Packet 3 | closed | docs mesh repair and authority normalization |
| Packet 4 | closed | source file rationale map |

## Packet 4 Acceptance

- `architecture/source_rationale.yaml` exists with hazard badges, write routes, package defaults, and per-file entries.
- `topology_doctor --source` exits 0 (zero missing rationale, zero stale entries, all required fields present).
- All write-route-bearing files have correct route assignments.
- `topology_doctor --strict` exits 0.

## Packet 3 Acceptance

- `docs/operations/current_state.md` exists and is current.
- Active docs registries do not point at missing files.
- Active docs paths do not contain spaces.
- `docs/runbooks/` has an `AGENTS.md` registry.
- `architecture/self_check/authority_index.md` exists.
- Reference docs do not claim parallel source-of-truth authority.
- `python scripts/topology_doctor.py --docs` passes.

## Boundaries

- Full `topology_doctor --strict` is not expected to pass in Packet 3.
- Scripts and state artifact classification remain later packets.
- Data rebuild remains a separate packet.
