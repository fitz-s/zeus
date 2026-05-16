# Dual-Track Metric Spine Refactor — Scoped AGENTS

This directory is the packet home for the Zeus Dual-Track Metric Spine
Refactor. Read `plan.md` before working in this packet.

## Boundaries

- This packet is not the Topology Enforcement Hardening packet. Do not touch
  its files, its receipts, or its active work record.
- Phase 0 writes only to documentation surfaces. Any source, schema, test, or
  script edit under Phase 0 is out of contract.
- Map/manifest maintenance is deferred to packet closeout. Do not update
  `workspace_map.md`, scoped `AGENTS.md`, or machine manifests mid-phase unless
  the authority file you are editing explicitly requires it.

## Required reads for a zero-context agent entering this packet

1. Root `AGENTS.md`
2. `docs/authority/zeus_current_architecture.md`
3. `docs/authority/zeus_dual_track_architecture.md` (created in Phase 0)
4. `plan.md` (this directory)
5. Current phase's evidence subdirectory

## Evidence layout

- `plan.md`
- `work_log.md`
- `phase{N}_evidence/` per phase
- `receipt.json` at every high-risk closeout
