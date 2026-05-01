# codex_p0_execute_data_audit_fixes.md

You are local Codex operating on Zeus branch `data-improve`. Execute P0 only. Do not rebuild data and do not mutate production DB.

Goals:
1. Add a read-only `scripts/audit_world_data_readiness.py` command that runs the SQL checks in this package or equivalent ORM queries.
2. Add fail-closed readiness gates for live/replay/calibration when critical canonical tables are empty or unsafe.
3. Add evidence-only views/constants for legacy `hourly_observations`, v1 `settlements`, and empty-provenance `observations`.
4. Add tests with unsafe fixture rows proving consumers reject: empty provenance, null market_slug, fallback source role, missing issue_time, reconstructed available_at, and legacy hourly table.

Constraints:
- No source behavior changes.
- No data rebuilds.
- No destructive migrations.
- All changes must be reversible and separately committed.

Verification:
- Run unit tests.
- Run readiness command against a copy of zeus-world.db.
- Save outputs for review.
