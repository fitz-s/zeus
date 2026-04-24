# Runtime State

This directory is reserved for local runtime databases, live control state,
projections, heartbeats, and telemetry. These files are intentionally not
tracked.

Canonical schema, invariants, and reproducible source code live in `src/`,
`architecture/`, `tests/`, and `docs/authority/`.

The only tracked files here are:
- `assumptions.json` — config seed / reference data
- `*.sha256` / `*.md5` — DB snapshot audit hash sidecars
