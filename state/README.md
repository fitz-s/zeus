# Runtime State

This directory is reserved for local runtime databases, live control state,
projections, heartbeats, and telemetry. These files are intentionally not
tracked.

Canonical schema, invariants, and reproducible source code live in `src/`,
`architecture/`, `tests/`, and `docs/authority/`.

The only files tracked here right now are this `README.md` and `.gitkeep`.
`.gitignore` also carves out `*.sha256` / `*.md5` (DB snapshot audit hash
sidecars) so they can be tracked when present; none exist in the tree at
present.
