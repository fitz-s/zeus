# codex_p3_execute_usage_path_hardening.md

Execute after P2 passes.

Goals:
1. Make calibration/replay/live consumers read safe eligibility views, not raw legacy tables.
2. Ban `hourly_observations` and v1 settlements from canonical paths except through explicit evidence adapters.
3. Require market identity for settlement/replay.
4. Require `training_allowed=1`, `causality_status='OK'`, `authority='VERIFIED'`, eligible source_role, and valid provenance for training.
5. Add consumer-level tests proving unsafe rows are ignored even when present.

Verification:
- Grep/read tests confirm no raw unsafe table reads in canonical paths.
- Replay/calibration fail if v2 market/forecast inputs are absent.
