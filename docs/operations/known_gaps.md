# Known Gaps

This is the canonical home for active known gaps. (It was previously a pointer to
`docs/to-do-list/known_gaps.md`, a location that no longer exists; the pointer is
removed to avoid a dead link.) Per-task open work also lives under
`docs/operations/current/`.

## Active gaps

- **LOW-track fusion coverage** (2026-07-21 read-only forecasts.db audit): `openmeteo_ecmwf_ifs9_bayes_fusion_v1` LOW track covers only 8/54 cities (Hong Kong, London, Miami, NYC, Paris, Seoul, Shanghai, Tokyo); the other 46 cities' LOW track is served only by the coarse 0.25° `ecmwf_open_data` fallback. HIGH track covers 49/54; 5 cities (Auckland, Jakarta, Jinan, Lagos, Zhengzhou) have zero `raw_model_forecasts` HIGH rows — genuinely data-gated. Re-query to confirm before acting.
- **109-table drop follow-up** (preserved from `legacy_archived_drop_safety_audit_2026-07-01`, archived 2026-07-21): that audit's 109-table remediation recommendation was rendered but never tracked — confirm superseded or action it.
- **heartbeat-sensor plist stale path** (preserved from `zeus_home_repo_migration`, archived 2026-07-21): the heartbeat-sensor launchd plist still points at the old `workspace-venus/bin` path; repoint to `~/zeus`.
